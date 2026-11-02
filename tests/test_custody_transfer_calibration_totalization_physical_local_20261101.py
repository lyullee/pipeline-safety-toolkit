"""Bounded calibration-to-mass-totalization calculation with an independent oracle."""

from __future__ import annotations

import math
import runpy
from pathlib import Path

import numpy as np
import pytest

from pipeline_integrity.calibration import (
    CalibrationRequest,
    affine_provider,
    boundary_metadata as calibration_boundary_metadata,
    fit_calibration,
    model_identity,
    predict_calibration,
)
from pipeline_integrity.metrology import MeasurementBasis
from pipeline_integrity.totalization import boundary_metadata, totalize_measurements


ROOT = Path(__file__).resolve().parents[1]
CALIBRATION_EXAMPLE = runpy.run_path(str(ROOT / "examples" / "calibration.py"))["make_case"]
TOTALIZATION_EXAMPLE = runpy.run_path(str(ROOT / "examples" / "totalization.py"))["make_case"]
def test_calibrated_mass_flow_totalization_matches_deming_trapezoid_and_covariance():
    raw_request, raw_context, _ = CALIBRATION_EXAMPLE(reviewed=True)
    provider = affine_provider(x_unit="kg/s", y_unit="kg/s", slope_unit="1")
    q_data = raw_request.model_dump(mode="python")
    basis_x = MeasurementBasis(
        quantity="meter_indicated_mass_flow", unit="kg/s", definition_id="meter_flow", evidence_id="source"
    )
    basis_y = MeasurementBasis(
        quantity="reference_mass_flow", unit="kg/s", definition_id="reference_flow", evidence_id="source"
    )
    q_data.update(
        asset_id="meter-a",
        clock_id="observation-clock",
        x_basis=basis_x,
        y_basis=basis_y,
        parameters=tuple(
            parameter.model_copy(update={"unit": "kg/s" if parameter.id == "offset" else "1"})
            for parameter in raw_request.parameters
        ),
        **model_identity(provider),
    )
    calibration_request = CalibrationRequest.model_validate(q_data)
    calibration_context = raw_context.model_copy(
        update={
            "asset_id": calibration_request.asset_id,
            "clock_id": calibration_request.clock_id,
            "metadata": {"calibration_boundary": calibration_boundary_metadata(calibration_request)},
        }
    )
    calibration = fit_calibration(calibration_request, calibration_context, provider)
    assert calibration["status"] == "computed"

    x = np.array([row.x for row in calibration_request.observations], dtype=float)
    y = np.array([row.y for row in calibration_request.observations], dtype=float)
    x_centered, y_centered = x - x.mean(), y - y.mean()
    sxx, syy, sxy = x_centered @ x_centered, y_centered @ y_centered, x_centered @ y_centered
    variance_ratio = 4.0
    expected_slope = (
        syy - variance_ratio * sxx
        + math.sqrt((syy - variance_ratio * sxx) ** 2 + 4 * variance_ratio * sxy**2)
    ) / (2 * sxy)
    expected_offset = y.mean() - expected_slope * x.mean()
    params = calibration["values"]["parameters"]
    assert params["slope"] == pytest.approx(expected_slope, rel=1e-8)
    assert params["offset"] == pytest.approx(expected_offset, abs=1e-7)

    prediction = predict_calibration(calibration, [1.0, 3.0, 5.0], calibration_context, provider)
    calibrated_rates = prediction["y"]
    request, context = TOTALIZATION_EXAMPLE(meaning="point_linear", reviewed=True)
    readings = tuple(
        row.model_copy(update={"estimate": float(value)})
        for row, value in zip(request.readings, calibrated_rates, strict=True)
    )
    request = request.model_copy(update={"readings": readings})
    context = context.model_copy(update={"metadata": {"totalization_boundary": boundary_metadata(request)}})
    integrated = totalize_measurements(request, context)
    assert integrated["status"] == "computed"
    assert integrated["custody_transfer_certified"] is False

    times = [row.estimate for row in request.times]
    independent_mass = math.fsum(
        (calibrated_rates[i] + calibrated_rates[i + 1]) * 0.5 * (times[i + 1] - times[i])
        for i in range(len(times) - 1)
    )
    assert integrated["values"]["complete_net_amount"] == pytest.approx(independent_mass, rel=2e-14)

    # Independently propagate the documented local linearization through the
    # complete supplied reading/time covariance, without calibration covariance.
    rates = calibrated_rates
    jacobian = np.array(
        [
            0.5,
            1.5,
            1.0,
            -(rates[0] + rates[1]) / 2,
            (rates[0] - rates[2]) / 2,
            (rates[1] + rates[2]) / 2,
        ]
    )
    covariance = np.asarray(request.covariance.values, dtype=float)
    expected_variance = float(jacobian @ covariance @ jacobian)
    assert integrated["uncertainty"]["output_covariance"][0][0] == pytest.approx(
        expected_variance, rel=2e-12, abs=1e-15
    )
