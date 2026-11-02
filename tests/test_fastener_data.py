import hashlib

import pytest

from pipeline_integrity.datasets import AssetSpec, catalog
from pipeline_integrity import fastener_data
from pipeline_integrity.fastener_data import FastenerTraceError, read_fastener_trace


def _spec_for_payload(filename, payload):
    original = next(item for item in catalog() if item.id == "fastener_clamping")
    asset = AssetSpec(
        filename=filename,
        url="https://data.example.test/" + filename,
        checksum="sha256:" + hashlib.sha256(payload).hexdigest(),
        checksum_origin="publisher_metadata",
        bytes=len(payload),
    )
    return original.model_copy(update={"assets": (asset,)})


def test_torque_trace_is_source_pinned_and_keeps_angle_and_signed_values(tmp_path, monkeypatch):
    filename = "raw_data_torque_test_01t.csv"
    payload = b"Angle (summary) [deg],Torque [Nm]\n0,2.5\n1,-0.5\n1,0\n"
    path = tmp_path / filename
    path.write_bytes(payload)

    monkeypatch.setattr(fastener_data, "catalog", lambda: (_spec_for_payload(filename, payload),))
    result = read_fastener_trace(path)

    assert result["publisher_checksum_verified"] is True
    assert result["trial_id"] == "fastener_test_01"
    assert result["coordinate"] == "angle_deg"
    assert result["elapsed_time_observed"] is False
    assert [sample["value_si"] for sample in result["samples"]] == [2.5, -0.5, 0.0]
    assert result["summary"]["repeated_adjacent_angles"] == 1
    assert result["summary"]["negative_values"] == 1


def test_force_trace_converts_kilonewtons_and_keeps_missing_rows(tmp_path, monkeypatch):
    filename = "raw_data_force_test_02f.csv"
    payload = b"Angle (summary) [deg],Force [kN]\n0,0.4\n,\n2,-0.1\n"
    path = tmp_path / filename
    path.write_bytes(payload)

    monkeypatch.setattr(fastener_data, "catalog", lambda: (_spec_for_payload(filename, payload),))
    result = read_fastener_trace(path)

    assert result["si_conversion_factor"] == 1000.0
    assert [sample["value_si"] for sample in result["samples"]] == [400.0, None, -100.0]
    assert result["summary"]["invalid_or_missing_rows"] == 1


@pytest.mark.parametrize(
    "filename, kind, payload",
    [
        (
            "raw_data_torque_test_01t.csv",
            "force",
            b"Angle (summary) [deg],Torque [Nm]\n0,1\n1,2\n",
        ),
        (
            "raw_data_force_test_49f.csv",
            "force",
            b"Angle (summary) [deg],Force [kN]\n0,1\n1,2\n",
        ),
        (
            "raw_data_torque_test_01t.csv",
            "torque",
            b"angle,torque\n0,1\n1,2\n",
        ),
    ],
)
def test_trace_rejects_channel_trial_and_schema_mismatch(tmp_path, filename, kind, payload, monkeypatch):
    path = tmp_path / filename
    path.write_bytes(payload)
    monkeypatch.setattr(fastener_data, "catalog", lambda: (_spec_for_payload(filename, payload),))

    with pytest.raises(FastenerTraceError):
        read_fastener_trace(path, kind=kind)
