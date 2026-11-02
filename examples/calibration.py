"""Authored paired measurements with explicit independent standard errors."""

from pipeline_integrity.calibration import (
    REVIEWS,
    CalibrationObservation,
    CalibrationRequest,
    affine_provider,
    boundary_metadata,
    covariance_order,
    fit_calibration,
    model_identity,
)
from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.inverse import FitParameter
from pipeline_integrity.metrology import MeasurementBasis, StandardCovariance


def make_case(*, reviewed=False, source_inputs=None):
    source = source_inputs is not None
    data = (
        source_inputs
        if source
        else {"x": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0], "y": [3.03, 4.99, 7.02, 8.96, 11.01, 13.02]}
    )
    x, y = data["x"], data["y"]
    ids = tuple(data.get("point_ids", [f"point-{i + 1}" for i in range(len(x))]))
    covariance = data.get("standard_joint_covariance")
    if source and (data.get("x_unit"), data.get("y_unit")) != ("1", "1"):
        raise ValueError("source example requires explicit dimensionless intermediate quantities")
    if covariance is None and not source:
        covariance = tuple(
            tuple((0.01 if i < len(x) else 0.04) if i == j else 0.0 for j in range(2 * len(x)))
            for i in range(2 * len(x))
        )
    provider = affine_provider(x_unit="1", y_unit="1", slope_unit="1")
    request = CalibrationRequest(
        asset_id="publisher-sonic-nozzle" if source else "authored-calibration",
        clock_id="source-observation-time-unknown" if source else "authored-clock",
        event_id="single-campaign-affine-fit",
        x_basis=MeasurementBasis(
            quantity=data.get("x_quantity", "instrument_indication"),
            unit="1",
            definition_id="x-definition",
            evidence_id="source",
        ),
        y_basis=MeasurementBasis(
            quantity=data.get("y_quantity", "reference_quantity"),
            unit="1",
            definition_id="y-definition",
            evidence_id="source",
        ),
        observations=tuple(
            CalibrationObservation(
                id=key,
                run_id="publisher-single-campaign" if source else "authored-run",
                population_id="publisher-nozzle" if source else "authored-population",
                role="fit",
                x=float(a),
                y=float(b),
                valid=True,
                evidence_id="source",
            )
            for key, a, b in zip(ids, x, y, strict=True)
        ),
        covariance=StandardCovariance(
            input_ids=covariance_order(ids),
            values=tuple(tuple(float(v) for v in row) for row in covariance),
            evidence_id="source",
            definition_reviewed=reviewed,
        )
        if covariance is not None
        else None,
        parameters=(
            FitParameter(
                id="offset",
                unit="1",
                lower=0.9 if source else -5.0,
                upper=1.1 if source else 5.0,
                initial=1.0,
            ),
            FitParameter(
                id="slope",
                unit="1",
                lower=-10.0 if source else 0.0,
                upper=0.0 if source else 5.0,
                initial=-3.5 if source else 2.0,
            ),
        ),
        x_domain=(0.0, 0.1) if source else (0.0, 8.0),
        model_id=provider.model_id,
        model_version=provider.version,
        **model_identity(provider),
        split_policy="fit_only",
        require_independent_validation=False,
        evidence_ids=("source",),
        applicability_reviews={key: True for key in REVIEWS} if reviewed else {},
        review_evidence_ids={key: "source" for key in REVIEWS} if reviewed else {},
    )
    context = RunContext(
        asset_id=request.asset_id,
        scenario_id="calibration-fit",
        clock_id=request.clock_id,
        origin="laboratory" if source else "synthetic",
        evidence=(
            EvidenceRef(
                id="source",
                citation=data["source_citation"]
                if source
                else "Original authored paired measurements and declared independent errors",
                origin="literature" if source else "synthetic",
                license_id=data["source_license_id"] if source else "MIT",
                sha256=data.get("source_asset_sha256"),
                locator=data.get("source_url", ""),
            ),
        ),
        metadata={
            "calibration_boundary": boundary_metadata(request),
            "source_record_digest": data.get("source_record_digest"),
            "source_origin": data.get("source_origin", "authored_paired_measurements_and_declared_errors"),
            "source_member_sha256": data.get("source_member_sha256"),
            "source_uncertainty_scale": data.get("source_uncertainty_scale"),
        },
    )
    return request, context, provider


if __name__ == "__main__":
    print(fit_calibration(*make_case())["status"])
