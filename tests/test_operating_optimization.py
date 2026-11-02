"""Native and reference cases written for later execution, not run during coding."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from examples.operating_optimization import make_case
from pipeline_integrity.contracts import digest
from pipeline_integrity.operating_optimization import (
    OperatingOptimizationRequest,
    operating_batch_binding,
    optimize_operating_plans,
    verify_operating_optimization_record,
)


def amend(q, **changes):
    return OperatingOptimizationRequest.model_validate(q.model_copy(update=changes).model_dump(mode="python"))


def batch_change(q, **changes):
    plans = list(q.plans)
    batch = plans[0].batches[0].model_copy(update=changes)
    payload = {
        "source": "authored revised snapshot",
        "operating_batch_binding": operating_batch_binding(q, batch),
    }
    batch = batch.model_copy(update={"performance_payload": payload, "performance_digest": digest(payload)})
    plans[0] = plans[0].model_copy(update={"batches": (batch,)})
    return amend(q, plans=tuple(plans))


def test_default_does_not_invoke_native_owners(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("unreviewed operating plan invoked a native owner")

    monkeypatch.setattr("pipeline_integrity.operating_optimization.evaluate_lifecycle", forbidden)
    monkeypatch.setattr("pipeline_integrity.operating_optimization.solve_schedule", forbidden)
    q, c = make_case()
    result = optimize_operating_plans(q, c)
    assert result["status"] == "needs_data" and result["financial_record"] is None
    assert verify_operating_optimization_record(result, request=q, context=c)


def test_complete_plans_reuse_cashflow_and_native_audit():
    q, c = make_case(reviewed=True)
    result = optimize_operating_plans(q, c)
    assert result["status"] == "computed_diagnostic"
    assert result["selected_plan_ids"] == ["investment"]
    assert result["finite_supplied_plan_optimum_proven"]
    assert not result["continuous_dispatch_or_hydraulics_optimized"]
    assert result["financial_record"]["alternatives"]["investment"]["present_cost"] == pytest.approx(150)
    for row in result["plan_results"]:
        assert row["schedule_record"]["audit"]["valid"]
        assert row["schedule_specification"]["makespan_cost_per_tick"] == 0
        assert row["window_balances"][0]["shortfall_kg"] == 0


def test_energy_budget_uses_native_integer_consumable_and_no_feasible_case():
    q, c = make_case(reviewed=True)
    result = optimize_operating_plans(amend(q, electrical_energy_budget_J=600), c)
    assert result["selected_plan_ids"] == ["investment"]
    assert result["plan_results"][0]["status"] == "infeasible_supplied_schedule"
    result = optimize_operating_plans(amend(q, electrical_energy_budget_J=400), c)
    assert result["status"] == "no_feasible_supplied_plan" and result["selected_plan_ids"] == []


def test_energy_quantum_is_exact_and_never_silently_rounded():
    q, c = make_case(reviewed=True)
    result = optimize_operating_plans(amend(q, energy_quantum_J=3), c)
    assert result["status"] == "out_of_scope"
    assert result["selected_plan_ids"] is None


def test_delivered_service_shortfall_excludes_only_that_complete_plan():
    q, c = make_case(reviewed=True)
    q = batch_change(q, delivered_mass_kg=99)
    result = optimize_operating_plans(q, c)
    assert result["plan_results"][0]["status"] == "infeasible_delivered_service"
    assert result["plan_results"][0]["window_balances"][0]["shortfall_kg"] == 1
    assert result["selected_plan_ids"] == ["investment"]


@pytest.mark.parametrize(
    "changes", [{"delivered_mass_kg": None}, {"electrical_energy_J": None}, {"reviewed": False}]
)
def test_unknown_supplied_batch_blocks_final_selection(changes):
    q, c = make_case(reviewed=True)
    result = optimize_operating_plans(batch_change(q, **changes), c)
    assert result["status"] == "needs_data" and result["selected_plan_ids"] is None


def test_resource_window_feasibility_is_owned_by_scheduler():
    q, c = make_case(reviewed=True)
    q = batch_change(q, duration_s=21)
    result = optimize_operating_plans(q, c)
    assert result["plan_results"][0]["status"] == "infeasible_supplied_schedule"
    assert result["selected_plan_ids"] == ["investment"]


def test_unresolved_cost_is_not_silently_dropped():
    q, c = make_case(reviewed=True)
    alternatives = list(q.financial_study.alternatives)
    event = alternatives[0].cost_events[0]
    event = event.model_copy(update={"money": event.money.model_copy(update={"amount": None})})
    alternatives[0] = alternatives[0].model_copy(update={"cost_events": (event,)})
    study = q.financial_study.model_copy(update={"alternatives": tuple(alternatives)})
    result = optimize_operating_plans(amend(q, financial_study=study), c)
    assert result["status"] == "needs_data" and result["selected_plan_ids"] is None


def test_unknown_native_feasibility_prevents_finite_optimum_claim(monkeypatch):
    q, c = make_case(reviewed=True)
    monkeypatch.setattr(
        "pipeline_integrity.operating_optimization.solve_schedule",
        lambda *args, **kwargs: SimpleNamespace(
            status="unknown", model_dump=lambda **kw: {"status": "unknown"}
        ),
    )
    result = optimize_operating_plans(q, c)
    assert result["status"] == "needs_data" and not result["finite_supplied_plan_optimum_proven"]


def test_performance_binding_prevents_mass_energy_reinterpretation():
    q, c = make_case(reviewed=True)
    plans = list(q.plans)
    plans[0] = plans[0].model_copy(
        update={"batches": (plans[0].batches[0].model_copy(update={"electrical_energy_J": 0}),)}
    )
    with pytest.raises(ValueError, match="performance binding"):
        optimize_operating_plans(amend(q, plans=tuple(plans)), c)


def test_context_and_missing_source_payload_rejected():
    q, c = make_case(reviewed=True)
    for bad in (
        c.model_copy(update={"clock_id": "other"}),
        c.model_copy(update={"evidence": ()}),
        c.model_copy(update={"metadata": {}}),
    ):
        with pytest.raises(ValueError):
            optimize_operating_plans(q, bad)
    with pytest.raises(ValueError):
        plans = list(q.plans)
        changed = plans[0].batches[0].model_copy(update={"performance_payload": {"changed": True}})
        plans[0] = plans[0].model_copy(update={"batches": (changed,)})
        amend(q, plans=tuple(plans))


def test_default_rehashed_qualification_mutation_rejected():
    q, c = make_case()
    result = deepcopy(optimize_operating_plans(q, c))
    result["continuous_dispatch_or_hydraulics_optimized"] = True
    result["record_digest"] = digest({k: v for k, v in result.items() if k != "record_digest"})
    with pytest.raises(ValueError):
        verify_operating_optimization_record(result, request=q, context=c)


@pytest.mark.parametrize(
    "field,value", [("start_s", 1.0), ("finish_s", 19.0), ("required_delivered_mass_kg", 101.0)]
)
def test_existing_performance_receipt_cannot_move_to_another_delivery_boundary(field, value, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("stale boundary receipt reached a financial or scheduling owner")

    monkeypatch.setattr("pipeline_integrity.operating_optimization.evaluate_lifecycle", forbidden)
    monkeypatch.setattr("pipeline_integrity.operating_optimization.solve_schedule", forbidden)
    q, c = make_case(reviewed=True)
    changed = q.windows[0].model_copy(update={field: value})
    q = amend(q, windows=(changed,))
    with pytest.raises(ValueError, match="performance binding"):
        optimize_operating_plans(q, c)


def test_performance_supplier_id_is_part_of_the_original_receipt(monkeypatch):
    monkeypatch.setattr(
        "pipeline_integrity.operating_optimization.evaluate_lifecycle",
        lambda *a, **k: pytest.fail("stale supplier receipt reached financial owner"),
    )
    q, c = make_case(reviewed=True)
    plans = list(q.plans)
    old = plans[0].batches[0]
    plans[0] = plans[0].model_copy(
        update={"batches": (old.model_copy(update={"performance_source_id": "different_provider"}),)}
    )
    with pytest.raises(ValueError, match="performance binding"):
        optimize_operating_plans(amend(q, plans=tuple(plans)), c)


def test_forecast_checksum_cannot_conflict_with_selected_evidence():
    q, c = make_case(reviewed=True)
    evidence = c.evidence[0].model_copy(update={"sha256": "a" * 64})
    c = c.model_copy(update={"evidence": (evidence,)})
    with pytest.raises(ValueError, match="forecast checksum"):
        optimize_operating_plans(q, c)
