"""Bounded analytic checks for pressure-wave arrival ordering and timing support."""

import pytest

from examples.pressure_wave import make_case
from pipeline_integrity.pressure_wave import boundary_metadata, evaluate_pressure_wave


def evaluate(q, context):
    context = context.model_copy(update={"metadata": {"pressure_wave_boundary": boundary_metadata(q)}})
    return evaluate_pressure_wave(q, context)


def test_arrival_support_contains_distance_over_speed_reference_and_orders_direction():
    # Synthetic sensor spacing 15 m with declared acoustic speed 1000 m/s gives 15 ms.
    q, context = make_case(reviewed=True, left_index=40, right_index=55)
    record = evaluate(q, context)
    lower, upper = record["arrival_delay_bounds_s"]

    assert lower <= 15.0 / 1000.0 <= upper
    assert upper - lower == pytest.approx(2 * q.left.sample_period_s)
    assert record["first_arrival_receiver"] == "left"
    assert record["conditional_travel_direction"] == "left_to_right"
    assert record["leak_detected"] is None
    assert record["position_m"] is None


def test_twice_the_sample_gap_at_half_the_cadence_preserves_arrival_delay():
    # A 30-sample gap at 0.5 ms represents the same 15 ms travel time as
    # a 15-sample gap at 1 ms; the physical distance/speed ratio is unchanged.
    q, context = make_case(reviewed=True, left_index=40, right_index=70)
    q = q.model_copy(
        update={
            "left": q.left.model_copy(update={"sample_period_s": 0.0005}),
            "right": q.right.model_copy(update={"sample_period_s": 0.0005}),
        }
    )
    record = evaluate(q, context)
    lower, upper = record["arrival_delay_bounds_s"]

    assert lower <= 15.0 / 1000.0 <= upper
    assert upper - lower == pytest.approx(2 * 0.0005)
    assert record["conditional_travel_direction"] == "left_to_right"


def test_reversing_sensor_arrival_order_reverses_conditional_direction():
    q, context = make_case(reviewed=True, left_index=55, right_index=40)
    record = evaluate(q, context)
    lower, upper = record["arrival_delay_bounds_s"]

    assert lower <= -15.0 / 1000.0 <= upper
    assert record["first_arrival_receiver"] == "right"
    assert record["conditional_travel_direction"] == "right_to_left"


def test_consistent_pressure_unit_scaling_preserves_feature_timing():
    q, context = make_case(reviewed=True, left_index=40, right_index=55)
    factor = 3.0
    q = q.model_copy(
        update={
            "left": q.left.model_copy(update={"values": tuple(v * factor for v in q.left.values)}),
            "right": q.right.model_copy(update={"values": tuple(v * factor for v in q.right.values)}),
            "minimum_negative_rate_pa_s": q.minimum_negative_rate_pa_s * factor,
            "minimum_rate_prominence_pa_s": q.minimum_rate_prominence_pa_s * factor,
        }
    )
    record = evaluate(q, context)
    assert record["arrival_delay_bounds_s"] == pytest.approx([0.014, 0.016])
    assert record["conditional_travel_direction"] == "left_to_right"


def test_endpoint_truncated_edge_withholds_and_nonpositive_absolute_pressure_is_out_of_scope():
    q, context = make_case(reviewed=True, left_index=1, right_index=20)
    assert evaluate(q, context)["status"] == "needs_data"

    q, context = make_case(reviewed=True, left_index=40, right_index=55)
    q = q.model_copy(
        update={"left": q.left.model_copy(update={"values": tuple(v - 100001.0 for v in q.left.values)})}
    )
    assert evaluate(q, context)["status"] == "out_of_scope"
