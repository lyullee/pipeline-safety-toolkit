"""Source-pinned application receipts around the existing calibration owner API."""

from .calibration import (
    CalibrationProvider,
    CalibrationRequest,
    predict_calibration,
    verify_calibration_record,
)
from .contracts import Contract, Name, RunContext, digest


class CalibrationApplicationRequest(Contract):
    source_id: Name
    calibration_record_digest: str
    application_id: Name
    context: RunContext
    x_values: tuple[float, ...]
    input_covariance_digest: str | None = None


def apply_verified_calibration(
    record: dict,
    request: CalibrationApplicationRequest,
    provider: CalibrationProvider,
    *,
    fit_request: CalibrationRequest | None = None,
    input_covariance: tuple[tuple[float, ...], ...] | None = None,
):
    """Replay calibration provenance and produce a sealed, uncertainty-explicit application.

    This delegates fit verification and model evaluation to ``calibration``. Input
    covariance is only source-bound here; it is not propagated through the model.
    """
    request = CalibrationApplicationRequest.model_validate_json(request.model_dump_json())
    if not isinstance(record, dict):
        raise TypeError("a calibration record dictionary is required")
    body = {key: value for key, value in record.items() if key != "record_digest"}
    if record.get("record_digest") != digest(body):
        raise ValueError("calibration record seal changed")
    if request.calibration_record_digest != record["record_digest"]:
        raise ValueError("application request names a different calibration record")
    if not verify_calibration_record(
        record, provider, request=fit_request, context=request.context
    )["valid"]:
        raise ValueError("calibration record does not replay against the supplied provider/context")
    if input_covariance is None:
        if request.input_covariance_digest is not None:
            raise ValueError("requested input covariance is missing")
        covariance = None
    else:
        covariance = tuple(tuple(float(value) for value in row) for row in input_covariance)
        if request.input_covariance_digest != digest(covariance):
            raise ValueError("application input covariance differs from its declared digest")
        if len(covariance) != len(request.x_values) or any(
            len(row) != len(request.x_values) for row in covariance
        ):
            raise ValueError("input covariance dimensions must match application values")
    prediction = predict_calibration(record, request.x_values, request.context, provider)
    result_body = {
        "schema": "pipeline_integrity.calibration_application.v1",
        "status": "computed",
        "source_id": request.source_id,
        "application_id": request.application_id,
        "context": request.context.model_dump(mode="json"),
        "calibration_record_digest": record["record_digest"],
        "provider_id": provider.model_id,
        "provider_configuration_digest": digest(provider.configuration),
        "fit_request_digest": digest(record["request"]),
        "input_values": list(request.x_values),
        "input_covariance_digest": request.input_covariance_digest,
        "input_covariance_retained_but_not_propagated": covariance is not None,
        "prediction": prediction,
        "application_uncertainty": None,
        "meaning": "source-bound forward application; input and model uncertainty are not propagated",
        "field_qualification": False,
    }
    return {**result_body, "record_digest": digest(result_body)}


def verify_calibration_application(record: dict, *, expected_source_id=None, expected_application_id=None):
    """Check receipt integrity and optionally bind the caller's source/application identity."""
    try:
        body = {key: value for key, value in record.items() if key != "record_digest"}
        if record.get("schema") != "pipeline_integrity.calibration_application.v1":
            raise ValueError("unsupported calibration application schema")
        if digest(body) != record.get("record_digest"):
            raise ValueError("calibration application receipt changed")
        if expected_source_id is not None and record.get("source_id") != expected_source_id:
            raise ValueError("source identity differs")
        if expected_application_id is not None and record.get("application_id") != expected_application_id:
            raise ValueError("application identity differs")
        if record.get("prediction", {}).get("calibration_digest") != record.get(
            "calibration_record_digest"
        ):
            raise ValueError("application prediction no longer matches its calibration record")
        return {"valid": True, "meaning": "receipt structure and declared identity only; not source replay"}
    except (AttributeError, TypeError, ValueError) as exc:
        return {"valid": False, "reason": str(exc)}
