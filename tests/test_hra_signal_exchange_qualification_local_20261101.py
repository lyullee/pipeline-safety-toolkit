"""Bounded contract check for the pipeline-to-HRA signal adapter."""
import pytest
from human_reliability import SignalSeries
from pipeline_integrity.adapters import hra_signal_series
from pipeline_integrity.contracts import SignalFrame


def frame(t, value, *, valid=True, clock="process-clock"):
    return SignalFrame(
        stream_id="pump.pressure",
        clock_id=clock,
        time_s=t,
        values={"pressure": value},
        units={"pressure": "Pa_abs"},
        valid={"pressure": valid},
        origin="simulation",
    )


def test_actual_hra_signal_series_preserves_clock_units_origin_and_missingness():
    frames = (frame(0.0, 101000.0), frame(1.5, None, valid=False), frame(4.0, 102000.0))
    result = hra_signal_series(frames, "pressure")
    assert isinstance(result, SignalSeries)
    assert result.time_s == (0.0, 1.5, 4.0)
    assert result.values == (101000.0, None, 102000.0)
    assert result.valid == (True, False, True)
    assert result.clock_id == "process-clock"
    assert result.unit == "Pa_abs" and result.origin == "simulation"


def test_adapter_requires_present_channel_and_aligned_input_clock():
    frames = (frame(0.0, 1.0), frame(1.0, 2.0, clock="other-clock"))
    with pytest.raises(ValueError, match="clock"):
        hra_signal_series(frames, "pressure")
    with pytest.raises(ValueError, match="channel"):
        hra_signal_series((frame(0.0, 1.0),), "temperature")
