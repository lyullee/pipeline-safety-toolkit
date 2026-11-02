"""Bounded analytic qualification of transmitter lag and declared fault timing."""

import math

import pytest
from lh2dt import instrumentation

from pipeline_integrity.contracts import EvidenceRef, RunContext, SignalFrame
from pipeline_integrity.control_exchange import ControlAxis
from pipeline_integrity.instrument_exchange import FaultWindow, TransmitterScenario, replay_transmitter


@pytest.mark.parametrize("tau,values,times", [
    (0.5, (1.0, 3.0, 2.0), (0.0, 0.25, 1.5)),
    (2.0, (0.0, 4.0, 1.0), (0.0, 1.0, 3.5)),
    (10.0, (2.0, 5.0, 5.0), (0.0, 0.1, 5.0)),
])
def test_native_transmitter_matches_exact_zero_order_hold_oracle(tau, values, times):
    axis = ControlAxis(quantity="level", unit="m", reference_id="tank-bottom")
    context = RunContext(
        asset_id="tank-level-transmitter", scenario_id="analytic-lag", clock_id="physical-time",
        origin="synthetic", evidence=(
            EvidenceRef(id="parameters", citation="Authored synthetic model inputs", origin="synthetic", sha256="a" * 64),
            EvidenceRef(id="source", citation="Authored synthetic signal", origin="synthetic", sha256="b" * 64),
        ),
    )
    scenario = TransmitterScenario(
        instrument_id=context.asset_id, source_stream_id="truth", channel="level", axis=axis,
        lower_range=0.0, upper_range=10.0, initial_filtered_value=values[0],
        time_constant_s=tau, parameter_evidence_id="parameters", source_evidence_id="source",
    )
    frames = tuple(SignalFrame(
        stream_id="truth", clock_id=context.clock_id, time_s=t, values={"level": v},
        units={"level": "m"}, valid={"level": True}, origin="synthetic",
    ) for t, v in zip(times, values, strict=True))

    result = replay_transmitter(scenario, frames, context, native_module=instrumentation)
    expected = values[0]
    for index, packet in enumerate(result["packets"]):
        if index:
            # The input is held from the previous source sample over this interval.
            previous_index = index - 1
            dt = times[index] - times[previous_index]
            expected = values[previous_index] + (expected - values[previous_index]) * math.exp(-dt / tau)
        reading = packet["reading"]
        assert reading["filtered_process_value"] == pytest.approx(expected, rel=2e-14, abs=2e-14)
        assert reading["indicated_value"] == pytest.approx(expected, rel=2e-14, abs=2e-14)


def test_communication_dropout_masks_report_only_and_preserves_lag_state():
    axis = ControlAxis(quantity="level", unit="m", reference_id="tank-bottom")
    context = RunContext(
        asset_id="tank-level-transmitter", scenario_id="dropout-boundary", clock_id="physical-time",
        origin="synthetic", evidence=(
            EvidenceRef(id="parameters", citation="Authored synthetic model inputs", origin="synthetic", sha256="a" * 64),
            EvidenceRef(id="source", citation="Authored synthetic signal", origin="synthetic", sha256="b" * 64),
        ),
    )
    scenario = TransmitterScenario(
        instrument_id=context.asset_id, source_stream_id="truth", channel="level", axis=axis,
        lower_range=0.0, upper_range=10.0, initial_filtered_value=1.0, time_constant_s=2.0,
        dropouts=(FaultWindow(start_s=1.0, end_s=2.0),),
        parameter_evidence_id="parameters", source_evidence_id="source",
    )
    frames = tuple(SignalFrame(
        stream_id="truth", clock_id=context.clock_id, time_s=float(i), values={"level": value},
        units={"level": "m"}, valid={"level": True}, origin="synthetic",
    ) for i, value in enumerate((1.0, 3.0, 5.0)))
    result = replay_transmitter(scenario, frames, context, native_module=instrumentation)
    p0, p1, p2 = result["packets"]
    expected_p2 = 3.0 + (1.0 - 3.0) * math.exp(-0.5)
    assert p1["frame"]["valid"]["level"] is False
    assert p2["frame"]["valid"]["level"] is True
    assert p2["reading"]["filtered_process_value"] == pytest.approx(expected_p2, rel=2e-14)
    assert p0["physical_source_time_s"] == 0.0 and p2["physical_source_time_s"] == 2.0
