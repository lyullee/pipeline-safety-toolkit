"""Independent straight-path symmetric TOFD depth checks through the owner bridge."""

import math
import runpy

import pytest

from pipeline_integrity.inspection_statistics import evaluate_inspection_statistics


CASES, CONTEXT = runpy.run_path("examples/inspection_statistics.py")["make_cases"]()
BASE = CASES["inspection.tofd_depth"]


def evaluate(*, separation=None, velocity=None, delay=None, wall=None, reference_changes=None):
    p = dict(BASE.parameters)
    p.update(
        {
            key: value
            for key, value in {
                "probe_index_separation_m": separation,
                "longitudinal_sound_speed_m_s": velocity,
                "delay_from_lateral_wave_s": delay,
                "wall_thickness_m": wall,
            }.items()
            if value is not None
        }
    )
    ref = BASE.tofd_reference
    if reference_changes is not None:
        ref = ref.model_copy(update=reference_changes)
    elif delay is not None:
        ref = ref.model_copy(
            update={"diffraction_arrival_s": ref.lateral_arrival_s + delay}
        )
    request = BASE.model_copy(update={"parameters": p, "tofd_reference": ref})
    return evaluate_inspection_statistics(request, CONTEXT)


def exact_depth(separation, velocity, delay):
    increment = velocity * delay / 2.0
    return math.sqrt(increment * (separation + increment))


@pytest.mark.parametrize(
    "separation,velocity,delay,wall",
    [
        (0.03, 6000.0, 1e-6, 0.02),
        (0.05, 5900.0, 2e-6, 0.03),
        (0.01, 6400.0, 0.5e-6, 0.02),
        (0.08, 5800.0, 4e-6, 0.06),
    ],
)
def test_symmetric_straight_path_depth_matches_closed_form(separation, velocity, delay, wall):
    result = evaluate(separation=separation, velocity=velocity, delay=delay, wall=wall)
    depth = exact_depth(separation, velocity, delay)
    assert result["status"] == "computed"
    assert result["values"]["tip_depth_m"] == pytest.approx(depth, rel=2e-12, abs=1e-15)
    assert result["values"]["remaining_backwall_ligament_m"] == pytest.approx(wall - depth, abs=1e-15)
    assert result["native_receipt"]["native"]["payload"]["model_id"] == "inspection.tofd_depth"
    assert result["inspection_or_material_acceptance"] is False


def test_zero_lateral_referenced_delay_places_tip_at_surface():
    result = evaluate(delay=0.0, reference_changes={"diffraction_arrival_s": BASE.tofd_reference.lateral_arrival_s})
    assert result["status"] == "computed"
    assert result["values"]["tip_depth_m"] == pytest.approx(0.0)
    assert result["values"]["remaining_backwall_ligament_m"] == pytest.approx(
        BASE.parameters["wall_thickness_m"]
    )


def test_probe_separation_growth_increases_depth_for_same_time_delay():
    narrow = evaluate(separation=0.02)["values"]["tip_depth_m"]
    wide = evaluate(separation=0.08)["values"]["tip_depth_m"]
    assert wide > narrow
    assert wide == pytest.approx(exact_depth(0.08, 6000.0, 1e-6), rel=2e-12)


def test_delay_must_equal_original_lateral_reference_pick_difference():
    with pytest.raises(ValueError, match="supplied delay"):
        evaluate(
            delay=2e-6,
            reference_changes={
                "diffraction_arrival_s": BASE.tofd_reference.diffraction_arrival_s
            },
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"lateral_arrival_s": None},
        {"diffraction_arrival_s": 4e-6},
        {"diffraction_frame_id": "another_frame"},
    ],
)
def test_unusable_or_noncausal_frame_reference_is_withheld(changes):
    result = evaluate(reference_changes=changes)
    assert result["status"] == "needs_data"
    assert result["native_receipt"] is None


def test_depth_exceeding_declared_wall_is_out_of_scope():
    result = evaluate(separation=0.03, velocity=6000.0, delay=100e-6, wall=0.01)
    assert result["status"] == "out_of_scope"
    assert result["values"]["tip_depth_m"] > 0.01
