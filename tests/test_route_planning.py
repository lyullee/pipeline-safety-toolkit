"""Deferred finite-route regression definitions; no solver/field qualification implied."""

from copy import deepcopy

import pytest

from examples.route_planning import make_case
from pipeline_integrity.contracts import digest
from pipeline_integrity.route_planning import (
    RoutePlanningRequest,
    evaluate_routes,
    route_boundary_metadata,
    verify_route_record,
)


def amend(q, c, **changes):
    q = RoutePlanningRequest.model_validate(q.model_copy(update=changes).model_dump(mode="python"))
    return q, c.model_copy(update={"metadata": {"route_boundary": route_boundary_metadata(q)}})


def change_candidate(q, c, index=0, **changes):
    items = list(q.candidates)
    items[index] = items[index].model_copy(update=changes)
    return amend(q, c, candidates=tuple(items))


def test_default_review_withholds_comparison():
    q, c = make_case()
    result = evaluate_routes(q, c)
    assert result["status"] == "needs_data" and result["pareto_candidate_ids"] is None
    assert verify_route_record(result, request=q, context=c)


def test_exact_front_and_no_arbitrary_weighted_choice():
    q, c = make_case(reviewed=True)
    result = evaluate_routes(q, c)
    assert result["status"] == "computed_diagnostic"
    assert result["pareto_candidate_ids"] == ["detour", "short"]
    assert result["selected_candidate_ids"] is None
    assert not result["global_terrain_optimum"] and not result["permit_compliance_established"]
    assert not result["uncertainty_or_correlation_solved"]


def test_lexicographic_choice_and_explicit_ties():
    q, c = make_case(reviewed=True, selection_mode="lexicographic")
    assert evaluate_routes(q, c)["selected_candidate_ids"] == ["short"]
    q, c = change_candidate(
        q, c, index=1, cost_items=q.candidates[0].cost_items, earthwork_m3=q.candidates[0].earthwork_m3
    )
    assert evaluate_routes(q, c)["selected_candidate_ids"] == ["detour", "short"]


@pytest.mark.parametrize(
    "changes",
    [
        {"length_m": None},
        {"earthwork_m3": None},
        {"cost_inventory_reviewed": False},
        {"geometry_and_quantities_reviewed": False},
        {"environmental_inventory_reviewed": False},
        {"conditions": {}},
        {"crossing_counts": {}},
    ],
)
def test_unresolved_contender_blocks_final_choice(changes):
    q, c = make_case(reviewed=True, selection_mode="lexicographic")
    # Length becomes required through a supplied constraint.
    q, c = amend(q, c, maximum_metrics={"length_m": 1000})
    q, c = change_candidate(q, c, **changes)
    result = evaluate_routes(q, c)
    assert result["status"] == "needs_data"
    assert result["selected_candidate_ids"] is None and result["pareto_candidate_ids"] is None


def test_unknown_cost_is_not_zero_and_known_infeasibility_can_exclude_it():
    q, c = make_case(reviewed=True)
    missing = q.candidates[0].cost_items[0].model_copy(update={"unit_rate": None})
    q, c = change_candidate(q, c, cost_items=(missing,))
    result = evaluate_routes(q, c)
    assert result["status"] == "needs_data"
    assert result["candidate_results"][0]["metrics"]["cost"] is None
    q, c = change_candidate(q, c, conditions={"access_available": False})
    result = evaluate_routes(q, c)
    assert result["status"] == "computed_diagnostic"
    assert result["pareto_candidate_ids"] == ["detour"]


def test_no_feasible_supplied_candidate_is_explicit():
    q, c = make_case(reviewed=True)
    q, c = amend(q, c, maximum_metrics={"length_m": 1})
    result = evaluate_routes(q, c)
    assert result["status"] == "no_feasible_supplied_candidate"
    assert result["pareto_candidate_ids"] == result["selected_candidate_ids"] == []


def test_unreviewed_geometry_cannot_establish_crossing_exclusion():
    q, c = make_case(reviewed=True)
    q, c = change_candidate(q, c, crossing_counts={"road": 999}, geometry_and_quantities_reviewed=False)
    assert evaluate_routes(q, c)["candidate_results"][0]["status"] == "needs_data"


def test_component_cost_ledger_preserves_quantity_unit_rate():
    q, c = make_case(reviewed=True)
    first = q.candidates[0].cost_items[0].model_copy(update={"quantity": 3, "unit_rate": 7})
    q, c = change_candidate(q, c, cost_items=(first,))
    row = evaluate_routes(q, c)["candidate_results"][0]
    assert row["metrics"]["cost"] == 21
    assert row["cost_ledger"][0]["quantity_unit"] == "lump"


def test_nonrepresentable_cost_is_not_serialized_as_missing_or_feasible():
    q, c = make_case(reviewed=True)
    quote = q.candidates[0].cost_items[0].model_copy(update={"quantity": 1e308, "unit_rate": 1e308})
    q, c = change_candidate(q, c, cost_items=(quote,))
    result = evaluate_routes(q, c)
    assert result["status"] == "out_of_scope" and result["selected_candidate_ids"] is None


def test_duplicate_cost_scope_and_invalid_order_rejected():
    q, c = make_case(reviewed=True)
    with pytest.raises(ValueError):
        change_candidate(q, c, cost_items=(q.candidates[0].cost_items[0],) * 2)
    with pytest.raises(ValueError):
        amend(q, c, selection_mode="lexicographic", selection_order=("cost",))


def test_context_evidence_clock_binding_and_nan_rejected():
    q, c = make_case(reviewed=True)
    for bad in (
        c.model_copy(update={"clock_id": "other"}),
        c.model_copy(update={"metadata": {}}),
        c.model_copy(update={"evidence": ()}),
    ):
        with pytest.raises(ValueError):
            evaluate_routes(q, bad)
    with pytest.raises(ValueError):
        change_candidate(q, c, length_m=float("nan"))


@pytest.mark.parametrize("field", ["pareto", "selection", "cost", "source", "qualification"])
def test_rehashed_mutation_fails_current_replay(field):
    q, c = make_case(reviewed=True, selection_mode="lexicographic")
    record = deepcopy(evaluate_routes(q, c))
    if field == "pareto":
        record["pareto_candidate_ids"] = ["dominated"]
    elif field == "selection":
        record["selected_candidate_ids"] = ["detour"]
    elif field == "cost":
        record["candidate_results"][0]["metrics"]["cost"] = 0
    elif field == "source":
        record["implementation_sha256"] = "0" * 64
    else:
        record["global_terrain_optimum"] = True
    record["record_digest"] = digest({k: v for k, v in record.items() if k != "record_digest"})
    with pytest.raises(ValueError):
        verify_route_record(record, request=q, context=c)
