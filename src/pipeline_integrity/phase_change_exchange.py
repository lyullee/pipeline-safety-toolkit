"""Reviewed thermal/phase signatures around existing LH2DT and CoolProp models.

The interface kinetics, equipment states and insulation boundary keep distinct
physical meanings.  No solidification solver or new heat-transfer law is added.
"""

from __future__ import annotations

import importlib
import json
import math
from dataclasses import asdict
from importlib.metadata import distribution, version
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, model_validator

from .contracts import (
    Contract,
    FlowBoundary,
    Fraction,
    Name,
    Nonnegative,
    Positive,
    RunContext,
    SignalFrame,
    digest,
    json_value,
)
from .datasets import file_digest
from .thermo import StateRequest
from .lumped_phase_change import PhaseStorageRequest

REVIEWS = (
    "geometry_ports_and_clock",
    "phase_and_common_property_reference",
    "closure_parameters_and_applicability",
    "thermal_boundary_and_energy_meaning",
)


class PhasePropertyBasis(Contract):
    fluid: Literal["Hydrogen", "ParaHydrogen"]
    backend: Literal["HEOS"] = "HEOS"
    energy_reference: Literal["runtime_pinned_reference"] = "runtime_pinned_reference"
    evidence_id: Name


class KineticInterfaceCase(Contract):
    kind: Literal["kinetic_interface"] = "kinetic_interface"
    interface_id: Name
    liquid_port_id: Name
    vapor_port_id: Name
    area_m2: Positive | None
    interface_temperature_K: Positive | None
    vapor_state: StateRequest | None
    mass_accommodation: Fraction | None
    energy_accommodation: Fraction | None
    maximum_relative_pressure_imbalance: Positive | None
    maximum_relative_temperature_imbalance: Positive | None
    liquid_side_heat_into_interface_W: float | None = None
    external_heat_into_interface_W: float | None = None


class SteadyThermalCase(Contract):
    kind: Literal["steady_vaporizer", "steady_reliquefier"]
    inlet_port_id: Name
    outlet_port_id: Name
    inlet_state: StateRequest | None
    outlet_pressure_Pa_abs: Positive | None
    mass_flow_kg_s: Nonnegative | None
    capacity_W: Positive | None
    UA_W_K: Positive | None = None
    reservoir_temperature_K: Positive | None = None
    minimum_approach_K: Nonnegative | None = None
    integration_segments: int = Field(default=192, ge=32, le=2048)
    target_vapor_mass_fraction: Fraction | None = None

    @model_validator(mode="after")
    def selected_signature(self):
        if self.kind == "steady_vaporizer" and self.target_vapor_mass_fraction is not None:
            raise ValueError("the owner vaporizer does not impose a target quality")
        if self.kind == "steady_reliquefier" and any(
            v is not None for v in (self.UA_W_K, self.reservoir_temperature_K, self.minimum_approach_K)
        ):
            raise ValueError(
                "the steady capacity-limited reliquefier does not consume UA or a cold-side temperature"
            )
        return self


class DynamicThermalCase(Contract):
    kind: Literal["dynamic_vaporizer", "dynamic_reliquefier"]
    inlet_port_id: Name
    outlet_port_id: Name
    initial_fluid_state: StateRequest | None
    inlet_state: StateRequest | None
    initial_wall_temperature_K: Positive | None
    fluid_volume_m3: Positive | None
    wall_heat_capacity_J_K: Positive | None
    fluid_wall_UA_W_K: Nonnegative | None
    reservoir_temperature_K: Positive | None
    reservoir_UA_W_K: Nonnegative | None
    capacity_W: Positive | None
    mass_flow_in_kg_s: Nonnegative | None
    mass_flow_out_kg_s: Nonnegative | None
    duration_s: Positive | None
    maximum_reviewed_step_s: Positive | None


class HeatPenetration(Contract):
    id: Name
    conductance_W_K: Nonnegative
    evidence_id: Name


class CryogenicInsulationCase(Contract):
    kind: Literal["vacuum_insulation"] = "vacuum_insulation"
    warm_boundary_id: Name
    cold_boundary_id: Name
    area_m2: Positive | None
    effective_emissivity: Fraction | None
    mli_solid_conductance_W_K: Nonnegative | None
    support_conductance_W_K: Nonnegative | None
    residual_gas_coefficient_W_m2_K_Pa: Nonnegative | None
    maximum_valid_vacuum_pressure_Pa_abs: Positive | None
    warm_temperature_K: Positive | None
    cold_temperature_K: Positive | None
    vacuum_pressure_Pa_abs: Nonnegative | None
    penetrations: tuple[HeatPenetration, ...] = ()


class UnsupportedPhaseCase(Contract):
    kind: Literal["bulk_freezing", "ambient_frost"]
    material: Name
    requested_physics: Name
    supplied_data: dict = Field(default_factory=dict)


class LumpedEnthalpyStorageCase(Contract):
    """Fixed-mass, uniform-temperature storage with caller-supplied sensible/latent properties."""
    kind: Literal["lumped_enthalpy_storage"] = "lumped_enthalpy_storage"
    storage: PhaseStorageRequest
    model_source_sha256: str | None = Field(default=None,pattern=r"^[a-f0-9]{64}$")
    absolute_energy_tolerance_J: Nonnegative = 1e-6


PhaseCase = Annotated[
    KineticInterfaceCase
    | SteadyThermalCase
    | DynamicThermalCase
    | CryogenicInsulationCase
    | LumpedEnthalpyStorageCase
    | UnsupportedPhaseCase,
    Field(discriminator="kind"),
]


class PhaseChangeRequest(Contract):
    id: Name
    asset_id: Name
    event_id: Name
    clock_id: Name
    time_s: Nonnegative | None = None
    pressure_reference: Literal["absolute"] = "absolute"
    property_basis: PhasePropertyBasis | None = None
    case: PhaseCase
    evidence_ids: tuple[Name, ...] = Field(min_length=1)
    review_evidence_ids: dict[str, Name] = Field(default_factory=dict)
    reviews: dict[str, bool] = Field(default_factory=lambda: dict.fromkeys(REVIEWS, False))
    owner_pins: dict = Field(default_factory=dict)
    absolute_energy_tolerance_W: Nonnegative = 1e-6
    absolute_mass_tolerance_kg_s: Nonnegative = 1e-10
    relative_energy_tolerance: Nonnegative = Field(default=1e-9, le=1e-3)

    @model_validator(mode="after")
    def shape(self):
        if set(self.reviews) != set(REVIEWS) or len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("named reviews and distinct selected evidence are required")
        case = self.case
        if isinstance(case,LumpedEnthalpyStorageCase):
            if (case.storage.asset_id,case.storage.clock_id)!=(self.asset_id,self.clock_id):
                raise ValueError("local phase-storage request must use the facade asset and clock")
            if self.time_s is not None and self.time_s!=case.storage.start_time_s:
                raise ValueError("local phase-storage start time differs from the facade application time")
            if self.property_basis is not None or self.owner_pins:
                raise ValueError("local lumped storage uses supplied material properties and a local source pin, not LH2 property/runtime pins")
        ports = [
            getattr(case, name)
            for name in (
                "inlet_port_id",
                "outlet_port_id",
                "liquid_port_id",
                "vapor_port_id",
                "warm_boundary_id",
                "cold_boundary_id",
            )
            if hasattr(case, name)
        ]
        if len(set(ports)) != len(ports):
            raise ValueError("physical ports and boundary identifiers must differ")
        if isinstance(case, CryogenicInsulationCase) and len({p.id for p in case.penetrations}) != len(
            case.penetrations
        ):
            raise ValueError("localized insulation heat paths must have unique identifiers")
        return self


def boundary_metadata(request):
    result = {
        "event_id": request.event_id,
        "operation": request.case.kind,
        "time_s": request.time_s,
        "clock_id": request.clock_id,
        "case_topology_digest": digest(
            {k: v for k, v in request.case.model_dump(mode="json").items() if k.endswith("_id")}
        ),
        "property_basis": json_value(request.property_basis),
        "pressure_reference": request.pressure_reference,
    }
    if isinstance(request.case,LumpedEnthalpyStorageCase):
        result.update(local_storage_request_digest=digest(request.case.storage),
                      local_model_source_sha256=request.case.model_source_sha256,
                      property_basis=None,pressure_reference=None,
                      energy_reference="solid_at_transition_temperature",
                      local_scope="fixed_mass_lumped_sensible_and_latent_enthalpy")
    return result


def local_phase_storage_metadata():
    """Discover only the local enthalpy implementation; no LH2/CoolProp import is made."""
    return dict(model_id="pipeline_integrity.lumped_phase_change",scope="fixed_mass_lumped_enthalpy_storage",
                model_source_sha256=file_digest(Path(__file__).with_name("lumped_phase_change.py")),
                shared_sources={name:file_digest(Path(__file__).with_name(name+".py")) for name in ("contracts","datasets")},
                energy_reference="solid_at_transition_temperature",spatial_front_resolved=False,
                material_properties="caller_supplied_constant_solid_cp_liquid_cp_latent_heat")


def _tree(root):
    rows = {p.relative_to(root).as_posix(): file_digest(p) for p in sorted(root.rglob("*.py"))}
    return {"python_files": len(rows), "sha256": digest(rows)}


def native_backend_metadata():
    """Explicit discovery after review; normal import/default requests do not call it."""
    import tomllib

    from . import contracts, equipment_exchange, thermo

    owner = importlib.import_module("lh2dt")
    cp = importlib.import_module("CoolProp")
    root = Path(owner.__file__).parent
    project = root.parent.parent
    manifest = tomllib.loads((project / "pyproject.toml").read_text(encoding="utf-8"))
    license_path = project / "LICENSE"
    if "MIT License" not in license_path.read_text(encoding="utf-8")[:100]:
        raise ValueError("the inspected owner MIT license is not retained")
    cp_dist = distribution("CoolProp")
    cp_binaries = {
        str(p): file_digest(Path(cp_dist.locate_file(p)))
        for p in cp_dist.files
        if str(p).endswith((".pyd", ".so", ".dll"))
    }
    anchors = thermo.coolprop_reference_anchors()
    return {
        "owner": {
            "id": "lh2dt_models",
            "version": manifest["project"]["version"],
            **_tree(root),
            "license": "MIT",
            "license_sha256": file_digest(license_path),
        },
        "runtime": {name: version(name) for name in ("CoolProp", "numpy", "scipy", "pydantic")},
        "CoolProp_revision": cp.__gitrevision__,
        "CoolProp_binary_tree_sha256": digest(cp_binaries),
        "reference_anchors": anchors,
        "shared_sources": {
            m.__name__: file_digest(Path(m.__file__)) for m in (contracts, equipment_exchange, thermo)
        },
    }


def _checked(value, cls):
    return cls.model_validate(value.model_dump(mode="python"))


def _policy(q, c):
    if (q.asset_id, q.clock_id) != (c.asset_id, c.clock_id) or c.metadata.get(
        "phase_change_boundary"
    ) != boundary_metadata(q):
        raise ValueError("thermal request differs from its actual asset/clock/topology/property context")
    evidence = [e.id for e in c.evidence]
    used = set(q.evidence_ids)
    if (
        len(set(evidence)) != len(evidence)
        or not used.issubset(evidence)
        or not set(q.review_evidence_ids.values()).issubset(used)
    ):
        raise ValueError("thermal evidence must be distinct, retained and selected in the actual context")
    if q.property_basis and q.property_basis.evidence_id not in used:
        raise ValueError("property reference evidence must be selected")
    if isinstance(q.case, CryogenicInsulationCase) and any(
        p.evidence_id not in used for p in q.case.penetrations
    ):
        raise ValueError("every thermal penetration must retain selected evidence")
    missing = [
        "review:" + name for name in REVIEWS if not q.reviews[name] or name not in q.review_evidence_ids
    ]
    if not q.owner_pins and not isinstance(q.case, (UnsupportedPhaseCase,LumpedEnthalpyStorageCase)):
        missing.append("current_native_source_and_runtime_pins")
    case = q.case
    if not isinstance(case, (CryogenicInsulationCase, UnsupportedPhaseCase,LumpedEnthalpyStorageCase)) and q.property_basis is None:
        missing.append("common_property_basis")
    if isinstance(case,LumpedEnthalpyStorageCase):
        selected=set(case.storage.evidence_ids)|{w.evidence_id for w in case.storage.windows}
        if not selected<=used:
            raise ValueError("every local phase-storage material/boundary evidence must be selected by the facade")
        if case.model_source_sha256 is None:
            missing.append("current_local_enthalpy_model_source_pin")
        if not case.storage.lumped_temperature_and_constant_properties_reviewed:
            missing.append("local_lumped_temperature_and_constant_material_property_review")
    required = {
        KineticInterfaceCase: (
            "area_m2",
            "interface_temperature_K",
            "vapor_state",
            "mass_accommodation",
            "energy_accommodation",
            "maximum_relative_pressure_imbalance",
            "maximum_relative_temperature_imbalance",
        ),
        SteadyThermalCase: ("inlet_state", "outlet_pressure_Pa_abs", "mass_flow_kg_s", "capacity_W"),
        DynamicThermalCase: (
            "initial_fluid_state",
            "inlet_state",
            "initial_wall_temperature_K",
            "fluid_volume_m3",
            "wall_heat_capacity_J_K",
            "fluid_wall_UA_W_K",
            "reservoir_temperature_K",
            "reservoir_UA_W_K",
            "capacity_W",
            "mass_flow_in_kg_s",
            "mass_flow_out_kg_s",
            "duration_s",
            "maximum_reviewed_step_s",
        ),
        CryogenicInsulationCase: (
            "area_m2",
            "effective_emissivity",
            "mli_solid_conductance_W_K",
            "support_conductance_W_K",
            "residual_gas_coefficient_W_m2_K_Pa",
            "maximum_valid_vacuum_pressure_Pa_abs",
            "warm_temperature_K",
            "cold_temperature_K",
            "vacuum_pressure_Pa_abs",
        ),
    }
    for name in required.get(type(case), ()):
        if getattr(case, name) is None:
            missing.append("missing:" + name)
    if isinstance(case, SteadyThermalCase):
        extra = (
            ("UA_W_K", "reservoir_temperature_K", "minimum_approach_K")
            if case.kind == "steady_vaporizer"
            else ("target_vapor_mass_fraction",)
        )
        missing += ["missing:" + name for name in extra if getattr(case, name) is None]
    for name in ("inlet_state", "initial_fluid_state", "vapor_state"):
        state = getattr(case, name, None)
        if (
            state
            and q.property_basis
            and (state.fluid, state.backend) != (q.property_basis.fluid, q.property_basis.backend)
        ):
            raise ValueError("thermal state changes fluid/backend relative to the common property basis")
    return missing


def _resolve(client, state):
    from .thermo import CheckedProvider, CoolPropProvider

    if frozenset(state.inputs) in {
        frozenset({"P", "T"}),
        frozenset({"P", "Hmass"}),
        frozenset({"P", "Smass"}),
    }:
        return client.state(state)
    resolved = CheckedProvider(CoolPropProvider(use_cache=False)).resolve(state)
    return client.properties.from_ph(resolved.pressure_pa_abs, resolved.enthalpy_j_kg)


def _native(q, c):
    from .equipment_exchange import EquipmentRequest, LH2EquipmentClient

    case = q.case
    if isinstance(case, CryogenicInsulationCase):
        m = importlib.import_module("lh2dt.vacuum_insulation")
        parameters = {
            "area_m2": case.area_m2,
            "effective_emissivity": case.effective_emissivity,
            "mli_solid_conductance_W_K": case.mli_solid_conductance_W_K,
            "support_conductance_W_K": case.support_conductance_W_K,
            "residual_gas_coefficient_W_m2K_Pa": case.residual_gas_coefficient_W_m2_K_Pa,
            "maximum_valid_vacuum_pressure_Pa": case.maximum_valid_vacuum_pressure_Pa_abs,
            "localized_heat_paths": tuple(
                m.LocalizedHeatPath(p.id, p.conductance_W_K) for p in case.penetrations
            ),
        }
        native = m.VacuumInsulation(m.VacuumInsulationParameters(**parameters)).evaluate(
            case.warm_temperature_K, case.cold_temperature_K, case.vacuum_pressure_Pa_abs
        )
        result = asdict(native)
        paths = [
            result[k]
            for k in (
                "radiation_heat_W",
                "mli_solid_heat_W",
                "support_heat_W",
                "residual_gas_heat_W",
                "localized_heat_W",
            )
        ]
        return (
            {
                "call": "lh2dt.VacuumInsulation.evaluate",
                "parameters": json_value(asdict(m.VacuumInsulationParameters(**parameters))),
                "result": result,
            },
            {
                "heat_into_cold_W": native.total_heat_into_cold_W,
                "heat_leaving_warm_W": native.total_heat_into_cold_W,
                "cumulative_heat_J": None,
            },
            {
                "parallel_path_sum_residual_W": native.total_heat_into_cold_W - math.fsum(paths),
                "energy_meaning": "instantaneous signed heat from named warm boundary into named cold boundary; no time integration",
            },
        )
    basis = q.property_basis
    property_id = (
        f"CoolProp-HEOS:{basis.fluid}:runtime-anchor:{digest(q.owner_pins['reference_anchors'][basis.fluid])}"
    )
    client = LH2EquipmentClient(fluid=basis.fluid, property_basis=property_id)
    props = client.properties
    if isinstance(case, KineticInterfaceCase):
        if case.mass_accommodation <= 0 or case.energy_accommodation <= 0:
            raise ValueError("kinetic accommodation coefficients must be positive")
        if not props.triple_temperature_K() < case.interface_temperature_K < props.critical_temperature_K():
            raise ValueError(
                "liquid-vapor interface must lie strictly between triple and critical temperatures"
            )
        pressure = props.saturation_pressure_Pa(case.interface_temperature_K)
        liquid, vapor_interface = props.saturated_liquid(pressure), props.saturated_vapor(pressure)
        vapor = _resolve(client, case.vapor_state)
        if vapor.phase != "gas" and vapor.quality != 1.0:
            raise ValueError(
                "kinetic gas film requires pure hydrogen vapor, not liquid/solid or mixed-phase bulk"
            )
        pimb = abs(vapor.pressure_Pa - pressure) / pressure
        timb = abs(vapor.temperature_K - case.interface_temperature_K) / case.interface_temperature_K
        if (
            pimb > case.maximum_relative_pressure_imbalance
            or timb > case.maximum_relative_temperature_imbalance
        ):
            raise ValueError("kinetic closure exceeds the caller-reviewed near-equilibrium envelope")
        m = importlib.import_module("lh2dt.phase_change")
        args = {
            "interface_pressure_Pa": pressure,
            "interface_temperature_K": case.interface_temperature_K,
            "vapor_pressure_Pa": vapor.pressure_Pa,
            "vapor_temperature_K": vapor.temperature_K,
            "specific_gas_constant_J_kgK": props.specific_gas_constant_J_kgK(),
            "accommodation_coefficient": case.mass_accommodation,
        }
        flux = m.schrage_mass_flux(**args)
        film_args = {
            "pressure_Pa": vapor.pressure_Pa,
            "temperature_K": vapor.temperature_K,
            "isobaric_heat_capacity_J_kgK": props.isobaric_heat_capacity_J_kgK(vapor),
            "specific_gas_constant_J_kgK": args["specific_gas_constant_J_kgK"],
            "energy_accommodation_coefficient": case.energy_accommodation,
        }
        film = m.kinetic_gas_film_heat_transfer_coefficient(**film_args)
        rate = flux.mass_flux_kg_m2_s * case.area_m2
        latent = vapor_interface.specific_enthalpy_J_kg - liquid.specific_enthalpy_J_kg
        sensible = (
            film.heat_transfer_coefficient_W_m2K
            * case.area_m2
            * (vapor.temperature_K - case.interface_temperature_K)
        )
        residual = (
            None
            if case.liquid_side_heat_into_interface_W is None or case.external_heat_into_interface_W is None
            else case.liquid_side_heat_into_interface_W
            + case.external_heat_into_interface_W
            + sensible
            - rate * latent
        )
        return (
            {
                "call": "lh2dt.phase_change.schrage_mass_flux + kinetic_gas_film_heat_transfer_coefficient",
                "request": args,
                "film_request": film_args,
                "result": asdict(flux),
                "film_result": asdict(film),
                "liquid_interface": asdict(liquid),
                "vapor_interface": asdict(vapor_interface),
                "bulk_vapor": asdict(vapor),
            },
            {
                "mass_flux_liquid_to_vapor_kg_m2_s": flux.mass_flux_kg_m2_s,
                "mass_rate_liquid_to_vapor_kg_s": rate,
                "latent_power_demand_W": rate * latent,
                "gas_sensible_heat_into_interface_W": sensible,
                "gas_film_coefficient_W_m2_K": film.heat_transfer_coefficient_W_m2K,
                "latent_heat_J_kg": latent,
                "cumulative_mass_transferred_kg": None,
            },
            {
                "relative_pressure_imbalance": pimb,
                "relative_temperature_imbalance": timb,
                "interface_energy_residual_W": residual,
                "energy_meaning": "conditional interfacial closure; interface temperature is prescribed, not solved; latent demand is not an available heat supply",
            },
        )
    if isinstance(case, SteadyThermalCase):
        model_id = "vaporizer" if case.kind == "steady_vaporizer" else "reliquefier"
        parameters = (
            {
                "UA_W_K": case.UA_W_K,
                "maximum_heat_W": case.capacity_W,
                "minimum_approach_K": case.minimum_approach_K,
                "integration_segments": case.integration_segments,
            }
            if model_id == "vaporizer"
            else {"cooling_capacity_W": case.capacity_W}
        )
        handle = client.build(
            EquipmentRequest(
                model_id=model_id,
                parameter_source=q.evidence_ids[0],
                parameters=parameters,
                parameter_policy="owner_native",
            ),
            c,
        )
        if handle.report.status != "ready":
            raise ValueError("native thermal asset factory rejected: " + str(handle.report.issues))
        inlet = _resolve(client, case.inlet_state)
        if model_id == "vaporizer":
            native = handle.native.evaluate(
                inlet, case.outlet_pressure_Pa_abs, case.mass_flow_kg_s, case.reservoir_temperature_K
            )
            heat = native.heat_into_hydrogen_W
        else:
            if case.mass_flow_kg_s <= 0:
                raise ValueError(
                    "native reliquefier requires positive mass flow; closed-flow cooling is unsupported"
                )
            native = handle.native.evaluate(
                inlet, case.outlet_pressure_Pa_abs, case.mass_flow_kg_s, case.target_vapor_mass_fraction
            )
            heat = -native.heat_removed_W
        residual = (
            case.mass_flow_kg_s * (native.outlet.specific_enthalpy_J_kg - inlet.specific_enthalpy_J_kg) - heat
        )
        return (
            {
                "call": f"lh2dt.{model_id}.evaluate",
                "build_report": handle.report.model_dump(mode="json"),
                "parameters": parameters,
                "inlet": asdict(inlet),
                "result": asdict(native),
            },
            {
                "heat_into_fluid_W": heat,
                "mass_flow_in_kg_s": case.mass_flow_kg_s,
                "mass_flow_out_kg_s": case.mass_flow_kg_s,
                "outlet_pressure_Pa_abs": native.outlet.pressure_Pa,
                "outlet_temperature_K": native.outlet.temperature_K,
                "outlet_vapor_mass_fraction": native.outlet.quality,
                "outlet_specific_enthalpy_J_kg": native.outlet.specific_enthalpy_J_kg,
                "cumulative_heat_J": None,
            },
            {
                "steady_energy_residual_W": residual,
                "steady_mass_residual_kg_s": 0.0,
                "energy_meaning": "steady open-control-volume enthalpy balance; no transient storage or inferred phase-change rate",
            },
        )
    parameters = {
        "fluid_volume_m3": case.fluid_volume_m3,
        "wall_heat_capacity_J_K": case.wall_heat_capacity_J_K,
        "fluid_wall_UA_W_K": case.fluid_wall_UA_W_K,
    }
    parameters.update(
        {
            "ambient_temperature_K": case.reservoir_temperature_K,
            "ambient_UA_W_K": case.reservoir_UA_W_K,
            "maximum_heat_W": case.capacity_W,
        }
        if case.kind == "dynamic_vaporizer"
        else {
            "cold_side_temperature_K": case.reservoir_temperature_K,
            "cold_side_UA_W_K": case.reservoir_UA_W_K,
            "maximum_cooling_W": case.capacity_W,
        }
    )
    if case.duration_s > case.maximum_reviewed_step_s:
        raise ValueError("single native step exceeds its reviewed duration envelope")
    handle = client.build(
        EquipmentRequest(
            model_id=case.kind,
            parameter_source=q.evidence_ids[0],
            parameters=parameters,
            parameter_policy="owner_native",
        ),
        c,
    )
    if handle.report.status != "ready":
        raise ValueError("native dynamic thermal factory rejected the parameters")
    initial, inlet = _resolve(client, case.initial_fluid_state), _resolve(client, case.inlet_state)
    state = handle.native.initialize(initial, wall_temperature_K=case.initial_wall_temperature_K)
    native = handle.native.step(
        state, inlet, case.mass_flow_in_kg_s, case.mass_flow_out_kg_s, case.duration_s
    )
    dU = native.state.internal_energy_J - state.internal_energy_J
    wall_change = case.wall_heat_capacity_J_K * (native.state.wall_temperature_K - state.wall_temperature_K)
    closed_adiabatic = case.mass_flow_in_kg_s == case.mass_flow_out_kg_s == case.reservoir_UA_W_K == 0.0
    return (
        {
            "call": f"lh2dt.{case.kind}.step",
            "build_report": handle.report.model_dump(mode="json"),
            "parameters": parameters,
            "initial_fluid": asdict(initial),
            "inlet": asdict(inlet),
            "state_before": asdict(state),
            "result": asdict(native),
        },
        {
            "mass_kg": native.state.mass_kg,
            "fluid_internal_energy_J": native.state.internal_energy_J,
            "wall_temperature_K": native.state.wall_temperature_K,
            "fluid_pressure_Pa_abs": native.fluid.pressure_Pa,
            "fluid_temperature_K": native.fluid.temperature_K,
            "fluid_vapor_mass_fraction": native.fluid.quality,
            "fluid_energy_change_J": dU,
            "wall_energy_change_J": wall_change,
            "total_energy_change_J": dU + wall_change,
            "endpoint_heat_from_wall_W": native.derivative.heat_from_wall_W,
            "endpoint_heat_from_reservoir_W": native.derivative.heat_from_ambient_W,
            "cumulative_external_heat_J": None,
            "cumulative_inlet_enthalpy_J": None,
            "cumulative_outlet_enthalpy_J": None,
        },
        {
            "endpoint_mass_residual_kg_s": native.mass_balance_residual_kg_s,
            "endpoint_fluid_energy_residual_W": native.energy_balance_residual_W,
            "endpoint_wall_energy_residual_W": native.wall_energy_balance_residual_W,
            "mass_increment_residual_kg": native.state.mass_kg
            - state.mass_kg
            - (case.mass_flow_in_kg_s - case.mass_flow_out_kg_s) * case.duration_s,
            "closed_adiabatic_total_energy_residual_J": dU + wall_change if closed_adiabatic else None,
            "energy_meaning": "native RK4 state and ENDPOINT derivatives; derivatives are not the RK4 step-average or an integrated heat/enthalpy ledger",
        },
    )


def _evaluate_local_storage(q, c, body):
    from .lumped_phase_change import evaluate_phase_storage
    before=local_phase_storage_metadata()
    case=q.case
    if before["model_source_sha256"]!=case.model_source_sha256:
        raise ValueError("local phase-storage source differs from the reviewed pin")
    body.update(provider=before,local_model_called=True,local_model=None,
                local_model_scope="fixed_mass_uniform_temperature_user_supplied_sensible_and_latent_properties",
                spatial_phase_front_resolved=False,native_called=False,numerical_validation_completed=False)
    try:
        local=evaluate_phase_storage(case.storage,c)
        if local.get("implementation_sha256")!=before["model_source_sha256"]:
            raise ValueError("local enthalpy result source differs from its reviewed implementation")
        if local.get("status")!="computed":
            raise ValueError("local enthalpy model did not return a computed calculation")
        final=local["states"][-1]
        change=final["enthalpy_J"]-case.storage.initial_enthalpy_J
        scale=max(abs(change),abs(local["direct_heat_J"]),abs(local["ambient_heat_J"]))
        limit=case.absolute_energy_tolerance_J+q.relative_energy_tolerance*scale
        residual=local["energy_residual_J"]
        body.update(local_model=json_value(local),values=dict(
            mass_kg=case.storage.mass_kg,temperature_K=final["temperature_K"],
            enthalpy_J=final["enthalpy_J"],liquid_mass_fraction=final["liquid_mass_fraction"],
            solid_mass_fraction=1-final["liquid_mass_fraction"],
            direct_heat_J=local["direct_heat_J"],ambient_heat_J=local["ambient_heat_J"],
            duration_s=final["time_s"]-case.storage.start_time_s),
            audits=dict(energy_residual_J=residual,energy_limit_J=limit,
                        energy_meaning="integrated fixed-mass sensible/latent enthalpy with prescribed ambient/direct heat",
                        mass_transfer_modelled=False,spatial_phase_front_resolved=False),
            status="computed_diagnostic" if abs(residual)<=limit else "diagnostic_rejected",
            notes=[] if abs(residual)<=limit else ["local integrated enthalpy budget did not close"])
    except (ValueError,RuntimeError,ArithmeticError,TypeError,KeyError) as error:
        body.update(status="out_of_scope",notes=[f"{type(error).__name__}: {error}"])
    if local_phase_storage_metadata()!=before:
        raise ValueError("local phase-storage source/shared references changed during execution")
    return {**body,"record_digest":digest(body)}


def evaluate_phase_change(request: PhaseChangeRequest, context: RunContext):
    q, c = _checked(request, PhaseChangeRequest), _checked(context, RunContext)
    missing = _policy(q, c)
    body = {
        "schema": "pipeline_integrity.phase_change_exchange.v1",
        "request": q.model_dump(mode="json"),
        "context": c.model_dump(mode="json"),
        "input_digest": digest({"request": q, "context": c}),
        "implementation_sha256": file_digest(Path(__file__)),
        "status": "needs_data",
        "native_called": False,
        "native": None,
        "provider": None,
        "values": None,
        "audits": None,
        "notes": missing,
        "physical_validation_claim": False,
        "broad_phase_change_complete": False,
    }
    if missing:
        return {**body, "record_digest": digest(body)}
    if isinstance(q.case, UnsupportedPhaseCase):
        body.update(
            status="out_of_scope",
            notes=[
                "No qualified bulk solidification/front solver in this facade; frost air/saturation clipping and deposition latent-energy coupling are not qualified"
            ],
            native_called=False,
        )
        return {**body, "record_digest": digest(body)}
    if isinstance(q.case,LumpedEnthalpyStorageCase):
        return _evaluate_local_storage(q,c,body)
    before = native_backend_metadata()
    if before != q.owner_pins:
        raise ValueError("thermal owner source/runtime/common reference pins changed")
    body["provider"] = before
    try:
        body["native_called"] = True
        native, values, audits = _native(q, c)
        body.update(
            native=json_value(native),
            values=json_value(values),
            audits=json_value(audits),
            status="computed_diagnostic",
            notes=[],
        )
        for name, value in audits.items():
            if value is not None and "residual_W" in name:
                scale = max(
                    [abs(v) for k, v in values.items() if isinstance(v, (int, float)) and k.endswith("_W")]
                    + [0.0]
                )
                if abs(value) > q.absolute_energy_tolerance_W + q.relative_energy_tolerance * scale:
                    body.update(
                        status="diagnostic_rejected", notes=["declared native energy budget did not close"]
                    )
        if isinstance(q.case, DynamicThermalCase):
            case = q.case
            mass_bound = q.absolute_mass_tolerance_kg_s * case.duration_s + q.relative_energy_tolerance * max(
                abs(case.mass_flow_in_kg_s - case.mass_flow_out_kg_s) * case.duration_s,
                abs(values["mass_kg"] - native["state_before"]["mass_kg"]),
            )
            energy_bound = (
                q.absolute_energy_tolerance_W * case.duration_s
                + q.relative_energy_tolerance
                * max(abs(values["fluid_energy_change_J"]), abs(values["wall_energy_change_J"]))
            )
            if (
                abs(audits["mass_increment_residual_kg"]) > mass_bound
                or abs(audits["endpoint_mass_residual_kg_s"]) > q.absolute_mass_tolerance_kg_s
                or audits["closed_adiabatic_total_energy_residual_J"] is not None
                and abs(audits["closed_adiabatic_total_energy_residual_J"]) > energy_bound
            ):
                body.update(
                    status="diagnostic_rejected",
                    notes=[
                        "native mass increment or closed adiabatic fluid/wall energy budget did not close"
                    ],
                )
    except (ValueError, RuntimeError, ArithmeticError, TypeError, KeyError) as error:
        body.update(status="out_of_scope", notes=[f"{type(error).__name__}: {error}"])
    if native_backend_metadata() != before:
        raise ValueError("thermal owner source, runtime or reference changed during execution")
    return {**body, "record_digest": digest(body)}


def verify_phase_change_record(record, *, request=None, context=None):
    try:
        json_value(record)
        if record["record_digest"] != digest({k: v for k, v in record.items() if k != "record_digest"}):
            raise ValueError("thermal result checksum changed")
        q = PhaseChangeRequest.model_validate_json(json.dumps(record["request"], allow_nan=False))
        c = RunContext.model_validate_json(json.dumps(record["context"], allow_nan=False))
        if (
            request is not None
            and _checked(request, PhaseChangeRequest) != q
            or context is not None
            and _checked(context, RunContext) != c
        ):
            raise ValueError("actual caller request/context differs from the retained thermal scope")
        if evaluate_phase_change(q, c) != record:
            raise ValueError(
                "thermal input/native physics/phase/units/budget/source/status differs from current replay"
            )
        return {"valid": True}
    except (ValueError, TypeError, KeyError, ImportError, AttributeError, OSError) as error:
        return {"valid": False, "reason": str(error)}


def to_signal_frames(record, context):
    if (
        record.get("status") != "computed_diagnostic"
        or not verify_phase_change_record(record, context=context)["valid"]
    ):
        raise ValueError("verified computed thermal record and actual context required")
    q = PhaseChangeRequest.model_validate_json(json.dumps(record["request"]))
    if isinstance(q.case,LumpedEnthalpyStorageCase):
        local=record.get("local_model")
        if not isinstance(local,dict) or local.get("request")!=q.case.storage.model_dump(mode="json"):
            raise ValueError("local phase-storage signal requires the bound original local result")
        units={"temperature_K":"K","enthalpy_J":"J","liquid_mass_fraction":"1",
               "solid_mass_fraction":"1","mass_kg":"kg"}
        return tuple(SignalFrame(
            stream_id=f"{context.asset_id}/{q.event_id}/{q.case.kind}/{q.case.storage.id}",
            clock_id=context.clock_id,time_s=row["time_s"],
            values=dict(temperature_K=row["temperature_K"],enthalpy_J=row["enthalpy_J"],
                        liquid_mass_fraction=row["liquid_mass_fraction"],
                        solid_mass_fraction=1-row["liquid_mass_fraction"],mass_kg=q.case.storage.mass_kg),
            units=units,valid={key:True for key in units},
            origin="synthetic" if context.origin=="synthetic" else "simulation",
            operating_mode="lumped_"+row["phase"]) for row in local["states"])
    if q.time_s is None:
        raise ValueError("a source without an actual observation/application time cannot create SignalFrames")
    values = record["values"]
    units = {
        k: "kg/(m2*s)"
        if k.endswith("_kg_m2_s")
        else "kg/s"
        if k.endswith("_kg_s")
        else "J/kg"
        if k.endswith("_J_kg")
        else "W/(m2*K)"
        if k.endswith("_W_m2_K")
        else "Pa"
        if k.endswith("_Pa_abs")
        else "K"
        if k.endswith("_K")
        else "J"
        if k.endswith("_J")
        else "kg"
        if k.endswith("_kg")
        else "W"
        if k.endswith("_W")
        else "1"
        for k in values
    }
    time = q.time_s + (q.case.duration_s if isinstance(q.case, DynamicThermalCase) else 0.0)
    return (
        SignalFrame(
            stream_id=f"{context.asset_id}/{q.event_id}/{q.case.kind}",
            clock_id=context.clock_id,
            time_s=time,
            values=values,
            units=units,
            valid={k: v is not None for k, v in values.items()},
            origin="synthetic" if context.origin == "synthetic" else "simulation",
            operating_mode="conditional_native_thermal_model",
        ),
    )


def to_flow_boundaries(record, context):
    if (
        record.get("status") != "computed_diagnostic"
        or not verify_phase_change_record(record, context=context)["valid"]
    ):
        raise ValueError("verified computed thermal record required")
    q = PhaseChangeRequest.model_validate_json(json.dumps(record["request"]))
    if isinstance(q.case,LumpedEnthalpyStorageCase):
        raise ValueError("fixed-mass lumped enthalpy storage has no equipment mass-flow boundary")
    if q.time_s is None:
        raise ValueError("flow exchange requires an explicit actual application time and clock")
    case, native = q.case, record["native"]
    if not isinstance(case, (SteadyThermalCase, DynamicThermalCase)):
        raise ValueError("interfacial kinetic demand/insulation heat is not an equipment flow boundary")
    inlet = native["inlet"]
    outlet = native["result"]["outlet"] if isinstance(case, SteadyThermalCase) else native["result"]["fluid"]
    rates = (
        (case.mass_flow_kg_s, case.mass_flow_kg_s)
        if isinstance(case, SteadyThermalCase)
        else (case.mass_flow_in_kg_s, case.mass_flow_out_kg_s)
    )
    time = q.time_s + (case.duration_s if isinstance(case, DynamicThermalCase) else 0.0)
    basis = f"CoolProp-HEOS:{q.property_basis.fluid}:runtime-anchor:{digest(q.owner_pins['reference_anchors'][q.property_basis.fluid])}"
    rows = []
    for port, direction, rate, state in zip(
        (case.inlet_port_id, case.outlet_port_id),
        ("into_component", "out_of_component"),
        rates,
        (inlet, outlet),
        strict=True,
    ):
        quality = state["quality"]
        phase = (
            "two_phase"
            if quality is not None
            else {"supercritical_gas": "gas", "supercritical_liquid": "liquid"}.get(
                state["phase"], state["phase"]
            )
        )
        rows.append(
            FlowBoundary(
                component_id=q.asset_id,
                port_id=port,
                clock_id=context.clock_id,
                time_s=time,
                direction=direction,
                fluid=q.property_basis.fluid,
                property_basis=basis,
                pressure_pa_abs=state["pressure_Pa"],
                temperature_k=state["temperature_K"],
                mass_flow_kg_s=rate,
                specific_enthalpy_j_kg=state["specific_enthalpy_J_kg"],
                phase=phase,
                vapour_mass_fraction=quality,
                evidence_ids=q.evidence_ids,
            )
        )
    return tuple(rows)
