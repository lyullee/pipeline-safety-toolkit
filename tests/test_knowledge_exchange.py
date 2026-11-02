"""Knowledge authorization gates and receipt corruption checks, without provider calls."""

import runpy
from copy import deepcopy
from datetime import UTC, datetime

import pytest

from pipeline_integrity.contracts import digest
from pipeline_integrity.knowledge_exchange import (
    AUTHORITY,
    KnowledgeRequest,
    SagaOfflineClient,
    evaluate_knowledge,
    knowledge_source_fingerprint,
    verify_knowledge_record,
    verify_knowledge_replay,
)


def cases():
    return runpy.run_path("examples/knowledge_exchange.py")["make_cases"]("a" * 64)


class NoCall:
    def run(self, _payload):
        raise AssertionError("unresolved input reached a knowledge provider")


@pytest.mark.parametrize("method", ("retrieve", "candidate_review", "threshold_proposal", "grounded_answer"))
@pytest.mark.parametrize(
    "change", ({"applicability_reviews": {}}, {"review_evidence_ids": {}}, {"owner_source_sha256": None})
)
def test_unreviewed_requests_stop_without_native(method, change):
    requests, context = cases()
    result = evaluate_knowledge(requests[method].model_copy(update=change), context, client=NoCall())
    assert result["status"] == "needs_data" and result["native_receipt"] is None
    assert result["authority"] == AUTHORITY
    assert verify_knowledge_record(result)


@pytest.mark.parametrize(
    "source_change",
    (
        {"source_version": None},
        {"license_id": None},
        {"rights_statement": None},
        {"content_sha256": None},
        {"valid_until_utc": None},
        {"permitted_operations": ()},
        {"valid_until_utc": datetime(2026, 10, 1, tzinfo=UTC)},
        {"asset_id": "other"},
        {"domain_id": "other"},
    ),
)
def test_unknown_rights_scope_and_period_hold(source_change):
    requests, context = cases()
    request = requests["retrieve"]
    sources = (request.sources[0].model_copy(update=source_change), request.sources[1])
    result = evaluate_knowledge(request.model_copy(update={"sources": sources}), context, client=NoCall())
    assert result["status"] == "needs_data" and verify_knowledge_record(result)


@pytest.mark.parametrize("field", ("asset_id", "clock_id"))
def test_context_mismatch_rejected(field):
    requests, context = cases()
    with pytest.raises(ValueError, match="asset or clock"):
        evaluate_knowledge(requests["retrieve"].model_copy(update={field: "other"}), context, client=NoCall())


@pytest.mark.parametrize("value", (False, float("nan"), float("inf"), "200000"))
def test_threshold_bool_nonfinite_and_strings_rejected(value):
    requests, context = cases()
    request = requests["candidate_review"].model_dump()
    request["candidates"][0]["threshold_value"] = value
    with pytest.raises(ValueError):
        evaluate_knowledge(request, context)


def test_zero_and_unknown_are_distinct_and_preserved():
    requests, _ = cases()
    data = requests["candidate_review"].model_dump()
    for value in (None, 0.0):
        data["candidates"][0]["threshold_value"] = value
        assert KnowledgeRequest.model_validate(data).candidates[0].threshold_value is value


@pytest.mark.parametrize(
    "change", ({"maximum_corpus_chars": 1000}, {"maximum_passages": 1}, {"retrieval_limit": False})
)
def test_budgets_and_boolean_count_rejected(change):
    requests, context = cases()
    if "maximum_corpus_chars" in change:
        request = requests["retrieve"]
        p = request.sources[0].passages[0].model_copy(update={"text": "x" * 1200})
        passages = (p, request.sources[0].passages[1])
        source = request.sources[0].model_copy(
            update={"passages": passages, "content_sha256": digest(passages)}
        )
        change = change | {"sources": (source, request.sources[1])}
    with pytest.raises(ValueError):
        evaluate_knowledge(requests["retrieve"].model_copy(update=change), context)


def test_source_digest_and_candidate_quote_forgery_rejected():
    requests, context = cases()
    request = requests["candidate_review"]
    bad = request.candidates[0].model_copy(update={"exact_quote": "fabricated source quote"})
    with pytest.raises(ValueError, match="exact reviewed source"):
        evaluate_knowledge(request.model_copy(update={"candidates": (bad,)}), context)
    bad_source = request.sources[0].model_copy(update={"content_sha256": "b" * 64})
    with pytest.raises(ValueError, match="declared digest"):
        evaluate_knowledge(request.model_copy(update={"sources": (bad_source, request.sources[1])}), context)


def test_stored_input_and_authority_tampering_rejected():
    requests, context = cases()
    result = evaluate_knowledge(requests["retrieve"], context)
    for key, value in (("status", "computed"), ("authority", dict(AUTHORITY, approved_rule=True))):
        changed = deepcopy(result)
        changed[key] = value
        changed["receipt_digest"] = digest({k: v for k, v in changed.items() if k != "receipt_digest"})
        with pytest.raises(ValueError):
            verify_knowledge_record(changed)


def test_client_mutation_and_unknown_reply_schema_rejected():
    requests, context = cases()

    class Mutator:
        def run(self, payload):
            payload["request"]["question"] = "changed"
            return {}

    class WrongSchema:
        def run(self, _payload):
            return {"schema_version": "future-unreviewed"}

    for client in (Mutator(), WrongSchema()):
        with pytest.raises(ValueError):
            evaluate_knowledge(requests["retrieve"], context, client=client)


def test_original_project_cannot_be_scratch_storage(tmp_path):
    with pytest.raises(ValueError, match="outside"):
        SagaOfflineClient(source_root=tmp_path, python_executable="python", work_root=tmp_path / "data")
    with pytest.raises(ValueError, match="required source"):
        knowledge_source_fingerprint(tmp_path)


def test_missing_client_and_empty_corpus_need_data():
    requests, context = cases()
    request = requests["retrieve"].model_copy(update={"sources": (), "allowed_source_ids": ()})
    result = evaluate_knowledge(request, context, client=NoCall())
    assert result["status"] == "needs_data" and verify_knowledge_record(result)
    assert evaluate_knowledge(requests["retrieve"], context)["status"] == "needs_data"


def test_unexecuted_knowledge_record_replays_exactly_without_provider():
    requests, context = cases()
    record = evaluate_knowledge(requests["retrieve"], context)
    assert verify_knowledge_replay(record)


def test_unexecuted_replay_never_invokes_a_supplied_provider():
    requests, context = cases()
    record = evaluate_knowledge(requests["retrieve"], context)
    with pytest.raises(ValueError, match="must be replayed without a provider"):
        verify_knowledge_replay(record, client=NoCall())


def test_executed_replay_requires_explicit_client():
    record = advisory_fixture("candidate_review")
    with pytest.raises(ValueError, match="requires its caller-selected replay client"):
        verify_knowledge_replay(record)


def test_fixed_offline_reply_replays_and_changed_reply_is_rejected():
    record = advisory_fixture("candidate_review")

    class FixedReply:
        def __init__(self, reply):
            self.reply = deepcopy(reply)

        def run(self, _payload):
            return deepcopy(self.reply)

    assert verify_knowledge_replay(record, client=FixedReply(record["native_receipt"]))
    changed = deepcopy(record["native_receipt"])
    changed["notes"] = ["changed provider output"]
    with pytest.raises(ValueError, match="did not reproduce"):
        verify_knowledge_replay(record, client=FixedReply(changed))


def advisory_fixture(method, *, threshold_value=200000.0):
    """A contract-only client; native execution is covered by the separate guide."""
    requests, context = cases()
    request = requests[method]
    if method == "candidate_review":
        request = request.model_copy(
            update={
                "candidates": (request.candidates[0].model_copy(update={"threshold_value": threshold_value}),)
            }
        )
    source = {"source_sha256": request.owner_source_sha256}
    native = {
        "schema_version": "saga.offline_exchange.v1",
        "request_digest": digest(request),
        "context_digest": digest(context),
        "authority": deepcopy(AUTHORITY),
        "network_calls": 0,
        "status": "computed" if method == "candidate_review" else "needs_data",
        "reasoning_executed": False,
        "answer": None,
        "citations": [],
        "owner_version": {
            "package": "contract-fixture",
            "project": "contract-fixture",
            "vector_model": "fixture",
        },
        "schema_digests": dict.fromkeys(
            ("Citation", "ChatRequest", "AnswerReview", "GroundedClaims", "HazopRuleInput"), "b" * 64
        ),
        "rule_candidates": [c.model_dump(mode="json") for c in request.candidates],
        "candidate_audit": None,
        "threshold_proposal": None,
        "native_request": None,
        "source_pin": {"before": source, "after": deepcopy(source), "unchanged": True},
        "loaded_modules": {
            "saga": "python/saga/__init__.py",
            "saga.database": "python/saga/database.py",
            "saga.hazop": "python/saga/hazop.py",
            "saga.rag": "python/saga/rag.py",
            "saga.schemas": "python/saga/schemas.py",
        },
    }
    if method == "candidate_review":
        candidate = request.candidates[0]
        native["native_request"] = [
            {
                "no": 1,
                "scenario_id": context.scenario_id,
                "tag_id": candidate.tag_id,
                "item_name": candidate.variable,
                "unit": candidate.unit,
                "threshold_type": candidate.threshold_type,
                "threshold_value": candidate.threshold_value,
                "compare_dir": candidate.compare_dir,
                "threshold_basis": candidate.rationale,
                "threshold_source": f"{candidate.source_id}:{candidate.passage_id}",
                "threshold_confidence": "pending",
                "source": "pipeline-private-candidate-audit",
                "owner_default_retained": None,
            }
        ]
        native["candidate_audit"] = {
            "scenario_id": context.scenario_id,
            "total_rules": 1,
            "ready_rules": 0,
            "not_ready_rules": 1,
            "monitoring_ready": False,
        }
    else:
        native["native_request"] = {
            "scenario_id": context.scenario_id,
            "tag_id": "knowledge-review",
            "equipment_name": request.asset_id,
            "context": request.question,
            "variable": "",
            "unit": "",
            "model": None,
        }
        native["threshold_proposal"] = {
            "scenario_id": context.scenario_id,
            "tag_id": "knowledge-review",
            "ready": False,
            "requires_human_approval": True,
            "candidates": [],
        }

    class Client:
        def run(self, _payload):
            return deepcopy(native)

    return evaluate_knowledge(request, context, client=Client())


@pytest.mark.parametrize(
    "field,value",
    (
        ("no", True),
        ("scenario_id", "other-scenario"),
        ("tag_id", "other-tag"),
        ("item_name", "temperature"),
        ("unit", "psi"),
        ("threshold_type", "DELTA_START"),
        ("threshold_value", None),
        ("compare_dir", "<="),
        ("threshold_basis", "changed rationale"),
        ("threshold_source", "other:p1"),
        ("source", "operating-rules"),
    ),
)
def test_rehashed_native_candidate_projection_changes_rejected(field, value):
    record = advisory_fixture("candidate_review")
    assert verify_knowledge_record(record)
    record["native_receipt"]["native_request"][0][field] = value
    record["receipt_digest"] = digest({k: v for k, v in record.items() if k != "receipt_digest"})
    with pytest.raises(ValueError, match="native candidate projection"):
        verify_knowledge_record(record)


@pytest.mark.parametrize("value", (None, 0.0))
def test_rehashed_missing_candidate_value_is_not_preserved_unknown_or_zero(value):
    record = advisory_fixture("candidate_review", threshold_value=value)
    assert verify_knowledge_record(record)
    del record["native_receipt"]["native_request"][0]["threshold_value"]
    record["receipt_digest"] = digest({k: v for k, v in record.items() if k != "receipt_digest"})
    with pytest.raises(ValueError, match="native candidate projection"):
        verify_knowledge_record(record)


def test_rehashed_boolean_is_not_preserved_numeric_zero():
    record = advisory_fixture("candidate_review", threshold_value=0.0)
    record["native_receipt"]["native_request"][0]["threshold_value"] = False
    record["receipt_digest"] = digest({k: v for k, v in record.items() if k != "receipt_digest"})
    with pytest.raises(ValueError, match="native candidate projection"):
        verify_knowledge_record(record)


@pytest.mark.parametrize(
    "field,value", (("scenario_id", "other"), ("ready_rules", 1), ("not_ready_rules", 0))
)
def test_rehashed_candidate_audit_context_and_readiness_changes_rejected(field, value):
    record = advisory_fixture("candidate_review")
    record["native_receipt"]["candidate_audit"][field] = value
    record["receipt_digest"] = digest({k: v for k, v in record.items() if k != "receipt_digest"})
    with pytest.raises(ValueError, match="audit changed"):
        verify_knowledge_record(record)


@pytest.mark.parametrize(
    "target,field,value",
    (
        ("threshold_proposal", "ready", True),
        ("threshold_proposal", "candidates", [{"threshold_value": 1.0}]),
        ("threshold_proposal", "requires_human_approval", False),
        ("threshold_proposal", "scenario_id", "other"),
        ("threshold_proposal", "tag_id", "other"),
        ("native_request", "equipment_name", "other"),
        ("native_request", "context", "different question"),
        ("native_request", "unit", "psi"),
        ("native_request", "model", "unreviewed-model"),
    ),
)
def test_rehashed_unexecuted_proposal_and_native_request_changes_rejected(target, field, value):
    record = advisory_fixture("threshold_proposal")
    assert verify_knowledge_record(record)
    record["native_receipt"][target][field] = value
    record["receipt_digest"] = digest({k: v for k, v in record.items() if k != "receipt_digest"})
    with pytest.raises(ValueError):
        verify_knowledge_record(record)


def test_rehashed_unexecuted_proposal_cannot_claim_computed_status():
    record = advisory_fixture("threshold_proposal")
    record["native_receipt"]["status"] = record["status"] = "computed"
    record["receipt_digest"] = digest({k: v for k, v in record.items() if k != "receipt_digest"})
    with pytest.raises(ValueError, match="unexecuted threshold"):
        verify_knowledge_record(record)
