"""Synthetic contract regressions for calibrated valve conductance binding."""

import pytest

from pipeline_integrity.calibrated_valve_boundary import bind_calibrated_valve_boundary
from pipeline_integrity.contracts import EvidenceRef, RunContext, SignalFrame, digest


def fixture():
    refs = tuple(
        EvidenceRef(id=name, citation=f"synthetic {name}", origin="synthetic")
        for name in ("source", "application", "mapping")
    )
    context = RunContext(
        asset_id="position-transmitter", scenario_id="run-1", clock_id="elapsed-s",
        origin="synthetic", evidence=refs,
    )
    network = RunContext(
        asset_id="network-1", scenario_id="run-1", clock_id="elapsed-s",
        origin="synthetic", evidence=(refs[2],),
    )
    calibration_digest = "a" * 64
    prediction = {
        "x": [0.0, 1.0, 2.0], "y": [0.0, 0.0004, 0.001],
        "x_unit": "V", "y_unit": "m2", "calibration_digest": calibration_digest,
        "uncertainty": None,
    }
    body = {
        "schema": "pipeline_integrity.calibration_application.v1", "status": "computed",
        "source_id": "sensor-campaign", "application_id": "conductance-calibration",
        "context": context.model_dump(mode="json"), "calibration_record_digest": calibration_digest,
        "provider_id": "synthetic-provider", "provider_configuration_digest": "b" * 64,
        "fit_request_digest": "c" * 64, "input_values": [0.0, 1.0, 2.0],
        "input_covariance_digest": None, "input_covariance_retained_but_not_propagated": False,
        "prediction": prediction, "application_uncertainty": None,
        "meaning": "synthetic fixture", "field_qualification": False,
    }
    application = body | {"record_digest": digest(body)}
    frames = tuple(
        SignalFrame(
            stream_id="position-transmitter", clock_id="elapsed-s", time_s=float(i),
            values={"signal": float(i)}, units={"signal": "V"}, valid={"signal": True}, origin="synthetic",
        )
        for i in range(3)
    )
    return application, frames, context, network


def bind(application, frames, network):
    return bind_calibrated_valve_boundary(
        application, frames, ("sample-0", "sample-1", "sample-2"),
        calibrated_channel="signal", source_stream_id="position-transmitter",
        valve_component_id="XV-101", start_node="upstream", end_node="downstream",
        initial_volume_flow_m3_s=0.01, required_duration_s=2.0,
        parameter_source_id="CdA-calibration",
        network_context=network, source_evidence_id="source",
        application_evidence_id="application", mapping_evidence_id="mapping",
    )


def test_calibrated_conductance_builds_existing_liquid_valve_history():
    application, frames, _, network = fixture()
    result = bind(application, frames, network)
    assert result.valve.effective_area_m2.time_s == (0.0, 1.0, 2.0)
    assert result.valve.effective_area_m2.values == (0.0, 0.0004, 0.001)
    assert result.valve.id == "XV-101"
    assert result.transient_solver_executed is False
    assert result.check_integrity()


def test_calibrated_conductance_requires_every_valid_observation_in_order():
    application, frames, _, network = fixture()
    changed = dict(application)
    changed["input_values"] = [0.0, 2.0]
    changed["prediction"] = dict(changed["prediction"], x=[0.0, 2.0], y=[0.0, 0.001])
    changed["record_digest"] = digest({key: value for key, value in changed.items() if key != "record_digest"})
    with pytest.raises(ValueError, match="all source observations"):
        bind(changed, frames, network)


def test_calibrated_conductance_rejects_invalid_samples_clock_and_short_history():
    application, frames, _, network = fixture()
    invalid = (frames[0], frames[1].model_copy(update={"valid": {"signal": False}}), frames[2])
    with pytest.raises(ValueError, match="complete valid"):
        bind(application, invalid, network)
    other_clock = frames[1].model_copy(update={"clock_id": "other-clock"})
    with pytest.raises(ValueError, match="stream, clock, origin"):
        bind(application, (frames[0], other_clock, frames[2]), network)
    with pytest.raises(ValueError, match="cover the liquid transient horizon"):
        bind_calibrated_valve_boundary(
            application, frames[:2], ("sample-0", "sample-1"),
            calibrated_channel="signal", source_stream_id="position-transmitter",
            valve_component_id="XV-101", start_node="upstream", end_node="downstream",
            initial_volume_flow_m3_s=0.01, required_duration_s=2.0,
            parameter_source_id="CdA-calibration", network_context=network,
            source_evidence_id="source", application_evidence_id="application",
            mapping_evidence_id="mapping",
        )
