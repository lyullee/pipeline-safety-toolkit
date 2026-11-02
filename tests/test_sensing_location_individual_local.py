"""Independent electrical-loop and signal travel-time references."""

import pytest
from pydantic import ValidationError

from pipeline_integrity.sensing_location import (
    ResistiveCableRequest, TimeOfFlightRequest,
    locate_resistive_cable, locate_time_of_flight,
)


@pytest.mark.parametrize("length,current,coefficient,series", [
    (0.0, 0.2, 0.003, 0.0), (25.0, 0.5, 0.002, 0.7),
    (230.0, 0.02, 0.015, 1.2), (1000.0, 0.1, 0.04, 0.5),
])
def test_resistance_location_inverts_independent_two_wire_network(length, current, coefficient, series):
    outgoing = coefficient * length
    returning = coefficient * length
    voltage = current * (outgoing + returning + series)
    q = ResistiveCableRequest(voltage_v=voltage, current_a=current,
        resistance_per_length_ohm_m=coefficient, resistance_basis="single_conductor",
        fixed_series_resistance_ohm=series)
    result = locate_resistive_cable(q)
    assert result.position_m == pytest.approx(length, rel=2e-14, abs=1e-12)
    loop = q.model_copy(update={"resistance_basis": "complete_loop",
                                "resistance_per_length_ohm_m": 2 * coefficient})
    assert locate_resistive_cable(loop).position_m == pytest.approx(result.position_m)
    assert result.within_declared_cable is None
    assert result.uncertainty_interval_m is None
    assert result.uncertainty_interpretation == "not_computed"
    assert result.leak_presence_inferred is False


@pytest.mark.parametrize("length,speed,latency", [
    (0.0, 2e8, 0.0), (40.0, 1.8e8, 2e-8), (300.0, 2.1e8, 1e-7),
])
def test_time_of_flight_distinguishes_one_way_from_round_trip(length, speed, latency):
    outward_time = length / speed
    q = TimeOfFlightRequest(elapsed_time_s=outward_time + outward_time + latency,
        propagation_speed_m_s=speed, timing_basis="round_trip", fixed_latency_s=latency)
    a = locate_time_of_flight(q)
    b = locate_time_of_flight(q.model_copy(update={
        "elapsed_time_s": outward_time + latency, "timing_basis": "one_way"}))
    assert a.position_m == pytest.approx(length, rel=2e-14, abs=1e-12)
    assert b.position_m == pytest.approx(length, rel=2e-14, abs=1e-12)


@pytest.mark.parametrize("length,inside,status", [
    (99.0, False, "outside_declared_cable"), (100.0, True, "computed"),
    (101.0, True, "computed"),
])
def test_position_bound_is_reported_without_clipping(length, inside, status):
    requests = (
        (locate_resistive_cable, ResistiveCableRequest(voltage_v=2.0,current_a=1.0,
         resistance_per_length_ohm_m=0.01,resistance_basis="single_conductor",cable_length_m=length)),
        (locate_time_of_flight, TimeOfFlightRequest(elapsed_time_s=1e-6,
         propagation_speed_m_s=2e8,timing_basis="round_trip",cable_length_m=length)),
    )
    for function, request in requests:
        result = function(request)
        assert result.position_m == pytest.approx(100.0)
        assert result.within_declared_cable is inside
        assert result.status == status


@pytest.mark.parametrize("field", ["voltage_v", "current_a", "resistance_per_length_ohm_m"])
def test_missing_electrical_input_is_not_a_zero_position(field):
    q = ResistiveCableRequest(voltage_v=1.0,current_a=0.1,
        resistance_per_length_ohm_m=0.1,resistance_basis="single_conductor")
    result = locate_resistive_cable(q.model_copy(update={field:None}))
    assert result.status == "needs_data" and result.position_m is None
    assert result.missing_inputs == (field,)


@pytest.mark.parametrize("field", ["elapsed_time_s", "propagation_speed_m_s"])
def test_missing_timing_input_withholds_position(field):
    q=TimeOfFlightRequest(elapsed_time_s=1.0,propagation_speed_m_s=1.0,timing_basis="one_way")
    result=locate_time_of_flight(q.model_copy(update={field:None}))
    assert result.status == "needs_data" and result.position_m is None


def test_declared_offsets_cannot_create_negative_physical_path():
    with pytest.raises(ValueError, match="series resistance"):
        locate_resistive_cable(ResistiveCableRequest(voltage_v=1.0,current_a=1.0,
            resistance_per_length_ohm_m=1.0,resistance_basis="single_conductor",
            fixed_series_resistance_ohm=2.0))
    with pytest.raises(ValueError, match="latency"):
        locate_time_of_flight(TimeOfFlightRequest(elapsed_time_s=1.0,
            propagation_speed_m_s=1.0,timing_basis="one_way",fixed_latency_s=2.0))


@pytest.mark.parametrize("bad", [0.0, -1.0, float("inf"), float("nan"), True])
def test_invalid_positive_inputs_are_revalidated_at_execution_boundary(bad):
    q=TimeOfFlightRequest(elapsed_time_s=1.0,propagation_speed_m_s=1.0,timing_basis="one_way")
    with pytest.raises(ValidationError):
        locate_time_of_flight(q.model_copy(update={"propagation_speed_m_s":bad}))
    r=ResistiveCableRequest(voltage_v=1.0,current_a=1.0,
        resistance_per_length_ohm_m=1.0,resistance_basis="single_conductor")
    with pytest.raises(ValidationError):
        locate_resistive_cable(r.model_copy(update={"current_a":bad}))


def test_overflow_is_not_reported_as_a_position():
    with pytest.raises(ValueError, match="finite"):
        locate_time_of_flight(TimeOfFlightRequest(elapsed_time_s=1e308,
            propagation_speed_m_s=1e308,timing_basis="one_way"))
