"""Steady thermal/Herschel--Bulkley pipe coupling using the slurry owner.

The finite-volume closure assumes a fixed-composition incompressible mixture,
one constant bore, and fully developed homogeneous transport. The property
provider supplies pressure-consistent enthalpy and temperature-dependent
rheology. Pressure dissipation is calculated only by ``slurry_transport``.
"""
from __future__ import annotations

import inspect
import json
import math
from bisect import bisect_right
from importlib.metadata import version
from pathlib import Path
from typing import Literal, Protocol

from pydantic import Field, model_validator

from . import slurry_transport
from .contracts import Contract, Fraction, Name, Nonnegative, Positive, RunContext, digest, json_value
from .datasets import file_digest
from .slurry_transport import SlurryPropertySnapshot, SlurryTransportRequest

SHA = r"^[a-f0-9]{64}$"
REVIEWS = (
    "fixed_composition_incompressible_single_phase",
    "temperature_rheology_and_enthalpy_support",
    "shared_energy_reference_including_pressure_work",
    "midpoint_cell_heat_and_mesh_applicability",
)


class _HydraulicTrialWithheld(RuntimeError):
    def __init__(self, record):
        self.record = record
        super().__init__("slurry owner withheld the coupled hydraulic trial")


class SlurryThermalState(Contract):
    pressure_pa_abs: Positive
    temperature_K: Positive
    specific_enthalpy_J_kg: float
    heat_capacity_J_kg_K: Positive
    mixture_id: Name
    enthalpy_reference_id: Name
    rheology: SlurryPropertySnapshot

    @model_validator(mode="after")
    def coherent(self):
        if self.rheology.temperature_K != self.temperature_K:
            raise ValueError("thermal state and rheology must describe the same temperature")
        return self


class SlurryThermalProvider(Protocol):
    def metadata(self) -> dict: ...
    def resolve(self, *, pressure_pa_abs: float, temperature_K: float) -> SlurryThermalState: ...


class SlurryThermalKnot(Contract):
    temperature_K: Positive
    thermal_enthalpy_J_kg: float
    consistency_Pa_s_n: Positive
    flow_index: Positive
    yield_stress_Pa: Nonnegative
    heat_capacity_J_kg_K: Positive | None = None


class SlurryHeatCapacityKnot(Contract):
    temperature_K: Positive
    heat_capacity_J_kg_K: Positive
    consistency_Pa_s_n: Positive
    flow_index: Positive
    yield_stress_Pa: Nonnegative


class SlurryThermalTable(Contract):
    mixture_id: Name
    enthalpy_reference_id: Name
    property_template: SlurryPropertySnapshot
    knots: tuple[SlurryThermalKnot, ...] = Field(min_length=2, max_length=10000)
    thermal_interpolation: Literal["linear_enthalpy", "integrated_linear_heat_capacity"] = "linear_enthalpy"
    enthalpy_knot_absolute_tolerance_J_kg: Nonnegative = 1e-6
    enthalpy_knot_relative_tolerance: Nonnegative = Field(default=1e-10, le=1e-3)

    @model_validator(mode="after")
    def support(self):
        if any(b.temperature_K <= a.temperature_K or b.thermal_enthalpy_J_kg <= a.thermal_enthalpy_J_kg
               for a, b in zip(self.knots, self.knots[1:])):
            raise ValueError("thermal table temperature and thermal enthalpy must strictly increase")
        lower, upper = self.property_template.temperature_support_K
        if self.knots[0].temperature_K < lower or self.knots[-1].temperature_K > upper:
            raise ValueError("thermal knots exceed the reviewed rheology temperature support")
        cp_values = [k.heat_capacity_J_kg_K for k in self.knots]
        if self.thermal_interpolation == "linear_enthalpy" and any(cp is not None for cp in cp_values):
            raise ValueError("linear enthalpy interpolation cannot silently ignore supplied heat-capacity knots")
        if self.thermal_interpolation == "integrated_linear_heat_capacity":
            if any(cp is None for cp in cp_values):
                raise ValueError("integrated heat-capacity mode requires a positive cp at every knot")
            for left, right in zip(self.knots, self.knots[1:]):
                integral = (left.heat_capacity_J_kg_K+right.heat_capacity_J_kg_K)*(right.temperature_K-left.temperature_K)/2
                declared = right.thermal_enthalpy_J_kg-left.thermal_enthalpy_J_kg
                if abs(declared-integral) > self.enthalpy_knot_absolute_tolerance_J_kg+self.enthalpy_knot_relative_tolerance*max(abs(declared),abs(integral)):
                    raise ValueError("enthalpy knots do not reconcile with the integrated heat-capacity curve")
        for knot in self.knots:
            data = self.property_template.model_dump(mode="python")
            data.update(temperature_K=knot.temperature_K, consistency_Pa_s_n=knot.consistency_Pa_s_n,
                        flow_index=knot.flow_index, yield_stress_Pa=knot.yield_stress_Pa)
            SlurryPropertySnapshot.model_validate(data)
        return self


def slurry_thermal_table_from_heat_capacity(*, mixture_id: str, enthalpy_reference_id: str,
                                           property_template: SlurryPropertySnapshot,
                                           knots: tuple[SlurryHeatCapacityKnot, ...],
                                           thermal_enthalpy_at_first_knot_J_kg: float = 0.0):
    """Integrate a caller-supplied cp curve while preserving its source/rheology basis."""
    if not isinstance(knots, tuple) or not 2 <= len(knots) <= 10000:
        raise ValueError("an explicit bounded tuple of heat-capacity/rheology knots is required")
    values = tuple(SlurryHeatCapacityKnot.model_validate_json(k.model_dump_json()) for k in knots)
    if any(b.temperature_K <= a.temperature_K for a,b in zip(values,values[1:])):
        raise ValueError("heat-capacity source temperatures must increase without sorting")
    enthalpy = thermal_enthalpy_at_first_knot_J_kg
    if not math.isfinite(enthalpy):
        raise ValueError("thermal enthalpy reference must be finite")
    transformed = []
    for i, knot in enumerate(values):
        if i:
            previous = values[i-1]
            enthalpy += (previous.heat_capacity_J_kg_K+knot.heat_capacity_J_kg_K)*(knot.temperature_K-previous.temperature_K)/2
        transformed.append(SlurryThermalKnot(**knot.model_dump(mode="python"),thermal_enthalpy_J_kg=enthalpy))
    return SlurryThermalTable(mixture_id=mixture_id,enthalpy_reference_id=enthalpy_reference_id,
                             property_template=property_template,knots=tuple(transformed),
                             thermal_interpolation="integrated_linear_heat_capacity")


class TabulatedSlurryThermalProvider:
    """Reviewed linear-in-temperature rheology and incompressible enthalpy.

    ``h(T,p) = h_thermal(T) + p/rho`` uses one constant mixture density. Table
    thermal enthalpy is caller supplied, not synthesized from a carrier fluid.
    No interpolation outside the table or phase transition is permitted.
    """

    def __init__(self, table: SlurryThermalTable):
        self.table = SlurryThermalTable.model_validate_json(table.model_dump_json())
        self._temperatures = tuple(k.temperature_K for k in self.table.knots)
        self._configuration_sha256 = digest(self.table)

    def metadata(self):
        if digest(self.table) != self._configuration_sha256:
            raise ValueError("slurry thermal table changed after provider construction")
        return dict(provider_id=self.table.property_template.provider_id,
                    provider_version=self.table.property_template.provider_version,
                    source_sha256=file_digest(Path(__file__)),
                    configuration_sha256=self._configuration_sha256,
                    property_source_sha256=self.table.property_template.source_sha256,
                    closure="piecewise_linear_rheology_and_"+self.table.thermal_interpolation+"_plus_p_over_rho")

    def resolve(self, *, pressure_pa_abs: float, temperature_K: float):
        if not math.isfinite(pressure_pa_abs) or pressure_pa_abs <= 0:
            raise ValueError("slurry property pressure must be positive absolute Pa")
        if not math.isfinite(temperature_K) or not self._temperatures[0] <= temperature_K <= self._temperatures[-1]:
            raise ValueError("slurry temperature lies outside the supplied thermal table")
        self.metadata()
        i = min(max(bisect_right(self._temperatures, temperature_K)-1, 0), len(self._temperatures)-2)
        left, right = self.table.knots[i:i+2]
        fraction = (temperature_K-left.temperature_K)/(right.temperature_K-left.temperature_K)

        def interpolate(name):
            a, b = getattr(left, name), getattr(right, name)
            return a+(b-a)*fraction

        data = self.table.property_template.model_dump(mode="python")
        data.update(temperature_K=temperature_K, consistency_Pa_s_n=interpolate("consistency_Pa_s_n"),
                    flow_index=interpolate("flow_index"), yield_stress_Pa=interpolate("yield_stress_Pa"))
        rheology = SlurryPropertySnapshot.model_validate(data)
        if self.table.thermal_interpolation == "integrated_linear_heat_capacity":
            delta = temperature_K-left.temperature_K
            slope = (right.heat_capacity_J_kg_K-left.heat_capacity_J_kg_K)/(right.temperature_K-left.temperature_K)
            cp = left.heat_capacity_J_kg_K+slope*delta
            thermal_enthalpy = left.thermal_enthalpy_J_kg+left.heat_capacity_J_kg_K*delta+slope*delta*delta/2
        else:
            cp = (right.thermal_enthalpy_J_kg-left.thermal_enthalpy_J_kg)/(right.temperature_K-left.temperature_K)
            thermal_enthalpy = interpolate("thermal_enthalpy_J_kg")
        return SlurryThermalState(
            pressure_pa_abs=pressure_pa_abs, temperature_K=temperature_K,
            specific_enthalpy_J_kg=thermal_enthalpy+pressure_pa_abs/rheology.mixture_density_kg_m3,
            heat_capacity_J_kg_K=cp, mixture_id=self.table.mixture_id,
            enthalpy_reference_id=self.table.enthalpy_reference_id, rheology=rheology)


class HeatedSlurryCell(Contract):
    id: Name
    inlet_port_id: Name
    outlet_port_id: Name
    length_m: Positive
    roughness_m: Nonnegative
    minor_loss_coefficient: Nonnegative = 0.0
    outlet_minus_inlet_elevation_m: float = 0.0
    ambient_temperature_K: Positive
    conductance_W_K: Nonnegative
    heater_into_fluid_W: float = 0.0
    geometry_evidence_id: Name
    heat_boundary_evidence_id: Name


class SlurryHydraulicControls(Contract):
    """Numerical/correlation options forwarded unchanged to the slurry owner."""
    quadrature_absolute_error_m3_s: Positive = 1e-12
    quadrature_relative_error: Positive = Field(default=1e-8, ge=1e-12, le=1e-3)
    quadrature_subinterval_limit: int = Field(default=100, ge=1, le=1000)
    root_absolute_stress_tolerance_Pa: Positive = 1e-8
    root_relative_stress_tolerance: Positive = Field(default=1e-10, ge=1e-14, le=1e-3)
    root_maximum_iterations: int = Field(default=100, ge=1, le=1000)
    flow_balance_absolute_m3_s: Nonnegative = 1e-10
    balance_relative_tolerance: Nonnegative = Field(default=1e-7, le=1e-3)
    energy_rate_balance_absolute_W: Nonnegative = 1e-7
    transition_policy: Literal["withhold", "use_declared_correlation"] = "withhold"
    friction_method: Literal["Clamond", "Colebrook", "Haaland", "Swamee_Jain_1976"] = "Clamond"


class HeatedSlurryRequest(Contract):
    id: Name
    asset_id: Name
    event_id: Name
    clock_id: Name
    time_s: Nonnegative
    mixture_id: Name
    enthalpy_reference_id: Name
    rheology_reference_basis: Name
    rheology_model: Literal["newtonian", "bingham", "power_law", "herschel_bulkley"]
    inlet_pressure_pa_abs: Positive
    inlet_temperature_K: Positive
    mass_flow_kg_s: Positive
    inner_diameter_m: Positive
    reference_mixture_density_kg_m3: Positive
    maximum_relative_density_difference: Nonnegative = Field(default=1e-10, le=1e-4)
    maximum_solid_mass_fraction_difference: Nonnegative = Field(default=1e-10, le=1e-4)
    maximum_omitted_acceleration_pressure_Pa: Nonnegative = 1e-3
    gravity_m_s2: Positive = 9.80665
    solid_volume_fraction: Fraction
    cells: tuple[HeatedSlurryCell, ...] = Field(min_length=1, max_length=4096)
    pressure_support_pa_abs: tuple[Positive, Positive]
    temperature_support_K: tuple[Positive, Positive]
    provider_identity: dict
    property_provider_kind: Literal["table", "custom"] = "custom"
    property_evidence_id: Name
    property_source_sha256: str = Field(pattern=SHA)
    evidence_ids: tuple[Name, ...] = Field(min_length=1)
    reviews: dict[str, bool] = Field(default_factory=lambda: dict.fromkeys(REVIEWS, False))
    review_evidence_ids: dict[str, Name] = Field(default_factory=dict)
    slurry_reviews: dict[str, bool] = Field(default_factory=lambda: dict.fromkeys(slurry_transport.REVIEWS, False))
    slurry_review_evidence_ids: dict[str, Name] = Field(default_factory=dict)
    slurry_owner_pins: dict
    coupling_owner_pins: dict
    maximum_wall_stress_bracket_Pa: Positive
    hydraulic_controls: SlurryHydraulicControls = Field(default_factory=SlurryHydraulicControls)
    maximum_residual_evaluations_per_cell: int = Field(default=150, ge=10, le=10000)
    maximum_total_residual_evaluations: int = Field(default=20000, ge=10, le=1000000)
    maximum_cached_property_states_per_cell: int = Field(default=30000, ge=3, le=300000)
    maximum_total_property_resolutions: int = Field(default=80000, ge=10, le=4000000)
    nonlinear_step_tolerance: Positive = Field(default=1e-11, ge=1e-14, le=1e-3)
    nonlinear_cost_tolerance: Positive = Field(default=1e-11, ge=1e-14, le=1e-3)
    nonlinear_gradient_tolerance: Positive = Field(default=1e-11, ge=1e-14, le=1e-3)
    nonlinear_relative_difference_step: Positive | None = None
    pressure_balance_absolute_Pa: Positive = 1e-3
    energy_balance_absolute_W: Positive = 1e-5
    specific_enthalpy_pressure_closure_tolerance_J_kg: Nonnegative = 1e-5
    balance_relative_tolerance: Nonnegative = Field(default=1e-8, le=1e-3)
    maximum_cell_temperature_change_K: Positive

    @model_validator(mode="after")
    def domains(self):
        if self.pressure_support_pa_abs[0] >= self.pressure_support_pa_abs[1] or self.temperature_support_K[0] >= self.temperature_support_K[1]:
            raise ValueError("heated slurry property bounds must be ordered")
        if set(self.reviews) != set(REVIEWS) or set(self.slurry_reviews) != set(slurry_transport.REVIEWS):
            raise ValueError("all named thermal and slurry reviews are required")
        if set(self.review_evidence_ids)-set(REVIEWS) or set(self.slurry_review_evidence_ids)-set(slurry_transport.REVIEWS):
            raise ValueError("unknown thermal or slurry review evidence keys")
        if len({c.id for c in self.cells}) != len(self.cells) or len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("heated slurry cell and evidence IDs must be unique")
        if any(c.inlet_port_id == c.outlet_port_id for c in self.cells) or any(a.outlet_port_id != b.inlet_port_id for a,b in zip(self.cells,self.cells[1:])):
            raise ValueError("heated slurry cells require distinct end ports and an ordered continuous port chain")
        if not self.pressure_support_pa_abs[0] <= self.inlet_pressure_pa_abs <= self.pressure_support_pa_abs[1] or not self.temperature_support_K[0] <= self.inlet_temperature_K <= self.temperature_support_K[1]:
            raise ValueError("heated slurry inlet lies outside the supplied property support")
        for key in ("provider_id", "provider_version"):
            if not isinstance(self.provider_identity.get(key), str) or not self.provider_identity[key].strip():
                raise ValueError("heated slurry provider identity must include a name and version")
        for key in ("source_sha256", "configuration_sha256", "property_source_sha256"):
            value = self.provider_identity.get(key)
            if not isinstance(value, str) or len(value) != 64 or any(x not in "0123456789abcdef" for x in value):
                raise ValueError("heated slurry provider identity must pin implementation, configuration and property source hashes")
        if self.provider_identity["property_source_sha256"] != self.property_source_sha256:
            raise ValueError("provider identity and selected property source pin differ")
        return self


def native_heated_slurry_metadata():
    from scipy.optimize import least_squares
    from scipy.optimize._lsq import common, trf
    from scipy.linalg import _flapack
    return dict(scipy_version=version("scipy"),
                numpy_version=version("numpy"),
                least_squares_source_sha256=file_digest(Path(inspect.getfile(least_squares))),
                trust_region_source_sha256=file_digest(Path(trf.__file__)),
                nonlinear_common_source_sha256=file_digest(Path(common.__file__)),
                lapack_binary_sha256=file_digest(Path(_flapack.__file__)),
                implementation_sha256=file_digest(Path(__file__)),
                slurry_module_sha256=file_digest(Path(slurry_transport.__file__)),
                nonlinear_method="bounded_trf_two_point_jacobian")


def boundary_metadata(request: HeatedSlurryRequest):
    return dict(asset_id=request.asset_id,event_id=request.event_id,clock_id=request.clock_id,
                time_s=request.time_s,inlet_port_id=request.cells[0].inlet_port_id,
                outlet_port_id=request.cells[-1].outlet_port_id,request_digest=digest(request),
                pressure_reference="absolute Pa",energy_reference=request.enthalpy_reference_id)


def bind_heated_slurry_context(request: HeatedSlurryRequest, context: RunContext) -> RunContext:
    """Bind one explicit request without altering its asset, clock or evidence."""
    q=HeatedSlurryRequest.model_validate(request.model_dump(mode="python"))
    c=RunContext.model_validate(context.model_dump(mode="python"))
    payload=c.model_dump(mode="python")
    payload["metadata"]={**c.metadata,"heated_slurry_boundary":boundary_metadata(q)}
    bound=RunContext.model_validate(payload)
    _policy(q,bound)
    return bound


def _policy(q, c):
    if (q.asset_id,q.event_id,q.clock_id)!=(c.asset_id,c.scenario_id,c.clock_id):
        raise ValueError("heated slurry asset/event/clock differs from context")
    if c.metadata.get("heated_slurry_boundary") != boundary_metadata(q):
        raise ValueError("heated slurry context does not bind the exact request and port chain")
    ids=[e.id for e in c.evidence]
    selected=set(q.evidence_ids)
    required={q.property_evidence_id,*q.review_evidence_ids.values(),*q.slurry_review_evidence_ids.values()}
    required.update(x for cell in q.cells for x in (cell.geometry_evidence_id,cell.heat_boundary_evidence_id))
    if len(ids)!=len(set(ids)) or not selected<=set(ids) or not required<=selected:
        raise ValueError("heated slurry property, geometry, heat and review evidence must be selected in context")
    evidence=next(e for e in c.evidence if e.id==q.property_evidence_id)
    if evidence.sha256!=q.property_source_sha256:
        raise ValueError("heated slurry property source pin differs from context evidence")
    missing=["thermal review:"+key for key in REVIEWS if not q.reviews[key] or key not in q.review_evidence_ids]
    missing.extend("slurry review:"+key for key in slurry_transport.REVIEWS
                   if not q.slurry_reviews[key] or key not in q.slurry_review_evidence_ids)
    if not q.slurry_owner_pins or not q.coupling_owner_pins:
        missing.append("reviewed slurry and nonlinear coupling source/runtime pins")
    return missing


def evaluate_heated_slurry(request: HeatedSlurryRequest, context: RunContext, *,
                           property_provider: SlurryThermalProvider | None = None,
                           property_table: SlurryThermalTable | None = None):
    """Solve outlet pressure/temperature jointly in each finite-volume cell."""
    q=HeatedSlurryRequest.model_validate(request.model_dump(mode="python"))
    c=RunContext.model_validate(context.model_dump(mode="python"))
    table=None if property_table is None else SlurryThermalTable.model_validate_json(property_table.model_dump_json())
    if q.property_provider_kind=="table":
        if property_provider is not None:
            raise ValueError("table mode cannot substitute a caller-registered custom provider")
        if table is not None:
            if q.temperature_support_K[0] < table.knots[0].temperature_K or q.temperature_support_K[1] > table.knots[-1].temperature_K:
                raise ValueError("requested slurry temperature support exceeds the supplied property table")
            property_provider=TabulatedSlurryThermalProvider(table)
    elif table is not None:
        raise ValueError("custom provider mode cannot silently use a thermal property table")
    body=dict(schema="pipeline_integrity.heated_slurry.v1",request=q.model_dump(mode="json"),
              context=c.model_dump(mode="json"),status="needs_data",issues=_policy(q,c),cells=[],values=None,
              property_table=None if table is None else table.model_dump(mode="json"),
              numerical_validation_completed=False,physical_validation_claim=False,
              whole_slurry_transport_complete=False,
              qualification="steady fixed-composition homogeneous incompressible slurry; midpoint cell heat exchange; no deposition, erosion, phase change or network inertia",
              energy_definition="specific enthalpy includes pressure flow work; internal plus kinetic plus potential budget retained",
              flow_direction="positive mass flow along the declared inlet-to-outlet cell port chain",
              implementation_sha256=file_digest(Path(__file__)))
    if property_provider is None:
        body["issues"].append("temperature-dependent thermo/rheology provider is not configured")
    if not body["issues"]:
        before=native_heated_slurry_metadata()
        if before!=q.coupling_owner_pins or slurry_transport.native_backend_metadata()!=q.slurry_owner_pins:
            raise ValueError("heated slurry numerical/hydraulic source pins differ from the reviewed request")
        identity=json_value(property_provider.metadata())
        if identity!=q.provider_identity:
            raise ValueError("heated slurry provider identity differs from the reviewed binding")
        with slurry_transport.SlurryTransportSession(
            q.slurry_owner_pins, maximum_evaluations=q.maximum_total_residual_evaluations
        ) as hydraulic_session:
            try:
                _solve(q,c,property_provider,body,hydraulic_session=hydraulic_session)
            except _HydraulicTrialWithheld as error:
                body.update(status=error.record["status"],
                            issues=["slurry hydraulic owner withheld a thermal trial",*error.record.get("notes",())],
                            withheld_hydraulic_trial=error.record,values=None)
            except (ValueError,RuntimeError,ArithmeticError,TypeError) as error:
                body.update(status="out_of_scope",issues=[f"{type(error).__name__}: {error}"],values=None)
        if native_heated_slurry_metadata()!=before or slurry_transport.native_backend_metadata()!=q.slurry_owner_pins:
            raise ValueError("heated slurry numerical/hydraulic source changed during execution")
        if json_value(property_provider.metadata())!=identity:
            raise ValueError("heated slurry provider identity changed during execution")
        for cell_result in body["cells"]:
            cell_result["hydraulic"] = hydraulic_session.finalize(cell_result["hydraulic"])
        if "withheld_hydraulic_trial" in body:
            body["withheld_hydraulic_trial"] = hydraulic_session.finalize(body["withheld_hydraulic_trial"])
        body["hydraulic_session_fence"] = hydraulic_session.source_fence_receipt()
        body["provider_identity"]=identity
    body=json_value(body)
    body["record_digest"]=digest(body)
    return body


def _solve(q,c,provider,body, *, hydraulic_session):
    import numpy as np
    from scipy.optimize import least_squares

    pmin,pmax=q.pressure_support_pa_abs
    tmin,tmax=q.temperature_support_K
    area=math.pi*q.inner_diameter_m**2/4
    total_calls=0
    total_property_calls=0
    states={}
    reference_solid_mass_fraction=None

    def state(p,t):
        nonlocal total_property_calls
        if not pmin<=p<=pmax or not tmin<=t<=tmax:
            raise ValueError("heated slurry trial exceeds reviewed pressure/temperature support")
        key=(float(p),float(t))
        if key not in states:
            if len(states)>=q.maximum_cached_property_states_per_cell:
                raise ValueError("heated slurry exceeded the per-cell property-state cache budget")
            total_property_calls+=1
            if total_property_calls>q.maximum_total_property_resolutions:
                raise ValueError("heated slurry exceeded the total property-resolution budget")
            supplied=provider.resolve(pressure_pa_abs=key[0],temperature_K=key[1])
            if not isinstance(supplied,SlurryThermalState):
                raise TypeError("slurry thermal providers must return SlurryThermalState")
            value=SlurryThermalState.model_validate_json(supplied.model_dump_json())
            prop=value.rheology
            if value.pressure_pa_abs!=key[0] or value.temperature_K!=key[1]:
                raise ValueError("slurry thermal provider changed the requested pressure or temperature")
            if (value.mixture_id,value.enthalpy_reference_id)!=(q.mixture_id,q.enthalpy_reference_id):
                raise ValueError("slurry thermal provider changed mixture or enthalpy reference")
            if prop.source_sha256!=q.property_source_sha256 or prop.evidence_id!=q.property_evidence_id:
                raise ValueError("slurry thermal snapshot changed source evidence")
            if (prop.provider_id,prop.provider_version)!=(q.provider_identity["provider_id"],q.provider_identity["provider_version"]):
                raise ValueError("slurry thermal snapshot changed the selected property provider")
            if prop.reference_basis!=q.rheology_reference_basis or prop.rheology!=q.rheology_model:
                raise ValueError("slurry thermal snapshot changed rheology law or its reference basis")
            if prop.solid_volume_fraction!=q.solid_volume_fraction:
                raise ValueError("heated slurry fixed-composition model cannot change solid concentration")
            if not prop.temperature_support_K[0]<=value.temperature_K<=prop.temperature_support_K[1] or not prop.concentration_support[0]<=q.solid_volume_fraction<=prop.concentration_support[1]:
                raise ValueError("slurry thermal endpoint exceeds temperature/concentration support")
            if abs(prop.mixture_density_kg_m3/q.reference_mixture_density_kg_m3-1)>q.maximum_relative_density_difference:
                raise ValueError("slurry density variation exceeds the reviewed incompressible approximation")
            if reference_solid_mass_fraction is not None:
                if q.solid_volume_fraction and prop.particle_density_kg_m3 is None:
                    raise ValueError("slurry provider lost the particle density needed for a fixed-composition mass ledger")
                solid_mass_fraction=(q.solid_volume_fraction*prop.particle_density_kg_m3/prop.mixture_density_kg_m3
                                     if q.solid_volume_fraction else 0.0)
                if not 0<=solid_mass_fraction<=1 or abs(solid_mass_fraction-reference_solid_mass_fraction)>q.maximum_solid_mass_fraction_difference:
                    raise ValueError("slurry solid mass fraction changed across a fixed-composition thermal path")
            states[key]=value
            if key[0]!=q.inlet_pressure_pa_abs:
                reference=state(q.inlet_pressure_pa_abs,key[1])
                actual=value.specific_enthalpy_J_kg-reference.specific_enthalpy_J_kg
                expected=(key[0]-q.inlet_pressure_pa_abs)/q.reference_mixture_density_kg_m3
                if abs(actual-expected)>q.specific_enthalpy_pressure_closure_tolerance_J_kg:
                    raise ValueError("slurry provider enthalpy is inconsistent with the reviewed incompressible pressure-work reference")
        return states[key]

    inlet=state(q.inlet_pressure_pa_abs,q.inlet_temperature_K)
    if not q.solid_volume_fraction:
        reference_solid_mass_fraction=0.0
    elif inlet.rheology.particle_density_kg_m3 is not None:
        reference_solid_mass_fraction=q.solid_volume_fraction*inlet.rheology.particle_density_kg_m3/inlet.rheology.mixture_density_kg_m3
        if not 0<=reference_solid_mass_fraction<=1:
            raise ValueError("initial slurry solid mass fraction is inconsistent with its volume/density ledger")
    initial=inlet
    heat_sum=elevation_sum=dissipation_sum=0.0
    all_valid=True
    for cell in q.cells:
        states={(inlet.pressure_pa_abs,inlet.temperature_K):inlet}
        cache={}
        cell_calls=0
        inlet_h=inlet.specific_enthalpy_J_kg
        pressure_scale=max(q.pressure_balance_absolute_Pa,q.balance_relative_tolerance*inlet.pressure_pa_abs)
        energy_characteristic=max(abs(cell.heater_into_fluid_W),
                                  cell.conductance_W_K*abs(cell.ambient_temperature_K-inlet.temperature_K),
                                  q.mass_flow_kg_s*inlet.heat_capacity_J_kg_K*q.maximum_cell_temperature_change_K,
                                  q.mass_flow_kg_s*q.gravity_m_s2*abs(cell.outlet_minus_inlet_elevation_m),1.0)
        energy_scale=max(q.energy_balance_absolute_W,q.balance_relative_tolerance*energy_characteristic)

        def residual(x):
            nonlocal total_calls,cell_calls
            key=(float(x[0]),float(x[1]))
            if key in cache:
                return cache[key][0]
            total_calls+=1
            cell_calls+=1
            if total_calls>q.maximum_total_residual_evaluations:
                raise ValueError("heated slurry exceeded the total residual-evaluation budget")
            if cell_calls>q.maximum_residual_evaluations_per_cell:
                raise ValueError("heated slurry exceeded the per-cell residual-evaluation budget")
            pout,tout=key
            midpoint=state((inlet.pressure_pa_abs+pout)/2,(inlet.temperature_K+tout)/2)
            outlet=state(pout,tout)
            hydraulic=SlurryTransportRequest(
                id=q.id+":"+cell.id+":hydraulic",asset_id=q.asset_id,event_id=q.event_id,clock_id=q.clock_id,time_s=q.time_s,
                inlet_port_id=cell.inlet_port_id,outlet_port_id=cell.outlet_port_id,
                inlet_pressure_pa_abs=inlet.pressure_pa_abs,volume_flow_m3_s=q.mass_flow_kg_s/midpoint.rheology.mixture_density_kg_m3,
                length_m=cell.length_m,inner_diameter_m=q.inner_diameter_m,roughness_m=cell.roughness_m,
                minor_loss_coefficient=cell.minor_loss_coefficient,outlet_minus_inlet_elevation_m=cell.outlet_minus_inlet_elevation_m,
                gravity_m_s2=q.gravity_m_s2,properties=midpoint.rheology,geometry_evidence_id=cell.geometry_evidence_id,
                maximum_wall_stress_bracket_Pa=q.maximum_wall_stress_bracket_Pa,evidence_ids=q.evidence_ids,
                reviews=q.slurry_reviews,review_evidence_ids=q.slurry_review_evidence_ids,owner_pins=q.slurry_owner_pins,
                **q.hydraulic_controls.model_dump(mode="python"))
            payload=c.model_dump(mode="python")
            payload["metadata"]={**c.metadata,"slurry_transport_boundary":slurry_transport.boundary_metadata(hydraulic)}
            local_context=RunContext.model_validate(payload)
            record=hydraulic_session.evaluate(hydraulic,local_context)
            if record["status"]!="computed_diagnostic":
                raise _HydraulicTrialWithheld(record)
            ambient=cell.conductance_W_K*(cell.ambient_temperature_K-(inlet.temperature_K+tout)/2)
            heat=ambient+cell.heater_into_fluid_W
            potential=q.mass_flow_kg_s*q.gravity_m_s2*cell.outlet_minus_inlet_elevation_m
            vin=q.mass_flow_kg_s/inlet.rheology.mixture_density_kg_m3/area
            vout=q.mass_flow_kg_s/outlet.rheology.mixture_density_kg_m3/area
            kinetic=q.mass_flow_kg_s*(vout*vout-vin*vin)/2
            energy=q.mass_flow_kg_s*(outlet.specific_enthalpy_J_kg-inlet_h)+potential+kinetic-heat
            pressure=pout-record["values"]["outlet_pressure_pa_abs"]
            normalized=np.array([pressure/pressure_scale,energy/energy_scale])
            cache[key]=(normalized,outlet,midpoint,record,ambient,heat,potential,kinetic,pressure,energy)
            return normalized

        x0=np.array([inlet.pressure_pa_abs,inlet.temperature_K])
        solution=least_squares(residual,x0,bounds=([pmin,tmin],[pmax,tmax]),
                               x_scale=[max(inlet.pressure_pa_abs,1.0),max(inlet.temperature_K,1.0)],
                               method="trf",jac="2-point",diff_step=q.nonlinear_relative_difference_step,
                               xtol=q.nonlinear_step_tolerance,ftol=q.nonlinear_cost_tolerance,
                               gtol=q.nonlinear_gradient_tolerance,
                               max_nfev=q.maximum_residual_evaluations_per_cell)
        key=tuple(float(v) for v in solution.x)
        residual(solution.x)
        _,outlet,midpoint,hydraulic,ambient,heat,potential,kinetic,pressure_error_pa,error_W=cache[key]
        enthalpy=q.mass_flow_kg_s*(outlet.specific_enthalpy_J_kg-inlet_h)
        pressure_work=q.mass_flow_kg_s*(outlet.pressure_pa_abs/outlet.rheology.mixture_density_kg_m3
                                      -inlet.pressure_pa_abs/inlet.rheology.mixture_density_kg_m3)
        pressure_limit=q.pressure_balance_absolute_Pa+q.balance_relative_tolerance*max(abs(inlet.pressure_pa_abs-outlet.pressure_pa_abs),1.0)
        energy_limit=q.energy_balance_absolute_W+q.balance_relative_tolerance*max(abs(enthalpy),abs(heat),abs(potential),abs(kinetic))
        omitted_acceleration_pressure=midpoint.rheology.mixture_density_kg_m3*kinetic/q.mass_flow_kg_s
        valid=bool(solution.success and abs(pressure_error_pa)<=pressure_limit and abs(error_W)<=energy_limit
                   and abs(omitted_acceleration_pressure)<=q.maximum_omitted_acceleration_pressure_Pa
                   and abs(outlet.temperature_K-inlet.temperature_K)<=q.maximum_cell_temperature_change_K)
        body["cells"].append(dict(cell_id=cell.id,inlet=inlet.model_dump(mode="json"),outlet=outlet.model_dump(mode="json"),
                                 midpoint=midpoint.model_dump(mode="json"),hydraulic=hydraulic,
                                 ambient_heat_into_fluid_W=ambient,heater_into_fluid_W=cell.heater_into_fluid_W,
                                 total_heat_into_fluid_W=heat,enthalpy_flux_change_W=enthalpy,
                                 potential_energy_flux_change_W=potential,kinetic_energy_flux_change_W=kinetic,
                                 pressure_flow_work_flux_change_W=pressure_work,
                                 internal_energy_flux_change_W=enthalpy-pressure_work,
                                 pressure_residual_Pa=pressure_error_pa,energy_residual_W=error_W,
                                 omitted_acceleration_pressure_Pa=omitted_acceleration_pressure,
                                 pressure_balance_limit_Pa=pressure_limit,energy_balance_limit_W=energy_limit,
                                 optimizer_success=bool(solution.success),optimizer_evaluations=int(solution.nfev),
                                 actual_residual_evaluations=cell_calls,
                                 cached_property_states=len(states),
                                 within_reviewed_budget=valid,mass_flow_in_kg_s=q.mass_flow_kg_s,mass_flow_out_kg_s=q.mass_flow_kg_s))
        heat_sum+=heat
        elevation_sum+=cell.outlet_minus_inlet_elevation_m
        dissipation_sum+=hydraulic["values"]["dissipated_power_W"]
        all_valid=all_valid and valid
        if not valid:
            body.update(status="diagnostic_rejected",issues=["cell nonlinear residual, solver or mesh-temperature budget failed"])
            return
        inlet=outlet
    vin=q.mass_flow_kg_s/initial.rheology.mixture_density_kg_m3/area
    vout=q.mass_flow_kg_s/inlet.rheology.mixture_density_kg_m3/area
    enthalpy_flux=q.mass_flow_kg_s*(inlet.specific_enthalpy_J_kg-initial.specific_enthalpy_J_kg)
    potential_flux=q.mass_flow_kg_s*q.gravity_m_s2*elevation_sum
    kinetic_flux=q.mass_flow_kg_s*(vout*vout-vin*vin)/2
    pressure_work_flux=math.fsum(row["pressure_flow_work_flux_change_W"] for row in body["cells"])
    internal_flux=q.mass_flow_kg_s*((inlet.specific_enthalpy_J_kg-inlet.pressure_pa_abs/inlet.rheology.mixture_density_kg_m3)
                                  -(initial.specific_enthalpy_J_kg-initial.pressure_pa_abs/initial.rheology.mixture_density_kg_m3))
    decomposition_residual=internal_flux+pressure_work_flux-enthalpy_flux
    residual_energy=enthalpy_flux+potential_flux+kinetic_flux-heat_sum
    global_limit=q.energy_balance_absolute_W+q.balance_relative_tolerance*max(abs(heat_sum),abs(enthalpy_flux),abs(potential_flux),abs(kinetic_flux))
    all_valid=all_valid and abs(residual_energy)<=global_limit
    all_valid=all_valid and abs(decomposition_residual)<=global_limit
    body.update(status="computed_diagnostic" if all_valid else "diagnostic_rejected",
                issues=[] if all_valid else ["global steady energy budget failed"],
                values=dict(outlet=inlet.model_dump(mode="json"),total_heat_into_fluid_W=heat_sum,
                            summed_native_dissipated_power_W=dissipation_sum,
                            global_enthalpy_flux_change_W=enthalpy_flux,
                            global_potential_energy_flux_change_W=potential_flux,
                            global_kinetic_energy_flux_change_W=kinetic_flux,
                            global_pressure_flow_work_flux_change_W=pressure_work_flux,
                            global_internal_energy_flux_change_W=internal_flux,
                            global_internal_plus_pressure_work_residual_W=decomposition_residual,
                            global_energy_residual_W=residual_energy,global_energy_balance_limit_W=global_limit,
                            net_inlet_minus_outlet_pressure_Pa=initial.pressure_pa_abs-inlet.pressure_pa_abs,
                            residual_evaluations=total_calls,property_resolutions=total_property_calls,
                            solid_mass_fraction=reference_solid_mass_fraction,
                            mass_flow_kg_s=q.mass_flow_kg_s))


def verify_heated_slurry_record(record, *, request=None, context=None, property_provider=None,
                               property_table=None):
    if digest({k:v for k,v in record.items() if k!="record_digest"})!=record.get("record_digest"):
        raise ValueError("heated slurry receipt changed")
    q=HeatedSlurryRequest.model_validate_json(json.dumps(record["request"],allow_nan=False))
    c=RunContext.model_validate_json(json.dumps(record["context"],allow_nan=False))
    if request is not None and request.model_dump(mode="json")!=q.model_dump(mode="json"):
        raise ValueError("heated slurry caller request differs")
    if context is not None and context.model_dump(mode="json")!=c.model_dump(mode="json"):
        raise ValueError("heated slurry caller context differs")
    table=None if record.get("property_table") is None else SlurryThermalTable.model_validate_json(json.dumps(record["property_table"]))
    if property_table is not None and property_table.model_dump(mode="json")!=record.get("property_table"):
        raise ValueError("heated slurry caller property table differs")
    if evaluate_heated_slurry(q,c,property_provider=property_provider,property_table=table)!=record:
        raise ValueError("heated slurry owner/provider replay differs")
    return True
