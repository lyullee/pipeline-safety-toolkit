"""Independent rigid-body balances and supplied-domain/receipt counterexamples."""

import copy
import math

import numpy as np
import pytest

from examples.lifting import make_case
from pipeline_integrity.contracts import digest
from pipeline_integrity.lifting import (
    EffectiveLiftingCapacity,
    ExternalLiftingLoad,
    LiftingRequest,
    Sling,
    boundary_metadata,
    evaluate_lifting,
    to_signal_frame,
    verify_lifting_record,
)


def case(**changes):
    request, context = make_case(reviewed=True)
    request = LiftingRequest.model_validate(request.model_copy(update=changes).model_dump(mode="python"))
    context = context.model_copy(update={"metadata": {"lifting_boundary": boundary_metadata(request)}})
    return request, context


def capacities_for(slings):
    targets = {("sling", s.id) for s in slings}
    targets.update(("attachment", s.attachment_id) for s in slings)
    targets.update(("anchor", s.anchor_id) for s in slings)
    return tuple(
        EffectiveLiftingCapacity(
            target_kind=k,
            target_id=n,
            force_limit_n=20000.0,
            direction_policy="all_directions",
            applicability_id="authored_case",
            evidence_id="authored",
            reviewed=True,
        )
        for k, n in sorted(targets)
    )


def test_default_does_not_call_solver(monkeypatch):
    def forbidden(*a, **k):
        raise AssertionError("unreviewed examples cannot solve")

    monkeypatch.setattr(np.linalg, "lstsq", forbidden)
    request, context = make_case()
    record = evaluate_lifting(request, context)
    assert record["status"] == "needs_data"
    assert not record["native_solver_called"]
    assert record["solution"] is None
    assert verify_lifting_record(record, request=request, context=context)


def test_independent_two_leg_balance_shared_hook_and_lugs():
    request, context = case()
    result = evaluate_lifting(request, context)
    assert result["status"] == "computed"
    solution = result["solution"]
    weight = 1000 * 9.80665
    assert solution["sling_tensions_n"] == pytest.approx(
        {"left": weight / math.sqrt(2), "right": weight / math.sqrt(2)}
    )
    checks = {(c["target_kind"], c["target_id"]): c for c in result["capacity_checks"]}
    assert len(checks) == 5
    assert checks[("anchor", "shared_hook")]["force_vector_n"] == pytest.approx([0, 0, -weight], abs=1e-10)
    assert checks[("anchor", "shared_hook")]["force_n"] == pytest.approx(weight)
    assert solution["net_force_n"] == pytest.approx([0, 0, 0], abs=1e-10)
    assert solution["net_moment_nm"] == pytest.approx([0, 0, 0], abs=1e-10)
    assert result["acceptance"] == "within_supplied_limits"
    assert not result["configuration_stability_solved"] and not result["dynamic_load_history_solved"]
    assert verify_lifting_record(result, request=request, context=context)


def test_three_vertical_legs_noncollinear_independent_torque():
    slings = tuple(
        Sling(
            id=name,
            attachment_id=name,
            attachment_m=point,
            anchor_id="a_" + name,
            anchor_m=(point[0], point[1], 3.0),
            geometry_evidence_id="authored",
        )
        for name, point in (("one", (0.0, 0.0, 0.0)), ("two", (2.0, 0.0, 0.0)), ("three", (0.0, 3.0, 0.0)))
    )
    request, context = case(
        slings=slings, capacities=capacities_for(slings), center_of_mass_m=(0.4, 0.9, 0.0)
    )
    result = evaluate_lifting(request, context)
    weight = request.mass_kg * 9.80665
    assert result["status"] == "computed"
    assert result["solution"]["sling_tensions_n"] == pytest.approx(
        {"one": 0.5 * weight, "two": 0.2 * weight, "three": 0.3 * weight}
    )


def test_explicit_couple_unequal_vertical_tensions():
    slings = tuple(
        Sling(
            id=str(x),
            attachment_id=str(x),
            attachment_m=(x, 0.0, 0.0),
            anchor_id="a" + str(x),
            anchor_m=(x, 0.0, 1.0),
            geometry_evidence_id="authored",
        )
        for x in (-1.0, 1.0)
    )
    load = ExternalLiftingLoad(
        id="couple",
        position_m=(0.0, 0.0, 0.0),
        force_n=(0.0, 0.0, 0.0),
        moment_nm=(0.0, 1000.0, 0.0),
        evidence_id="authored",
    )
    request, context = case(slings=slings, capacities=capacities_for(slings), external_loads=(load,))
    result = evaluate_lifting(request, context)
    weight = request.mass_kg * 9.80665
    assert result["solution"]["sling_tensions_n"] == pytest.approx(
        {"-1.0": (weight - 1000) / 2, "1.0": (weight + 1000) / 2}
    )


@pytest.mark.parametrize("angle", [0.0, 0.4, 1.5, math.pi])
def test_rigid_coordinate_rotation_preserves_forces(angle):
    request, _ = case()
    rotation = np.asarray(
        [[math.cos(angle), 0.0, math.sin(angle)], [0.0, 1.0, 0.0], [-math.sin(angle), 0.0, math.cos(angle)]]
    )

    def rotate(point):
        return tuple(float(v) for v in rotation @ point)

    slings = tuple(
        s.model_copy(update={"attachment_m": rotate(s.attachment_m), "anchor_m": rotate(s.anchor_m)})
        for s in request.slings
    )
    request, context = case(
        slings=slings,
        gravity_m_s2=rotate(request.gravity_m_s2),
        center_of_mass_m=rotate(request.center_of_mass_m),
    )
    result = evaluate_lifting(request, context)
    assert result["status"] == "computed"
    assert list(result["solution"]["sling_tensions_n"].values()) == pytest.approx(
        [1000 * 9.80665 / math.sqrt(2)] * 2
    )


@pytest.mark.parametrize("shift", [(2.0, -3.0, 9.0), (1e6, 1e6, -1e6)])
def test_translated_origin_and_reference_preserve_result(shift):
    original, _ = case()

    def move(point):
        return tuple(a + b for a, b in zip(point, shift, strict=True))

    slings = tuple(
        s.model_copy(update={"attachment_m": move(s.attachment_m), "anchor_m": move(s.anchor_m)})
        for s in original.slings
    )
    request, context = case(
        slings=slings,
        center_of_mass_m=move(original.center_of_mass_m),
        moment_reference_m=move(original.moment_reference_m),
    )
    assert evaluate_lifting(request, context)["solution"]["sling_tensions_n"] == pytest.approx(
        {"left": 1000 * 9.80665 / math.sqrt(2), "right": 1000 * 9.80665 / math.sqrt(2)}
    )


@pytest.mark.parametrize("scale", [0.01, 1.0, 100.0])
def test_row_length_scale_is_not_a_load_change(scale):
    request, context = case(moment_length_scale_m=scale)
    result = evaluate_lifting(request, context)
    assert result["status"] == "computed"
    assert result["solution"]["sling_tensions_n"]["left"] == pytest.approx(1000 * 9.80665 / math.sqrt(2))


def test_capacity_multiplier_is_explicit_not_a_dynamics_model():
    request, context = case(load_multiplier=2.0)
    result = evaluate_lifting(request, context)
    assert result["status"] == "computed"
    assert result["solution"]["sling_tensions_n"]["left"] == pytest.approx(2 * 1000 * 9.80665 / math.sqrt(2))
    assert not result["dynamic_load_history_solved"]


@pytest.mark.parametrize(
    "changes",
    [
        {"mass_kg": None},
        {"geometry_reviewed": False},
        {"complete_free_body_reviewed": False},
        {
            "external_loads": (
                ExternalLiftingLoad(
                    id="unknown", position_m=(0.0, 0.0, 0.0), force_n=None, evidence_id="authored"
                ),
            )
        },
        {
            "external_loads": (
                ExternalLiftingLoad(
                    id="unknown",
                    position_m=(0.0, 0.0, 0.0),
                    force_n=(0.0, 0.0, 0.0),
                    moment_nm=None,
                    evidence_id="authored",
                ),
            )
        },
    ],
)
def test_unknown_inputs_do_not_become_zero_loads(changes):
    request, context = case(**changes)
    result = evaluate_lifting(request, context)
    assert result["status"] == "needs_data" and not result["native_solver_called"]
    assert result["solution"] is None


def test_missing_capacity_preserves_nominal_not_acceptance():
    request, context = case(capacities=())
    result = evaluate_lifting(request, context)
    assert result["status"] == "needs_data"
    assert result["solution"] is not None
    assert result["acceptance"] == "needs_data"
    assert all(c["within_limit"] is None for c in result["capacity_checks"])
    with pytest.raises(ValueError):
        to_signal_frame(result, context)


def test_known_overload_is_not_erased_by_other_missing_capacity():
    request, context = case()
    request, context = case(capacities=(request.capacities[0].model_copy(update={"force_limit_n": 10.0}),))
    result = evaluate_lifting(request, context)
    assert result["status"] == "needs_data" and result["acceptance"] == "exceeds_supplied_limits"
    assert any(c["within_limit"] is False for c in result["capacity_checks"])


def test_all_reviewed_overload_is_computed_failed_screen_not_solver_failure():
    request, context = case(load_multiplier=3.0)
    result = evaluate_lifting(request, context)
    assert result["status"] == "computed" and result["acceptance"] == "exceeds_supplied_limits"
    assert result["solution"] is not None


@pytest.mark.parametrize(
    "rated,angle,expected", [((0.0, 0.0, -1.0), 1e-6, "computed"), ((0.0, 0.0, 1.0), 0.1, "out_of_scope")]
)
def test_anchor_reaction_direction_uses_force_on_hardware(rated, angle, expected):
    request, context = case()
    capacities = tuple(
        c.model_copy(
            update={"direction_policy": "within_cone", "rated_direction": rated, "maximum_angle_rad": angle}
        )
        if c.target_kind == "anchor"
        else c
        for c in request.capacities
    )
    request, context = case(capacities=capacities)
    result = evaluate_lifting(request, context)
    assert result["status"] == expected
    assert result["solution"] is not None


def test_redundant_slings_no_minimum_norm_load_sharing():
    request, context = case()
    slings = (*request.slings, request.slings[0].model_copy(update={"id": "redundant"}))
    request, context = case(slings=slings, capacities=capacities_for(slings))
    result = evaluate_lifting(request, context)
    assert result["status"] == "out_of_scope" and result["solution"] is None
    assert "not uniquely" in result["issues"][0]


def test_tensile_only_refuses_negative_required_force():
    slings = tuple(
        Sling(
            id=str(x),
            attachment_id=str(x),
            attachment_m=(x, 0.0, 0.0),
            anchor_id="a" + str(x),
            anchor_m=(x, 0.0, 1.0),
            geometry_evidence_id="authored",
        )
        for x in (-1.0, 1.0)
    )
    request, context = case(
        slings=slings, capacities=capacities_for(slings), center_of_mass_m=(2.0, 0.0, 0.0)
    )
    result = evaluate_lifting(request, context)
    assert result["status"] == "out_of_scope" and result["solution"] is None


def test_unsatisfiable_force_and_moment_not_lstsq_accepted():
    load = ExternalLiftingLoad(
        id="unbalanced", position_m=(0.0, 0.0, 0.0), force_n=(0.0, 100.0, 0.0), evidence_id="authored"
    )
    request, context = case(external_loads=(load,))
    result = evaluate_lifting(request, context)
    assert result["status"] == "out_of_scope" and result["solution"] is None
    assert "tolerances" in result["issues"][0]


def test_condition_limit_is_enforced():
    request, context = case(maximum_condition_number=1.01)
    result = evaluate_lifting(request, context)
    assert result["status"] == "out_of_scope" and "condition" in result["issues"][0]


def test_exact_vertical_direction_cone_does_not_invent_an_angle_tolerance():
    sling = Sling(
        id="vertical",
        attachment_id="lug",
        attachment_m=(0.0, 0.0, 0.0),
        anchor_id="hook",
        anchor_m=(0.0, 0.0, 1.0),
        geometry_evidence_id="authored",
    )
    capacities = tuple(
        c.model_copy(
            update={
                "direction_policy": "within_cone",
                "rated_direction": (0.0, 0.0, -1.0),
                "maximum_angle_rad": 0.0,
            }
        )
        if c.target_kind == "anchor"
        else c
        for c in capacities_for((sling,))
    )
    request, context = case(slings=(sling,), capacities=capacities)
    result = evaluate_lifting(request, context)
    assert result["status"] == "computed"
    anchor = next(c for c in result["capacity_checks"] if c["target_kind"] == "anchor")
    assert anchor["direction_angle_rad"] == 0
    assert anchor["force_vector_n"] == [0.0, 0.0, -1000 * 9.80665]


def test_all_positive_three_leg_barycentric_reference_under_random_rotation():
    generator = np.random.default_rng(592180)
    points = ((0.0, 0.0, 0.0), (3.0, 0.0, 0.0), (0.0, 2.0, 0.0))
    for _ in range(12):
        fractions = generator.uniform(0.1, 1.0, 3)
        fractions /= fractions.sum()
        rotation, _ = np.linalg.qr(generator.normal(size=(3, 3)))
        if np.linalg.det(rotation) < 0:
            rotation[:, 0] *= -1

        def rotate(point, rotation=rotation):
            return tuple(float(v) for v in rotation @ point)

        slings = tuple(
            Sling(
                id=str(i),
                attachment_id="l" + str(i),
                attachment_m=rotate(p),
                anchor_id="h" + str(i),
                anchor_m=rotate((p[0], p[1], 4.0)),
                geometry_evidence_id="authored",
            )
            for i, p in enumerate(points)
        )
        center = rotate(tuple(float(v) for v in np.asarray(fractions) @ np.asarray(points)))
        request, context = case(
            slings=slings,
            capacities=capacities_for(slings),
            center_of_mass_m=center,
            gravity_m_s2=rotate((0.0, 0.0, -9.80665)),
        )
        record = evaluate_lifting(request, context)
        assert record["status"] == "computed"
        assert record["solution"]["sling_tensions_n"] == pytest.approx(
            {str(i): float(v) * 1000 * 9.80665 for i, v in enumerate(fractions)}
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("mass_kg", math.inf),
        ("mass_kg", math.nan),
        ("mass_kg", True),
        ("load_multiplier", 0.0),
        ("rank_relative_cutoff", 1.0),
    ],
)
def test_cloned_invalid_request_is_strictly_revalidated(field, value):
    request, context = case()
    with pytest.raises(ValueError):
        evaluate_lifting(request.model_copy(update={field: value}), context)


def test_context_boundaries_and_evidence_are_enforced():
    request, context = case()
    for invalid in (
        context.model_copy(update={"asset_id": "wrong"}),
        context.model_copy(update={"clock_id": "wrong"}),
        context.model_copy(update={"metadata": {}}),
        context.model_copy(update={"evidence": ()}),
        context.model_copy(update={"metadata": {"value": math.inf}}),
    ):
        with pytest.raises(ValueError):
            evaluate_lifting(request, invalid)


def test_duplicate_physical_coordinates_and_capacity_targets_refused():
    request, _ = case()
    with pytest.raises(ValueError):
        case(slings=(request.slings[0], request.slings[1].model_copy(update={"anchor_m": (1.0, 0.0, 1.0)})))
    with pytest.raises(ValueError):
        case(capacities=(*request.capacities, request.capacities[0]))
    with pytest.raises(ValueError):
        case(capacities=(request.capacities[0].model_copy(update={"target_id": "unknown"}),))


@pytest.mark.parametrize(
    "mutation",
    [
        "tension",
        "anchor",
        "acceptance",
        "status",
        "runtime",
        "implementation",
        "stability",
        "request_mass",
        "context",
    ],
)
def test_rehashed_receipt_corruptions_refused(mutation):
    request, context = case()
    record = copy.deepcopy(evaluate_lifting(request, context))
    if mutation == "tension":
        record["solution"]["sling_tensions_n"]["left"] *= 2
    elif mutation == "anchor":
        record["capacity_checks"][0]["force_vector_n"][2] *= -1
    elif mutation == "acceptance":
        record["acceptance"] = "approved"
    elif mutation == "status":
        record["status"] = "needs_data"
    elif mutation == "runtime":
        record["runtime"]["numpy"] = "forged"
    elif mutation == "implementation":
        record["implementation_sha256"] = "0" * 64
    elif mutation == "stability":
        record["configuration_stability_solved"] = True
    elif mutation == "request_mass":
        record["request"]["mass_kg"] *= 2
    else:
        record["context"]["scenario_id"] = "forged"
    record["record_digest"] = digest({k: v for k, v in record.items() if k != "record_digest"})
    with pytest.raises(ValueError):
        verify_lifting_record(record, request=request, context=context)


def test_signal_origin_and_time_preserve_computation_semantics():
    request, context = case(time_s=12.0)
    context = context.model_copy(update={"origin": "laboratory"})
    result = evaluate_lifting(request, context)
    frame = to_signal_frame(result, context)
    assert frame.origin == "simulation" and frame.time_s == 12.0
    assert all(v == "N" for v in frame.units.values())
    assert "authored_payload" in frame.stream_id and frame.clock_id == request.clock_id
    assert all(frame.valid.values())
