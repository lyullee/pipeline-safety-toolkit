"""Procedure provenance/replay cases; execution deferred to Nurion."""

from copy import deepcopy

import pytest

from pipeline_integrity.contracts import EvidenceRef, RunContext, digest
from pipeline_integrity.procedures import (
    Procedure,
    ProcedureItem,
    ProcedureRecord,
    assess_procedure,
    verify_procedure_result,
)


def case():
    q = Procedure(
        id="site",
        version="1",
        applicable_asset_classes=("pipe",),
        items=(
            ProcedureItem(
                id="check",
                task="Record supplied review",
                criterion_source="caller-reviewed-rule",
                required_evidence_ids=("observation",),
            ),
        ),
    )
    c = RunContext(asset_id="pipe", scenario_id="review", clock_id="review-clock", origin="user_declared")
    e = EvidenceRef(id="observation", citation="caller observation", origin="user_declared")
    r = ProcedureRecord(
        item_id="check",
        result="satisfied",
        evidence=(e,),
        reviewer_id="reviewer",
        reviewed_at="2026-10-28T00:00:00+09:00",
    )
    return q, c, r


def test_procedure_result_replays_without_granting_acceptance():
    q, c, r = case()
    result = assess_procedure(q, (r,), c, asset_class="pipe")
    assert result["all_records_satisfied"] is True
    assert result["independent_physical_or_regulatory_acceptance"] is False
    assert verify_procedure_result(result, procedure=q, records=(r,), context=c)


@pytest.mark.parametrize("reviewed_at", ["2026-10-28T00:00:00", "2026-10-28", "not-a-time"])
def test_procedure_record_requires_parseable_timezone_aware_review_time(reviewed_at):
    with pytest.raises(ValueError):
        ProcedureRecord(
            item_id="check",
            result="satisfied",
            reviewer_id="reviewer",
            reviewed_at=reviewed_at,
        )


def test_replay_detects_same_instant_with_different_preserved_utc_offset():
    q, c, r = case()
    result = assess_procedure(q, (r,), c, asset_class="pipe")
    same_instant_other_offset = r.model_copy(update={"reviewed_at": "2026-10-27T15:00:00+00:00"})
    with pytest.raises(ValueError, match="caller records differ"):
        verify_procedure_result(result, records=(same_instant_other_offset,))


def test_conflicting_evidence_id_is_not_merged():
    q, c, r = case()
    c = c.model_copy(
        update={"evidence": (r.evidence[0].model_copy(update={"citation": "a different observation"}),)}
    )
    with pytest.raises(ValueError, match="conflicting identity"):
        assess_procedure(q, (r,), c, asset_class="pipe")


def test_duplicate_evidence_cannot_satisfy_by_ambiguity():
    q, c, r = case()
    r = r.model_copy(update={"evidence": r.evidence * 2})
    with pytest.raises(ValueError, match="unique"):
        assess_procedure(q, (r,), c, asset_class="pipe")


def test_cloned_invalid_record_is_revalidated():
    q, c, r = case()
    with pytest.raises(ValueError):
        assess_procedure(q, (r.model_copy(update={"result": "approved"}),), c, asset_class="pipe")


def test_resealed_result_still_requires_same_decisions():
    q, c, r = case()
    result = deepcopy(assess_procedure(q, (r,), c, asset_class="pipe"))
    result["items"]["check"]["state"] = "blocked"
    result["result_digest"] = digest({k: v for k, v in result.items() if k != "result_digest"})
    with pytest.raises(ValueError, match="replay"):
        verify_procedure_result(result)


@pytest.mark.parametrize("verdict", ["not_satisfied", "not_observed"])
def test_explicit_negative_or_unobserved_is_not_hidden_by_missing_evidence(verdict):
    q, c, r = case()
    r = r.model_copy(update={"result": verdict, "evidence": ()})
    result = assess_procedure(q, (r,), c, asset_class="pipe")
    item = result["items"]["check"]
    assert item["state"] == item["recorded_result"] == verdict
    assert item["readiness"] == "needs_evidence"
    assert item["missing_evidence_ids"] == ["observation"]
    assert result["all_records_satisfied"] is False


def test_negative_declaration_remains_visible_when_prerequisite_blocks():
    q, c, r = case()
    successor = q.items[0].model_copy(update={"id": "followup", "prerequisites": ("check",)})
    q = q.model_copy(update={"items": (*q.items, successor)})
    r = r.model_copy(update={"item_id": "followup", "result": "not_satisfied"})
    result = assess_procedure(q, (r,), c, asset_class="pipe")
    item = result["items"]["followup"]
    assert item["state"] == "not_satisfied" and item["readiness"] == "blocked"
    assert item["blocked_by"] == ["check"] and result["any_recorded_not_satisfied"]
    assert verify_procedure_result(result)
