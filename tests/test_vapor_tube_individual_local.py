"""Independent passive parcel motion and explicit sampling-delay tests."""

import pytest
from pydantic import ValidationError

from pipeline_integrity.vapor_tube import (
    VaporTubeDelayRequest, VaporTubeLocationRequest,
    evaluate_vapor_tube_delay, locate_vapor_tube_parcel,
)


def delay(**updates):
    fields=dict(distance_to_detector_m=30.0,air_speed_m_s=2.0,
        operation="continuous",constant_speed_passive_parcel_and_delay_origin_reviewed=True)
    fields.update(updates)
    return VaporTubeDelayRequest(**fields)


@pytest.mark.parametrize("distance,speed,expected", [(0.0,2.0,0.0),(30.0,2.0,15.0),(120.0,3.0,40.0)])
def test_continuous_advection_matches_independent_constant_velocity_motion(distance,speed,expected):
    q=delay().model_copy(update={"distance_to_detector_m":distance,"air_speed_m_s":speed})
    r=evaluate_vapor_tube_delay(q)
    assert r.status == "computed"
    assert r.parcel_arrival_delay_s == pytest.approx(expected)
    assert speed * r.transit_time_s == pytest.approx(distance)
    assert r.waiting_time_s == 0.0
    assert not r.uncertainty_computed and not r.leak_presence_inferred


@pytest.mark.parametrize("wait", [0.0,7.0,40.0])
def test_single_purge_uses_supplied_residual_wait_once(wait):
    q=delay().model_copy(update={"operation":"single_purge","waiting_time_to_airflow_s":wait,
                                "uninterrupted_purge_duration_s":15.0})
    r=evaluate_vapor_tube_delay(q)
    assert r.status == "computed" and r.parcel_arrival_delay_s == pytest.approx(15.0 + wait)


@pytest.mark.parametrize("field", ["waiting_time_to_airflow_s","uninterrupted_purge_duration_s"])
def test_unknown_purge_phase_or_window_is_not_guessed(field):
    q=delay().model_copy(update={"operation":"single_purge","waiting_time_to_airflow_s":1.0,
        "uninterrupted_purge_duration_s":20.0,field:None})
    r=evaluate_vapor_tube_delay(q)
    assert r.status == "needs_data" and r.parcel_arrival_delay_s is None


def test_insufficient_purge_does_not_invent_later_cycle_transport():
    q=delay().model_copy(update={"operation":"single_purge","waiting_time_to_airflow_s":4.0,
                                "uninterrupted_purge_duration_s":14.0})
    r=evaluate_vapor_tube_delay(q)
    assert r.status == "purge_too_short" and r.transit_time_s == 15.0
    assert r.parcel_arrival_delay_s is None


@pytest.mark.parametrize("position", [0.0,25.0,80.0])
def test_parcel_location_has_one_way_airflow_start_clock_and_latency(position):
    speed=2.5; latency=0.7
    q=VaporTubeLocationRequest(elapsed_since_airflow_start_s=position/speed+latency,
        air_speed_m_s=speed,fixed_detector_latency_s=latency,
        parcel_present_at_airflow_start_reviewed=True,
        constant_speed_uninterrupted_advection_reviewed=True)
    r=locate_vapor_tube_parcel(q)
    assert r.status == "computed" and r.distance_to_detector_m == pytest.approx(position)


def test_unreviewed_entry_origin_is_withheld_in_both_directions():
    assert evaluate_vapor_tube_delay(delay().model_copy(update={
        "constant_speed_passive_parcel_and_delay_origin_reviewed":False})).status == "needs_data"
    q=VaporTubeLocationRequest(elapsed_since_airflow_start_s=10.0,air_speed_m_s=2.0,
        constant_speed_uninterrupted_advection_reviewed=True)
    assert locate_vapor_tube_parcel(q).status == "needs_data"


def test_outside_tube_and_missing_inputs_are_not_clipped_or_zeroed():
    r=evaluate_vapor_tube_delay(delay().model_copy(update={"tube_length_m":29.0}))
    assert r.status == "outside_declared_tube" and r.distance_to_detector_m == 30.0
    assert r.parcel_arrival_delay_s is None
    assert evaluate_vapor_tube_delay(delay().model_copy(update={"distance_to_detector_m":None})).status == "needs_data"


def test_location_cable_bound_and_negative_corrected_clock_remain_explicit():
    q=VaporTubeLocationRequest(elapsed_since_airflow_start_s=10.0,air_speed_m_s=2.0,
        tube_length_m=19.0,parcel_present_at_airflow_start_reviewed=True,
        constant_speed_uninterrupted_advection_reviewed=True)
    r=locate_vapor_tube_parcel(q)
    assert r.status == "outside_declared_tube" and r.distance_to_detector_m == 20.0
    with pytest.raises(ValueError,match="latency"):
        locate_vapor_tube_parcel(q.model_copy(update={"fixed_detector_latency_s":11.0}))


@pytest.mark.parametrize("bad", [0.0,-1.0,float("nan"),float("inf"),True])
def test_invalid_speed_revalidates_modified_requests(bad):
    with pytest.raises(ValidationError):
        evaluate_vapor_tube_delay(delay().model_copy(update={"air_speed_m_s":bad}))


def test_continuous_mode_cannot_hide_purge_wait_or_duration():
    with pytest.raises(ValueError,match="waiting"):
        evaluate_vapor_tube_delay(delay().model_copy(update={"waiting_time_to_airflow_s":1.0}))
    with pytest.raises(ValueError,match="finite purge"):
        evaluate_vapor_tube_delay(delay().model_copy(update={"uninterrupted_purge_duration_s":15.0}))
