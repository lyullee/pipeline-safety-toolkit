"""Open-use human-demand counts, explicit Bayesian prior and pipeline HRA binding."""

from __future__ import annotations

import pytest

from human_reliability import (
    ApplicabilityReview,
    BetaPrior,
    HumanFailureEvent,
    builtin_evidence_catalog,
)
from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.risk_exchange import HRARiskClient


def test_open_observed_count_beta_posterior_binds_to_pipeline_hra_probability():
    catalog = builtin_evidence_catalog()
    row = catalog.get("sacada.action.r0003")
    source = catalog.sources[row.source_id]
    assert row.kind == "observed_count"
    assert source.rights.numeric_reuse == "government_public_use"

    event = HumanFailureEvent(
        id="manual_isolation_failure",
        task_id="synthetic_manual_response",
        step_ids=("manual_command",),
        role="response",
        failure_definition="Declared manual isolation objective is not achieved before its response deadline",
        opportunity_definition="One manual isolation demand after an automatic isolation failure",
        conditioning="Automatic isolation has failed in an authored synthetic case",
        deadline_s=30.0,
        includes_recovery=True,
        includes_timing=True,
        dependency_group="manual_response_context",
    )
    alpha, beta = 0.5, 0.5
    prior = BetaPrior(alpha=alpha, beta=beta, evidence_id="assumed:jeffreys_prior")
    review = ApplicabilityReview(
        analyst="local_independent_oracle",
        target_domain="synthetic_pipeline_response",
        rationale="The open count source is used only to verify update and evidence binding; transfer to pipeline work is unvalidated.",
        scope_rationale="Retain the source's combined timing and recovery scope in the human response event.",
        included_effects=("timing", "recovery"),
    )
    binding = catalog.bind(row.id, event, prior=prior, review=review)

    expected = (row.failures + alpha) / (row.opportunities + alpha + beta)
    assert binding.estimate.probability == pytest.approx(expected, abs=1e-15)
    assert binding.estimate.trace["failures"] == row.failures
    assert binding.estimate.trace["opportunities"] == row.opportunities
    assert binding.estimate.status == "research"
    assert binding.estimate.uncertainty["alpha"] == row.failures + alpha
    assert binding.estimate.uncertainty["beta"] == row.opportunities - row.failures + beta
    assert binding.estimate.uncertainty["credibility"] == 0.95
    assert binding.estimate.trace["field_validation_claim"] is False

    evidence_ids = {record.id for record in binding.evidence}
    context_evidence = (
        EvidenceRef(
            id="human_data:" + row.id,
            citation=source.url,
            locator=row.locator,
            origin="literature",
            license_id=source.rights.license_id,
            limitations=tuple(source.limitations) + tuple(row.limitations),
        ),
        EvidenceRef(
            id=prior.evidence_id,
            citation="Explicit authored Bayesian prior",
            origin="user_declared",
            limitations=("Analyst-selected prior; not observed data.",),
        ),
    )
    assert evidence_ids == {item.id for item in context_evidence}
    context = RunContext(
        asset_id="synthetic_process",
        scenario_id="manual_response_estimation",
        clock_id="single_demand",
        origin="synthetic",
        evidence=context_evidence,
    )
    exchanged = HRARiskClient().bind_human_estimate(
        binding.estimate,
        event,
        condition_id="automatic_isolation_failed",
        opportunity_id="one_manual_demand",
        context=context,
        protection_ids=("manual_isolation", "manual_recovery", "manual_deadline"),
    )
    assert exchanged.id == event.id
    assert exchanged.probability == pytest.approx(expected, abs=1e-15)
    assert exchanged.qualification == "research"
    assert exchanged.includes == ("timing", "recovery")
    assert exchanged.dependency_groups == ("manual_response_context",)
    assert set(exchanged.evidence_ids) == evidence_ids
