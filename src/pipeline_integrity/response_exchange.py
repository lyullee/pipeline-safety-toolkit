"""Evidence and clock boundaries around the owner's response-time and recovery models."""

import json
import math
from importlib.metadata import version
from pathlib import Path
from types import SimpleNamespace
from typing import Annotated, Literal

from pydantic import Field, model_validator

from .contracts import Contract, Fraction, Name, Nonnegative, Positive, RunContext, digest, json_value
from .datasets import file_digest
from .risk_exchange import (
    FaultNode,
    HRARiskClient,
    JointStateCount,
    JointStateCountTable,
    RiskProbability,
    fault_tree_request_from_joint_counts,
)


class InformationResponseTrial(Contract):
    """One paired information/response observation in a declared opportunity."""

    id: Name
    information_error: bool | None
    response_error: bool | None
    evidence_id: Name
    sampling_cluster_id: Name | None = None


class InformationResponseStudy(Contract):
    information_event_id: Name
    response_event_id: Name
    condition_id: Name
    opportunity_id: Name
    opportunity_definition: Name
    population_id: Name
    grouping_unit: Name
    evidence_id: Name
    missingness_review_evidence_id: Name
    sampling_unit_evidence_id: Name | None = None
    missing_policy: Literal["require_complete", "reviewed_complete_cases"] = "require_complete"
    trials: tuple[InformationResponseTrial, ...] = Field(min_length=1, max_length=100000)

    @model_validator(mode="after")
    def paired_identity(self):
        if self.information_event_id == self.response_event_id:
            raise ValueError("information and response events need distinct identities")
        if len({row.id for row in self.trials}) != len(self.trials):
            raise ValueError("paired observations need unique opportunity trial identities")
        if self.grouping_unit != "trial" and any(row.sampling_cluster_id is None for row in self.trials):
            raise ValueError("non-trial sampling units require an explicit cluster ID for every trial")
        if self.grouping_unit != "trial" and self.sampling_unit_evidence_id is None:
            raise ValueError("non-trial sampling units require evidence for the cluster definition")
        if self.grouping_unit == "trial" and any(
            row.sampling_cluster_id not in {None, row.id} for row in self.trials
        ):
            raise ValueError("trial-level grouping cannot combine multiple trial IDs into a cluster")
        return self


def information_response_joint_counts(study: InformationResponseStudy, context: RunContext):
    """Preserve paired errors and missingness before existing joint-state normalization.

    Observed association does not establish a causal effect of information quality.
    Complete-case selection is explicit and never imputes an unknown outcome.
    """
    from .risk_exchange import JointStateCount, JointStateCountTable

    q = InformationResponseStudy.model_validate(study.model_dump(mode="python"))
    c = RunContext.model_validate(context.model_dump(mode="python"))
    available = [item.id for item in c.evidence]
    required = {q.evidence_id, q.missingness_review_evidence_id, *(r.evidence_id for r in q.trials)}
    if q.sampling_unit_evidence_id is not None:
        required.add(q.sampling_unit_evidence_id)
    if len(available) != len(set(available)) or not required <= set(available):
        raise ValueError("paired information/response and missingness evidence is absent or ambiguous")
    missing = [r.id for r in q.trials if r.information_error is None or r.response_error is None]
    if missing and q.missing_policy == "require_complete":
        raise ValueError("unknown information/response outcomes require an explicit selection review")
    counts = {}
    missing_ids = set(missing)
    for row in q.trials:
        if row.id in missing_ids:
            continue
        key = (row.information_error, row.response_error)
        counts[key] = counts.get(key, 0) + 1
    if not counts:
        raise ValueError("no complete paired observations are available")
    source = {
        "study": q.model_dump(mode="json"),
        "context_digest": digest(c),
        "excluded_trial_ids": missing,
        "unknown_outcomes_imputed": False,
        "causal_information_effect_estimated": False,
        "field_population_transfer_qualified": False,
    }
    return JointStateCountTable(
        event_ids=(q.information_event_id, q.response_event_id),
        rows=tuple(
            JointStateCount(states={q.information_event_id: key[0], q.response_event_id: key[1]}, count=count)
            for key, count in sorted(counts.items())
        ),
        condition_id=q.condition_id,
        opportunity_id=q.opportunity_id,
        opportunity_definition=q.opportunity_definition,
        population_id=q.population_id,
        grouping_unit=q.grouping_unit,
        evidence_id=q.evidence_id,
        source_payload=source,
        source_digest=digest(source),
    )


InformationResponseOutcomePattern = Literal[
    "complete_00",
    "complete_01",
    "complete_10",
    "complete_11",
    "missing_information_only",
    "missing_response_only",
    "missing_both",
]


class InformationResponsePatternCount(Contract):
    pattern: InformationResponseOutcomePattern
    count: int = Field(ge=0)


class InformationResponseClusterMissingness(Contract):
    cluster_id: Name
    trial_count: int = Field(ge=1)
    complete_trial_count: int = Field(ge=0)
    incomplete_trial_count: int = Field(ge=0)
    pattern_counts: tuple[InformationResponsePatternCount, ...] = Field(min_length=7, max_length=7)

    @model_validator(mode="after")
    def totals_reconcile(self):
        if self.complete_trial_count + self.incomplete_trial_count != self.trial_count:
            raise ValueError("cluster complete and incomplete counts must reconcile")
        if sum(item.count for item in self.pattern_counts) != self.trial_count:
            raise ValueError("cluster outcome-pattern counts must reconcile to its trial count")
        return self


class InformationResponseMissingnessAudit(Contract):
    """Descriptive paired-outcome completeness report; no missingness mechanism is fitted."""

    schema_version: Literal["pipeline_integrity.information_response_missingness_audit.v1"] = (
        "pipeline_integrity.information_response_missingness_audit.v1"
    )
    source_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    context_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    population_id: Name
    grouping_unit: Name
    missing_policy: Literal["require_complete", "reviewed_complete_cases"]
    trial_count: int = Field(ge=1)
    complete_trial_count: int = Field(ge=0)
    incomplete_trial_count: int = Field(ge=0)
    pattern_counts: tuple[InformationResponsePatternCount, ...] = Field(min_length=7, max_length=7)
    cluster_summaries: tuple[InformationResponseClusterMissingness, ...] = ()
    cluster_summaries_available: bool
    unknown_outcomes_imputed: Literal[False] = False
    missingness_mechanism_inferred: Literal[False] = False
    selection_bias_corrected: Literal[False] = False
    independent_sampling_claim: Literal[False] = False
    implementation_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    result_digest: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def totals_reconcile(self):
        if self.complete_trial_count + self.incomplete_trial_count != self.trial_count:
            raise ValueError("complete and incomplete counts must reconcile to the study size")
        if sum(item.count for item in self.pattern_counts) != self.trial_count:
            raise ValueError("outcome-pattern counts must reconcile to the study size")
        if self.cluster_summaries_available != bool(self.cluster_summaries):
            raise ValueError("cluster-summary availability must match the supplied summaries")
        return self

    def check_integrity(self):
        if digest(self.model_dump(mode="json", exclude={"result_digest"})) != self.result_digest:
            raise ValueError("information/response receipt changed")
        checked = InformationResponseMissingnessAudit.model_validate(self.model_dump(mode="python"))
        if digest(checked.model_dump(mode="json", exclude={"result_digest"})) != self.result_digest:
            raise ValueError("information/response missingness audit receipt changed")
        return True


_INFORMATION_RESPONSE_PATTERNS = (
    "complete_00",
    "complete_01",
    "complete_10",
    "complete_11",
    "missing_information_only",
    "missing_response_only",
    "missing_both",
)


def _information_response_pattern(trial: InformationResponseTrial) -> str:
    if trial.information_error is None and trial.response_error is None:
        return "missing_both"
    if trial.information_error is None:
        return "missing_information_only"
    if trial.response_error is None:
        return "missing_response_only"
    return f"complete_{int(trial.information_error)}{int(trial.response_error)}"


def information_response_missingness_audit(
    study: InformationResponseStudy, context: RunContext
) -> InformationResponseMissingnessAudit:
    """Summarize unknown paired outcomes overall and by declared non-trial cluster.

    The report describes which fields are unknown. It does not diagnose MCAR/MAR/MNAR,
    adjust probabilities, or claim the declared clusters are independent.
    """
    q = InformationResponseStudy.model_validate(study.model_dump(mode="python"))
    c = RunContext.model_validate(context.model_dump(mode="python"))
    available = [item.id for item in c.evidence]
    required = {q.evidence_id, q.missingness_review_evidence_id, *(row.evidence_id for row in q.trials)}
    if q.sampling_unit_evidence_id is not None:
        required.add(q.sampling_unit_evidence_id)
    if len(available) != len(set(available)) or not required <= set(available):
        raise ValueError("paired-outcome and missingness evidence must be present and unambiguous")

    totals = {pattern: 0 for pattern in _INFORMATION_RESPONSE_PATTERNS}
    grouped: dict[str, dict[str, int]] = {}
    for trial in q.trials:
        pattern = _information_response_pattern(trial)
        totals[pattern] += 1
        if q.grouping_unit != "trial":
            if trial.sampling_cluster_id is None:
                raise ValueError("declared cluster is missing for a non-trial study")
            counts = grouped.setdefault(
                trial.sampling_cluster_id,
                {key: 0 for key in _INFORMATION_RESPONSE_PATTERNS},
            )
            counts[pattern] += 1

    pattern_counts = tuple(
        InformationResponsePatternCount(pattern=key, count=totals[key])
        for key in _INFORMATION_RESPONSE_PATTERNS
    )
    cluster_summaries = tuple(
        InformationResponseClusterMissingness(
            cluster_id=cluster_id,
            trial_count=sum(counts.values()),
            complete_trial_count=sum(counts[key] for key in _INFORMATION_RESPONSE_PATTERNS[:4]),
            incomplete_trial_count=sum(counts[key] for key in _INFORMATION_RESPONSE_PATTERNS[4:]),
            pattern_counts=tuple(
                InformationResponsePatternCount(pattern=key, count=counts[key])
                for key in _INFORMATION_RESPONSE_PATTERNS
            ),
        )
        for cluster_id, counts in sorted(grouped.items())
    )
    body = {
        "schema_version": "pipeline_integrity.information_response_missingness_audit.v1",
        "source_digest": digest(q),
        "context_digest": digest(c),
        "population_id": q.population_id,
        "grouping_unit": q.grouping_unit,
        "missing_policy": q.missing_policy,
        "trial_count": len(q.trials),
        "complete_trial_count": sum(totals[key] for key in _INFORMATION_RESPONSE_PATTERNS[:4]),
        "incomplete_trial_count": sum(totals[key] for key in _INFORMATION_RESPONSE_PATTERNS[4:]),
        "pattern_counts": pattern_counts,
        "cluster_summaries": cluster_summaries,
        "cluster_summaries_available": bool(cluster_summaries),
        "unknown_outcomes_imputed": False,
        "missingness_mechanism_inferred": False,
        "selection_bias_corrected": False,
        "independent_sampling_claim": False,
        "implementation_sha256": file_digest(Path(__file__)),
    }
    result = InformationResponseMissingnessAudit(**body, result_digest=digest(body))
    result.check_integrity()
    return result


def verify_information_response_missingness_audit(
    result: InformationResponseMissingnessAudit,
    study: InformationResponseStudy,
    context: RunContext,
) -> bool:
    """Recompute a descriptive completeness report from its study/context inputs."""
    checked = InformationResponseMissingnessAudit.model_validate(result.model_dump(mode="python"))
    checked.check_integrity()
    if information_response_missingness_audit(study, context) != checked:
        raise ValueError("information/response missingness audit differs on replay")
    return True


class InformationResponseStateIdentificationBound(Contract):
    information_error: bool
    response_error: bool
    observed_count: int = Field(ge=0)
    compatible_incomplete_count: int = Field(ge=0)
    lower_count: int = Field(ge=0)
    upper_count: int = Field(ge=0)
    lower_probability: Fraction
    upper_probability: Fraction

    @model_validator(mode="after")
    def bounds_reconcile(self):
        if self.lower_count != self.observed_count:
            raise ValueError("identification lower count must equal its observed count")
        if self.upper_count != self.observed_count + self.compatible_incomplete_count:
            raise ValueError("identification upper count must include compatible incomplete records")
        if self.lower_probability > self.upper_probability:
            raise ValueError("identification probability bounds must be ordered")
        return self


class InformationResponseMissingOutcomeBounds(Contract):
    """Finite-record bounds under every completion compatible with observed outcomes."""

    schema_version: Literal["pipeline_integrity.information_response_missing_outcome_bounds.v1"] = (
        "pipeline_integrity.information_response_missing_outcome_bounds.v1"
    )
    source_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    context_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    population_id: Name
    grouping_unit: Name
    trial_count: int = Field(ge=1)
    complete_trial_count: int = Field(ge=0)
    incomplete_trial_count: int = Field(ge=0)
    state_bounds: tuple[InformationResponseStateIdentificationBound, ...] = Field(min_length=4, max_length=4)
    interval_scope: Literal["four_separate_finite_record_identification_bounds"] = (
        "four_separate_finite_record_identification_bounds"
    )
    unknown_outcomes_imputed: Literal[False] = False
    missingness_mechanism_assumed: Literal[False] = False
    selection_bias_corrected: Literal[False] = False
    sampling_uncertainty_estimated: Literal[False] = False
    population_transfer_qualified: Literal[False] = False
    implementation_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    result_digest: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def totals_reconcile(self):
        if self.complete_trial_count + self.incomplete_trial_count != self.trial_count:
            raise ValueError("complete and incomplete records must reconcile to the study size")
        if sum(item.observed_count for item in self.state_bounds) != self.complete_trial_count:
            raise ValueError("observed joint-state counts must reconcile to complete records")
        if any(item.upper_count > self.trial_count for item in self.state_bounds):
            raise ValueError("joint-state upper count cannot exceed the finite record count")
        if {(item.information_error, item.response_error) for item in self.state_bounds} != {
            (False, False), (False, True), (True, False), (True, True)
        }:
            raise ValueError("joint-state bounds must cover each paired Boolean state exactly once")
        if any(
            item.lower_probability != item.lower_count / self.trial_count
            or item.upper_probability != item.upper_count / self.trial_count
            for item in self.state_bounds
        ):
            raise ValueError("joint-state probabilities must match their finite-record counts")
        return self

    def check_integrity(self):
        if digest(self.model_dump(mode="json", exclude={"result_digest"})) != self.result_digest:
            raise ValueError("information/response receipt changed")
        checked = InformationResponseMissingOutcomeBounds.model_validate(self.model_dump(mode="python"))
        body = checked.model_dump(mode="json", exclude={"result_digest"})
        if digest(body) != self.result_digest:
            raise ValueError("paired information/response missing-outcome bounds receipt changed")
        return True


def information_response_missing_outcome_bounds(
    study: InformationResponseStudy, context: RunContext
) -> InformationResponseMissingOutcomeBounds:
    """Bound each joint-state share over all completions compatible with observed fields.

    These are deterministic identification bounds for the supplied finite records. They
    neither impute unknown values nor estimate sampling uncertainty or a field population.
    The four state-wise intervals are separate; their endpoints are not a joint confidence
    region and need not be simultaneously attainable.
    """
    q = InformationResponseStudy.model_validate(study.model_dump(mode="python"))
    c = RunContext.model_validate(context.model_dump(mode="python"))
    # Reuse the existing evidence, identity, and missingness review gate without selecting
    # complete cases or altering the caller's missingness policy.
    audit = information_response_missingness_audit(q, c)
    states = ((False, False), (False, True), (True, False), (True, True))
    observed = {state: 0 for state in states}
    compatible = {state: 0 for state in states}
    complete_count = 0
    for trial in q.trials:
        information_values = (False, True) if trial.information_error is None else (trial.information_error,)
        response_values = (False, True) if trial.response_error is None else (trial.response_error,)
        possible = {(information, response) for information in information_values for response in response_values}
        if trial.information_error is not None and trial.response_error is not None:
            state = (trial.information_error, trial.response_error)
            observed[state] += 1
            complete_count += 1
        else:
            for state in possible:
                compatible[state] += 1
    bounds = tuple(
        InformationResponseStateIdentificationBound(
            information_error=state[0],
            response_error=state[1],
            observed_count=observed[state],
            compatible_incomplete_count=compatible[state],
            lower_count=observed[state],
            upper_count=observed[state] + compatible[state],
            lower_probability=observed[state] / len(q.trials),
            upper_probability=(observed[state] + compatible[state]) / len(q.trials),
        )
        for state in states
    )
    body = {
        "schema_version": "pipeline_integrity.information_response_missing_outcome_bounds.v1",
        "source_digest": digest(q),
        "context_digest": digest(c),
        "population_id": q.population_id,
        "grouping_unit": q.grouping_unit,
        "trial_count": len(q.trials),
        "complete_trial_count": complete_count,
        "incomplete_trial_count": len(q.trials) - complete_count,
        "state_bounds": bounds,
        "interval_scope": "four_separate_finite_record_identification_bounds",
        "unknown_outcomes_imputed": False,
        "missingness_mechanism_assumed": False,
        "selection_bias_corrected": False,
        "sampling_uncertainty_estimated": False,
        "population_transfer_qualified": False,
        "implementation_sha256": file_digest(Path(__file__)),
    }
    # Touch the audit result so the validation path remains explicit and deterministic.
    if audit.trial_count != len(q.trials):
        raise ValueError("missingness audit does not match the supplied finite record count")
    result = InformationResponseMissingOutcomeBounds(**body, result_digest=digest(body))
    result.check_integrity()
    return result


def verify_information_response_missing_outcome_bounds(
    result: InformationResponseMissingOutcomeBounds,
    study: InformationResponseStudy,
    context: RunContext,
) -> bool:
    """Replay finite-record bounds from the original paired observations and context."""
    checked = InformationResponseMissingOutcomeBounds.model_validate(result.model_dump(mode="python"))
    checked.check_integrity()
    if information_response_missing_outcome_bounds(study, context) != checked:
        raise ValueError("paired information/response missing-outcome bounds differ on replay")
    return True


class InformationResponseFaultTreeTruth(Contract):
    information_error: bool
    response_error: bool
    top_event: bool


class InformationResponseFaultTreeMissingOutcomeRequest(Contract):
    maximum_tree_nodes: int = Field(default=1000, ge=1, le=100000)
    maximum_fault_tree_work: int = Field(default=20000000, ge=4, le=50000000)


class InformationResponseFaultTreeMissingOutcomeBounds(Contract):
    schema_version: Literal["pipeline_integrity.information_response_fta_missing_outcome_bounds.v1"] = (
        "pipeline_integrity.information_response_fta_missing_outcome_bounds.v1"
    )
    source_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    context_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    request: InformationResponseFaultTreeMissingOutcomeRequest
    top_event_id: Literal["fault_tree_top"] = "fault_tree_top"
    trial_count: int = Field(ge=1)
    complete_trial_count: int = Field(ge=0)
    incomplete_trial_count: int = Field(ge=0)
    observed_complete_top_event_count: int = Field(ge=0)
    lower_top_event_count: int = Field(ge=0)
    upper_top_event_count: int = Field(ge=0)
    lower_probability: Fraction
    upper_probability: Fraction
    truth_table: tuple[InformationResponseFaultTreeTruth, ...] = Field(min_length=4, max_length=4)
    interval_scope: Literal["finite_record_top_event_identification_bounds"] = (
        "finite_record_top_event_identification_bounds"
    )
    missingness_mechanism_assumed: Literal[False] = False
    unknown_outcomes_imputed: Literal[False] = False
    selection_bias_corrected: Literal[False] = False
    sampling_uncertainty_estimated: Literal[False] = False
    field_population_transfer_qualified: Literal[False] = False
    risk_provider: dict
    risk_provider_identity_kind: Literal["source_pinned", "caller_declared"]
    implementation_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    result_digest: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def counts_and_probabilities_reconcile(self):
        if self.complete_trial_count + self.incomplete_trial_count != self.trial_count:
            raise ValueError("complete and incomplete FTA records must reconcile to the study size")
        if self.observed_complete_top_event_count > self.complete_trial_count:
            raise ValueError("observed top-event count cannot exceed complete outcomes")
        if not self.observed_complete_top_event_count <= self.lower_top_event_count:
            raise ValueError("top-event lower count cannot omit observed complete outcomes")
        if not self.lower_top_event_count <= self.upper_top_event_count <= self.trial_count:
            raise ValueError("top-event identification counts must be ordered within the record size")
        if self.lower_top_event_count > self.observed_complete_top_event_count + self.incomplete_trial_count:
            raise ValueError("top-event lower count exceeds the observed plus incomplete records")
        if self.upper_top_event_count > self.observed_complete_top_event_count + self.incomplete_trial_count:
            raise ValueError("top-event upper count exceeds the observed plus incomplete records")
        if self.upper_top_event_count < self.observed_complete_top_event_count:
            raise ValueError("top-event upper count cannot omit observed complete outcomes")
        if self.lower_probability != self.lower_top_event_count / self.trial_count:
            raise ValueError("top-event lower probability must match its finite-record count")
        if self.upper_probability != self.upper_top_event_count / self.trial_count:
            raise ValueError("top-event upper probability must match its finite-record count")
        states = {(row.information_error, row.response_error) for row in self.truth_table}
        if states != {(False, False), (False, True), (True, False), (True, True)}:
            raise ValueError("FTA truth table must cover each paired Boolean state exactly once")
        provider_source_digest = self.risk_provider.get("source_digest")
        if (provider_source_digest is not None) != (self.risk_provider_identity_kind == "source_pinned"):
            raise ValueError("FTA provider identity kind must match its source digest availability")
        if provider_source_digest is not None and (
            not isinstance(provider_source_digest, str)
            or len(provider_source_digest) != 64
            or any(char not in "0123456789abcdef" for char in provider_source_digest)
        ):
            raise ValueError("FTA provider source digest must be a lowercase SHA-256")
        return self

    def check_integrity(self):
        if digest(self.model_dump(mode="json", exclude={"result_digest"})) != self.result_digest:
            raise ValueError("information/response receipt changed")
        checked = InformationResponseFaultTreeMissingOutcomeBounds.model_validate(
            self.model_dump(mode="python")
        )
        if digest(checked.model_dump(mode="json", exclude={"result_digest"})) != self.result_digest:
            raise ValueError("paired information/response FTA missing-outcome receipt changed")
        return True


def information_response_fault_tree_missing_outcome_bounds(
    study: InformationResponseStudy,
    context: RunContext,
    root: FaultNode,
    events: tuple[RiskProbability, ...],
    risk_client: HRARiskClient,
    request: InformationResponseFaultTreeMissingOutcomeRequest,
) -> InformationResponseFaultTreeMissingOutcomeBounds:
    """Bound a declared FTA top-event share over compatible missing paired outcomes.

    The supplied owner FTA evaluates the four deterministic paired event states. Each
    incomplete record is then allowed to take any state compatible with its observed
    fields. This gives sharp separate finite-record endpoints without imputing outcomes.
    """
    from .risk_exchange import JointStateCount, JointStateCountTable

    q = InformationResponseStudy.model_validate(study.model_dump(mode="python"))
    c = RunContext.model_validate(context.model_dump(mode="python"))
    tree = FaultNode.model_validate(root.model_dump(mode="python"))
    opts = InformationResponseFaultTreeMissingOutcomeRequest.model_validate(request.model_dump(mode="python"))
    checked_events = tuple(RiskProbability.model_validate(event.model_dump(mode="python")) for event in events)
    if not isinstance(risk_client, HRARiskClient):
        raise TypeError("an existing HRARiskClient owner adapter is required")
    if len(checked_events) != 2 or {event.id for event in checked_events} != {
        q.information_event_id,
        q.response_event_id,
    }:
        raise ValueError("FTA events must match the paired information and response event IDs")
    if any(event.context != c for event in checked_events):
        raise ValueError("FTA event contexts must match the paired study context")
    if any((event.condition_id, event.opportunity_id, event.opportunity_definition) != (
        q.condition_id,
        q.opportunity_id,
        q.opportunity_definition,
    ) for event in checked_events):
        raise ValueError("FTA event condition and opportunity scope must match the paired study")
    audit = information_response_missingness_audit(q, c)
    if 4 * opts.maximum_tree_nodes > opts.maximum_fault_tree_work:
        raise ValueError("FTA missing-outcome evaluation exceeds its explicit tree work budget")

    provider_snapshot = json_value(risk_client.provider)
    provider_source_digest = provider_snapshot.get("source_digest")
    if provider_source_digest is not None and (
        not isinstance(provider_source_digest, str)
        or len(provider_source_digest) != 64
        or any(char not in "0123456789abcdef" for char in provider_source_digest)
    ):
        raise ValueError("owner risk provider source digest must be a lowercase SHA-256")
    provider_identity_kind = "source_pinned" if provider_source_digest is not None else "caller_declared"
    state_keys = ((False, False), (False, True), (True, False), (True, True))
    truth = {}
    for state_index, state in enumerate(state_keys):
        table_source = {
            "study_digest": digest(q),
            "context_digest": digest(c),
            "state": state,
            "evaluation": "deterministic paired state through supplied owner FTA",
        }
        table = JointStateCountTable(
            event_ids=(q.information_event_id, q.response_event_id),
            rows=(JointStateCount(
                states={q.information_event_id: state[0], q.response_event_id: state[1]}, count=1
            ),),
            condition_id=q.condition_id,
            opportunity_id=q.opportunity_id,
            opportunity_definition=q.opportunity_definition,
            population_id=q.population_id,
            grouping_unit=q.grouping_unit,
            evidence_id=q.evidence_id,
            source_payload=table_source,
            source_digest=digest(table_source),
        )
        state_events = []
        for event in checked_events:
            event_value = state[0 if event.id == q.information_event_id else 1]
            event_source = {
                "state": state,
                "probability": float(event_value),
                "source_event_digest": event.source_digest,
                "paired_study_digest": digest(q),
                "deterministic_truth_table_row": state_index,
            }
            fields = event.model_dump(mode="python", exclude={"binding_digest"})
            fields.update(
                probability=float(event_value),
                source_kind="derived",
                source_payload=event_source,
                source_digest=digest(event_source),
            )
            state_events.append(RiskProbability.model_validate(fields))
        fta_request, _ = fault_tree_request_from_joint_counts(
            tree,
            tuple(state_events),
            table,
            maximum_events=2,
            maximum_states=4,
            maximum_tree_nodes=opts.maximum_tree_nodes,
        )
        if json_value(risk_client.provider) != provider_snapshot:
            raise ValueError("the owner risk provider identity changed during truth-table evaluation")
        result = risk_client.fault_tree(fta_request, c)
        if json_value(risk_client.provider) != provider_snapshot:
            raise ValueError("the owner risk provider identity changed during truth-table evaluation")
        if result.get("status") != "exchange_checked" or result.get("probability") is None:
            raise ValueError("the existing FTA owner withheld a deterministic state evaluation")
        probability = float(result["probability"]["probability"])
        if not (math.isclose(probability, 0.0, abs_tol=1e-12, rel_tol=0.0)
                or math.isclose(probability, 1.0, abs_tol=1e-12, rel_tol=0.0)):
            raise ValueError("owner FTA did not return a deterministic Boolean top-event truth value")
        truth[state] = probability >= 0.5

    known_top = lower_count = upper_count = complete = 0
    for trial in q.trials:
        information_values = (False, True) if trial.information_error is None else (trial.information_error,)
        response_values = (False, True) if trial.response_error is None else (trial.response_error,)
        compatible = [truth[(information, response)]
                      for information in information_values for response in response_values]
        if trial.information_error is not None and trial.response_error is not None:
            complete += 1
            known_top += int(compatible[0])
            lower_count += int(compatible[0])
            upper_count += int(compatible[0])
        else:
            lower_count += int(all(compatible))
            upper_count += int(any(compatible))
    body = {
        "schema_version": "pipeline_integrity.information_response_fta_missing_outcome_bounds.v1",
        "top_event_id": "fault_tree_top",
        "source_digest": digest({
            "study": q,
            "root": tree,
            "events": checked_events,
            "risk_provider": provider_snapshot,
            "truth_table": [
                {"information_error": state[0], "response_error": state[1], "top_event": truth[state]}
                for state in state_keys
            ],
        }),
        "context_digest": digest(c),
        "request": opts,
        "trial_count": len(q.trials),
        "complete_trial_count": complete,
        "incomplete_trial_count": len(q.trials) - complete,
        "observed_complete_top_event_count": known_top,
        "lower_top_event_count": lower_count,
        "upper_top_event_count": upper_count,
        "lower_probability": lower_count / len(q.trials),
        "upper_probability": upper_count / len(q.trials),
        "truth_table": tuple(
            InformationResponseFaultTreeTruth(
                information_error=state[0], response_error=state[1], top_event=truth[state]
            ) for state in state_keys
        ),
        "interval_scope": "finite_record_top_event_identification_bounds",
        "missingness_mechanism_assumed": False,
        "unknown_outcomes_imputed": False,
        "selection_bias_corrected": False,
        "sampling_uncertainty_estimated": False,
        "field_population_transfer_qualified": False,
        "risk_provider": provider_snapshot,
        "risk_provider_identity_kind": provider_identity_kind,
        "implementation_sha256": file_digest(Path(__file__)),
    }
    if audit.trial_count != len(q.trials):
        raise ValueError("missingness audit does not match the paired study")
    record = InformationResponseFaultTreeMissingOutcomeBounds(**body, result_digest=digest(body))
    record.check_integrity()
    return record


def verify_information_response_fault_tree_missing_outcome_bounds(
    result: InformationResponseFaultTreeMissingOutcomeBounds,
    study: InformationResponseStudy,
    context: RunContext,
    root: FaultNode,
    events: tuple[RiskProbability, ...],
    risk_client: HRARiskClient,
) -> bool:
    """Replay the owner FTA truth table and finite-record missing-outcome bounds."""
    checked = InformationResponseFaultTreeMissingOutcomeBounds.model_validate(
        result.model_dump(mode="python")
    )
    checked.check_integrity()
    replay = information_response_fault_tree_missing_outcome_bounds(
        study, context, root, events, risk_client, checked.request
    )
    if replay != checked:
        raise ValueError("paired information/response FTA missing-outcome bounds differ on replay")
    return True


class InformationResponseBootstrapRequest(Contract):
    """Bounded paired-cluster bootstrap options; no causal or population transfer claim."""

    replicates: int = Field(default=4000, ge=100, le=20000)
    seed: int = Field(ge=0, le=4294967295)
    confidence_level: float = Field(default=0.95, gt=0.0, lt=1.0)
    maximum_resampling_work: int = Field(default=20000000, ge=1, le=50000000)


class InformationResponseFaultTreeBootstrapRequest(InformationResponseBootstrapRequest):
    """Budgeted paired-cluster uncertainty propagation through the owner FTA API."""

    maximum_tree_nodes: int = Field(default=1000, ge=1, le=100000)
    maximum_fault_tree_work: int = Field(default=20000000, ge=1, le=50000000)


class InformationResponseFaultTreeBootstrapResult(Contract):
    schema_version: Literal["pipeline_integrity.information_response_fta_bootstrap.v1"] = (
        "pipeline_integrity.information_response_fta_bootstrap.v1"
    )
    source_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    context_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    request: InformationResponseFaultTreeBootstrapRequest
    top_event_id: Literal["fault_tree_top"] = "fault_tree_top"
    estimand: Literal["trial_weighted_complete_case_top_event_probability"] = (
        "trial_weighted_complete_case_top_event_probability"
    )
    grouping_unit: Name
    missing_policy: Literal["require_complete", "reviewed_complete_cases"]
    point_estimate: Fraction
    lower_bound: Fraction
    upper_bound: Fraction
    confidence_level: Fraction
    interval_method: Literal["cluster_percentile"] = "cluster_percentile"
    interval_scope: Literal["single_top_event_pointwise"] = "single_top_event_pointwise"
    cluster_count: int = Field(ge=2)
    complete_trial_count: int = Field(ge=1)
    excluded_trial_ids: tuple[Name, ...]
    risk_provider: dict
    risk_provider_identity_kind: Literal["source_pinned", "caller_declared"]
    random_generator: Literal["numpy.PCG64"] = "numpy.PCG64"
    numpy_version: Name
    implementation_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    result_digest: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def ordered_interval(self):
        if self.lower_bound > self.upper_bound:
            raise ValueError("FTA bootstrap interval bounds must be ordered")
        if self.confidence_level != self.request.confidence_level:
            raise ValueError("FTA bootstrap confidence level must match the request")
        return self

    def check_integrity(self):
        checked = InformationResponseFaultTreeBootstrapResult.model_validate(self.model_dump(mode="python"))
        body = checked.model_dump(mode="json", exclude={"result_digest"})
        if digest(body) != self.result_digest:
            raise ValueError("paired information/response FTA bootstrap receipt changed")


class InformationResponseStateInterval(Contract):
    information_error: bool
    response_error: bool
    observed_count: int = Field(ge=0)
    observed_probability: Fraction
    lower_bound: Fraction
    upper_bound: Fraction
    degenerate_interval: bool

    @model_validator(mode="after")
    def ordered_interval(self):
        if self.lower_bound > self.upper_bound:
            raise ValueError("bootstrap interval bounds must be ordered")
        return self


class InformationResponseBootstrapResult(Contract):
    schema_version: Literal["pipeline_integrity.information_response_cluster_bootstrap.v1"] = (
        "pipeline_integrity.information_response_cluster_bootstrap.v1"
    )
    source_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    context_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    request: InformationResponseBootstrapRequest
    grouping_unit: Name
    cluster_count: int = Field(ge=2)
    complete_trial_count: int = Field(ge=1)
    estimand: Literal["trial_weighted_complete_case_joint_state_probability"] = (
        "trial_weighted_complete_case_joint_state_probability"
    )
    excluded_trial_ids: tuple[Name, ...]
    state_intervals: tuple[InformationResponseStateInterval, ...] = Field(min_length=4, max_length=4)
    interval_method: Literal["cluster_percentile"] = "cluster_percentile"
    interval_scope: Literal["four_separate_pointwise_intervals"] = "four_separate_pointwise_intervals"
    random_generator: Literal["numpy.PCG64"] = "numpy.PCG64"
    numpy_version: Name
    implementation_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    result_digest: str = Field(pattern=r"^[a-f0-9]{64}$")

    def check_integrity(self):
        checked = InformationResponseBootstrapResult.model_validate(self.model_dump(mode="python"))
        body = checked.model_dump(mode="json", exclude={"result_digest"})
        if digest(body) != self.result_digest:
            raise ValueError("paired information/response bootstrap receipt changed")


def information_response_cluster_bootstrap(
    study: InformationResponseStudy,
    context: RunContext,
    request: InformationResponseBootstrapRequest,
) -> InformationResponseBootstrapResult:
    """Estimate paired-state sampling intervals by resampling declared independent clusters."""
    import numpy as np

    q = InformationResponseStudy.model_validate(study.model_dump(mode="python"))
    c = RunContext.model_validate(context.model_dump(mode="python"))
    opts = InformationResponseBootstrapRequest.model_validate(request.model_dump(mode="python"))
    table = information_response_joint_counts(q, c)
    state_keys = ((False, False), (False, True), (True, False), (True, True))
    state_index = {key: index for index, key in enumerate(state_keys)}
    clusters: dict[str, list[int]] = {}
    excluded = []
    for trial in q.trials:
        if trial.information_error is None or trial.response_error is None:
            excluded.append(trial.id)
            continue
        cluster_id = trial.sampling_cluster_id or trial.id
        vector = clusters.setdefault(cluster_id, [0, 0, 0, 0])
        vector[state_index[(trial.information_error, trial.response_error)]] += 1
    cluster_ids = sorted(clusters)
    if len(cluster_ids) < 2:
        raise ValueError("at least two independent complete clusters are required for sampling intervals")
    if opts.replicates * len(cluster_ids) > opts.maximum_resampling_work:
        raise ValueError("paired-cluster bootstrap exceeds its explicit resampling work budget")
    cluster_counts = np.asarray([clusters[key] for key in cluster_ids], dtype=np.int64)
    observed = cluster_counts.sum(axis=0)
    complete_count = int(observed.sum())
    if complete_count < 1:
        raise ValueError("no complete paired trials are available for resampling")
    generator = np.random.Generator(np.random.PCG64(opts.seed))
    bootstrap = np.empty((opts.replicates, len(state_keys)), dtype=np.float64)
    for index in range(opts.replicates):
        selected = generator.integers(0, len(cluster_ids), size=len(cluster_ids))
        counts = cluster_counts[selected].sum(axis=0)
        bootstrap[index] = counts / counts.sum()
    tail = (1.0 - opts.confidence_level) / 2.0
    bounds = np.quantile(bootstrap, (tail, 1.0 - tail), axis=0, method="linear")
    intervals = tuple(
        InformationResponseStateInterval(
            information_error=state[0],
            response_error=state[1],
            observed_count=int(observed[i]),
            observed_probability=float(observed[i] / complete_count),
            lower_bound=float(bounds[0, i]),
            upper_bound=float(bounds[1, i]),
            degenerate_interval=bool(observed[i] == 0),
        )
        for i, state in enumerate(state_keys)
    )
    source = {"study": q, "joint_count_table_digest": table.source_digest}
    body = {
        "schema_version": "pipeline_integrity.information_response_cluster_bootstrap.v1",
        "source_digest": digest(source),
        "context_digest": digest(c),
        "request": opts,
        "grouping_unit": q.grouping_unit,
        "estimand": "trial_weighted_complete_case_joint_state_probability",
        "interval_scope": "four_separate_pointwise_intervals",
        "cluster_count": len(cluster_ids),
        "complete_trial_count": complete_count,
        "excluded_trial_ids": tuple(excluded),
        "state_intervals": intervals,
        "interval_method": "cluster_percentile",
        "random_generator": "numpy.PCG64",
        "numpy_version": version("numpy"),
        "implementation_sha256": file_digest(Path(__file__)),
    }
    result = InformationResponseBootstrapResult(**body, result_digest=digest(body))
    result.check_integrity()
    return result


def verify_information_response_cluster_bootstrap(
    result: InformationResponseBootstrapResult,
    study: InformationResponseStudy,
    context: RunContext,
) -> bool:
    """Replay a paired-cluster bootstrap receipt against current inputs and RNG implementation."""
    checked = InformationResponseBootstrapResult.model_validate(result.model_dump(mode="python"))
    checked.check_integrity()
    replay = information_response_cluster_bootstrap(study, context, checked.request)
    if replay != checked:
        raise ValueError("paired-cluster bootstrap replay differs from its sealed receipt")
    return True


def information_response_fault_tree_cluster_bootstrap(
    study: InformationResponseStudy,
    context: RunContext,
    root: FaultNode,
    events: tuple[RiskProbability, ...],
    risk_client: HRARiskClient,
    request: InformationResponseFaultTreeBootstrapRequest,
) -> InformationResponseFaultTreeBootstrapResult:
    """Propagate paired-cluster uncertainty through the existing joint-state FTA owner.

    Every replicate resamples complete independent clusters, normalizes its paired
    event states, and asks the supplied risk client to evaluate the declared tree.
    The method does not combine marginal intervals or invent an independent-event
    approximation. The caller's owner API remains responsible for FTA semantics.
    """
    import numpy as np

    from .risk_exchange import JointStateCountTable

    q = InformationResponseStudy.model_validate(study.model_dump(mode="python"))
    c = RunContext.model_validate(context.model_dump(mode="python"))
    tree = FaultNode.model_validate(root.model_dump(mode="python"))
    opts = InformationResponseFaultTreeBootstrapRequest.model_validate(request.model_dump(mode="python"))
    checked_events = tuple(RiskProbability.model_validate(event.model_dump(mode="python")) for event in events)
    if not isinstance(risk_client, HRARiskClient):
        raise TypeError("an existing HRARiskClient owner adapter is required")
    if len(checked_events) != 2 or {event.id for event in checked_events} != {
        q.information_event_id,
        q.response_event_id,
    }:
        raise ValueError("FTA bootstrap events must match the paired information and response event IDs")
    if any(event.context != c for event in checked_events):
        raise ValueError("FTA event contexts must match the paired study context")
    if any((event.condition_id, event.opportunity_id, event.opportunity_definition) != (
        q.condition_id,
        q.opportunity_id,
        q.opportunity_definition,
    ) for event in checked_events):
        raise ValueError("FTA event condition and opportunity scope must match the paired study")

    information_response_joint_counts(q, c)
    state_keys = ((False, False), (False, True), (True, False), (True, True))
    state_index = {state: index for index, state in enumerate(state_keys)}
    clusters: dict[str, list[int]] = {}
    excluded = []
    for trial in q.trials:
        if trial.information_error is None or trial.response_error is None:
            excluded.append(trial.id)
            continue
        key = trial.sampling_cluster_id or trial.id
        vector = clusters.setdefault(key, [0, 0, 0, 0])
        vector[state_index[(trial.information_error, trial.response_error)]] += 1
    cluster_ids = sorted(clusters)
    if len(cluster_ids) < 2:
        raise ValueError("at least two independent complete clusters are required for sampling intervals")
    if opts.replicates * len(cluster_ids) > opts.maximum_resampling_work:
        raise ValueError("paired-cluster bootstrap exceeds its explicit resampling work budget")
    if (opts.replicates + 1) * opts.maximum_tree_nodes * 4 > opts.maximum_fault_tree_work:
        raise ValueError("FTA bootstrap exceeds its explicit tree evaluation work budget")

    counts_by_cluster = np.asarray([clusters[key] for key in cluster_ids], dtype=np.int64)
    observed = counts_by_cluster.sum(axis=0)
    complete_count = int(observed.sum())
    if complete_count == 0:
        raise ValueError("no complete paired trials are available for FTA resampling")

    provider_snapshot = json_value(risk_client.provider)
    provider_source_digest = provider_snapshot.get("source_digest")
    if provider_source_digest is not None and (
        not isinstance(provider_source_digest, str)
        or len(provider_source_digest) != 64
        or any(char not in "0123456789abcdef" for char in provider_source_digest)
    ):
        raise ValueError("owner risk provider source digest must be a lowercase SHA-256")
    provider_identity_kind = "source_pinned" if provider_source_digest is not None else "caller_declared"
    source = {
        "study": q,
        "context_digest": digest(c),
        "root": tree,
        "events": checked_events,
        "risk_provider": provider_snapshot,
    }
    source_digest = digest(source)

    def evaluate(counts, replicate_index, selected_ids):
        payload = {
            "source_digest": source_digest,
            "replicate_index": replicate_index,
            "selected_cluster_ids": selected_ids,
            "state_counts": [int(value) for value in counts],
        }
        table = JointStateCountTable(
            event_ids=(q.information_event_id, q.response_event_id),
            rows=tuple(
                JointStateCount(
                    states={q.information_event_id: state[0], q.response_event_id: state[1]},
                    count=int(counts[index]),
                )
                for index, state in enumerate(state_keys)
                if counts[index] > 0
            ),
            condition_id=q.condition_id,
            opportunity_id=q.opportunity_id,
            opportunity_definition=q.opportunity_definition,
            population_id=q.population_id,
            grouping_unit=q.grouping_unit,
            evidence_id=q.evidence_id,
            source_payload=payload,
            source_digest=digest(payload),
        )
        if json_value(risk_client.provider) != provider_snapshot:
            raise ValueError("the owner risk provider identity changed during FTA resampling")
        total = int(np.asarray(counts).sum())
        sample_events = []
        for event in checked_events:
            event_index = 0 if event.id == q.information_event_id else 1
            probability = float(
                sum(
                    int(counts[index])
                    for index, state in enumerate(state_keys)
                    if state[event_index]
                )
                / total
            )
            event_source = {
                "probability": probability,
                "derivation": "paired_cluster_bootstrap_joint_state_marginal",
                "source_event_digest": event.source_digest,
                "paired_source_digest": source_digest,
                "replicate_index": replicate_index,
                "state_counts": [int(value) for value in counts],
            }
            event_fields = event.model_dump(mode="python", exclude={"binding_digest"})
            event_fields.update(
                probability=probability,
                source_kind="derived",
                source_payload=event_source,
                source_digest=digest(event_source),
            )
            sample_events.append(RiskProbability.model_validate(event_fields))
        fta_request, _ = fault_tree_request_from_joint_counts(
            tree,
            tuple(sample_events),
            table,
            maximum_events=2,
            maximum_states=4,
            maximum_tree_nodes=opts.maximum_tree_nodes,
        )
        result = risk_client.fault_tree(fta_request, c)
        if not isinstance(result, dict):
            raise ValueError("the existing FTA owner returned a non-object receipt")
        owner_body = {key: value for key, value in result.items() if key != "result_digest"}
        if (
            result.get("schema") != "pipeline_integrity.risk_exchange.v1"
            or result.get("kind") != "fault_tree"
            or result.get("result_digest") != digest(owner_body)
            or result.get("input_digest") != digest(fta_request)
            or result.get("inputs") != fta_request.model_dump(mode="json")
            or result.get("context") != c.model_dump(mode="json")
            or result.get("provider") != provider_snapshot
            or result.get("native_result") is None
            or result.get("issues") != []
            or result.get("status") != "exchange_checked"
            or result.get("probability") is None
        ):
            raise ValueError("the existing FTA owner withheld a bootstrap replicate")
        probability = RiskProbability.model_validate_json(json.dumps(json_value(result["probability"])))
        if (
            probability.context != c
            or probability.source_kind != "derived"
            or probability.role != "derived"
            or probability.source_payload.get("kind") != "fault_tree"
            or probability.source_payload.get("request_digest") != digest(fta_request)
            or probability.source_payload.get("provider") != provider_snapshot
            or result["native_result"] != {"top_probability": probability.probability}
        ):
            raise ValueError("the FTA owner top-event result is not bound to the submitted request")
        return float(probability.probability)

    point = evaluate(observed, -1, tuple(cluster_ids))
    generator = np.random.Generator(np.random.PCG64(opts.seed))
    top_probabilities = np.empty(opts.replicates, dtype=np.float64)
    for index in range(opts.replicates):
        selected = generator.integers(0, len(cluster_ids), size=len(cluster_ids))
        counts = counts_by_cluster[selected].sum(axis=0)
        selected_ids = tuple(cluster_ids[int(value)] for value in selected)
        top_probabilities[index] = evaluate(counts, index, selected_ids)
    tail = (1.0 - opts.confidence_level) / 2.0
    lower, upper = np.quantile(top_probabilities, (tail, 1.0 - tail), method="linear")
    body = {
        "schema_version": "pipeline_integrity.information_response_fta_bootstrap.v1",
        "top_event_id": "fault_tree_top",
        "source_digest": source_digest,
        "context_digest": digest(c),
        "request": opts,
        "estimand": "trial_weighted_complete_case_top_event_probability",
        "grouping_unit": q.grouping_unit,
        "missing_policy": q.missing_policy,
        "point_estimate": point,
        "lower_bound": float(lower),
        "upper_bound": float(upper),
        "confidence_level": opts.confidence_level,
        "interval_method": "cluster_percentile",
        "interval_scope": "single_top_event_pointwise",
        "cluster_count": len(cluster_ids),
        "complete_trial_count": complete_count,
        "excluded_trial_ids": tuple(excluded),
        "risk_provider": provider_snapshot,
        "risk_provider_identity_kind": provider_identity_kind,
        "random_generator": "numpy.PCG64",
        "numpy_version": version("numpy"),
        "implementation_sha256": file_digest(Path(__file__)),
    }
    result = InformationResponseFaultTreeBootstrapResult(**body, result_digest=digest(body))
    result.check_integrity()
    return result


def verify_information_response_fault_tree_cluster_bootstrap(
    result: InformationResponseFaultTreeBootstrapResult,
    study: InformationResponseStudy,
    context: RunContext,
    root: FaultNode,
    events: tuple[RiskProbability, ...],
    risk_client: HRARiskClient,
) -> bool:
    """Replay an FTA uncertainty receipt against the supplied owner adapter and inputs."""
    checked = InformationResponseFaultTreeBootstrapResult.model_validate(result.model_dump(mode="python"))
    checked.check_integrity()
    replay = information_response_fault_tree_cluster_bootstrap(
        study, context, root, events, risk_client, checked.request
    )
    if replay != checked:
        raise ValueError("paired-cluster FTA bootstrap replay differs from its sealed receipt")
    return True


class ResponseWindow(Contract):
    clock_id: Name
    cue_time_s: Nonnegative
    limit_time_s: Nonnegative
    evidence_id: Name
    coordinate: Literal["elapsed_from_context_origin"] = "elapsed_from_context_origin"

    @model_validator(mode="after")
    def positive_window(self):
        if self.limit_time_s <= self.cue_time_s:
            raise ValueError("the response limit must follow the cue on the same clock")
        return self

    @property
    def available_s(self):
        return self.limit_time_s - self.cue_time_s


class LognormalResponse(Contract):
    kind: Literal["lognormal"] = "lognormal"
    median_s: Positive
    log_sigma: Positive
    evidence_id: Name


class WeibullResponse(Contract):
    kind: Literal["weibull"] = "weibull"
    scale_s: Positive
    shape: Positive
    evidence_id: Name


class ResponseObservation(Contract):
    id: Name
    duration_s: Nonnegative
    outcome: Literal["correct_completion", "right_censored", "wrong_response", "unknown"]
    evidence_id: Name


class EmpiricalResponse(Contract):
    kind: Literal["kaplan_meier"] = "kaplan_meier"
    observations: tuple[ResponseObservation, ...] = Field(min_length=1, max_length=100_000)
    censoring_review_evidence_id: Name

    @model_validator(mode="after")
    def distinct_trials(self):
        if len({v.id for v in self.observations}) != len(self.observations):
            raise ValueError("completion/censoring observations need distinct trial identities")
        return self


ResponseModel = Annotated[
    LognormalResponse | WeibullResponse | EmpiricalResponse, Field(discriminator="kind")
]


class DeadlineEffect(Contract):
    kind: Literal["deadline"] = "deadline"
    window: ResponseWindow
    time_model: ResponseModel
    population_definition: Name
    conditioning: Literal["given_no_initial_failure"] = "given_no_initial_failure"
    bin_boundaries_s: tuple[Positive, ...] = Field(default=(), max_length=1000)

    @model_validator(mode="after")
    def ordered_bins(self):
        if any(b <= a for a, b in zip(self.bin_boundaries_s, self.bin_boundaries_s[1:], strict=False)):
            raise ValueError("completion bin boundaries must strictly increase")
        return self


class DeadlineScenario(Contract):
    """One reviewed cue-to-limit duration with demand-population probability mass."""

    id: Name
    available_time_s: Positive
    probability_mass: Fraction
    evidence_id: Name
    time_model: ResponseModel | None = None
    response_population_definition: Name | None = None

    @model_validator(mode="after")
    def finite_positive_values(self):
        if not math.isfinite(self.available_time_s):
            raise ValueError("scenario available time must be finite")
        if not math.isfinite(self.probability_mass) or self.probability_mass <= 0:
            raise ValueError("scenario probability mass must be finite and positive")
        if (self.time_model is None) != (self.response_population_definition is None):
            raise ValueError("a scenario-specific response model requires its own population definition")
        return self


class DeadlineScenarioSet(Contract):
    scenarios: tuple[DeadlineScenario, ...] = Field(min_length=1, max_length=10000)
    distribution_evidence_id: Name
    available_time_population_definition: Name
    maximum_scenarios: int = Field(default=256, ge=1, le=10000)
    maximum_empirical_observations: int = Field(default=100000, ge=1, le=1000000)

    @model_validator(mode="after")
    def normalized_weights(self):
        if len({row.id for row in self.scenarios}) != len(self.scenarios):
            raise ValueError("deadline scenarios need distinct identities")
        if len(self.scenarios) > self.maximum_scenarios:
            raise ValueError("deadline scenario evaluation budget exceeded")
        observation_count = math.fsum(
            len(row.time_model.observations)
            for row in self.scenarios
            if isinstance(row.time_model, EmpiricalResponse)
        )
        if observation_count > self.maximum_empirical_observations:
            raise ValueError("deadline scenario empirical-observation budget exceeded")
        if not math.isclose(
            math.fsum(row.probability_mass for row in self.scenarios), 1.0, rel_tol=0, abs_tol=1e-12
        ):
            raise ValueError("deadline scenario probability masses must sum to one")
        return self


class RecoveryEffect(Contract):
    kind: Literal["recovery"] = "recovery"
    failure_to_recover_given_error: Fraction
    evidence_id: Name
    population_definition: Name
    conditioning: Literal["given_initial_error"] = "given_initial_error"
    time_scope: Literal["untimed"] = "untimed"


class HumanResponseRequest(Contract):
    condition_id: Name
    opportunity_id: Name
    protection_ids: tuple[Name, ...] = Field(min_length=1)
    applicability_evidence_id: Name
    applicability_rationale: Name
    effect: Annotated[DeadlineEffect | RecoveryEffect, Field(discriminator="kind")]

    @model_validator(mode="after")
    def unique_protection(self):
        if len(set(self.protection_ids)) != len(self.protection_ids):
            raise ValueError("credited protection identities must not repeat")
        return self


class CognitiveStage(Contract):
    id: Name
    conditional_failure_probability: Fraction
    evidence_id: Name


class CognitiveResponse(Contract):
    stages: tuple[CognitiveStage, ...] = Field(min_length=1, max_length=100)
    applicability_evidence_id: Name
    applicability_rationale: Name
    conditioning: Literal["given_all_earlier_stages_succeed"] = "given_all_earlier_stages_succeed"

    @model_validator(mode="after")
    def distinct_stages(self):
        if len({s.id for s in self.stages}) != len(self.stages):
            raise ValueError("cognitive stages must have distinct identities in the reviewed order")
        return self


class HumanResponseResult(Contract):
    schema_version: Literal["pipeline_integrity.human_response.v1"] = "pipeline_integrity.human_response.v1"
    status: Literal["exchange_checked", "needs_data"]
    context: RunContext
    provider: dict
    request: HumanResponseRequest
    initial_estimate: dict
    native_event: dict
    native_estimate: dict | None
    native_completion_bins: tuple[dict, ...]
    probability: RiskProbability | None
    issues: tuple[str, ...]
    qualification: Literal["research"] = "research"
    field_calibrated: Literal[False] = False
    work_authorized: Literal[False] = False
    implementation_sha256: Name
    result_digest: str = Field(pattern=r"^[a-f0-9]{64}$")

    def check_integrity(self):
        checked = HumanResponseResult.model_validate(self.model_dump(mode="python"))
        body = checked.model_dump(mode="json", exclude={"result_digest"})
        if digest(body) != self.result_digest:
            raise ValueError("the response estimate or its source record changed")
        if (self.status == "exchange_checked") != (self.probability is not None and not self.issues):
            raise ValueError("an unresolved response has no exportable probability")

    def risk_probability(self):
        self.check_integrity()
        if self.status != "exchange_checked":
            raise ValueError("unresolved response evidence cannot be exported as a probability")
        return RiskProbability.model_validate(self.probability.model_dump(mode="python"))


class HumanResponseScenarioResult(Contract):
    schema_version: Literal["pipeline_integrity.human_response_deadline_scenarios.v1"] = (
        "pipeline_integrity.human_response_deadline_scenarios.v1"
    )
    status: Literal["exchange_checked", "needs_data"]
    context: RunContext
    provider: dict
    request: HumanResponseRequest
    scenario_set: DeadlineScenarioSet
    initial_estimate: dict
    native_event: dict
    scenario_results: tuple[dict, ...]
    aggregate_native_estimate: dict | None
    probability: RiskProbability | None
    issues: tuple[str, ...]
    qualification: Literal["research"] = "research"
    field_calibrated: Literal[False] = False
    work_authorized: Literal[False] = False
    implementation_sha256: Name
    result_digest: str = Field(pattern=r"^[a-f0-9]{64}$")

    def check_integrity(self):
        checked = HumanResponseScenarioResult.model_validate(self.model_dump(mode="python"))
        body = checked.model_dump(mode="json", exclude={"result_digest"})
        if digest(body) != self.result_digest:
            raise ValueError("deadline-scenario result or its source record changed")
        if (self.status == "exchange_checked") != (
            self.probability is not None and self.aggregate_native_estimate is not None and not self.issues
        ):
            raise ValueError("an unresolved deadline-scenario result has no exportable probability")

    def risk_probability(self):
        self.check_integrity()
        if self.status != "exchange_checked":
            raise ValueError("unresolved response evidence cannot be exported as a probability")
        return RiskProbability.model_validate(self.probability.model_dump(mode="python"))


class HumanResponseClient:
    """Reuse the native HRA estimators; never infer a distribution from a planned finish time."""

    def __init__(self, *, api=None, risk_client=None, provider=None):
        if api is None:
            from human_reliability import timing
            from human_reliability.contracts import Estimate, HumanFailureEvent
            from human_reliability.dependency import apply_recovery
            from human_reliability.methods import cognitive_chain

            risk_client = HRARiskClient()
            root = Path(timing.__file__).parent
            provider = risk_client.provider | {
                "timing_sha256": file_digest(root / "timing.py"),
                "dependency_sha256": file_digest(root / "dependency.py"),
                "methods_sha256": file_digest(root / "methods.py"),
            }
            api = SimpleNamespace(
                Estimate=Estimate,
                HumanFailureEvent=HumanFailureEvent,
                LognormalTime=timing.LognormalTime,
                WeibullTime=timing.WeibullTime,
                KaplanMeierTime=timing.KaplanMeierTime,
                apply_deadline=timing.apply_deadline,
                response_bins=timing.response_bins,
                apply_recovery=apply_recovery,
                cognitive_chain=cognitive_chain,
            )
        if provider is None or risk_client is None:
            raise ValueError("an injected HRA API needs its source identity and risk binding client")
        self.api, self.risk_client, self.provider = api, risk_client, json_value(provider)

    def cognitive_estimate(self, event, specification: CognitiveResponse, context: RunContext):
        """Obtain a native initial estimate from reviewed, ordered conditional stage probabilities."""
        specification = CognitiveResponse.model_validate(specification.model_dump(mode="python"))
        context = RunContext.model_validate(context.model_dump(mode="python"))
        event = self.api.HumanFailureEvent.model_validate(event.model_dump(mode="python"))
        evidence_ids = tuple(
            dict.fromkeys(
                (specification.applicability_evidence_id, *(s.evidence_id for s in specification.stages))
            )
        )
        if len({e.id for e in context.evidence}) != len(context.evidence) or not set(evidence_ids) <= {
            e.id for e in context.evidence
        }:
            raise ValueError("cognitive stage and applicability evidence must uniquely exist in context")
        native = self.api.cognitive_chain(
            event.id,
            {s.id: s.conditional_failure_probability for s in specification.stages},
            conditioning=event.conditioning,
            evidence_ids=evidence_ids,
        )
        native = self.api.Estimate.model_validate(native.model_dump(mode="python"))
        trace = native.trace
        masses = tuple(trace["first_failure_probabilities"].values())
        if (
            native.event_id != event.id
            or native.conditioning != event.conditioning
            or native.status != "research"
            or native.includes
            or set(native.evidence_ids) != set(evidence_ids)
            or trace["stage_order"] != [s.id for s in specification.stages]
            or set(trace["first_failure_probabilities"]) != {s.id for s in specification.stages}
            or any(not math.isfinite(v) or not 0 <= v <= 1 for v in (*masses, trace["all_stages_succeed"]))
            or not math.isclose(sum(masses) + trace["all_stages_succeed"], 1.0, abs_tol=1e-12)
            or not math.isclose(sum(masses), native.probability, abs_tol=1e-12)
        ):
            raise ValueError(
                "native cognitive stage result changed its conditioning, order or probability partition"
            )
        return self.api.Estimate.model_validate(
            native.model_dump(mode="python")
            | {
                "trace": trace
                | {
                    "pipeline_cognitive_binding": {
                        "specification": specification.model_dump(mode="json"),
                        "context_digest": digest(context),
                        "native_effect_digest": digest(native),
                    }
                },
            }
        )

    def evaluate(self, initial, event, request: HumanResponseRequest, context: RunContext):
        request = HumanResponseRequest.model_validate(request.model_dump(mode="python"))
        context = RunContext.model_validate(context.model_dump(mode="python"))
        if len({e.id for e in context.evidence}) != len(context.evidence):
            raise ValueError("response context evidence identities must be unique")
        initial = self.api.Estimate.model_validate(initial.model_dump(mode="python"))
        event = self.api.HumanFailureEvent.model_validate(event.model_dump(mode="python"))
        cognitive_binding = initial.trace.get("pipeline_cognitive_binding")
        if cognitive_binding is not None and cognitive_binding["context_digest"] != digest(context):
            raise ValueError("the cognitive estimate belongs to a different source context")
        if initial.event_id != event.id or initial.conditioning != event.conditioning:
            raise ValueError(
                "the initial estimate and final event must have the same identity and conditioning"
            )
        if len(set(initial.includes)) != len(initial.includes):
            raise ValueError("the native estimate repeats an included effect")
        if {"timing", "recovery"} & set(initial.includes):
            raise ValueError("timing/recovery already included; a joint timed-recovery model is not supplied")
        effect = request.effect
        evidence_ids = set(initial.evidence_ids) | {request.applicability_evidence_id}
        if isinstance(effect, DeadlineEffect):
            if not event.includes_timing or event.includes_recovery:
                raise ValueError(
                    "the final event must explicitly include timing and exclude unmodeled recovery"
                )
            if event.deadline_s is None or not math.isclose(
                event.deadline_s, effect.window.available_s, rel_tol=1e-12, abs_tol=1e-9
            ):
                raise ValueError("native event deadline must equal the reviewed cue-to-limit duration")
            if effect.window.clock_id != context.clock_id:
                raise ValueError("cue, process limit and context must use the same clock")
            evidence_ids.add(effect.window.evidence_id)
            if isinstance(effect.time_model, EmpiricalResponse):
                evidence_ids.add(effect.time_model.censoring_review_evidence_id)
                evidence_ids.update(v.evidence_id for v in effect.time_model.observations)
            else:
                evidence_ids.add(effect.time_model.evidence_id)
        else:
            if not event.includes_recovery or event.includes_timing or event.deadline_s is not None:
                raise ValueError("untimed recovery cannot claim a deadline or a timing-inclusive event")
            evidence_ids.add(effect.evidence_id)
        if not evidence_ids <= {e.id for e in context.evidence}:
            raise ValueError("all initial, model, clock and applicability evidence must exist in context")

        issues, native, bins = [], None, ()
        if isinstance(effect, DeadlineEffect):
            model_spec = effect.time_model
            if isinstance(model_spec, EmpiricalResponse):
                if any(v.outcome in {"wrong_response", "unknown"} for v in model_spec.observations):
                    issues.append(
                        "wrong responses/unknown outcomes are not ordinary right-censored completions"
                    )
                    model = None
                else:
                    model = self.api.KaplanMeierTime(
                        [v.duration_s for v in model_spec.observations],
                        [v.outcome == "correct_completion" for v in model_spec.observations],
                    )
                model_evidence = model_spec.censoring_review_evidence_id
            elif isinstance(model_spec, LognormalResponse):
                model = self.api.LognormalTime(model_spec.median_s, model_spec.log_sigma)
                model_evidence = model_spec.evidence_id
            else:
                model = self.api.WeibullTime(model_spec.scale_s, model_spec.shape)
                model_evidence = model_spec.evidence_id
            if model is not None:
                try:
                    native = self.api.apply_deadline(
                        initial, model, effect.window.available_s, evidence_id=model_evidence
                    )
                    if effect.bin_boundaries_s:
                        bins = tuple(
                            self.api.response_bins(model, effect.bin_boundaries_s, initial.probability)
                        )
                except ValueError as error:
                    issues.append(f"native response model unresolved: {error}")
                    native, bins = None, ()
        else:
            native = self.api.apply_recovery(
                initial, effect.failure_to_recover_given_error, evidence_id=effect.evidence_id
            )

        probability = None
        if native is not None:
            native = self.api.Estimate.model_validate(native.model_dump(mode="python"))
            added = "timing" if isinstance(effect, DeadlineEffect) else "recovery"
            if (
                native.event_id != initial.event_id
                or native.conditioning != initial.conditioning
                or native.includes != initial.includes + (added,)
                or native.status != "research"
                or not set(initial.evidence_ids) <= set(native.evidence_ids)
                or not set(native.evidence_ids) <= evidence_ids
            ):
                raise ValueError(
                    "the native result changed the event, included effects, qualification or evidence"
                )
            if (added == "timing" and native.probability < initial.probability) or (
                added == "recovery" and native.probability > initial.probability
            ):
                raise ValueError("the native effect contradicts its declared probability direction")
            if bins and (
                any(not math.isfinite(row["probability"]) or not 0 <= row["probability"] <= 1 for row in bins)
                or not math.isclose(sum(row["probability"] for row in bins), 1.0, abs_tol=1e-12)
            ):
                raise ValueError("native completion bins do not form a probability partition")
            bound_estimate = self.api.Estimate.model_validate(
                native.model_dump(mode="python")
                | {
                    "evidence_ids": tuple(dict.fromkeys((*native.evidence_ids, *sorted(evidence_ids)))),
                    "trace": native.trace
                    | {
                        "pipeline_response_binding": {
                            "request": request.model_dump(mode="json"),
                            "context_digest": digest(context),
                            "native_effect_digest": digest(native),
                        }
                    },
                }
            )
            probability = self.risk_client.bind_human_estimate(
                bound_estimate,
                event,
                condition_id=request.condition_id,
                opportunity_id=request.opportunity_id,
                context=context,
                protection_ids=request.protection_ids,
            )
        body = dict(
            schema_version="pipeline_integrity.human_response.v1",
            status="needs_data" if issues else "exchange_checked",
            context=context,
            provider=self.provider,
            request=request,
            initial_estimate=initial.model_dump(mode="json"),
            native_event=event.model_dump(mode="json"),
            native_estimate=None if native is None else native.model_dump(mode="json"),
            native_completion_bins=bins,
            probability=probability,
            issues=tuple(issues),
            qualification="research",
            field_calibrated=False,
            work_authorized=False,
            implementation_sha256=file_digest(Path(__file__)),
        )
        result = HumanResponseResult(**body, result_digest=digest(body))
        result.check_integrity()
        return result

    def verify_result(self, result):
        """Replay one deadline/recovery result from its sealed HRA inputs and owner API."""
        try:
            checked = HumanResponseResult.model_validate(result.model_dump(mode="python"))
            checked.check_integrity()
            if checked.provider != self.provider:
                raise ValueError("response result belongs to a different owner provider identity")
            initial = self.api.Estimate.model_validate_json(json.dumps(checked.initial_estimate))
            event = self.api.HumanFailureEvent.model_validate_json(json.dumps(checked.native_event))
            request = HumanResponseRequest.model_validate(checked.request.model_dump(mode="python"))
            context = RunContext.model_validate(checked.context.model_dump(mode="python"))
            if self.evaluate(initial, event, request, context) != checked:
                raise ValueError("response owner replay differs from its sealed result")
            return {"valid": True}
        except (ValueError, TypeError, KeyError, AttributeError) as error:
            return {"valid": False, "reason": str(error)}

    def evaluate_deadline_scenarios(
        self,
        initial,
        event,
        request: HumanResponseRequest,
        scenario_set: DeadlineScenarioSet,
        context: RunContext,
    ):
        """Integrate reviewed deadline uncertainty using the owner model at each supplied point."""
        request = HumanResponseRequest.model_validate(request.model_dump(mode="python"))
        scenario_set = DeadlineScenarioSet.model_validate(scenario_set.model_dump(mode="python"))
        context = RunContext.model_validate(context.model_dump(mode="python"))
        if not isinstance(request.effect, DeadlineEffect):
            raise ValueError("deadline scenarios require a deadline effect")
        needed = {scenario_set.distribution_evidence_id}
        needed.update(row.evidence_id for row in scenario_set.scenarios)
        for row in scenario_set.scenarios:
            if isinstance(row.time_model, EmpiricalResponse):
                needed.add(row.time_model.censoring_review_evidence_id)
                needed.update(observation.evidence_id for observation in row.time_model.observations)
            elif row.time_model is not None:
                needed.add(row.time_model.evidence_id)
        if not needed <= {item.id for item in context.evidence}:
            raise ValueError("deadline distribution evidence must exist in the actual context")

        scenario_results, issues = [], []
        for scenario in scenario_set.scenarios:
            limit_time = request.effect.window.cue_time_s + scenario.available_time_s
            if not math.isfinite(limit_time):
                raise ValueError("scenario deadline is outside the finite clock range")
            window = request.effect.window.model_copy(update={"limit_time_s": limit_time})
            effect = request.effect.model_copy(
                update={
                    "window": window,
                    "time_model": scenario.time_model or request.effect.time_model,
                    "population_definition": scenario.response_population_definition
                    or request.effect.population_definition,
                }
            )
            scenario_request = request.model_copy(update={"effect": effect})
            scenario_event = event.model_copy(update={"deadline_s": scenario.available_time_s})
            result = self.evaluate(initial, scenario_event, scenario_request, context)
            scenario_results.append(result.model_dump(mode="json"))
            if result.status != "exchange_checked":
                issues.extend(f"{scenario.id}: {issue}" for issue in result.issues)

        aggregate, probability = None, None
        if not issues:
            weighted_probability = math.fsum(
                scenario.probability_mass * row["probability"]["probability"]
                for scenario, row in zip(scenario_set.scenarios, scenario_results, strict=True)
            )
            first = self.api.Estimate.model_validate_json(json.dumps(scenario_results[0]["native_estimate"]))
            trace = first.trace | {
                "pipeline_deadline_scenario_aggregation": {
                    "schema": "pipeline_integrity.human_response_deadline_scenarios.v1",
                    "scenario_set": scenario_set.model_dump(mode="json"),
                    "scenario_result_digests": [row["result_digest"] for row in scenario_results],
                    "probability_mass_weighted_failure_probability": weighted_probability,
                    "interpretation": "weighted average of owner fixed-deadline outputs at caller-reviewed scenarios; no interpolation",
                }
            }
            aggregate = self.api.Estimate.model_validate(
                first.model_dump(mode="python")
                | {
                    "probability": weighted_probability,
                    "evidence_ids": tuple(dict.fromkeys((*first.evidence_ids, *sorted(needed)))),
                    "trace": trace,
                }
            )
            probability = self.risk_client.bind_human_estimate(
                aggregate,
                event,
                condition_id=request.condition_id,
                opportunity_id=request.opportunity_id,
                context=context,
                protection_ids=request.protection_ids,
            )

        body = dict(
            schema_version="pipeline_integrity.human_response_deadline_scenarios.v1",
            status="needs_data" if issues else "exchange_checked",
            context=context,
            provider=self.provider,
            request=request,
            scenario_set=scenario_set,
            initial_estimate=initial.model_dump(mode="json"),
            native_event=event.model_dump(mode="json"),
            scenario_results=tuple(scenario_results),
            aggregate_native_estimate=None if aggregate is None else aggregate.model_dump(mode="json"),
            probability=probability,
            issues=tuple(issues),
            qualification="research",
            field_calibrated=False,
            work_authorized=False,
            implementation_sha256=file_digest(Path(__file__)),
        )
        result = HumanResponseScenarioResult(**body, result_digest=digest(body))
        result.check_integrity()
        return result

    def verify_deadline_scenario_result(self, result):
        try:
            checked = HumanResponseScenarioResult.model_validate(result.model_dump(mode="python"))
            checked.check_integrity()
            initial = self.api.Estimate.model_validate_json(json.dumps(checked.initial_estimate))
            event = self.api.HumanFailureEvent.model_validate_json(json.dumps(checked.native_event))
            request = HumanResponseRequest.model_validate(checked.request.model_dump(mode="python"))
            context = RunContext.model_validate(checked.context.model_dump(mode="python"))
            scenarios = DeadlineScenarioSet.model_validate(checked.scenario_set.model_dump(mode="python"))
            if self.evaluate_deadline_scenarios(initial, event, request, scenarios, context) != checked:
                raise ValueError("deadline-scenario owner replay differs")
            return {"valid": True}
        except (ValueError, TypeError, KeyError, AttributeError) as error:
            return {"valid": False, "reason": str(error)}
