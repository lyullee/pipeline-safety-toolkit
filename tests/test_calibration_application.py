"""Source and application identity tests; numerical owner replay is covered elsewhere."""

from types import SimpleNamespace

import pytest

from pipeline_integrity.calibration_application import (
    CalibrationApplicationRequest,
    apply_verified_calibration,
    verify_calibration_application,
)
from pipeline_integrity.contracts import RunContext, digest


def test_application_receipt_binds_source_fit_and_optional_covariance(monkeypatch):
    import pipeline_integrity.calibration_application as adapter

    context = RunContext(asset_id="meter", scenario_id="run-1", clock_id="utc", origin="measured")
    fit_body = {"schema": "pipeline_integrity.calibration.v1", "request": {"model_id": "gain"}}
    calibration = {**fit_body, "record_digest": digest(fit_body)}
    provider = SimpleNamespace(model_id="gain", configuration={"revision": 3})
    monkeypatch.setattr(adapter, "verify_calibration_record", lambda *a, **k: {"valid": True})
    monkeypatch.setattr(
        adapter,
        "predict_calibration",
        lambda record, x, context, provider: {
            "x": list(x),
            "y": [2 * value for value in x],
            "calibration_digest": calibration["record_digest"],
        },
    )
    covariance = ((0.04, 0.01), (0.01, 0.09))
    request = CalibrationApplicationRequest(
        source_id="campaign-8",
        calibration_record_digest=calibration["record_digest"],
        application_id="online-window-3",
        context=context,
        x_values=(1.0, 2.0),
        input_covariance_digest=digest(covariance),
    )
    result = apply_verified_calibration(calibration, request, provider, input_covariance=covariance)
    assert result["input_covariance_retained_but_not_propagated"]
    assert result["input_covariance_digest"] == digest(covariance)
    assert result["application_uncertainty"] is None
    assert verify_calibration_application(
        result, expected_source_id="campaign-8", expected_application_id="online-window-3"
    )["valid"]


def test_application_rejects_wrong_fit_digest_and_missing_covariance(monkeypatch):
    import pipeline_integrity.calibration_application as adapter

    context = RunContext(asset_id="meter", scenario_id="run-1", clock_id="utc", origin="measured")
    fit_body = {"schema": "pipeline_integrity.calibration.v1", "request": {"model_id": "gain"}}
    calibration = {**fit_body, "record_digest": digest(fit_body)}
    provider = SimpleNamespace(model_id="gain", configuration={})
    monkeypatch.setattr(adapter, "verify_calibration_record", lambda *a, **k: {"valid": True})
    monkeypatch.setattr(
        adapter,
        "predict_calibration",
        lambda record, x, context, provider: {
            "x": list(x),
            "y": list(x),
            "calibration_digest": calibration["record_digest"],
        },
    )
    request = CalibrationApplicationRequest(
        source_id="campaign-8",
        calibration_record_digest="0" * 64,
        application_id="application-1",
        context=context,
        x_values=(1.0,),
    )
    with pytest.raises(ValueError, match="different calibration"):
        apply_verified_calibration(calibration, request, provider)
    request = request.model_copy(
        update={
            "calibration_record_digest": calibration["record_digest"],
            "input_covariance_digest": "1" * 64,
        }
    )
    with pytest.raises(ValueError, match="covariance is missing"):
        apply_verified_calibration(calibration, request, provider)


def test_application_verifier_detects_receipt_changes():
    result = {
        "schema": "pipeline_integrity.calibration_application.v1",
        "source_id": "source",
        "application_id": "application",
        "calibration_record_digest": "a" * 64,
        "prediction": {"calibration_digest": "a" * 64},
    }
    sealed = {**result, "record_digest": digest(result)}
    assert verify_calibration_application(sealed)["valid"]
    changed = {**sealed, "application_id": "changed"}
    assert not verify_calibration_application(changed)["valid"]
