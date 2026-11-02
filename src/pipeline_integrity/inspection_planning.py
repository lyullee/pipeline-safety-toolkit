"""Finite inspection campaigns compiled to the shared scheduler with owner-computed detection likelihoods."""

import math
from decimal import ROUND_CEILING, Decimal
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, model_validator

from .adapters import ExternalResult, MaterialClient
from .contracts import Contract, Fraction, Name, Nonnegative, Positive, RunContext, digest
from .datasets import file_digest
from .scheduling import (
    Count,
    Demand,
    Operation,
    OperationMode,
    PlanningResource,
    ScheduleRequest,
    ScheduleResult,
    solve_schedule,
    ticks,
)


class InspectionHypothesis(Contract):
    id: Name
    prior_mass: Fraction
    unrevealed_weight: Nonnegative
    evidence_id: Name


class LogisticInspectionPOD(Contract):
    kind: Literal["logistic"] = "logistic"
    midpoint_size_m: Positive
    log_size_scale: Positive
    supported_minimum_size_m: Nonnegative
    supported_maximum_size_m: Positive
    evidence_id: Name

    @model_validator(mode="after")
    def support(self):
        if self.supported_maximum_size_m <= self.supported_minimum_size_m:
            raise ValueError("declared detection support must have positive width")
        return self


class DeclaredInspectionPOD(Contract):
    kind: Literal["declared"] = "declared"
    probabilities: dict[Name, Fraction]
    evidence_id: Name
    scope: Literal["detection_given_hypothesis_and_covered"] = "detection_given_hypothesis_and_covered"


class InspectionOption(Contract):
    id: Name
    method_id: Name
    start_s: Nonnegative
    duration_s: Positive
    coverage_fraction: Fraction
    sizes_m: dict[Name, Nonnegative]
    detection: Annotated[LogisticInspectionPOD | DeclaredInspectionPOD, Field(discriminator="kind")]
    resources: dict[Name, Demand] = Field(default_factory=dict)
    budget_use: dict[Name, Demand] = Field(default_factory=dict)
    evidence_id: Name


class InspectionTarget(Contract):
    id: Name
    location_id: Name
    hypotheses: tuple[InspectionHypothesis, ...] = Field(min_length=1, max_length=100)
    options: tuple[InspectionOption, ...] = Field(min_length=1, max_length=100)
    required: bool = False

    @model_validator(mode="after")
    def aligned(self):
        ids = {h.id for h in self.hypotheses}
        if len(ids) != len(self.hypotheses) or not math.isclose(
            math.fsum(h.prior_mass for h in self.hypotheses), 1.0, rel_tol=0, abs_tol=1e-12
        ):
            raise ValueError("distinct inspection hypotheses must carry normalized prior masses")
        if len({o.id for o in self.options}) != len(self.options) or any(
            o.id == "not_selected" for o in self.options
        ):
            raise ValueError("option IDs must be distinct and not use the reserved not_selected mode")
        for option in self.options:
            if set(option.sizes_m) != ids:
                raise ValueError("each option needs a size in meters for every declared hypothesis")
            if isinstance(option.detection, DeclaredInspectionPOD):
                if set(option.detection.probabilities) != ids:
                    raise ValueError("declared POD probabilities must match the hypothesis identities")
                if any(
                    option.sizes_m[key] == 0 and pod != 0
                    for key, pod in option.detection.probabilities.items()
                ):
                    raise ValueError("this no-false-call model cannot assign detection to a zero-size state")
        return self


class InspectionCampaign(Contract):
    horizon_s: Positive
    time_quantum_s: Positive = 1.0
    targets: tuple[InspectionTarget, ...] = Field(min_length=1, max_length=1000)
    resources: tuple[PlanningResource, ...] = ()
    budgets: dict[Name, Count] = Field(default_factory=dict)
    score_quantum: Positive
    criterion_unit: Name
    evidence_id: Name
    model_scope: Literal["expected_unrevealed_weight_at_horizon"] = "expected_unrevealed_weight_at_horizon"
    coverage_interpretation: Literal["random_coverage_independent_of_hypothesis"] = (
        "random_coverage_independent_of_hypothesis"
    )
    false_indications: Literal["excluded"] = "excluded"
    maximum_material_calls: int = Field(default=10000, ge=1, le=100000)
    maximum_seconds: Positive = Field(default=30.0, le=3600)
    relative_gap_limit: Fraction = 0.0
    random_seed: int = Field(default=0, ge=0, le=2147483647)

    @model_validator(mode="after")
    def bounds(self):
        if len({t.id for t in self.targets}) != len(self.targets):
            raise ValueError("inspection target identities must be unique")
        if len({t.location_id for t in self.targets}) != len(self.targets):
            raise ValueError("repeated plans for the same location need a joint repeated-inspection model")
        resources = {r.id for r in self.resources}
        if len(resources) != len(self.resources):
            raise ValueError("resource identities must be unique")
        ticks(self.horizon_s, self.time_quantum_s)
        calls = len(self.targets)
        for target in self.targets:
            for option in target.options:
                calls += 1 + (
                    len(target.hypotheses) if isinstance(option.detection, LogisticInspectionPOD) else 0
                )
                if set(option.resources) - resources or set(option.budget_use) - self.budgets.keys():
                    raise ValueError("inspection options reference undeclared resources or budgets")
                ticks(option.start_s, self.time_quantum_s)
                ticks(option.duration_s, self.time_quantum_s)
                finish = Decimal(str(option.start_s)) + Decimal(str(option.duration_s))
                if finish > Decimal(str(self.horizon_s)):
                    raise ValueError("inspection appointments must finish within the campaign horizon")
        if calls > self.maximum_material_calls:
            raise ValueError("inspection material-call budget exceeded")
        return self


class InspectionPlan(Contract):
    schema_version: Literal["pipeline_integrity.inspection_plan.v1"] = "pipeline_integrity.inspection_plan.v1"
    status: Literal["optimal", "feasible", "infeasible", "unknown", "withheld", "needs_data"]
    context: RunContext
    campaign: InspectionCampaign
    native_material_results: tuple[ExternalResult, ...]
    option_scores: tuple[dict, ...]
    scheduling_specification: ScheduleRequest | None
    schedule: ScheduleResult | None
    selected: tuple[dict, ...]
    unrounded_model_score: Nonnegative | None
    model_score_lower_bound: Nonnegative | None
    rounding_error_upper_bound: Nonnegative
    rounded_score_proven_optimal: bool
    unrounded_score_proven_optimal: Literal[False] = False
    issues: tuple[str, ...]
    criterion_is_failure_probability: Literal[False] = False
    field_calibrated: Literal[False] = False
    work_authorized: Literal[False] = False
    implementation_sha256: Name
    result_digest: Name

    def check_integrity(self):
        InspectionPlan.model_validate(self.model_dump(mode="python"))
        if digest(self.model_dump(mode="json", exclude={"result_digest"})) != self.result_digest:
            raise ValueError("inspection plan or source evidence changed")
        for result in self.native_material_results:
            if digest(result.payload) != result.payload_digest:
                raise ValueError("the owner material result changed")
        if self.schedule is not None:
            self.schedule.check_integrity()
            if self.schedule.specification_digest != digest(self.scheduling_specification):
                raise ValueError("the shared schedule differs from the inspection compilation")


def _cost(score, quantum):
    if not math.isfinite(score) or score < 0:
        raise ValueError("inspection model scores must be finite and nonnegative")
    cost = int((Decimal(str(score)) / Decimal(str(quantum))).to_integral_value(rounding=ROUND_CEILING))
    if cost > 1_000_000_000:
        raise ValueError("inspection score precision exceeds the shared integer cost budget")
    return cost


def plan_inspections(campaign: InspectionCampaign, context: RunContext, *, materials=None, backend=None):
    """Minimize a declared unrevealed-burden criterion; detection is not repair or physical risk reduction."""
    campaign = InspectionCampaign.model_validate(campaign.model_dump(mode="python"))
    context = RunContext.model_validate(context.model_dump(mode="python"))
    evidence = {e.id for e in context.evidence}
    required = {campaign.evidence_id} | {r.evidence_id for r in campaign.resources}
    required.update(h.evidence_id for t in campaign.targets for h in t.hypotheses)
    required.update(
        e for t in campaign.targets for o in t.options for e in (o.evidence_id, o.detection.evidence_id)
    )
    if len(evidence) != len(context.evidence) or required - evidence:
        raise ValueError(
            "inspection hypotheses, detection, appointments and planning need unique context evidence"
        )
    materials = MaterialClient() if materials is None else materials
    native, scores, issues, jobs = [], [], [], []

    def evaluate(model_id, parameters, target):
        result = materials.evaluate(model_id, parameters, context, location_id=target.location_id)
        result = ExternalResult.model_validate(result.model_dump(mode="python"))
        payload = result.payload
        if (
            digest(payload) != result.payload_digest
            or result.owner != "matintegrity"
            or result.status != payload["status"]
            or payload["model_id"] != model_id
            or payload["inputs"] != parameters
            or payload["context"]["asset_id"] != context.asset_id
            or payload["context"]["location_id"] != target.location_id
            or payload["context"]["time_basis"] != context.clock_id
            or payload["context"]["metadata"]["pipeline_context_digest"] != digest(context)
        ):
            raise ValueError("the material result changed its inputs, source identity or clock")
        native.append(result)
        return result

    def score(target, option):
        probabilities = []
        if option is None:
            probabilities = [0.0] * len(target.hypotheses)
            coverage, key = 0.0, "not_selected"
        else:
            coverage, key = option.coverage_fraction, option.id
            for h in target.hypotheses:
                size = option.sizes_m[h.id]
                if isinstance(option.detection, DeclaredInspectionPOD):
                    probabilities.append(option.detection.probabilities[h.id])
                else:
                    curve = option.detection
                    if (
                        size != 0
                        and not curve.supported_minimum_size_m <= size <= curve.supported_maximum_size_m
                    ):
                        issues.append(f"detection_size_outside_reviewed_support:{target.id}:{key}:{h.id}")
                        return None
                    result = evaluate(
                        "inspection.logistic_pod",
                        {
                            "flaw_size_m": size,
                            "midpoint_size_m": curve.midpoint_size_m,
                            "log_size_scale": curve.log_size_scale,
                        },
                        target,
                    )
                    if result.status != "computed":
                        issues.append(f"detection_unresolved:{target.id}:{key}:{result.status}")
                        return None
                    pod = result.payload["values"]["probability_of_detection"]
                    if (
                        isinstance(pod, bool)
                        or not isinstance(pod, (int, float))
                        or not math.isfinite(pod)
                        or not 0 <= pod <= 1
                    ):
                        raise ValueError("the owner returned an invalid detection probability")
                    probabilities.append(float(pod))
        result = evaluate(
            "inspection.no_indication_update",
            {
                "prior_weights": [h.prior_mass for h in target.hypotheses],
                "probabilities_of_detection": probabilities,
                "coverage_fraction": coverage,
            },
            target,
        )
        impossible = coverage == 1 and all(
            pod == 1 for pod, h in zip(probabilities, target.hypotheses, strict=True) if h.prior_mass > 0
        )
        if result.status == "out_of_scope" and impossible:
            value, likelihood, posterior = 0.0, 0.0, None
        elif result.status == "computed":
            posterior = result.payload["values"]["posterior_weights"]
            likelihood = result.payload["values"]["probability_of_no_indication"]
            if (
                not isinstance(posterior, list)
                or len(posterior) != len(target.hypotheses)
                or any(
                    isinstance(p, bool) or not isinstance(p, (int, float)) for p in (*posterior, likelihood)
                )
                or not math.isfinite(likelihood)
                or not 0 < likelihood <= 1
                or any(not math.isfinite(p) or not 0 <= p <= 1 for p in posterior)
                or not math.isclose(math.fsum(posterior), 1.0, abs_tol=1e-12)
            ):
                raise ValueError("the owner returned invalid no-indication likelihoods or posterior masses")
            value = likelihood * math.fsum(
                p * h.unrevealed_weight for p, h in zip(posterior, target.hypotheses, strict=True)
            )
        else:
            issues.append(f"no_indication_unresolved:{target.id}:{key}:{result.status}")
            return None
        row = {
            "target_id": target.id,
            "option_id": key,
            "score": value,
            "rounded_cost": _cost(value, campaign.score_quantum),
            "probability_of_no_indication": likelihood,
            "posterior_given_no_indication": posterior,
            "no_indication_update_status": result.status,
            "zero_likelihood_has_no_conditional_posterior": impossible,
        }
        scores.append(row)
        return row

    for target in campaign.targets:
        modes = []
        if not target.required:
            baseline = score(target, None)
            if baseline is not None:
                modes.append(
                    OperationMode(
                        id="not_selected",
                        duration_s=0.0,
                        fixed_cost=baseline["rounded_cost"],
                        evidence_id=campaign.evidence_id,
                        earliest_start_s=0.0,
                        latest_finish_s=0.0,
                    )
                )
        for option in target.options:
            row = score(target, option)
            if row is not None:
                finish = float(Decimal(str(option.start_s)) + Decimal(str(option.duration_s)))
                modes.append(
                    OperationMode(
                        id=option.id,
                        duration_s=option.duration_s,
                        resources=option.resources,
                        consumables=option.budget_use,
                        fixed_cost=row["rounded_cost"],
                        evidence_id=option.evidence_id,
                        earliest_start_s=option.start_s,
                        latest_finish_s=finish,
                    )
                )
        if modes:
            jobs.append(Operation(id=target.id, modes=tuple(modes)))

    specification, schedule, selected, model_score, lower_bound = None, None, (), None, None
    if issues:
        status = "needs_data"
    else:
        specification = ScheduleRequest(
            horizon_s=campaign.horizon_s,
            time_quantum_s=campaign.time_quantum_s,
            operations=tuple(jobs),
            resources=campaign.resources,
            consumable_budgets=campaign.budgets,
            makespan_cost_per_tick=0,
            objective_unit="ceiling_scaled_unrevealed_weight",
            evidence_id=campaign.evidence_id,
            maximum_intervals=101000,
            maximum_seconds=campaign.maximum_seconds,
            relative_gap_limit=campaign.relative_gap_limit,
            random_seed=campaign.random_seed,
        )
        schedule = solve_schedule(specification, context, backend=backend)
        status = schedule.status
        if status in {"optimal", "feasible"}:
            by_option = {(r["target_id"], r["option_id"]): r for r in scores}
            selected = tuple(
                row | {"score": by_option[row["operation_id"], row["mode_id"]]["score"]}
                for row in schedule.timeline
            )
            model_score = math.fsum(row["score"] for row in selected)
            lower_bound = max(
                0.0,
                float(
                    (Decimal(schedule.audit["integer_lower_bound"]) - len(campaign.targets))
                    * Decimal(str(campaign.score_quantum))
                ),
            )
    initial = InspectionPlan(
        status=status,
        context=context,
        campaign=campaign,
        native_material_results=tuple(native),
        option_scores=tuple(scores),
        scheduling_specification=specification,
        schedule=schedule,
        selected=selected,
        unrounded_model_score=model_score,
        model_score_lower_bound=lower_bound,
        rounding_error_upper_bound=float(
            Decimal(len(campaign.targets)) * Decimal(str(campaign.score_quantum))
        ),
        rounded_score_proven_optimal=schedule.proven_optimal if schedule is not None else False,
        issues=tuple(issues),
        implementation_sha256=file_digest(Path(__file__)),
        result_digest="pending",
    )
    data = initial.model_dump(mode="python", exclude={"result_digest"})
    result = InspectionPlan(**data, result_digest=digest(data))
    result.check_integrity()
    return result
