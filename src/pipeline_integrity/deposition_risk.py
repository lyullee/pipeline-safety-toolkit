"""Conditional hydrate/wax envelope comparisons; physical phase models remain providers."""

from __future__ import annotations

import json
import math
import platform
from bisect import bisect_left
from importlib.metadata import version
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .contracts import Contract, Fraction, Name, Nonnegative, Positive, RunContext, digest, json_value
from .datasets import file_digest

SHA = r"^[a-f0-9]{64}$"
REVIEWS = (
    "composition_water_activity_inhibitor_and_wax_basis",
    "equilibrium_boundary_model_support_and_interpolation",
    "pressure_temperature_uncertainty_and_clock_location",
    "sampled_exposure_is_not_kinetics_or_plugging",
)


def _receipt_budget(value):
    stack, count = [(value, 0)], 0
    while stack:
        item, depth = stack.pop()
        count += 1
        if count > 10000 or depth > 16:
            raise ValueError("native receipt node/depth work budget exceeded")
        if isinstance(item, (dict, tuple, list)):
            if len(item) + count > 10000:
                raise ValueError("native receipt node budget exceeded")
            stack.extend((v, depth + 1) for v in (item.values() if isinstance(item, dict) else item))


class DepositionBasis(Contract):
    composition_mole_fractions: dict[Name, Fraction | None]
    composition_definition_id: Name
    water_activity_mode: Literal["supplied_value", "native_composition_model", "unknown"]
    water_activity: Fraction | None
    water_activity_definition_id: Name
    inhibitor_mass_fraction: Fraction | None
    inhibitor_definition_id: Name
    wax_characterization_sha256: str | None = Field(pattern=SHA)
    evidence_id: Name

    @model_validator(mode="after")
    def coherent(self):
        if not 1 <= len(self.composition_mole_fractions) <= 128:
            raise ValueError("bounded explicit composition is required")
        z = list(self.composition_mole_fractions.values())
        if all(v is not None for v in z) and not math.isclose(math.fsum(z), 1.0, rel_tol=0, abs_tol=1e-12):
            raise ValueError("mole fractions must sum to one; no automatic normalization")
        if self.water_activity_mode == "supplied_value" and self.water_activity is None:
            raise ValueError("supplied water activity mode requires its actual value")
        if self.water_activity_mode != "supplied_value" and self.water_activity is not None:
            raise ValueError("native/unknown activity must not masquerade as supplied activity")
        return self


class EnvelopeKnot(Contract):
    pressure_pa_abs: Positive
    boundary_temperature_lower_k: Positive | None
    boundary_temperature_upper_k: Positive | None

    @model_validator(mode="after")
    def uncertainty(self):
        if (
            self.boundary_temperature_lower_k is not None
            and self.boundary_temperature_upper_k is not None
            and self.boundary_temperature_lower_k > self.boundary_temperature_upper_k
        ):
            raise ValueError("boundary temperature bounds are reversed")
        return self


class DepositionEnvelope(Contract):
    status: Literal["computed", "withheld"]
    phenomenon: Literal["hydrate", "wax"]
    parameter_basis_sha256: str = Field(pattern=SHA)
    provider_id: Name
    provider_version: Name
    provider_source_sha256: str = Field(pattern=SHA)
    provider_configuration_sha256: str = Field(pattern=SHA)
    interpolation: Literal["piecewise_linear_temperature_in_pressure"]
    stability_side: Literal["below_boundary_temperature"]
    boundary_uncertainty_basis_id: Name
    knots: tuple[EnvelopeKnot, ...] = Field(min_length=2, max_length=5000)
    native_receipt: dict = Field(default_factory=dict)
    evaluation_sha256: str = Field(pattern=SHA)

    @model_validator(mode="after")
    def ordered(self):
        if any(
            b.pressure_pa_abs <= a.pressure_pa_abs for a, b in zip(self.knots, self.knots[1:], strict=False)
        ):
            raise ValueError("pressure knots must strictly increase on one selected branch without sorting")
        _receipt_budget(self.native_receipt)
        if len(json.dumps(json_value(self.native_receipt))) > 200000:
            raise ValueError("native envelope receipt exceeds bounded JSON byte budget")
        return self


class DepositionObservation(Contract):
    id: Name
    time_s: Nonnegative
    pressure_bounds_pa_abs: tuple[Positive, Positive] | None
    temperature_bounds_k: tuple[Positive, Positive] | None
    valid: bool | None
    evidence_id: Name

    @model_validator(mode="after")
    def bounds(self):
        if any(
            v is not None and v[0] > v[1] for v in (self.pressure_bounds_pa_abs, self.temperature_bounds_k)
        ):
            raise ValueError("observation uncertainty bounds are reversed")
        return self


class DepositionRequest(Contract):
    id: Name
    asset_id: Name
    location_id: Name
    physical_port_id: Name
    clock_id: Name
    source_origin: Literal["measured", "laboratory", "simulation", "synthetic"]
    phenomenon: Literal["hydrate", "wax"]
    basis: DepositionBasis
    observations: tuple[DepositionObservation, ...] = Field(min_length=2, max_length=100000)
    source_mode: Literal["reviewed_envelope", "provider"]
    provider_id: Name
    provider_version: Name
    provider_source_sha256: str = Field(pattern=SHA)
    provider_configuration_sha256: str = Field(pattern=SHA)
    expected_envelope_payload_sha256: str | None = Field(default=None, pattern=SHA)
    observation_source_sha256: str = Field(pattern=SHA)
    maximum_sample_gap_s: Positive
    maximum_interpolation_pressure_gap_pa: Positive
    minimum_sampled_window_s: Positive
    maximum_knots: int = Field(default=5000, ge=2, le=5000)
    maximum_pressure_knot_comparisons: int = Field(default=10000000, ge=1, le=500000000)
    reviews: dict[str, bool] = Field(default_factory=dict)
    review_evidence_ids: dict[str, Name] = Field(default_factory=dict)
    evidence_id: Name

    @model_validator(mode="after")
    def alignment(self):
        if len({r.id for r in self.observations}) != len(self.observations) or any(
            b.time_s <= a.time_s for a, b in zip(self.observations, self.observations[1:], strict=False)
        ):
            raise ValueError("observation IDs must be distinct and clock must increase without sorting")
        if (set(self.reviews) | set(self.review_evidence_ids)) - set(REVIEWS):
            raise ValueError("review keys differ from this envelope applicability boundary")
        if self.source_mode == "reviewed_envelope" and self.expected_envelope_payload_sha256 is None:
            raise ValueError(
                "reviewed envelope requires a preselected payload digest excluding evaluation seal"
            )
        return self


def envelope_payload_sha256(envelope):
    return digest(envelope.model_dump(mode="python", exclude={"evaluation_sha256"}))


def envelope_binding(q, c, envelope):
    return digest({"request": q, "context": c, "envelope_payload": envelope_payload_sha256(envelope)})


def boundary_metadata(q):
    return {
        "request_sha256": digest(q),
        "meaning": "fixed-basis sampled equilibrium-envelope margins; no plugging probability",
    }


def _at(pressures, knots, pressure, index):
    j = bisect_left(pressures, pressure)
    if j < len(pressures) and pressure == pressures[j]:
        return (knots[j].boundary_temperature_lower_k, knots[j].boundary_temperature_upper_k)[index]
    left, right = j - 1, j
    weight = (pressure - pressures[left]) / (pressures[right] - pressures[left])
    values = (
        (knots[left].boundary_temperature_lower_k, knots[right].boundary_temperature_lower_k)
        if index == 0
        else (knots[left].boundary_temperature_upper_k, knots[right].boundary_temperature_upper_k)
    )
    return (1 - weight) * values[0] + weight * values[1]


def assess_deposition(request: DepositionRequest, context: RunContext, *, envelope=None, provider=None):
    if not 2 <= len(request.observations) <= 100000:
        raise ValueError("observation budget exceeded before cloning")
    if envelope is not None and len(envelope.knots) > request.maximum_knots:
        raise ValueError("envelope knot budget exceeded before cloning")
    if envelope is not None:
        _receipt_budget(envelope.native_receipt)
    q = DepositionRequest.model_validate(request.model_dump(mode="python"))
    c = RunContext.model_validate(context.model_dump(mode="python"))
    if (q.asset_id, q.clock_id, q.source_origin) != (c.asset_id, c.clock_id, c.origin) or c.metadata.get(
        "deposition_boundary"
    ) != boundary_metadata(q):
        raise ValueError(
            "context must bind exact deposition request, asset, clock and original generation origin"
        )
    evidence = [e.id for e in c.evidence]
    needed = {
        q.evidence_id,
        q.basis.evidence_id,
        *q.review_evidence_ids.values(),
        *(r.evidence_id for r in q.observations),
    }
    if len(set(evidence)) != len(evidence) or needed - set(evidence):
        raise ValueError("all composition/model/observation/review evidence must be selected")
    missing = [k for k in REVIEWS if q.reviews.get(k) is not True or k not in q.review_evidence_ids]
    if (
        any(v is None for v in q.basis.composition_mole_fractions.values())
        or q.basis.water_activity_mode == "unknown"
        or q.basis.inhibitor_mass_fraction is None
        or q.phenomenon == "wax"
        and q.basis.wax_characterization_sha256 is None
    ):
        missing.append("known fixed composition, water activity model, inhibitor and applicable wax basis")
    body = {
        "schema": "deposition_risk.v1",
        "request": q.model_dump(mode="json"),
        "context": c.model_dump(mode="json"),
        "status": "needs_data",
        "missing": missing,
        "envelope": None,
        "point_comparisons": [],
        "sampled_below_boundary_windows": [],
        "continuous_exposure_duration_s": None,
        "kinetics_calculated": False,
        "deposited_mass_kg": None,
        "plugging_probability": None,
        "field_or_code_acceptance": False,
        "output_origin": "simulation",
        "module_sha256": file_digest(Path(__file__)),
        "thermo_source_sha256": file_digest(Path(__file__).with_name("thermo.py")),
        "contracts_source_sha256": file_digest(Path(__file__).with_name("contracts.py")),
        "runtime": {
            "python": platform.python_version(),
            "pydantic": version("pydantic"),
            "pydantic_core": version("pydantic-core"),
        },
    }

    def seal():
        json_value(body)
        return dict(body, record_sha256=digest(body))

    if missing:
        return seal()
    if q.source_mode == "provider":
        if envelope is not None:
            raise ValueError("provider output cannot be silently replaced with a stored curve")
        if provider is None:
            body["missing"].append("explicit original envelope provider")
            return seal()
        envelope = provider(q, c)
    elif provider is not None:
        raise ValueError("reviewed envelope cannot silently switch to native provider mode")
    if envelope is None:
        body["missing"].append("reviewed equilibrium envelope")
        return seal()
    if not isinstance(envelope, DepositionEnvelope):
        raise TypeError("provider must return DepositionEnvelope")
    if (
        len(envelope.knots) > q.maximum_knots
        or len(envelope.knots) * len(q.observations) > q.maximum_pressure_knot_comparisons
    ):
        raise ValueError("envelope comparison work budget exceeded")
    _receipt_budget(envelope.native_receipt)
    e = DepositionEnvelope.model_validate(envelope.model_dump(mode="python"))
    if (
        e.phenomenon,
        e.parameter_basis_sha256,
        e.provider_id,
        e.provider_version,
        e.provider_source_sha256,
        e.provider_configuration_sha256,
    ) != (
        q.phenomenon,
        digest(q.basis),
        q.provider_id,
        q.provider_version,
        q.provider_source_sha256,
        q.provider_configuration_sha256,
    ):
        raise ValueError("envelope fixed physical basis or original provider source/configuration differs")
    if (
        q.expected_envelope_payload_sha256 is not None
        and envelope_payload_sha256(e) != q.expected_envelope_payload_sha256
    ):
        raise ValueError("preselected envelope payload changed")
    if e.evaluation_sha256 != envelope_binding(q, c, e):
        raise ValueError("envelope input/context/output binding changed")
    body["envelope"] = e.model_dump(mode="json")
    if e.status != "computed" or any(
        k.boundary_temperature_lower_k is None or k.boundary_temperature_upper_k is None for k in e.knots
    ):
        body["missing"].append("computed complete boundary uncertainty envelope")
        return seal()
    pressures = [k.pressure_pa_abs for k in e.knots]
    if any(
        b - a > q.maximum_interpolation_pressure_gap_pa
        for a, b in zip(pressures, pressures[1:], strict=False)
    ):
        body["missing"].append("boundary pressure gap exceeds the reviewed interpolation resolution")
        return seal()
    start = None
    for i, row in enumerate(q.observations):
        result = {
            "observation_id": row.id,
            "status": "needs_data",
            "classification": "unresolved",
            "temperature_margin_bounds_k": None,
        }
        if (
            row.valid is True
            and row.pressure_bounds_pa_abs is not None
            and row.temperature_bounds_k is not None
        ):
            lo, hi = row.pressure_bounds_pa_abs
            if lo < pressures[0] or hi > pressures[-1]:
                result["status"] = "out_of_scope"
            else:
                points = [lo, hi, *(p for p in pressures if lo < p < hi)]
                lower = min(_at(pressures, e.knots, p, 0) for p in points)
                upper = max(_at(pressures, e.knots, p, 1) for p in points)
                margins = (row.temperature_bounds_k[0] - upper, row.temperature_bounds_k[1] - lower)
                if not all(math.isfinite(v) for v in margins):
                    raise ValueError("temperature comparison cannot be represented with finite margins")
                result.update(
                    status="computed",
                    temperature_margin_bounds_k=list(margins),
                    classification="entirely_below_boundary"
                    if margins[1] < 0
                    else "entirely_above_boundary"
                    if margins[0] > 0
                    else "boundary_or_uncertainty_overlap",
                )
        body["point_comparisons"].append(result)
        if i and row.time_s - q.observations[i - 1].time_s > q.maximum_sample_gap_s:
            start = None
        if result["classification"] != "entirely_below_boundary":
            start = None
            continue
        if start is None:
            start = i
        span = row.time_s - q.observations[start].time_s
        if span >= q.minimum_sampled_window_s:
            body["sampled_below_boundary_windows"].append(
                {
                    "start_observation_id": q.observations[start].id,
                    "end_observation_id": row.id,
                    "sample_span_s": span,
                    "sample_count": i - start + 1,
                    "meaning": "below the selected equilibrium boundary at supplied samples only; not continuous residence or deposition",
                }
            )
    statuses = {r["status"] for r in body["point_comparisons"]}
    body["status"] = (
        "needs_data"
        if "needs_data" in statuses
        else "out_of_scope"
        if "out_of_scope" in statuses
        else "computed"
    )
    return seal()


def verify_deposition_record(record, *, provider=None, request=None, context=None):
    if digest({k: v for k, v in record.items() if k != "record_sha256"}) != record.get("record_sha256"):
        raise ValueError("deposition record seal changed")
    q = DepositionRequest.model_validate_json(json.dumps(record["request"]))
    c = RunContext.model_validate_json(json.dumps(record["context"]))
    if (
        request is not None
        and digest(request) != digest(q)
        or context is not None
        and digest(context) != digest(c)
    ):
        raise ValueError("caller boundary differs from stored deposition boundary")
    e = None
    if q.source_mode == "reviewed_envelope" and record["envelope"] is not None:
        e = DepositionEnvelope.model_validate_json(json.dumps(record["envelope"]))
    if assess_deposition(q, c, envelope=e, provider=provider) != record:
        raise ValueError("deposition source/runtime/native replay differs")
    return True


class ThermopackHydrateConfig(Contract):
    eos: Name
    mixing: Name
    alpha: Name
    maximum_fluid_phases: int = Field(ge=2, le=3)
    hydrate_parameter_reference: Name
    minimum_pressure_pa_abs: Positive
    maximum_pressure_pa_abs: Positive
    minimum_temperature_k: Positive
    boundary_temperature_error_bound_k: Nonnegative | None
    boundary_uncertainty_basis_id: Name


def thermopack_source_fingerprint(package_root):
    root = Path(package_root).resolve()
    pins, total = {}, 0
    scanned = 0
    for path in root.rglob("*"):
        scanned += 1
        if scanned > 20000:
            raise ValueError("ThermoPack package traversal budget exceeded")
        if path.is_file() and path.suffix.lower() in {".py", ".dll", ".so", ".dylib", ".pyd"}:
            total += path.stat().st_size
            if len(pins) >= 512 or total > 100000000:
                raise ValueError("ThermoPack package fingerprint exceeds file/byte budget")
            pins[path.relative_to(root).as_posix()] = file_digest(path)
    if "hydrate.py" not in pins or "thermo.py" not in pins:
        raise ValueError("selected ThermoPack installation has no reviewed hydrate/thermo source")
    return {"files": pins, "source_sha256": digest(pins)}


class ThermopackHydrateProvider:
    """Optional actual upstream curve call, restricted to reviewed low-water uninhibited gas."""

    def __init__(self, config: ThermopackHydrateConfig):
        self.config = ThermopackHydrateConfig.model_validate(config.model_dump(mode="python"))

    def __call__(self, request, context):
        q, cfg = request, self.config
        if (q.phenomenon, q.provider_id) != (
            "hydrate",
            "thermopack_hydrate_appearance",
        ) or q.provider_configuration_sha256 != digest(cfg):
            raise ValueError("ThermoPack adapter requires its reviewed hydrate configuration")
        if (
            q.basis.water_activity_mode != "native_composition_model"
            or q.basis.inhibitor_mass_fraction != 0.0
        ):
            raise ValueError(
                "this upstream curve API does not accept supplied activity or inhibitor corrections"
            )
        components = tuple(q.basis.composition_mole_fractions)
        if (
            any("," in name or " " in name for name in components)
            or "H2O" not in components
            or any(v is None for v in q.basis.composition_mole_fractions.values())
        ):
            raise ValueError("native ordered ThermoPack component IDs and explicit H2O fraction are required")
        # This is the reviewed upstream numerical support guard, not a physical hydrate threshold.
        if q.basis.composition_mole_fractions["H2O"] > 250e-6:
            raise ValueError("upstream hydrate mapping supports only its declared low-water numerical range")
        if (
            cfg.maximum_pressure_pa_abs <= cfg.minimum_pressure_pa_abs
            or cfg.boundary_temperature_error_bound_k is None
        ):
            raise ValueError("reviewed pressure support and known boundary error bound are required")
        import importlib

        try:
            native_module = importlib.import_module("thermopack.hydrate")
        except ImportError as exc:
            raise ValueError(
                "hydrate API absent; reviewed source build required (PyPI 2.2.3 unsupported)"
            ) from exc
        native_class = getattr(native_module, "hydrate", None)
        if native_class is None or any(
            not callable(getattr(native_class, name, None))
            for name in ("init_thermo", "init_hydrate", "get_hydrate_apperance_curve", "get_model_id")
        ):
            raise ValueError("reviewed upstream hydrate method contract is absent or incompatible")

        pin = thermopack_source_fingerprint(Path(native_module.__file__).parent)
        if pin["source_sha256"] != q.provider_source_sha256 or version("thermopack") != q.provider_version:
            raise ValueError("native ThermoPack source/binary/version changed")
        model = native_class()
        model.init_thermo(cfg.eos, cfg.mixing, cfg.alpha, ",".join(components), cfg.maximum_fluid_phases)
        model.init_hydrate(parameter_reference=cfg.hydrate_parameter_reference)
        temperatures, pressures = model.get_hydrate_apperance_curve(
            cfg.minimum_pressure_pa_abs,
            tuple(q.basis.composition_mole_fractions.values()),
            cfg.minimum_temperature_k,
            cfg.maximum_pressure_pa_abs,
            print_to_file=False,
        )
        if (
            temperatures is None
            or pressures is None
            or len(temperatures) != len(pressures)
            or not 2 <= len(pressures) <= q.maximum_knots
        ):
            raise ValueError("native curve returned no complete bounded single-branch trajectory")
        error = cfg.boundary_temperature_error_bound_k
        knots = tuple(
            EnvelopeKnot(
                pressure_pa_abs=float(p),
                boundary_temperature_lower_k=float(t) - error,
                boundary_temperature_upper_k=float(t) + error,
            )
            for t, p in zip(temperatures, pressures, strict=True)
        )
        if any(
            not cfg.minimum_pressure_pa_abs <= k.pressure_pa_abs <= cfg.maximum_pressure_pa_abs for k in knots
        ) or any(float(t) < cfg.minimum_temperature_k for t in temperatures):
            raise ValueError("native branch left its declared pressure/temperature mapping support")
        result = DepositionEnvelope(
            status="computed",
            phenomenon="hydrate",
            parameter_basis_sha256=digest(q.basis),
            provider_id=q.provider_id,
            provider_version=q.provider_version,
            provider_source_sha256=q.provider_source_sha256,
            provider_configuration_sha256=q.provider_configuration_sha256,
            interpolation="piecewise_linear_temperature_in_pressure",
            stability_side="below_boundary_temperature",
            boundary_uncertainty_basis_id=cfg.boundary_uncertainty_basis_id,
            knots=knots,
            native_receipt={
                "owner": "ThermoPack",
                "license": "Apache-2.0",
                "source_pin": pin,
                "configuration": cfg.model_dump(mode="json"),
                "native_model_id": model.get_model_id(),
                "function": "get_hydrate_apperance_curve",
                "numpy_version": version("numpy"),
                "requested_mapping_maximum_points_upstream": 5000,
            },
            evaluation_sha256="0" * 64,
        )
        if thermopack_source_fingerprint(Path(native_module.__file__).parent) != pin:
            raise ValueError("ThermoPack source changed during native mapping")
        return result.model_copy(update={"evaluation_sha256": envelope_binding(q, context, result)})
