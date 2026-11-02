"""Deferred numerical cases: run on the requested parallel verification environment."""

import builtins
import math
from copy import deepcopy

import pytest

from examples.pulling import make_case
from pipeline_integrity.contracts import digest
from pipeline_integrity.pulling import (
    PullingRequest,
    boundary_metadata,
    evaluate_pulling,
    verify_pulling_record,
)


def amend(q, c, **changes):
    q = PullingRequest.model_validate(q.model_copy(update=changes).model_dump(mode="python"))
    return q, c.model_copy(update={"metadata": {"pulling_boundary": boundary_metadata(q)}})


def test_default_never_imports_native_integration(monkeypatch):
    original = builtins.__import__

    def guarded(name, *args, **kwargs):
        if name.startswith("scipy"):
            pytest.fail("unreviewed guide invoked optional integration")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    q, c = make_case()
    r = evaluate_pulling(q, c)
    assert r["status"] == "needs_data" and not r["native_called"] and r["solution"] is None
    assert verify_pulling_record(r, request=q, context=c)


def test_horizontal_gravity_normal_friction_reference():
    q, c = make_case(reviewed=True)
    r = evaluate_pulling(q, c)
    expected = 1000.0 + 0.2 * 10 * 9.80665 * 100
    assert r["status"] == "computed_diagnostic"
    assert r["solution"]["puller_tension_N"] == pytest.approx(expected, rel=1e-10)
    assert r["solution"]["whole_path_tension_upper_bound_N"] == pytest.approx(expected, rel=1e-12)
    assert r["capacity_checks"]["axial_tension"] == "within_model_upper_bound"
    assert r["solution"]["power_budget"]["friction_dissipation_W"] == pytest.approx((expected - 1000.0) * 0.2)
    assert abs(r["solution"]["power_budget"]["power_residual_W"]) < 1e-7
    assert not r["pipe_bending_rigidity_or_buckling_solved"] and not r["field_qualified"]
    assert verify_pulling_record(r, request=q, context=c)


def test_massless_capstan_closed_form():
    q, c = make_case(reviewed=True, kind="capstan")
    r = evaluate_pulling(q, c)
    expected = 1000.0 * math.exp(0.2 * math.pi / 2)
    assert r["status"] == "computed_diagnostic"
    assert r["solution"]["puller_tension_N"] == pytest.approx(expected, rel=1e-8)
    assert r["solution"]["maximum_geometric_outer_fiber_strain"] == pytest.approx(0.005)
    assert r["solution"]["whole_path_tension_upper_bound_N"] >= expected
    assert r["capacity_checks"]["axial_tension"] == "within_model_upper_bound"


@pytest.mark.parametrize("angle", [-0.3, 0.0, 0.6])
def test_inclined_straight_separate_tangential_and_normal_weight(angle):
    q, c = make_case(reviewed=True)
    tangent = (math.cos(angle), 0.0, math.sin(angle))
    segment = q.segments[0].model_copy(update={"initial_tangent": tangent, "length_m": 10.0})
    q, c = amend(q, c, segments=(segment,))
    r = evaluate_pulling(q, c)
    expected = 1000.0 + 10 * 9.80665 * (0.2 * abs(math.cos(angle)) + math.sin(angle)) * 10
    assert r["solution"]["puller_tension_N"] == pytest.approx(expected, rel=1e-9)
    assert r["solution"]["total_opposed_body_force_N"] == pytest.approx(
        10 * 9.80665 * math.sin(angle) * 10, abs=1e-7
    )


def test_buoyancy_neutral_and_negative_effective_weight_not_clamped():
    q, c = make_case(reviewed=True)
    area = 0.01
    segment = q.segments[0].model_copy(
        update={"immersed_fraction": 1.0, "external_fluid_density_kg_m3": 1000.0}
    )
    q, c = amend(q, c, segments=(segment,), displaced_area_m2=area)
    neutral = evaluate_pulling(q, c)
    assert neutral["solution"]["puller_tension_N"] == pytest.approx(1000.0)
    segment = segment.model_copy(update={"external_fluid_density_kg_m3": 1500.0})
    q, c = amend(q, c, segments=(segment,))
    buoyant = evaluate_pulling(q, c)
    assert buoyant["segment_prefix"][0]["effective_mass_kg_m"] == -5.0
    assert buoyant["solution"]["puller_tension_N"] == pytest.approx(
        1000.0 + 0.2 * 5 * 9.80665 * 100, rel=1e-10
    )


def test_supplied_axial_resistance_and_external_power():
    q, c = make_case(reviewed=True)
    segment = q.segments[0].model_copy(update={"axial_resistance_N_m": 5.0})
    q, c = amend(q, c, segments=(segment,))
    r = evaluate_pulling(q, c)
    assert r["solution"]["total_axial_resistance_N"] == pytest.approx(500.0)
    assert r["solution"]["power_budget"]["axial_resistance_dissipation_W"] == pytest.approx(100.0)
    assert r["solution"]["power_budget"]["puller_input_W"] - r["solution"]["power_budget"][
        "trailing_boundary_output_W"
    ] == pytest.approx(r["solution"]["power_budget"]["friction_dissipation_W"] + 100.0)


@pytest.mark.parametrize(
    "field", ["pipe_mass_kg_m", "contents_mass_kg_m", "displaced_area_m2", "trailing_tension_N"]
)
def test_unknown_global_inputs_withheld_before_native(field):
    q, c = make_case(reviewed=True)
    q, c = amend(q, c, **{field: None})
    r = evaluate_pulling(q, c)
    assert r["status"] == "needs_data" and not r["native_called"] and r["solution"] is None


@pytest.mark.parametrize(
    "field", ["sliding_friction", "immersed_fraction", "external_fluid_density_kg_m3", "axial_resistance_N_m"]
)
def test_unknown_distributed_inputs_not_defaulted(field):
    q, c = make_case(reviewed=True)
    q, c = amend(q, c, segments=(q.segments[0].model_copy(update={field: None}),))
    r = evaluate_pulling(q, c)
    assert r["status"] == "needs_data" and not r["native_called"]


def test_unknown_required_speed_keeps_forces_without_power():
    q, c = make_case(reviewed=True)
    q, c = amend(q, c, pull_speed_m_s=None)
    r = evaluate_pulling(q, c)
    assert r["status"] == "needs_data" and r["solution"]["power_budget"] is None
    q, c = amend(q, c, power_budget_required=False)
    assert evaluate_pulling(q, c)["status"] == "computed_diagnostic"


def test_missing_limits_not_pass_and_known_overload_not_hidden():
    q, c = make_case(reviewed=True)
    q, c = amend(q, c, limits=None)
    r = evaluate_pulling(q, c)
    assert r["status"] == "needs_data" and r["solution"] is not None
    q, c = make_case(reviewed=True)
    q, c = amend(q, c, limits=q.limits.model_copy(update={"effective_axial_tension_limit_N": 1500.0}))
    r = evaluate_pulling(q, c)
    assert (
        r["status"] == "computed_diagnostic"
        and r["capacity_checks"]["axial_tension"] == "exceeds_supplied_limit"
    )


def test_curved_model_upper_bound_not_treated_as_actual_overload():
    q, c = make_case(reviewed=True, kind="capstan")
    segment = q.segments[0].model_copy(update={"sliding_friction": 0.0})
    # A horizontal-plane curved guide has zero tangential weight. The deliberately
    # conservative curved bound includes a positive |w| term without claiming an exact peak.
    q, c = amend(
        q,
        c,
        pipe_mass_kg_m=10.0,
        segments=(segment,),
        limits=q.limits.model_copy(update={"effective_axial_tension_limit_N": 1100.0}),
    )
    r = evaluate_pulling(q, c)
    assert r["solution"]["sampled_tension_peak_N"] == pytest.approx(1000.0)
    assert r["capacity_checks"]["axial_tension"] == "indeterminate_conservative_enclosure"


def test_slack_or_required_compression_refused():
    q, c = make_case(reviewed=True)
    segment = q.segments[0].model_copy(update={"initial_tangent": (0.0, 0.0, -1.0), "sliding_friction": 0.0})
    q, c = amend(q, c, segments=(segment,))
    r = evaluate_pulling(q, c)
    assert r["status"] == "out_of_scope" and r["solution"] is None


@pytest.mark.parametrize("change", ["position", "kink"])
def test_discontinuous_guide_not_implicitly_repaired(change):
    q, c = make_case(reviewed=True)
    next_segment = q.segments[0].model_copy(
        update={
            "id": "second",
            "start_m": (101.0, 0.0, 0.0) if change == "position" else (100.0, 0.0, 0.0),
            "initial_tangent": (0.0, 1.0, 0.0) if change == "kink" else (1.0, 0.0, 0.0),
        }
    )
    q, c = amend(q, c, segments=(*q.segments, next_segment))
    with pytest.raises(ValueError):
        evaluate_pulling(q, c)


@pytest.mark.parametrize(
    "change",
    [
        {"initial_tangent": (2.0, 0.0, 0.0)},
        {"bend_radius_m": 10.0, "initial_curve_normal": (1.0, 0.0, 0.0)},
        {"sliding_friction": -1.0},
    ],
)
def test_invalid_cloned_geometry_and_coefficients_revalidated(change):
    q, c = make_case(reviewed=True)
    q = q.model_copy(update={"segments": (q.segments[0].model_copy(update=change),)})
    with pytest.raises(ValueError):
        evaluate_pulling(q, c)


@pytest.mark.parametrize("target", ["clock", "frame", "evidence", "nan"])
def test_context_and_nonfinite_values_not_relabelled_missing(target):
    q, c = make_case(reviewed=True)
    if target == "clock":
        c = c.model_copy(update={"clock_id": "other"})
    elif target == "frame":
        c = c.model_copy(update={"metadata": {}})
    elif target == "evidence":
        c = c.model_copy(update={"evidence": ()})
    else:
        q = q.model_copy(update={"trailing_tension_N": float("nan")})
    with pytest.raises(ValueError):
        evaluate_pulling(q, c)


@pytest.mark.parametrize("target", ["tension", "budget", "capacity", "source", "qualification", "context"])
def test_rehashed_replay_mutations_refused(target):
    q, c = make_case(reviewed=True)
    r = deepcopy(evaluate_pulling(q, c))
    if target == "tension":
        r["solution"]["puller_tension_N"] *= 2
    elif target == "budget":
        r["solution"]["total_friction_N"] = 0.0
    elif target == "capacity":
        r["capacity_checks"]["axial_tension"] = "approved"
    elif target == "source":
        r["implementation_sha256"] = "0" * 64
    elif target == "qualification":
        r["pipe_bending_rigidity_or_buckling_solved"] = True
    else:
        r["context"]["scenario_id"] = "changed"
    r["record_digest"] = digest({k: v for k, v in r.items() if k != "record_digest"})
    with pytest.raises(ValueError):
        verify_pulling_record(r, request=q, context=c)
