"""Prepared for HPC: local information oracles, shared constraints and tampering.

This file was authored without running pytest in the coding session.
"""

import math
from copy import deepcopy

import pytest

from examples.sensor_placement import make_case
from pipeline_integrity.contracts import digest
from pipeline_integrity.scheduling import (
    CapacityWindow,
    PlannedOperation,
    ScheduleCandidate,
    audit_schedule,
    ticks,
)
from pipeline_integrity.sensor_placement import (
    SensorPlacementRequest,
    boundary_metadata,
    plan_sensor_placement,
    verify_sensor_placement_record,
)


class FiniteModeOracle:
    """Test-only independent enumeration passed through the public schedule audit."""

    def __init__(self):
        self.calls = 0

    def solve(self, spec):
        self.calls += 1
        feasible = []
        assert len(spec.operations) == 1
        operation = spec.operations[0]
        for mode in operation.modes:
            start = ticks(mode.earliest_start_s, spec.time_quantum_s)
            row = PlannedOperation(
                operation_id=operation.id,
                mode_id=mode.id,
                start_tick=start,
                finish_tick=start + ticks(mode.duration_s, spec.time_quantum_s),
            )
            audit = audit_schedule(spec, (row,))
            if audit["valid"]:
                feasible.append((audit["objective"], row))
        provider = {
            "id": "authored finite-mode scheduling oracle",
            "version": "1",
            "wall_time_s": float(self.calls),
        }
        if not feasible:
            return ScheduleCandidate(
                specification_digest=digest(spec),
                native_status="INFEASIBLE",
                best_bound=0.0,
                provider=provider,
            )
        objective, row = min(feasible, key=lambda v: (v[0], v[1].mode_id))
        return ScheduleCandidate(
            specification_digest=digest(spec),
            native_status="OPTIMAL",
            operations=(row,),
            objective=float(objective),
            best_bound=float(objective),
            provider=provider,
        )


def amend(q, c, **changes):
    q = SensorPlacementRequest.model_validate(q.model_copy(update=changes).model_dump(mode="python"))
    c = c.model_copy(update={"metadata": {"sensor_placement_boundary": boundary_metadata(q)}})
    return q, c


def test_default_does_not_compute_or_import_optional_design_solver(monkeypatch):
    import pipeline_integrity.sensor_placement as module

    monkeypatch.setattr(module, "_analyse", lambda q: pytest.fail("unreviewed design computed"))
    q, c = make_case()
    r = plan_sensor_placement(q, c)
    assert r["status"] == "needs_data" and not r["native_information_called"] and not r["scheduler_called"]
    assert r["runtime"] is None and r["selected"] is None
    assert verify_sensor_placement_record(r, request=q, context=c)


@pytest.mark.parametrize("criterion", ["D_opt", "A_opt", "E_opt"])
def test_cross_sensor_correlation_changes_the_preferred_pair(criterion):
    q, c = make_case(reviewed=True)
    q, c = amend(q, c, criterion=criterion)
    backend = FiniteModeOracle()
    r = plan_sensor_placement(q, c, backend=backend)
    assert r["status"] == "optimal" and r["global_design_proven_optimal"]
    assert set(r["selected"]["sensor_ids"]) == {"a", "c"}
    information = r["selected"]["diagnostic"]["normalized_information"][0][0]
    assert information == pytest.approx(1.25, rel=1e-12)
    ab = next(v for v in r["subsets"] if set(v["sensor_ids"]) == {"a", "b"})
    assert ab["diagnostic"]["normalized_information"][0][0] == pytest.approx(2 / 1.9, rel=1e-12)
    assert r["selected"]["diagnostic"]["inverse_information_normalized_coordinates"][0][0] == pytest.approx(
        0.8
    )
    assert r["detection_probability_calculated"] is False and r["field_qualified"] is False
    assert "wall_time_s" not in r["schedule_semantics"]["native"]["provider"]
    assert verify_sensor_placement_record(r, request=q, context=c, backend=backend)


def test_scale_is_explicit_in_information_not_a_new_noise_assumption():
    q, c = make_case(reviewed=True)
    before = plan_sensor_placement(q, c, backend=FiniteModeOracle())
    q, c = amend(q, c, parameters=(q.parameters[0].model_copy(update={"reference_scale": 0.002}),))
    after = plan_sensor_placement(q, c, backend=FiniteModeOracle())
    assert after["selected"]["diagnostic"]["normalized_information"][0][0] == pytest.approx(
        4 * before["selected"]["diagnostic"]["normalized_information"][0][0]
    )
    assert after["selected"]["loss"] == pytest.approx(before["selected"]["loss"] - 2 * math.log(2))


def test_budget_and_required_sensor_are_shared_scheduler_constraints():
    q, c = make_case(reviewed=True)
    q, c = amend(q, c, candidates=tuple(s.model_copy(update={"required": s.id == "b"}) for s in q.candidates))
    r = plan_sensor_placement(q, c, backend=FiniteModeOracle())
    assert set(r["selected"]["sensor_ids"]) == {"b", "c"}
    q, c = amend(q, c, budgets={"capital_count": 1})
    r = plan_sensor_placement(q, c, backend=FiniteModeOracle())
    assert r["selected"]["sensor_ids"] == ["b"]


def test_resource_calendar_blocks_a_nominally_good_design():
    q, c = make_case(reviewed=True)
    resource = q.resources[0].model_copy(
        update={"calendar": (CapacityWindow(start_s=0.0, finish_s=10.0, capacity=0),)}
    )
    q, c = amend(q, c, resources=(resource,))
    r = plan_sensor_placement(q, c, backend=FiniteModeOracle())
    assert r["status"] == "infeasible" and r["selected"] is None and not r["global_design_proven_optimal"]


def test_partial_cross_covariance_does_not_become_independence_or_global_optimum():
    q, c = make_case(reviewed=True)
    covariance = ((10000.0, None, 0.0), (None, 10000.0, 0.0), (0.0, 0.0, 40000.0))
    q, c = amend(q, c, noise=q.noise.model_copy(update={"covariance": covariance}))
    r = plan_sensor_placement(q, c, backend=FiniteModeOracle())
    assert r["status"] == "needs_data" and r["selected"] is not None
    assert not r["joint_noise_completion_qualified"] and not r["global_design_proven_optimal"]
    assert next(v for v in r["subsets"] if set(v["sensor_ids"]) == {"a", "b"})["status"] == "needs_data"


def test_unknown_quote_keeps_qualified_menu_but_withholds_optimality():
    q, c = make_case(reviewed=True)
    q, c = amend(
        q,
        c,
        candidates=tuple(
            s.model_copy(update={"budget_use": {"capital_count": None}}) if s.id == "c" else s
            for s in q.candidates
        ),
    )
    r = plan_sensor_placement(q, c, backend=FiniteModeOracle())
    assert r["status"] == "needs_data" and not r["global_design_proven_optimal"]
    assert r["selected"] is not None and set(r["selected"]["sensor_ids"]) == {"a", "b"}


def test_reviewed_menu_scope_is_not_full_candidate_optimality():
    q, c = make_case(reviewed=True)
    q, c = amend(q, c, menus=(("a", "c"),))
    r = plan_sensor_placement(q, c, backend=FiniteModeOracle())
    assert r["status"] == "optimal" and r["finite_menu_proven_optimal"]
    assert not r["global_design_proven_optimal"] and r["global_scope"] == "caller supplied finite menus only"


def test_known_indefinite_joint_noise_is_rejected_without_repair():
    q, c = make_case(reviewed=True)
    covariance = ((10000.0, 20000.0, 0.0), (20000.0, 10000.0, 0.0), (0.0, 0.0, 40000.0))
    q, c = amend(q, c, noise=q.noise.model_copy(update={"covariance": covariance}))
    r = plan_sensor_placement(q, c, backend=FiniteModeOracle())
    assert r["status"] == "out_of_scope" and r["selected"] is None and not r["scheduler_called"]


def test_unobservable_parameter_is_not_regularized_into_a_design():
    q, c = make_case(reviewed=True)
    parameter = q.parameters[0].model_copy(update={"id": "second_parameter"})
    model = q.linearization.model_copy(
        update={"parameter_ids": ("leak_rate", "second_parameter"), "jacobian": ((100000.0, 0.0),) * 3}
    )
    q, c = amend(q, c, parameters=(*q.parameters, parameter), linearization=model)
    r = plan_sensor_placement(q, c, backend=FiniteModeOracle())
    assert r["status"] == "infeasible" and r["selected"] is None
    assert all(v["status"] == "rank_deficient" for v in r["subsets"])


@pytest.mark.parametrize("field", ["linearization", "noise", "parameter_scale", "window", "geometry"])
def test_missing_design_data_does_not_run_native_information(field):
    q, c = make_case(reviewed=True)
    if field == "linearization":
        change = {"linearization": None}
    elif field == "noise":
        change = {"noise": None}
    elif field == "parameter_scale":
        change = {"parameters": (q.parameters[0].model_copy(update={"reference_scale": None}),)}
    elif field == "window":
        change = {"observation_start_s": None}
    else:
        change = {
            "candidates": (
                q.candidates[0].model_copy(update={"position_m": None, "physical_port_id": None}),
                *q.candidates[1:],
            )
        }
    q, c = amend(q, c, **change)
    r = plan_sensor_placement(q, c, backend=FiniteModeOracle())
    assert r["status"] == "needs_data" and not r["native_information_called"]


def test_decimal_time_grid_uses_shared_ticks_without_binary_sum_drift():
    q, c = make_case(reviewed=True)
    q, c = amend(q, c, observation_start_s=0.1, observation_duration_s=0.2, time_quantum_s=0.1)
    r = plan_sensor_placement(q, c, backend=FiniteModeOracle())
    assert r["status"] == "optimal" and r["schedule_request"]["horizon_s"] == 0.3
    assert r["schedule_semantics"]["timeline"][0]["finish_tick"] == 3


@pytest.mark.parametrize("field", ["asset", "clock", "boundary", "evidence", "nonfinite"])
def test_cloned_invalid_context_or_input_is_revalidated(field):
    q, c = make_case(reviewed=True)
    if field == "asset":
        c = c.model_copy(update={"asset_id": "wrong"})
    elif field == "clock":
        c = c.model_copy(update={"clock_id": "wrong"})
    elif field == "boundary":
        c = c.model_copy(update={"metadata": {}})
    elif field == "evidence":
        c = c.model_copy(update={"evidence": ()})
    else:
        q = q.model_copy(
            update={"linearization": q.linearization.model_copy(update={"jacobian": ((float("nan"),),) * 3})}
        )
    with pytest.raises(ValueError):
        plan_sensor_placement(q, c, backend=FiniteModeOracle())


@pytest.mark.parametrize("change", ["loss", "selection", "information", "noise", "qualification", "source"])
def test_rehashed_design_mutations_refused(change):
    q, c = make_case(reviewed=True)
    backend = FiniteModeOracle()
    r = deepcopy(plan_sensor_placement(q, c, backend=backend))
    if change == "loss":
        r["selected"]["loss"] += 1.0
    elif change == "selection":
        r["selected"]["sensor_ids"] = ["a", "b"]
    elif change == "information":
        r["selected"]["diagnostic"]["normalized_information"][0][0] *= 2
    elif change == "noise":
        r["request"]["noise"]["covariance"][0][1] = 0.0
        r["request"]["noise"]["covariance"][1][0] = 0.0
    elif change == "qualification":
        r["field_qualified"] = True
    else:
        r["implementation_sha256"] = "0" * 64
    r["record_digest"] = digest({k: v for k, v in r.items() if k != "record_digest"})
    with pytest.raises(ValueError):
        verify_sensor_placement_record(r, request=q, context=c, backend=backend)


def test_actual_cp_sat_matches_the_finite_mode_oracle():
    pytest.importorskip("ortools.sat.python.cp_model")
    q, c = make_case(reviewed=True)
    actual = plan_sensor_placement(q, c)
    reference = plan_sensor_placement(q, c, backend=FiniteModeOracle())
    assert (
        actual["status"] == "optimal"
        and actual["selected"]["sensor_ids"] == reference["selected"]["sensor_ids"]
    )
    assert actual["selected"]["loss"] == pytest.approx(reference["selected"]["loss"])
    assert actual["schedule_semantics"]["native"]["provider"]["id"] == "OR-Tools CP-SAT"
    assert verify_sensor_placement_record(actual, request=q, context=c)
