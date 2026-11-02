"""Prepared for deferred HPC execution; sampled compatibility never proves arrest."""

from copy import deepcopy
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

from pipeline_integrity.contracts import digest
from pipeline_integrity.fracture_arrest import (
    assess_fracture_arrest,
    boundary_metadata,
    trajectory_binding,
    trajectory_payload_sha256,
    verify_fracture_arrest_record,
)

spec = spec_from_file_location("arrest_example", Path(__file__).parents[1] / "examples/fracture_arrest.py")
example = module_from_spec(spec)
spec.loader.exec_module(example)


def rebind(q, c, trajectory, **updates):
    updates["expected_trajectory_payload_sha256"] = trajectory_payload_sha256(trajectory)
    q = type(q).model_validate(q.model_copy(update=updates).model_dump(mode="python"))
    c = c.model_copy(update={"metadata": {"fracture_arrest_boundary": boundary_metadata(q)}})
    trajectory = trajectory.model_copy(update={"evaluation_sha256": trajectory_binding(q, c, trajectory)})
    return q, c, trajectory


def test_reviews_withhold_before_provider():
    q, c, _ = example.make_case()
    r = assess_fracture_arrest(q, c)
    assert r["status"] == "needs_data" and r["trajectory"] is None
    assert verify_fracture_arrest_record(r)


def test_sampled_window_not_continuous_arrest():
    q, c, p = example.make_case(reviewed=True)
    r = assess_fracture_arrest(q, c, trajectory=p)
    assert r["status"] == "computed"
    assert r["point_comparisons"][0]["energy_margin_j_m2"] == -500.0
    assert r["sampled_stationary_compatible_windows"] == [
        {
            "start_point_id": "point_1",
            "end_point_id": "point_3",
            "sample_span_s": 2.0,
            "sample_count": 3,
            "meaning": "criteria hold at provided samples only; unobserved intervals and restart remain unresolved",
        }
    ]
    assert r["dynamic_arrest_proven"] is False and r["continuous_hold_proven"] is False
    assert r["field_or_code_acceptance"] is False and r["failure_probability"] is None
    assert verify_fracture_arrest_record(r)


@pytest.mark.parametrize(
    "field",
    [
        "local_pressure_pa_abs",
        "temperature_k",
        "propagation_velocity_m_s",
        "dynamic_resistance_j_m2",
        "driving_energy_release_j_m2",
    ],
)
def test_unknown_simultaneous_values_not_filled(field):
    q, c, p = example.make_case(reviewed=True)
    p = p.model_copy(update={"points": (p.points[0].model_copy(update={field: None}), *p.points[1:])})
    q, c, p = rebind(q, c, p)
    r = assess_fracture_arrest(q, c, trajectory=p)
    assert r["status"] == "needs_data" and r["point_comparisons"] == []


@pytest.mark.parametrize(
    "field", ["material_orientation_id", "gas_composition_id", "eos_basis_id", "decompression_source_sha256"]
)
def test_unknown_physical_boundary_withholds(field):
    q, c, p = example.make_case(reviewed=True)
    q, c, p = rebind(q, c, p, **{field: None})
    assert assess_fracture_arrest(q, c, trajectory=p)["status"] == "needs_data"


@pytest.mark.parametrize("field,value", [("temperature_k", 350.0), ("crack_path_position_m", 20.0)])
def test_source_outside_reviewed_domain(field, value):
    q, c, p = example.make_case(reviewed=True)
    points = list(p.points)
    points[-1] = points[-1].model_copy(update={field: value})
    p = p.model_copy(update={"points": tuple(points)})
    q, c, p = rebind(q, c, p)
    r = assess_fracture_arrest(q, c, trajectory=p)
    assert r["status"] == "out_of_scope" and r["sampled_stationary_compatible_windows"] == []


def test_large_sampling_gap_breaks_window():
    q, c, p = example.make_case(reviewed=True)
    q, c, p = rebind(q, c, p, maximum_sample_gap_s=0.5)
    assert assess_fracture_arrest(q, c, trajectory=p)["sampled_stationary_compatible_windows"] == []


def test_low_energy_and_pressure_are_insufficient_if_crack_moves():
    q, c, p = example.make_case(reviewed=True)
    p = p.model_copy(
        update={
            "points": tuple(row.model_copy(update={"propagation_velocity_m_s": 10.0}) for row in p.points)
        }
    )
    q, c, p = rebind(q, c, p)
    assert assess_fracture_arrest(q, c, trajectory=p)["sampled_stationary_compatible_windows"] == []


def test_fixed_stationary_velocity_does_not_hide_position_motion():
    q, c, p = example.make_case(reviewed=True)
    p = p.model_copy(
        update={
            "points": tuple(
                row.model_copy(update={"crack_path_position_m": float(i)}) for i, row in enumerate(p.points)
            )
        }
    )
    q, c, p = rebind(q, c, p)
    assert assess_fracture_arrest(q, c, trajectory=p)["sampled_stationary_compatible_windows"] == []


def test_reversed_path_and_nonfinite_copy_rejected():
    q, c, p = example.make_case(reviewed=True)
    p = p.model_copy(
        update={"points": (p.points[0].model_copy(update={"crack_path_position_m": 2.0}), *p.points[1:])}
    )
    q, c, p = rebind(q, c, p)
    with pytest.raises(ValueError):
        assess_fracture_arrest(q, c, trajectory=p)
    with pytest.raises(ValueError):
        rebind(
            q,
            c,
            p.model_copy(
                update={
                    "points": (p.points[0].model_copy(update={"temperature_k": float("nan")}), *p.points[1:])
                }
            ),
        )


@pytest.mark.parametrize(
    "field,value",
    [("dynamic_arrest_proven", True), ("continuous_hold_proven", True), ("point_comparisons", [])],
)
def test_rehashed_qualification_or_scores_tamper(field, value):
    q, c, p = example.make_case(reviewed=True)
    r = deepcopy(assess_fracture_arrest(q, c, trajectory=p))
    r[field] = value
    r["record_sha256"] = digest({k: v for k, v in r.items() if k != "record_sha256"})
    with pytest.raises(ValueError):
        verify_fracture_arrest_record(r)


def test_trajectory_preselection_and_evaluation_binding():
    q, c, p = example.make_case(reviewed=True)
    with pytest.raises(ValueError):
        assess_fracture_arrest(q, c, trajectory=p.model_copy(update={"provider_source_sha256": "f" * 64}))
    with pytest.raises(ValueError):
        assess_fracture_arrest(q, c, trajectory=p.model_copy(update={"evaluation_sha256": "f" * 64}))


def test_point_budget_before_cloning():
    q, c, p = example.make_case(reviewed=True)
    with pytest.raises(ValueError):
        assess_fracture_arrest(q.model_copy(update={"maximum_points": 2}), c, trajectory=p)


def test_original_provider_replayed_instead_of_stored_trajectory():
    q, c, p = example.make_case(reviewed=True)
    q, c, _ = rebind(q, c, p, source_mode="provider")
    calls = []

    def provider(request, context):
        calls.append(digest(request))
        return p.model_copy(update={"evaluation_sha256": trajectory_binding(request, context, p)})

    r = assess_fracture_arrest(q, c, provider=provider)
    assert verify_fracture_arrest_record(r, provider=provider)
    assert len(calls) == 2
    with pytest.raises(ValueError):
        verify_fracture_arrest_record(r)


def test_later_restart_never_becomes_dynamic_arrest_proof():
    q, c, p = example.make_case(reviewed=True)
    restart = p.points[-1].model_copy(
        update={
            "id": "restart",
            "time_s": 4.0,
            "crack_path_position_m": 1.2,
            "propagation_velocity_m_s": 5.0,
            "driving_energy_release_j_m2": 1400.0,
            "local_pressure_pa_abs": 3e6,
        }
    )
    p = p.model_copy(update={"points": (*p.points, restart)})
    q, c, p = rebind(q, c, p, time_limits_s=(0.0, 4.0))
    result = assess_fracture_arrest(q, c, trajectory=p)
    assert result["sampled_stationary_compatible_windows"]
    assert result["point_comparisons"][-1]["sampled_stationary_compatible"] is False
    assert result["dynamic_arrest_proven"] is False and result["continuous_hold_proven"] is False


def test_dynamic_energy_definition_cannot_accept_static_ki():
    q, c, p = example.make_case(reviewed=True)
    with pytest.raises(ValueError):
        assess_fracture_arrest(
            q, c, trajectory=p.model_copy(update={"resistance_definition": "mode_i_initiation"})
        )
    with pytest.raises(ValueError):
        assess_fracture_arrest(
            q, c, trajectory=p.model_copy(update={"new_crack_area_basis_id": "other_area"})
        )
    with pytest.raises(ValueError):
        assess_fracture_arrest(q, c.model_copy(update={"origin": "measured"}), trajectory=p)
