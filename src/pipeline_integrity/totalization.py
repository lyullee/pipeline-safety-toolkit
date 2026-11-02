"""Observed-window totals with explicit sample meaning and complete joint covariance."""

from __future__ import annotations

import json
import math
import platform
from importlib.metadata import version
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .contracts import Contract, Name, Nonnegative, Positive, RunContext, digest
from .datasets import file_digest
from .metrology import (
    CoverageDeclaration,
    MeasurementBasis,
    MeasurementBudgetRequest,
    MeasurementDecisionRule,
    MeasurementInput,
    MeasurementLinearization,
    MeasurementOutput,
    StandardCovariance,
    propagate_measurement_uncertainty,
)
from .quadrature import integrate_linear_interval
from .units import convert_quantity

REVIEWS = (
    "clock_source_and_basis_mapping",
    "sample_meaning_and_maximum_gap",
    "direction_and_no_counter_reset",
)


class TotalizationRequest(Contract):
    id: Name
    asset_id: Name
    meter_id: Name
    clock_id: Name
    event_id: Name
    clock_offset_s: float
    clock_representation_tolerance_s: Nonnegative = 1e-9
    nominal_positive_source_port: Name
    nominal_positive_target_port: Name
    sample_meaning: Literal["point_linear", "interval_mean", "cumulative_counter"]
    sign_convention: Literal["nonnegative", "signed_nominal_ports"]
    readings: tuple[MeasurementInput, ...] = Field(min_length=1, max_length=65536)
    times: tuple[MeasurementInput, ...] = Field(min_length=2, max_length=65537)
    output_basis: MeasurementBasis
    maximum_interval_s: Positive | None
    covariance: StandardCovariance | None = None
    uncertainty_required: bool = True
    local_uncertainty_approximation_reviewed: bool = False
    coverage: CoverageDeclaration | None = None
    decision_rules: tuple[MeasurementDecisionRule, ...] = ()
    evidence_id: Name
    applicability_reviews: dict[str, bool] = Field(default_factory=dict)

    @model_validator(mode="after")
    def coherent(self):
        expected = len(self.times) - (1 if self.sample_meaning == "interval_mean" else 0)
        if len(self.readings) != expected:
            raise ValueError("point/counter samples align with times; interval means align with time spans")
        if self.nominal_positive_source_port == self.nominal_positive_target_port:
            raise ValueError("totalization requires distinct nominal directional ports")
        if set(self.applicability_reviews) - set(REVIEWS):
            raise ValueError("unknown totalization review")
        ids = tuple(v.id for v in (*self.readings, *self.times))
        if len(set(ids)) != len(ids):
            raise ValueError("all reading/time identifiers must be unique")
        if self.covariance and self.covariance.input_ids != ids:
            raise ValueError("joint covariance order is readings followed by times")
        if any(v.basis != self.readings[0].basis for v in self.readings):
            raise ValueError("readings must have exactly the same physical/state basis")
        if any(v.basis != self.times[0].basis or v.basis.unit != "s" for v in self.times):
            raise ValueError("time coordinates must share an explicit SI second basis")
        if self.times[0].basis.definition_id != self.clock_id:
            raise ValueError("time basis definition must identify the request clock")
        known = [v.estimate for v in self.times if v.estimate is not None]
        if any(v < 0 for v in known) or any(b <= a for a, b in zip(known, known[1:], strict=False)):
            raise ValueError("known elapsed coordinates must be nonnegative and strictly increasing")
        if any(not math.isfinite(v + self.clock_offset_s) or v + self.clock_offset_s < 0 for v in known):
            raise ValueError("known coordinates plus offset must be finite nonnegative common-clock times")
        converted = [v + self.clock_offset_s for v in known]
        if any(b <= a for a, b in zip(converted, converted[1:], strict=False)):
            raise ValueError("common-clock offset loses the ordering or resolution of elapsed coordinates")
        for a, b in zip(known, known[1:], strict=False):
            converted_duration = (b + self.clock_offset_s) - (a + self.clock_offset_s)
            if abs(converted_duration - (b - a)) > self.clock_representation_tolerance_s:
                raise ValueError("common-clock offset changes the represented measurement duration")
        source_basis = self.readings[0].basis
        if source_basis.pressure_reference is not None or self.output_basis.pressure_reference is not None:
            raise ValueError("pressure-time exposure requires a separate reviewed measurand definition")
        if source_basis.reference_state != self.output_basis.reference_state:
            raise ValueError("totalization cannot change reference state, composition or moisture basis")
        source_unit = source_basis.unit
        if self.sample_meaning != "cumulative_counter":
            source_unit = f"({source_unit}) * s"
        try:
            scale = convert_quantity(1.0, source_unit, self.output_basis.unit)
        except Exception as exc:
            raise ValueError(
                "output unit must represent the integral of rate or difference of counter"
            ) from exc
        if scale != 1.0:
            raise ValueError("all totalization coordinates require coherent SI units")
        if any(r.output_id not in {"net_amount", "window_duration"} for r in self.decision_rules):
            raise ValueError("decision rules refer to net_amount or window_duration")
        if self.decision_rules and not self.uncertainty_required:
            raise ValueError("declared decisions require the measurement uncertainty budget")
        return self


def boundary_metadata(request):
    return {
        "meter_id": request.meter_id,
        "event_id": request.event_id,
        "clock_id": request.clock_id,
        "clock_offset_s": request.clock_offset_s,
        "clock_representation_tolerance_s": request.clock_representation_tolerance_s,
        "nominal_positive_source_port": request.nominal_positive_source_port,
        "nominal_positive_target_port": request.nominal_positive_target_port,
        "sample_meaning": request.sample_meaning,
        "reading_basis": request.readings[0].basis.model_dump(mode="json"),
        "output_basis": request.output_basis.model_dump(mode="json"),
    }


def _policy(request, context):
    if (request.asset_id, request.clock_id) != (context.asset_id, context.clock_id):
        raise ValueError("totalization asset/clock differs from RunContext")
    if context.metadata.get("totalization_boundary") != boundary_metadata(request):
        raise ValueError("totalization boundary, ports and basis must bind the exact context")
    evidence = {e.id for e in context.evidence}
    required = {request.evidence_id, request.output_basis.evidence_id}
    for v in (*request.readings, *request.times):
        required.update((v.evidence_id, v.basis.evidence_id))
        if v.basis.reference_state:
            required.add(v.basis.reference_state.evidence_id)
    for declaration in (request.covariance, request.coverage, *request.decision_rules):
        if declaration:
            required.add(declaration.evidence_id)
    if not required <= evidence:
        raise ValueError("every quantity, clock, physical basis and review requires context evidence")
    return [key for key in REVIEWS if request.applicability_reviews.get(key) is not True]


def _seal(record):
    record["record_digest"] = digest(record)
    return record


def totalize_measurements(request: TotalizationRequest, context: RunContext):
    request = TotalizationRequest.model_validate(request.model_dump(mode="python"))
    context = RunContext.model_validate(context.model_dump(mode="python"))
    missing_reviews = _policy(request, context)
    record = {
        "schema": "pipeline_integrity.totalization.v1",
        "request": request.model_dump(mode="json"),
        "context": context.model_dump(mode="json"),
        "status": "needs_data",
        "issues": [],
        "intervals": [],
        "values": None,
        "uncertainty": None,
        "source_inputs_digest": digest((*request.readings, *request.times)),
        "implementation_sha256": file_digest(Path(__file__)),
        "quadrature_sha256": file_digest(Path(__file__).with_name("quadrature.py")),
        "metrology_sha256": file_digest(Path(__file__).with_name("metrology.py")),
        "contracts_sha256": file_digest(Path(__file__).with_name("contracts.py")),
        "runtime": {
            "python": platform.python_version(),
            **{name: version(name) for name in ("numpy", "pydantic", "pydantic_core", "pint")},
        },
        "missing_intervals_filled": False,
        "calibration_inferred": False,
        "custody_transfer_certified": False,
    }
    if missing_reviews or request.maximum_interval_s is None:
        record["issues"] = [
            *missing_reviews,
            *([] if request.maximum_interval_s else ["maximum_interval_s unresolved"]),
        ]
        return _seal(record)
    if any(v.valid is False for v in (*request.readings, *request.times)):
        record.update(
            status="out_of_scope", issues=["explicit invalid measurements retained; no integration"]
        )
        return _seal(record)
    positive, negative, durations, skipped = [], [], [], []
    try:
        for i, (first, last) in enumerate(zip(request.times, request.times[1:], strict=False)):
            samples = (
                request.readings[i : i + 2]
                if request.sample_meaning != "interval_mean"
                else (request.readings[i],)
            )
            points = (first, last, *samples)
            if any(v.valid is not True or v.estimate is None for v in points):
                skipped.append(i)
                continue
            dt = last.estimate - first.estimate
            if dt > request.maximum_interval_s:
                skipped.append(i)
                continue
            if request.sample_meaning == "cumulative_counter":
                amount = math.fsum((samples[1].estimate, -samples[0].estimate))
                incoming, outgoing = max(amount, 0.0), max(-amount, 0.0)
                forbidden_negative = amount < 0
            else:
                p, q = samples[0].estimate, samples[-1].estimate
                incoming, outgoing = integrate_linear_interval(p, q, dt)
                forbidden_negative = p < 0 or q < 0
            if request.sign_convention == "nonnegative" and forbidden_negative:
                record.update(
                    status="out_of_scope",
                    issues=["negative flow/counter increment contradicts the declared sign convention"],
                )
                return _seal(record)
            positive.append(incoming)
            negative.append(outgoing)
            durations.append(dt)
            record["intervals"].append(
                {
                    "index": i,
                    "start_s": first.estimate + request.clock_offset_s,
                    "finish_s": last.estimate + request.clock_offset_s,
                    "positive_model_amount": incoming,
                    "negative_model_amount": outgoing,
                }
            )
        incoming, outgoing, duration = math.fsum(positive), math.fsum(negative), math.fsum(durations)
        net = math.fsum((incoming, -outgoing))
        if not all(math.isfinite(v) for v in (incoming, outgoing, duration, net)):
            raise ArithmeticError("totalization exceeds floating representation")
    except (ValueError, ArithmeticError, OverflowError) as error:
        record.update(status="out_of_scope", issues=[str(error)])
        return _seal(record)
    complete = not skipped
    record.update(status="computed" if complete else "needs_data", skipped_interval_indices=skipped)
    record["values"] = {
        "known_interval_net_amount": net,
        "complete_net_amount": net if complete else None,
        "positive_model_amount": incoming,
        "negative_model_amount": outgoing,
        "covered_duration_s": duration,
        "complete_window_duration_s": duration if complete else None,
        "unit": request.output_basis.unit,
        "maximum_clock_representation_error_s": max(
            (
                abs(
                    ((b.estimate + request.clock_offset_s) - (a.estimate + request.clock_offset_s))
                    - (b.estimate - a.estimate)
                )
                for a, b in zip(request.times, request.times[1:], strict=False)
                if a.estimate is not None and b.estimate is not None
            ),
            default=0.0,
        ),
        "directional_semantics": "signed_counter_increments_not_unobserved_gross_flow"
        if request.sample_meaning == "cumulative_counter"
        else "declared_piecewise_sample_model",
    }
    if not complete:
        record["issues"] = ["missing samples, unknown validity or long gaps leave intervals unresolved"]
        return _seal(record)
    if not request.uncertainty_required:
        return _seal(record)
    inputs = (*request.readings, *request.times)
    if len(inputs) > 64:
        record.update(
            status="out_of_scope",
            issues=[
                "full joint uncertainty is bounded to 64 coordinates; nominal amount retained, no independence or window splitting inferred"
            ],
        )
        return _seal(record)
    n, m = len(request.readings), len(request.times)
    derivative = [0.0] * (n + m)
    samples = [v.estimate for v in request.readings]
    times = [v.estimate for v in request.times]
    for i, (first, last) in enumerate(zip(times, times[1:], strict=False)):
        dt = last - first
        if request.sample_meaning == "point_linear":
            if dt / 2 == 0:
                record.update(
                    status="out_of_scope", issues=["time sensitivity underflows; nominal integral retained"]
                )
                return _seal(record)
            derivative[i] += dt / 2
            derivative[i + 1] += dt / 2
            summed = samples[i] + samples[i + 1]
            mean = summed / 2 if math.isfinite(summed) else samples[i] / 2 + samples[i + 1] / 2
            if mean == 0 and summed != 0:
                record.update(
                    status="out_of_scope", issues=["time sensitivity underflows; nominal integral retained"]
                )
                return _seal(record)
            derivative[n + i] -= mean
            derivative[n + i + 1] += mean
        elif request.sample_meaning == "interval_mean":
            derivative[i] = dt
            derivative[n + i] -= samples[i]
            derivative[n + i + 1] += samples[i]
    if request.sample_meaning == "cumulative_counter":
        derivative[0], derivative[n - 1] = -1.0, 1.0
    duration_derivative = [0.0] * (n + m)
    duration_derivative[n], duration_derivative[-1] = -1.0, 1.0
    duration_basis = MeasurementBasis(
        quantity="window_duration",
        unit="s",
        definition_id=f"{request.clock_id}:window_duration",
        evidence_id=request.evidence_id,
    )
    budget_request = MeasurementBudgetRequest(
        id=f"{request.id}:budget",
        asset_id=request.asset_id,
        clock_id=request.clock_id,
        event_id=request.event_id,
        time_s=times[-1] + request.clock_offset_s,
        inputs=inputs,
        covariance=request.covariance,
        coverage=request.coverage,
        decision_rules=request.decision_rules,
        linearization=MeasurementLinearization(
            model_id="observed_window_totalization",
            model_version="1",
            provider_source_sha256=record["implementation_sha256"],
            configuration_sha256=digest(request),
            inputs_sha256=digest(inputs),
            outputs=(
                MeasurementOutput(id="net_amount", estimate=net, basis=request.output_basis),
                MeasurementOutput(id="window_duration", estimate=duration, basis=duration_basis),
            ),
            jacobian=(tuple(derivative), tuple(duration_derivative)),
            evidence_id=request.evidence_id,
            local_approximation_reviewed=request.local_uncertainty_approximation_reviewed,
        ),
    )
    uncertainty = propagate_measurement_uncertainty(budget_request, context)
    record["uncertainty"] = uncertainty
    record["status"] = uncertainty["status"]
    record["issues"] = list(uncertainty["issues"])
    return _seal(record)


def verify_totalization_record(record, request=None, context=None):
    try:
        if record.get("record_digest") != digest({k: v for k, v in record.items() if k != "record_digest"}):
            raise ValueError("totalization seal differs")
        parsed = TotalizationRequest.model_validate_json(json.dumps(record["request"]))
        ctx = RunContext.model_validate_json(json.dumps(record["context"]))
        if (request is not None and request != parsed) or (context is not None and context != ctx):
            raise ValueError("expected totalization request/context differs")
        if totalize_measurements(parsed, ctx) != record:
            raise ValueError("totalization source, intervals, quantities or covariance differs on replay")
        return {
            "valid": True,
            "meaning": "unsigned current-source numerical/context replay; no field certification",
        }
    except (ValueError, TypeError, KeyError, AttributeError) as error:
        return {"valid": False, "reason": str(error)}
