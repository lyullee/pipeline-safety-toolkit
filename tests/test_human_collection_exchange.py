"""Synthetic contracts for the pinned HRA 0.5 collection-to-risk bridge."""

from types import SimpleNamespace

import pytest
from pydantic import BaseModel

import pipeline_integrity.human_collection_exchange as exchange
from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.risk_exchange import HRARiskClient


SOURCE_HASHES = dict(exchange.HRA_SOURCE_SHA256)


class OwnerEstimate(BaseModel):
    event_id: str
    method: str = "reviewed_collection"
    probability: float
    conditioning: str
    evidence_ids: tuple[str, ...]
    uncertainty: dict = {}
    includes: tuple[str, ...] = ()
    status: str = "research"
    trace: dict


class OwnerEvent(BaseModel):
    id: str
    task_id: str
    step_ids: tuple[str, ...] = ("step",)
    role: str = "initiating"
    failure_definition: str
    opportunity_definition: str
    conditioning: str
    deadline_s: float | None = None
    includes_recovery: bool = False
    includes_timing: bool = False
    dependency_group: str | None = None


class FakeCollectionBundle:
    def __init__(self):
        self.protocol = SimpleNamespace(
            id="protocol-v1",
            event_id="operator_error",
            task_id="isolate_line",
            conditioning="alarm and declared operating state",
            failure_definition="the declared isolation task fails",
            opportunity_definition="one registered isolation demand",
            source_id="training_simulator",
            setting="simulator",
            license_evidence="license",
            coverage_review="coverage",
            registry_review="registry",
            adjudication_rule="adjudication",
            collection_authorization="authorization",
        )
        self.windows = (SimpleNamespace(reconciliation_evidence="window_review"),)
        self.demands = (
            SimpleNamespace(
                disposition_evidence="demand_review",
                context={"mode": "alarm"},
                context_evidence="context_review",
            ),
        )
        self.outcomes = (SimpleNamespace(evidence="outcome_review"),)
        self.audit = {
            "protocol_digest": "a" * 64,
            "collection_digest": "b" * 64,
            "denominator_reconciled": True,
            "industrial_hep_validated": False,
            "counts": {"initial": {"failures": 1, "eligible_opportunities": 10}},
        }
        self.estimate_calls = 0

    def require_reconciled(self):
        return dict(self.audit)

    def hep(self, *, estimand, prior, independence_review, missingness_review=None, **selection):
        self.estimate_calls += 1
        return OwnerEstimate(
            event_id=self.protocol.event_id,
            probability=0.1,
            conditioning=self.protocol.conditioning,
            evidence_ids=("prior", f"{self.protocol.id}:{self.audit['collection_digest']}"),
            status="empirical" if self.protocol.setting == "natural_field" else "research",
            trace={
                "collection_audit": self.audit,
                "source_id": self.protocol.source_id,
                "setting": self.protocol.setting,
                "estimand": estimand,
                "selection": selection,
                "independence_review": independence_review,
                "missingness_review": missingness_review,
            },
        )


def client():
    return HRARiskClient(
        api=SimpleNamespace(Estimate=OwnerEstimate, HumanFailureEvent=OwnerEvent),
        provider={"id": "human-reliability-toolkit", "version": "0.5.0", "source_digest": "c" * 64},
    )


def setup_context():
    evidence_ids = (
        "license",
        "coverage",
        "registry",
        "adjudication",
        "authorization",
        "mapping",
        "independence_review",
        "missingness_review",
        "window_review",
        "demand_review",
        "context_review",
        "outcome_review",
        "prior",
        "protocol-v1:" + "b" * 64,
    )
    evidence = tuple(
        EvidenceRef(
            id=identity,
            citation=f"Reviewed artifact {identity}",
            origin="simulation" if identity == "outcome_review" else "user_declared",
            sha256="b" * 64 if identity.startswith("protocol-v1:") else f"{index:064x}",
        )
        for index, identity in enumerate(evidence_ids, 1)
    )
    context = RunContext(
        asset_id="pipe-A",
        scenario_id="alarm-isolation",
        clock_id="elapsed-s",
        origin="simulation",
        evidence=evidence,
    )
    event = OwnerEvent(
        id="operator_error",
        task_id="isolate_line",
        failure_definition="the declared isolation task fails",
        opportunity_definition="one registered isolation demand",
        conditioning="alarm and declared operating state",
    )
    return context, event


def call(bundle, event, context, **overrides):
    kwargs = {
        "client": client(),
        "context": context,
        "estimand": "final",
        "prior": object(),
        "independence_review": "reviewed opportunity dependence",
        "independence_review_evidence_id": "independence_review",
        "mapping_evidence_id": "mapping",
        "target_condition_id": "alarm-condition",
        "target_opportunity_id": "one-isolation-demand",
        "selection": {"conditions": {"mode": "alarm"}},
    }
    kwargs.update(overrides)
    return exchange.bind_human_collection_estimate(bundle, event, **kwargs)


def test_owner_posterior_binds_with_raw_owner_estimate_and_replays(monkeypatch):
    monkeypatch.setattr(exchange, "_source_identity", lambda bundle, consumer: dict(SOURCE_HASHES))
    bundle = FakeCollectionBundle()
    context, event = setup_context()

    result = call(
        bundle,
        event,
        context,
        missingness_review="reviewed outcome mechanism",
        missingness_review_evidence_id="missingness_review",
    )

    assert result.risk_probability.probability == 0.1
    assert result.risk_probability.condition_id == "alarm-condition"
    assert "mapping" in result.risk_probability.evidence_ids
    assert "independence_review" in result.risk_probability.evidence_ids
    assert "missingness_review" in result.risk_probability.evidence_ids
    assert result.owner_estimate["evidence_ids"] == ["prior", "protocol-v1:" + "b" * 64]
    assert result.industrial_hep_validated is False
    assert result.field_population_transfer_qualified is False
    assert result.check_integrity()
    replay = exchange.verify_human_collection_risk_binding(
        result,
        bundle,
        event,
        client=client(),
        context=context,
        estimand="final",
        prior=object(),
        independence_review="reviewed opportunity dependence",
        independence_review_evidence_id="independence_review",
        missingness_review="reviewed outcome mechanism",
        missingness_review_evidence_id="missingness_review",
        mapping_evidence_id="mapping",
        target_condition_id="alarm-condition",
        target_opportunity_id="one-isolation-demand",
        selection={"conditions": {"mode": "alarm"}},
    )
    assert replay == {"valid": True, "record_digest": result.record_digest}
    assert bundle.estimate_calls == 2


def test_incomplete_denominator_is_rejected_before_owner_estimation(monkeypatch):
    monkeypatch.setattr(exchange, "_source_identity", lambda bundle, consumer: dict(SOURCE_HASHES))
    bundle = FakeCollectionBundle()
    bundle.audit["denominator_reconciled"] = False
    context, event = setup_context()

    with pytest.raises(ValueError, match="denominator"):
        call(bundle, event, context)
    assert bundle.estimate_calls == 0


def test_natural_field_owner_estimate_is_requalified_as_research_for_process_handoff(monkeypatch):
    monkeypatch.setattr(exchange, "_source_identity", lambda bundle, consumer: dict(SOURCE_HASHES))
    bundle = FakeCollectionBundle()
    bundle.protocol.setting = "natural_field"
    context, event = setup_context()

    result = call(bundle, event, context)

    assert result.owner_estimate["status"] == "empirical"
    assert result.risk_probability.qualification == "research"
    assert result.industrial_hep_validated is False


def test_mapping_evidence_must_be_present_and_hash_pinned(monkeypatch):
    monkeypatch.setattr(exchange, "_source_identity", lambda bundle, consumer: dict(SOURCE_HASHES))
    bundle = FakeCollectionBundle()
    context, event = setup_context()
    missing_hash = context.model_copy(
        update={
            "evidence": tuple(
                item.model_copy(update={"sha256": None}) if item.id == "mapping" else item
                for item in context.evidence
            )
        }
    )

    with pytest.raises(ValueError, match="source-hash pinned"):
        call(bundle, event, missing_hash)


def test_independence_review_requires_hash_pinned_context_evidence(monkeypatch):
    monkeypatch.setattr(exchange, "_source_identity", lambda bundle, consumer: dict(SOURCE_HASHES))
    bundle = FakeCollectionBundle()
    context, event = setup_context()
    missing_hash = context.model_copy(
        update={
            "evidence": tuple(
                item.model_copy(update={"sha256": None})
                if item.id == "independence_review"
                else item
                for item in context.evidence
            )
        }
    )

    with pytest.raises(ValueError, match="independence/missingness review evidence"):
        call(bundle, event, missing_hash)
    assert bundle.estimate_calls == 0


def test_missingness_review_requires_a_pinned_evidence_identifier(monkeypatch):
    monkeypatch.setattr(exchange, "_source_identity", lambda bundle, consumer: dict(SOURCE_HASHES))
    bundle = FakeCollectionBundle()
    context, event = setup_context()

    with pytest.raises(ValueError, match="missingness review requires"):
        call(
            bundle,
            event,
            context,
            missingness_review="reviewed outcome process",
            missingness_review_evidence_id=None,
        )
    assert bundle.estimate_calls == 0


def test_event_must_match_frozen_owner_protocol(monkeypatch):
    monkeypatch.setattr(exchange, "_source_identity", lambda bundle, consumer: dict(SOURCE_HASHES))
    bundle = FakeCollectionBundle()
    context, event = setup_context()

    with pytest.raises(ValueError, match="frozen collection protocol"):
        call(bundle, event.model_copy(update={"task_id": "different_task"}), context)
    assert bundle.estimate_calls == 0


def test_collection_change_during_owner_estimation_is_rejected(monkeypatch):
    monkeypatch.setattr(exchange, "_source_identity", lambda bundle, consumer: dict(SOURCE_HASHES))
    bundle = FakeCollectionBundle()
    context, event = setup_context()
    original_hep = bundle.hep

    def changed_during_estimation(**kwargs):
        estimate = original_hep(**kwargs)
        bundle.audit["collection_digest"] = "d" * 64
        return estimate

    bundle.hep = changed_during_estimation
    with pytest.raises(ValueError, match="changed during estimation"):
        call(bundle, event, context)


def test_resealed_scope_tampering_is_rejected(monkeypatch):
    monkeypatch.setattr(exchange, "_source_identity", lambda bundle, consumer: dict(SOURCE_HASHES))
    bundle = FakeCollectionBundle()
    context, event = setup_context()
    result = call(bundle, event, context)
    altered = result.model_copy(update={"target_opportunity_id": "different-opportunity"})

    with pytest.raises(ValueError, match="risk input is not bound"):
        altered.check_integrity()
