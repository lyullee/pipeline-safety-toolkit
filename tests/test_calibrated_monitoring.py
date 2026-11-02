"""Calibration-to-monitoring adapter boundaries; owner numerical tests remain separate."""

import pytest

from pipeline_integrity.calibrated_monitoring import (
    build_calibrated_monitoring_input,
    verify_calibrated_monitoring_input,
)
from pipeline_integrity.contracts import EvidenceRef, RunContext, SignalFrame, digest


def fixture():
    context = RunContext(
        asset_id="meter-1",
        scenario_id="run-1",
        clock_id="elapsed-seconds",
        origin="measured",
        evidence=tuple(
            EvidenceRef(id=name, citation=f"reviewed {name}", origin="user_declared")
            for name in ("source", "application", "mapping")
        ),
    )
    calibration_digest = "a" * 64
    prediction = {
        "x": [1.0, 3.0],
        "y": [10.0, 30.0],
        "x_unit": "m",
        "y_unit": "m/s",
        "calibration_digest": calibration_digest,
        "uncertainty": None,
    }
    body = {
        "schema": "pipeline_integrity.calibration_application.v1",
        "status": "computed",
        "source_id": "meter-campaign-1",
        "application_id": "online-segment-1",
        "context": context.model_dump(mode="json"),
        "calibration_record_digest": calibration_digest,
        "provider_id": "declared-provider",
        "provider_configuration_digest": "b" * 64,
        "fit_request_digest": "c" * 64,
        "input_values": [1.0, 3.0],
        "input_covariance_digest": None,
        "input_covariance_retained_but_not_propagated": False,
        "prediction": prediction,
        "application_uncertainty": None,
        "meaning": "source-bound forward application",
        "field_qualification": False,
    }
    application = {**body, "record_digest": digest(body)}
    frames = (
        SignalFrame(
            stream_id="flow-sensor", clock_id=context.clock_id, time_s=0.0,
            values={"length": 1.0}, units={"length": "m"}, valid={"length": True}, origin="measured",
        ),
        SignalFrame(
            stream_id="flow-sensor", clock_id=context.clock_id, time_s=1.0,
            values={"length": None}, units={"length": "m"}, valid={"length": False}, origin="measured",
        ),
        SignalFrame(
            stream_id="flow-sensor", clock_id=context.clock_id, time_s=2.0,
            values={"length": 3.0}, units={"length": "m"}, valid={"length": True}, origin="measured",
        ),
    )
    return context, application, frames


def apply(application, frames, *, ids=("row-1", "row-2", "row-3"), **changes):
    fields = dict(
        calibrated_channel="length",
        source_stream_id="flow-sensor",
        role="training",
        split_id="split-1",
        physical_run_id="run-1",
        source_group_id="site-1",
        source_evidence_id="source",
        application_evidence_id="application",
        mapping_evidence_id="mapping",
        reference_kind="caller_declared_reference",
    )
    return build_calibrated_monitoring_input(application, frames, ids, **(fields | changes))


def test_calibrated_monitoring_preserves_rows_order_clock_and_invalidity():
    _, application, frames = fixture()
    result = apply(application, frames)
    split = result.split
    assert split.observation_ids == ("row-1", "row-2", "row-3")
    assert [frame.time_s for frame in split.frames] == [0.0, 1.0, 2.0]
    assert [frame.values["length"] for frame in split.frames] == [10.0, None, 30.0]
    assert [frame.valid["length"] for frame in split.frames] == [True, False, True]
    assert all(frame.units["length"] == "m/s" for frame in split.frames)
    assert result.calibrated_observation_ids == ("row-1", "row-3")
    assert result.clock_transformed is False and result.uncertainty_propagated is False
    assert verify_calibrated_monitoring_input(result)


def test_calibrated_monitoring_refuses_partial_or_reordered_calibration_rows():
    _, application, frames = fixture()
    altered = dict(application)
    altered["input_values"] = [3.0]
    altered["prediction"] = dict(altered["prediction"], x=[3.0], y=[30.0])
    altered_body = {key: value for key, value in altered.items() if key != "record_digest"}
    altered["record_digest"] = digest(altered_body)
    with pytest.raises(ValueError, match="every valid source row"):
        apply(altered, frames)

    altered = dict(application)
    altered["input_values"] = [3.0, 1.0]
    altered["prediction"] = dict(altered["prediction"], x=[3.0, 1.0], y=[30.0, 10.0])
    altered_body = {key: value for key, value in altered.items() if key != "record_digest"}
    altered["record_digest"] = digest(altered_body)
    with pytest.raises(ValueError, match="every valid source row"):
        apply(altered, frames)


def test_calibrated_monitoring_refuses_clock_unit_identity_and_receipt_changes():
    _, application, frames = fixture()
    with pytest.raises(ValueError, match="clock and origin"):
        apply(application, (frames[0].model_copy(update={"clock_id": "other"}), *frames[1:]))
    with pytest.raises(ValueError, match="channel or calibration input unit"):
        apply(application, (frames[0].model_copy(update={"units": {"length": "cm"}}), *frames[1:]))
    with pytest.raises(ValueError, match="unique"):
        apply(application, frames, ids=("same", "same", "last"))
    with pytest.raises(ValueError, match="receipt changed"):
        apply(application | {"application_id": "changed"}, frames)
