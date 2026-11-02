"""Synthetic harmonic-motion oracle for calibrated accelerometer traces.

The fixture is authored numerical input, not a measured sensor record.
"""
import math

import pytest

from pipeline_integrity.leak_multisensor_data import (
    build_accelerometer_pair_trace_record,
    parse_accelerometer_csv,
    qualify_accelerometer_pair,
)


def _member(sensor):
    return (
        "Dataset of Leak Simulations in Experimental Testbed Water Network/Accelerometer/"
        f"Branched/Circumferential Crack/BR_CC_0.18 LPS_{sensor}.csv"
    )


def _csv(sensor, displacement_amplitude_m, *, frequency_hz=50.0, sample_rate_hz=1000.0):
    # x=A sin(2 pi f t), so the acceleration oracle is - (2 pi f)^2 x.
    # 400 samples cover exactly 20 cycles without duplicating the endpoint.
    raw_rows = ["Sample,Value"]
    acceleration = []
    for index in range(400):
        time_s = index / sample_rate_hz
        value_m_s2 = -(2.0 * math.pi * frequency_hz) ** 2 * displacement_amplitude_m * math.sin(
            2.0 * math.pi * frequency_hz * time_s
        )
        acceleration.append(value_m_s2)
        raw_rows.append(f"{time_s:.6f},{value_m_s2 / 9.80665:.15g}")  # source values are in g
    raw = ("\n".join(raw_rows) + "\n").encode("ascii")
    mapping = dict(
        delimiter=",", header_rows=1, coordinate_column=0, value_column=1,
        coordinate_scale_to_s=1.0, value_scale_to_m_s2=9.80665,
        value_offset_m_s2=0.0, clock_id="synthetic-clock", sensor_id=sensor,
        maximum_rows=500, mapping_evidence_id=f"mapping-{sensor}",
    )
    return raw, acceleration, parse_accelerometer_csv(raw, **mapping)


def test_calibrated_accelerometer_pair_trace_matches_harmonic_acceleration_oracle():
    raw_a1, expected_a1, record_a1 = _csv("A1", 2.0e-4)
    raw_a2, expected_a2, record_a2 = _csv("A2", 1.0e-4)
    member_a1, member_a2 = _member("A1"), _member("A2")
    qualification = qualify_accelerometer_pair(
        record_a1, raw_a1, member_a1, record_a2, raw_a2, member_a2,
        pairing_evidence_id="synthetic-pair", common_trigger_verified=True,
        sample_alignment_verified=True, unit_mapping_reviewed=True,
    )
    trace_record = build_accelerometer_pair_trace_record(
        qualification, record_a1, raw_a1, member_a1, record_a2, raw_a2, member_a2,
        expected_pairing_evidence_id="synthetic-pair", asset_id="synthetic-rig",
        trace_id_by_sensor={"A1": "trace-a1", "A2": "trace-a2"}, origin="laboratory",
        time_origin_shift_s=0.0, time_origin_evidence_id="authored-time-origin",
        maximum_grid_error_s=1e-12,
    )
    assert not trace_record["interpolation_or_resampling_performed"]
    for sensor, expected, amplitude in (
        ("A1", expected_a1, 2.0e-4),
        ("A2", expected_a2, 1.0e-4),
    ):
        trace = trace_record["traces"][sensor]
        expected_peak = (2.0 * math.pi * 50.0) ** 2 * amplitude
        assert trace["unit"] == "m/s^2"
        assert trace["sample_period_s"] == pytest.approx(0.001, abs=1e-12)
        assert trace["start_time_s"] == 0.0
        assert trace["values"] == pytest.approx(expected, rel=1e-12, abs=1e-11)
        assert max(trace["values"]) == pytest.approx(expected_peak, rel=1e-10)
        assert min(trace["values"]) == pytest.approx(-expected_peak, rel=1e-10)
        assert math.sqrt(sum(value * value for value in trace["values"]) / len(trace["values"])) == pytest.approx(
            expected_peak / math.sqrt(2.0), rel=1e-10
        )
