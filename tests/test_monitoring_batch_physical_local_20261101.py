"""Adapter contract check against process-monitoring-toolkit's real SignalBatch."""
import numpy as np
import pytest
import sys
from pathlib import Path
from types import ModuleType

from pipeline_integrity.adapters import monitoring_batch
from pipeline_integrity.contracts import SignalFrame

_contract_file = (
    Path(__file__).resolve().parents[2]
    / "tmp/analysis/repos/process-monitoring-toolkit/src/process_monitoring/contracts.py"
)
_owner_package = ModuleType("process_monitoring")
_owner_package.__path__ = [str(_contract_file.parent)]
sys.modules["process_monitoring"] = _owner_package
from process_monitoring.contracts import SignalBatch


def frames(*, modes=("steady", "steady", "steady"), times=(0.0, 1.0, 3.0)):
    return tuple(
        SignalFrame(
            stream_id="line-A", clock_id="operation-17", time_s=t,
            values={"pressure": 2.0 + i if i != 1 else None, "flow": 0.1 * i},
            units={"pressure": "Pa_abs", "flow": "m3/s"},
            valid={"pressure": i != 1, "flow": True},
            origin="synthetic", operating_mode=modes[i],
        )
        for i, t in enumerate(times)
    )


def test_monitoring_batch_constructs_owner_type_and_preserves_rows_and_gaps():
    batch = monitoring_batch(frames(), metadata={"review": "authored"})
    assert isinstance(batch, SignalBatch)
    assert batch.time_s.tolist() == [0.0, 1.0, 3.0]
    assert batch.channels == ("pressure", "flow")
    assert batch.units == ("Pa_abs", "m3/s")
    assert batch.valid.tolist() == [[True, True], [False, True], [True, True]]
    assert np.isnan(batch.values[1, 0]) and batch.values[1, 1] == pytest.approx(0.1)
    assert batch.modes.tolist() == ["steady"] * 3
    assert batch.metadata["clock_id"] == "operation-17"
    assert batch.metadata["stream_id"] == "line-A" and batch.origin == "synthetic"


def test_adapter_rejects_nonmonotonic_time_before_owner_batch_creation():
    with pytest.raises(ValueError, match="strictly increase"):
        monitoring_batch(frames(times=(0.0, 2.0, 1.0)))


def test_adapter_rejects_metadata_that_rebinds_clock_or_origin():
    with pytest.raises(ValueError, match="contradicts"):
        monitoring_batch(frames(), metadata={"clock_id": "other-clock"})
    with pytest.raises(ValueError, match="contradicts"):
        monitoring_batch(frames(), metadata={"origin": "measured"})


def test_adapter_does_not_fill_partial_operating_mode_identity():
    with pytest.raises(ValueError, match="complete or absent"):
        monitoring_batch(frames(modes=("steady", None, "steady")))
