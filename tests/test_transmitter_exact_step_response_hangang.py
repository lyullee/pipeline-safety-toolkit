"""Analytic step-response oracle for the native deterministic transmitter."""
import math

import pytest

from pipeline_integrity.contracts import EvidenceRef, RunContext, SignalFrame
from pipeline_integrity.control_exchange import ControlAxis
from pipeline_integrity.instrument_exchange import TransmitterScenario, replay_transmitter


@pytest.mark.parametrize("sample_period_s", (0.125, 0.25, 0.5))
def test_native_transmitter_zero_order_hold_matches_exact_exponential_step(sample_period_s):
    tau_s = 0.75
    initial_value = -0.5
    step_value = 1.25
    sample_count = round(3.0 / sample_period_s) + 1
    source = tuple(
        SignalFrame(
            stream_id="process-pressure",
            clock_id="elapsed",
            time_s=index * sample_period_s,
            values={"pressure": step_value},
            units={"pressure": "Pa"},
            valid={"pressure": True},
            origin="synthetic",
        )
        for index in range(sample_count)
    )
    context = RunContext(
        asset_id="pressure-transmitter",
        scenario_id="step-response",
        clock_id="elapsed",
        origin="synthetic",
        evidence=(
            EvidenceRef(id="source-evidence", citation="Authored constant pressure step", origin="synthetic"),
            EvidenceRef(id="parameter-evidence", citation="Authored transmitter parameters", origin="synthetic"),
        ),
    )
    scenario = TransmitterScenario(
        instrument_id="pressure-transmitter",
        source_stream_id="process-pressure",
        channel="pressure",
        axis=ControlAxis(quantity="pressure", unit="Pa", pressure_reference="absolute", reference_id="synthetic:pressure-datum"),
        lower_range=-10.0,
        upper_range=10.0,
        initial_filtered_value=initial_value,
        time_constant_s=tau_s,
        scale_factor=1.2,
        bias=0.3,
        parameter_evidence_id="parameter-evidence",
        source_evidence_id="source-evidence",
    )
    replay = replay_transmitter(scenario, source, context)
    assert len(replay["packets"]) == sample_count
    for index, packet in enumerate(replay["packets"]):
        elapsed_s = index * sample_period_s
        exact_state = step_value + (initial_value - step_value) * math.exp(-elapsed_s / tau_s)
        reading = packet["reading"]
        assert packet["physical_source_time_s"] == pytest.approx(elapsed_s, abs=1e-12)
        assert reading["filtered_process_value"] == pytest.approx(exact_state, abs=1e-12)
        assert reading["indicated_value"] == pytest.approx(1.2 * exact_state + 0.3, abs=1e-12)
