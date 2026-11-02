"""Gas differential-pressure meters and explicit real-gas reference-volume states.

Fluids owns the metering correlations and iterative solution. The existing
thermodynamic provider resolves HEOS states; no standard coefficients are copied.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import math
from importlib.metadata import distribution, version
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .contracts import Contract, Name, Nonnegative, Positive, RunContext, SignalFrame, digest, json_value
from .datasets import file_digest
from .models.equipment import LiquidMeter, liquid_meter
from .thermo import CheckedProvider, CoolPropProvider, StateRequest

SCHEMA = "pipeline_integrity.gas_meter.v1"
REVIEWS = (
    "meter_geometry_and_actual_pressure_taps",
    "steady_nonchoked_single_gas_phase",
    "correlation_or_calibrated_coefficients_and_support",
    "gas_composition_and_expansibility_exponent",
    "same_composition_reference_volume_basis",
)
INPUT_UNITS = {
    "length": "m",
    "pressure": "Pa_abs",
    "temperature": "K",
    "time": "s",
    "mole_fraction": "1",
    "coefficient": "1",
    "reynolds": "1",
    "mach": "1",
}


class GasComponent(Contract):
    fluid: Name
    mole_fraction: Positive = Field(le=1)

    @model_validator(mode="after")
    def identity(self):
        if any(c in self.fluid for c in "[]&:|"):
            raise ValueError("components identify backend fluids without embedded mixtures or imposed phases")
        return self


class GasMeterGeometry(Contract):
    native_meter_type: Name
    inner_pipe_diameter_m: Positive
    secondary_dimension_m: Positive
    secondary_dimension_meaning: Literal["circular_throat_diameter", "cone_end_diameter", "wedge_flow_height"]
    taps: Name | None
    tap_position: Name | None = None
    tap_description: Name

    @model_validator(mode="after")
    def bore(self):
        if self.secondary_dimension_m >= self.inner_pipe_diameter_m:
            raise ValueError("secondary meter dimension must lie strictly inside the pipe bore")
        return self


class GasVolumeReference(Contract):
    pressure_pa_abs: Positive | None
    temperature_K: Positive | None
    volume_basis: Literal["same_composition_dry", "same_composition_wet"] | None
    evidence_id: Name


class GasMeterSupport(Contract):
    beta_range: tuple[Positive, Positive]
    reynolds_range: tuple[Nonnegative, Positive]
    upstream_pressure_range_pa_abs: tuple[Positive, Positive]
    upstream_temperature_range_K: tuple[Positive, Positive]
    maximum_pressure_drop_fraction: Positive = Field(le=0.25)
    maximum_upstream_mach: Positive = Field(le=0.3)
    evidence_id: Name

    @model_validator(mode="after")
    def ordered(self):
        if (
            any(
                a > b
                for a, b in (
                    self.beta_range,
                    self.reynolds_range,
                    self.upstream_pressure_range_pa_abs,
                    self.upstream_temperature_range_K,
                )
            )
            or self.beta_range[1] >= 1
        ):
            raise ValueError("meter support intervals must be ordered with beta strictly below one")
        return self


class GasMeterRequest(Contract):
    asset_id: Name
    meter_id: Name
    location_id: Name
    clock_id: Name
    event_id: Name
    time_s: Nonnegative | None
    clock_offset_s: Nonnegative
    nominal_positive_source_port: Name
    nominal_positive_target_port: Name
    upstream_port_id: Name
    downstream_port_id: Name
    geometry: GasMeterGeometry | None
    components: tuple[GasComponent, ...] | None
    volume_basis: Literal["same_composition_dry", "same_composition_wet"] | None
    upstream_pressure_pa_abs: Positive | None
    downstream_pressure_pa_abs: Positive | None
    upstream_temperature_K: Positive | None
    coefficient_mode: Literal["native_correlation", "specified_pair"]
    specified_discharge_coefficient: Positive | None = None
    specified_expansibility_factor: Positive | None = Field(default=None, le=1)
    exponent_basis: Literal["local_isentropic_rho_a2_over_P", "reviewed_cp_cv_approximation"]
    reference: GasVolumeReference | None
    require_reference_volume: bool = True
    support: GasMeterSupport | None
    reversed_geometry_reviewed: bool = False
    reversed_geometry_evidence_id: Name | None = None
    geometry_evidence_id: Name
    observation_evidence_id: Name
    composition_evidence_id: Name
    coefficient_evidence_id: Name
    evidence_ids: tuple[Name, ...] = Field(min_length=1)
    applicability_reviews: dict[str, bool] = Field(default_factory=dict)
    review_evidence_ids: dict[str, Name] = Field(default_factory=dict)
    input_units: dict[str, str]
    expected_fluids_version: Name = "1.3.1"
    expected_coolprop_version: Name = "8.0.0"

    @model_validator(mode="after")
    def identities(self):
        if self.input_units != INPUT_UNITS:
            raise ValueError("gas metering requires explicit SI absolute-pressure input units")
        if self.nominal_positive_source_port == self.nominal_positive_target_port or {
            self.upstream_port_id,
            self.downstream_port_id,
        } != {self.nominal_positive_source_port, self.nominal_positive_target_port}:
            raise ValueError("distinct actual meter ports must match the declared nominal pair")
        if self.components is not None:
            if (
                not self.components
                or len({c.fluid for c in self.components}) != len(self.components)
                or not math.isclose(
                    sum(c.mole_fraction for c in self.components), 1.0, rel_tol=0, abs_tol=1e-12
                )
            ):
                raise ValueError("unique gas component mole fractions must sum to one without normalization")
        if self.coefficient_mode == "native_correlation" and (
            self.specified_discharge_coefficient is not None
            or self.specified_expansibility_factor is not None
        ):
            raise ValueError("choose native correlations or an explicit coefficient pair")
        if (set(self.applicability_reviews) | set(self.review_evidence_ids)) - set(REVIEWS):
            raise ValueError("unknown meter applicability review")
        return self


def composition_digest(request):
    return (
        digest([c.model_dump(mode="json") for c in request.components])
        if request.components is not None
        else None
    )


def boundary_metadata(request):
    return {
        "meter_id": request.meter_id,
        "location_id": request.location_id,
        "event_id": request.event_id,
        "nominal_positive_ports": [
            request.nominal_positive_source_port,
            request.nominal_positive_target_port,
        ],
        "clock_offset_s": request.clock_offset_s,
        "volume_basis": request.volume_basis,
        "composition_digest": composition_digest(request),
        "reference": request.reference.model_dump(mode="json") if request.reference is not None else None,
        "reference_volume_requested": request.require_reference_volume,
    }


def _policy(request, context):
    if (request.asset_id, request.clock_id) != (context.asset_id, context.clock_id):
        raise ValueError("gas meter request and RunContext asset/clock differ")
    ids = [e.id for e in context.evidence]
    selected = request.evidence_ids
    if len(set(ids)) != len(ids) or len(set(selected)) != len(selected) or set(selected) - set(ids):
        raise ValueError("meter selected evidence must be unique and present in RunContext")
    needed = {
        request.geometry_evidence_id,
        request.observation_evidence_id,
        request.composition_evidence_id,
        request.coefficient_evidence_id,
        *request.review_evidence_ids.values(),
    }
    if request.reference is not None:
        needed.add(request.reference.evidence_id)
    if request.support is not None:
        needed.add(request.support.evidence_id)
    if request.reversed_geometry_evidence_id is not None:
        needed.add(request.reversed_geometry_evidence_id)
    if needed - set(selected):
        raise ValueError("all meter boundary/property/review evidence must be selected")
    if context.metadata.get("gas_meter_boundary") != boundary_metadata(request):
        raise ValueError(
            "RunContext gas_meter_boundary does not bind meter/ports/composition/reference/event"
        )
    missing = [
        f"review and evidence required: {key}"
        for key in REVIEWS
        if request.applicability_reviews.get(key) is not True or key not in request.review_evidence_ids
    ]
    for key in (
        "time_s",
        "geometry",
        "components",
        "volume_basis",
        "upstream_pressure_pa_abs",
        "downstream_pressure_pa_abs",
        "upstream_temperature_K",
        "support",
    ):
        if getattr(request, key) is None:
            missing.append("required input unknown: " + key)
    if request.require_reference_volume and (
        request.reference is None
        or any(
            getattr(request.reference, key) is None
            for key in ("pressure_pa_abs", "temperature_K", "volume_basis")
        )
    ):
        missing.append(
            "explicit reference absolute pressure, temperature and same-composition volume basis required"
        )
    if request.coefficient_mode == "specified_pair" and (
        request.specified_discharge_coefficient is None or request.specified_expansibility_factor is None
    ):
        missing.append("reviewed discharge and expansibility coefficient pair required")
    if (
        request.coefficient_mode == "native_correlation"
        and request.geometry is not None
        and "orifice" in request.geometry.native_meter_type.lower()
        and request.geometry.taps is None
    ):
        missing.append("actual orifice pressure tap selector required; no default tap inference")
    return missing


def _backend():
    import CoolProp.CoolProp as cp
    import fluids.flow_meter as meter

    tree = hashlib.sha256()
    dist = distribution("fluids")
    paths = sorted(
        p for p in dist.files if str(p).replace("\\", "/").startswith("fluids/") and str(p).endswith(".py")
    )
    for path in paths:
        tree.update(str(path).replace("\\", "/").encode())
        tree.update(dist.locate_file(path).read_bytes())
    pin = {
        "fluids_version": version("fluids"),
        "fluids_source_tree_sha256": tree.hexdigest(),
        "fluids_source_tree_files": len(paths),
        "flow_meter_module_sha256": file_digest(Path(meter.__file__)),
        "CoolProp_version": version("CoolProp"),
        "CoolProp_revision": cp.get_global_param_string("gitrevision"),
        "CoolProp_extension_sha256": file_digest(Path(cp.__file__)),
        "property_adapter_sha256": file_digest(Path(inspect.getfile(CoolPropProvider))),
        "liquid_meter_adapter_sha256": file_digest(Path(inspect.getfile(liquid_meter))),
    }
    return meter, cp, pin


def available_gas_meter_types():
    """Explicit optional-library discovery; unreviewed evaluations never call this."""
    meter, _, pin = _backend()
    return {
        "meter_types": sorted(meter.all_meters),
        "meaning": "native selector names, not standards compliance or field qualification",
        "backend": pin,
    }


def _fluid(request):
    if len(request.components) == 1:
        return request.components[0].fluid
    return "&".join(f"{c.fluid}[{c.mole_fraction:.17g}]" for c in request.components)


def _early_scope(request):
    if request.upstream_pressure_pa_abs < request.downstream_pressure_pa_abs:
        return (
            "declared upstream pressure is below the downstream tap; ports are never automatically exchanged"
        )
    if request.reference is not None and request.reference.volume_basis != request.volume_basis:
        return "wet/dry or composition-changing reference-volume conversion is not implemented"
    if request.volume_basis == "same_composition_dry" and any(
        c.fluid.lower() in {"water", "h2o"} for c in request.components
    ):
        return "dry-basis declaration conflicts with an explicit water component"
    if request.upstream_port_id != request.nominal_positive_source_port:
        if (
            request.coefficient_mode != "specified_pair"
            or request.reversed_geometry_reviewed is not True
            or request.reversed_geometry_evidence_id is None
        ):
            return "reverse direction requires a separately reviewed coefficient pair and geometry evidence; native correlations are not automatically reversed"
    support = request.support
    p = request.upstream_pressure_pa_abs
    t = request.upstream_temperature_K
    if (
        not support.upstream_pressure_range_pa_abs[0] <= p <= support.upstream_pressure_range_pa_abs[1]
        or not support.upstream_temperature_range_K[0] <= t <= support.upstream_temperature_range_K[1]
    ):
        return "measured state lies outside caller-reviewed operating support"
    if (p - request.downstream_pressure_pa_abs) / p > support.maximum_pressure_drop_fraction:
        return "pressure-drop fraction exceeds reviewed moderate-drop support (hard maximum 0.25)"
    return None


def _native(request, body):
    meter, cp, backend = _backend()
    body["backend"] = backend
    if (
        backend["fluids_version"] != request.expected_fluids_version
        or backend["CoolProp_version"] != request.expected_coolprop_version
    ):
        raise ValueError("native library version differs from reviewed request")
    geometry = request.geometry
    kind = geometry.native_meter_type
    if kind not in meter.all_meters:
        raise ValueError("unknown native differential-pressure meter selector")
    meaning = (
        "cone_end_diameter"
        if "cone" in kind
        else "wedge_flow_height"
        if "wedge" in kind
        else "circular_throat_diameter"
    )
    if geometry.secondary_dimension_meaning != meaning:
        raise ValueError("secondary meter dimension meaning differs from the native meter family")
    provider = CheckedProvider(CoolPropProvider())
    fluid = _fluid(request)

    def call(name, inputs, operation, *, meter_call=False):
        entry = {"function": name, "inputs": inputs}
        body["native_calls"].append(entry)
        body["native_called"] = True
        if meter_call:
            body["meter_called"] = True
        try:
            value = operation()
        except Exception as exc:
            entry.update(status="failed", error=f"{type(exc).__name__}: {exc}")
            raise

        def safe(item):
            if isinstance(item, float) and not math.isfinite(item):
                return {"value": None, "nonfinite": str(item)}
            if isinstance(item, (list, tuple)):
                return [safe(v) for v in item]
            if isinstance(item, dict):
                return {k: safe(v) for k, v in item.items()}
            return item

        entry.update(
            status="returned",
            output=safe(value.model_dump(mode="json") if isinstance(value, Contract) else value),
        )
        return value

    def state(pressure, temperature, label):
        query = StateRequest(fluid=fluid, backend="HEOS", inputs={"P": pressure, "T": temperature})
        value = call(
            "CoolPropProvider.resolve",
            {"role": label, **query.model_dump(mode="json")},
            lambda: provider.resolve(query),
        )
        if value.phase not in {"gas", "supercritical_gas"}:
            raise ValueError(
                "meter requires gas or below-critical-pressure gaslike state; liquid/two-phase/fully supercritical states are outside scope"
            )
        if not math.isclose(value.pressure_pa_abs, pressure, rel_tol=1e-9) or not math.isclose(
            value.temperature_k, temperature, rel_tol=1e-9
        ):
            raise ValueError("property provider changed the measured absolute state")
        z = call(
            "CoolProp.PropsSI",
            {"role": label + "_Z", "output": "Z", "P": pressure, "T": temperature, "fluid": "HEOS::" + fluid},
            lambda: float(cp.PropsSI("Z", "P", pressure, "T", temperature, "HEOS::" + fluid)),
        )
        if not math.isfinite(z) or z <= 0:
            raise ValueError("property backend returned nonphysical compressibility")
        return value, z

    canonical = []
    for component in request.components:
        name = call(
            "CoolProp.get_fluid_param_string",
            {"fluid": component.fluid, "parameter": "name"},
            lambda component=component: cp.get_fluid_param_string(component.fluid, "name"),
        )
        canonical.append(name)
    if len(set(canonical)) != len(canonical):
        raise ValueError("gas components resolve to duplicate canonical backend fluids")
    if request.volume_basis == "same_composition_dry" and any(
        name in {"Water", "HeavyWater"} for name in canonical
    ):
        raise ValueError(
            "dry-basis declaration contains a canonical water component, including a backend alias"
        )

    upstream, z_up = state(request.upstream_pressure_pa_abs, request.upstream_temperature_K, "upstream")
    if (
        upstream.viscosity_pa_s is None
        or upstream.speed_of_sound_m_s is None
        or upstream.cp_j_kg_k is None
        or upstream.cv_j_kg_k is None
    ):
        body.update(
            status="needs_data", notes=["property backend did not resolve viscosity, sound speed, Cp and Cv"]
        )
        return
    reference, z_ref = (None, None)
    if request.require_reference_volume:
        reference, z_ref = state(
            request.reference.pressure_pa_abs, request.reference.temperature_K, "reference_same_composition"
        )
    rho = upstream.density_kg_m3
    mu = upstream.viscosity_pa_s
    gamma_s = rho * upstream.speed_of_sound_m_s**2 / request.upstream_pressure_pa_abs
    cp_cv = upstream.cp_j_kg_k / upstream.cv_j_kg_k
    exponent = gamma_s if request.exponent_basis == "local_isentropic_rho_a2_over_P" else cp_cv
    if exponent <= 1 or not math.isfinite(exponent):
        raise ValueError("native gas expansibility requires a finite exponent greater than one")
    beta = call(
        "fluids.differential_pressure_meter_beta",
        {"D": geometry.inner_pipe_diameter_m, "D2": geometry.secondary_dimension_m, "meter_type": kind},
        lambda: float(
            meter.differential_pressure_meter_beta(
                D=geometry.inner_pipe_diameter_m, D2=geometry.secondary_dimension_m, meter_type=kind
            )
        ),
    )
    if not request.support.beta_range[0] <= beta <= request.support.beta_range[1]:
        raise ValueError("native meter beta lies outside caller-reviewed geometry support")
    p1 = request.upstream_pressure_pa_abs
    p2 = request.downstream_pressure_pa_abs
    inputs = {
        "D": geometry.inner_pipe_diameter_m,
        "D2": geometry.secondary_dimension_m,
        "P1": p1,
        "P2": p2,
        "rho": rho,
        "mu": mu,
        "k": exponent,
        "meter_type": kind,
        "taps": geometry.taps,
        "tap_position": geometry.tap_position,
    }
    if request.coefficient_mode == "specified_pair":
        coefficient = request.specified_discharge_coefficient
        epsilon = request.specified_expansibility_factor
        flow_inputs = {
            "D": inputs["D"],
            "Do": inputs["D2"],
            "P1": p1,
            "P2": p2,
            "rho": rho,
            "C": coefficient,
            "expansibility": epsilon,
            "meter_type": kind,
        }
        mass = call(
            "fluids.flow_meter_discharge",
            flow_inputs,
            lambda: float(meter.flow_meter_discharge(**flow_inputs)),
            meter_call=True,
        )
    elif p1 == p2:
        body.update(
            status="out_of_scope",
            notes=[
                "zero differential pressure has a zero-flow limit but the native Reynolds-dependent coefficient is undefined at zero; use a reviewed pair to evaluate that limit"
            ],
        )
        return
    else:
        mass = call(
            "fluids.differential_pressure_meter_solver",
            inputs,
            lambda: float(meter.differential_pressure_meter_solver(**inputs)),
            meter_call=True,
        )
        coefficient, epsilon = call(
            "fluids.differential_pressure_meter_C_epsilon",
            {**inputs, "m": mass},
            lambda: meter.differential_pressure_meter_C_epsilon(**inputs, m=mass),
            meter_call=True,
        )
    if (
        not all(math.isfinite(v) for v in (mass, coefficient, epsilon))
        or mass < 0
        or coefficient <= 0
        or not 0 < epsilon <= 1
    ):
        raise ValueError("native flow/coefficient/expansibility left the physical domain")
    forward = float(
        meter.flow_meter_discharge(
            D=inputs["D"],
            Do=inputs["D2"],
            P1=p1,
            P2=p2,
            rho=rho,
            C=coefficient,
            expansibility=epsilon,
            meter_type=kind,
        )
    )
    reynolds = 4 * mass / (math.pi * geometry.inner_pipe_diameter_m * mu)
    fit_axes = None
    if request.coefficient_mode == "native_correlation" and kind.startswith("Hollingshead"):
        if "venturi" in kind:
            fit_axes = {
                "reynolds_range": [
                    math.exp(min(meter.venturi_logRes_Hollingshead)),
                    math.exp(max(meter.venturi_logRes_Hollingshead)),
                ]
            }
        else:
            table = (
                meter.cone_Hollingshead_tck
                if "cone" in kind
                else meter.wedge_Hollingshead_tck
                if "wedge" in kind
                else meter.orifice_std_Hollingshead_tck
            )
            fit_axes = {
                "beta_range": [float(min(table[0])), float(max(table[0]))],
                "reynolds_range": [math.exp(min(table[1])), math.exp(max(table[1]))],
            }
        if not fit_axes["reynolds_range"][0] <= reynolds <= fit_axes["reynolds_range"][1] or (
            "beta_range" in fit_axes
            and not fit_axes["beta_range"][0] - 1e-12 <= beta <= fit_axes["beta_range"][1] + 1e-12
        ):
            raise ValueError("native interpolation fit would extrapolate outside its actual stored axes")
    mach = mass / (rho * math.pi * geometry.inner_pipe_diameter_m**2 / 4 * upstream.speed_of_sound_m_s)
    if (
        not request.support.reynolds_range[0] <= reynolds <= request.support.reynolds_range[1]
        or mach > request.support.maximum_upstream_mach
    ):
        raise ValueError("computed Reynolds number or upstream Mach lies outside reviewed support")
    if not math.isclose(mass, forward, rel_tol=1e-9, abs_tol=1e-12):
        raise ValueError(
            "implicit native flow does not reproduce its final coefficient/expansibility relation"
        )
    sign = 1.0 if request.upstream_port_id == request.nominal_positive_source_port else -1.0
    actual = mass / rho
    ref = mass / reference.density_kg_m3 if reference is not None else None
    compensation = rho / reference.density_kg_m3 if reference is not None else None
    p_t_z = (
        p1
        / request.reference.pressure_pa_abs
        * request.reference.temperature_K
        / request.upstream_temperature_K
        * z_ref
        / z_up
        if reference is not None
        else None
    )
    if compensation is not None and not math.isclose(compensation, p_t_z, rel_tol=1e-8, abs_tol=1e-12):
        raise ValueError("same-composition real-gas density and P/T/Z compensation disagree")
    liquid_check = None
    if meaning == "circular_throat_diameter" and coefficient <= 1:
        limiting = liquid_meter(
            LiquidMeter(
                pipe_diameter_m=geometry.inner_pipe_diameter_m,
                opening_diameter_m=geometry.secondary_dimension_m,
                pressure_difference_pa=p1 - p2,
                density_kg_m3=rho,
                discharge_coefficient=coefficient,
            )
        )
        liquid_check = {
            "existing_model": "instrument.liquid_dp_meter",
            "mass_flow_before_expansibility_kg_s": limiting.values["mass_flow_kg_s"],
            "gas_mass_after_expansibility_kg_s": limiting.values["mass_flow_kg_s"] * epsilon,
        }
        if not math.isclose(
            mass, liquid_check["gas_mass_after_expansibility_kg_s"], rel_tol=1e-9, abs_tol=1e-12
        ):
            raise ValueError("existing incompressible meter relation and gas expansibility differ")
    body["values"] = {
        "signed_mass_flow_kg_s": sign * mass,
        "signed_actual_volume_flow_m3_s": sign * actual,
        "signed_reference_volume_flow_m3_s": sign * ref if ref is not None else None,
        "upstream_density_kg_m3": rho,
        "reference_density_kg_m3": reference.density_kg_m3 if reference is not None else None,
        "reynolds": reynolds,
        "discharge_coefficient": coefficient,
        "expansibility_factor": epsilon,
        "beta": beta,
        "local_isentropic_exponent": gamma_s,
        "cp_cv_ratio": cp_cv,
        "exponent_supplied_to_native": exponent,
        "compressibility_Z": z_up,
        "reference_compressibility_Z": z_ref,
        "upstream_mach": mach,
        "pressure_difference_pa": p1 - p2,
    }
    body["audits"] = {
        "native_forward_mass_residual_kg_s": mass - forward,
        "actual_volume_mass_residual_kg_s": actual * rho - mass,
        "reference_volume_mass_residual_kg_s": ref * reference.density_kg_m3 - mass
        if ref is not None
        else None,
        "density_compensation_factor": compensation,
        "PTZ_compensation_factor": p_t_z,
        "existing_liquid_meter_check": liquid_check,
        "native_interpolation_fit_axes": fit_axes,
        "canonical_backend_components": canonical,
        "tap_pressure_ratio": p2 / p1,
        "ideal_gas_critical_ratio_screen": (2 / (exponent + 1)) ** (exponent / (exponent - 1)),
        "choking_meaning": "tap-ratio diagnostic only; actual vena-contracta/throat choking is not inferred; reviewed nonchoked applicability remains required",
        "reference_volume_status": "computed_same_composition" if reference is not None else "not_requested",
        "basis_digest": digest(
            {
                "composition": composition_digest(request),
                "volume_basis": request.volume_basis,
                "reference": request.reference,
                "property_backend": "HEOS",
            }
        ),
    }
    body["status"] = "computed"


def evaluate_gas_meter(request: GasMeterRequest, context: RunContext):
    request = GasMeterRequest.model_validate(request.model_dump(mode="python"))
    context = RunContext.model_validate(context.model_dump(mode="python"))
    body = {
        "schema": SCHEMA,
        "request": request.model_dump(mode="json"),
        "context": context.model_dump(mode="json"),
        "status": "needs_data",
        "native_called": False,
        "meter_called": False,
        "values": None,
        "audits": None,
        "backend": None,
        "native_calls": [],
        "adapter_sha256": file_digest(Path(__file__)),
        "qualification": "research_screening; no custody transfer, whole-standard or field calibration qualification",
    }
    body["input_digest"] = digest({"request": body["request"], "context": body["context"]})
    missing = _policy(request, context)
    if missing:
        body["notes"] = missing
    else:
        outside = _early_scope(request)
        if outside:
            body.update(status="out_of_scope", notes=[outside])
        else:
            try:
                _native(request, body)
            except (ValueError, ArithmeticError, RuntimeError, TypeError) as exc:
                body.update(status="out_of_scope", notes=[f"{type(exc).__name__}: {exc}"])
    body = json_value(body)
    body["record_digest"] = digest(body)
    return body


def verify_gas_meter_record(
    record, request: GasMeterRequest | None = None, context: RunContext | None = None
):
    """Replay deterministic library mathematics; an unsigned digest is not approval."""
    try:
        body = {key: value for key, value in record.items() if key != "record_digest"}
        if record["schema"] != SCHEMA or digest(body) != record["record_digest"]:
            raise ValueError("gas meter record changed")
        supplied = GasMeterRequest.model_validate_json(json.dumps(record["request"], allow_nan=False))
        ctx = RunContext.model_validate_json(json.dumps(record["context"], allow_nan=False))
        if (request is not None and supplied != request) or (context is not None and ctx != context):
            raise ValueError("gas meter request/context differs from the expected boundary")
        replay = evaluate_gas_meter(supplied, ctx)
        if replay != record:
            raise ValueError(
                "meter state, native relation, source pin, reference conversion or status differs on replay"
            )
        return {
            "valid": True,
            "meaning": "unsigned current-source numerical replay and boundary consistency, not historical execution or engineering approval",
        }
    except (ValueError, TypeError, KeyError, ImportError, OSError, AttributeError) as exc:
        return {"valid": False, "reason": str(exc)}


def to_signal_frame(record, context: RunContext):
    if record.get("status") != "computed" or not verify_gas_meter_record(record, context=context)["valid"]:
        raise ValueError("verified computed meter result with matching context required")
    request = GasMeterRequest.model_validate_json(json.dumps(record["request"]))
    fields = {
        "signed_mass_flow_kg_s": "kg/s",
        "signed_actual_volume_flow_m3_s": "m3/s",
        "signed_reference_volume_flow_m3_s": "m3/s",
        "pressure_difference_pa": "Pa",
    }
    values = {key: record["values"][key] for key in fields}
    return SignalFrame(
        stream_id=f"{request.asset_id}:{request.meter_id}",
        clock_id=context.clock_id,
        time_s=request.time_s + request.clock_offset_s,
        values=values,
        units=fields,
        valid={key: value is not None for key, value in values.items()},
        origin="synthetic" if context.origin == "synthetic" else "simulation",
        operating_mode="reviewed-gas-meter-estimate",
    )


def to_signal_frames(records, context: RunContext):
    frames = tuple(to_signal_frame(record, context) for record in records)
    if any(b.time_s <= a.time_s for a, b in zip(frames, frames[1:], strict=False)):
        raise ValueError("meter signal history must preserve strictly increasing source times")
    return frames
