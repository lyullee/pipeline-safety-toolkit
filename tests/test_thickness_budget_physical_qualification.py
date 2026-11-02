"""Bounded thickness-loss bookkeeping checked against independent equations."""

import math
import runpy

import pytest

from pipeline_integrity.fabrication_exchange import evaluate_fabrication


CASES, CONTEXT = runpy.run_path("examples/fabrication_methods.py")["make_cases"]()
BASE = CASES["fabrication.thickness_budget"]


def evaluate(**updates):
    parameters = dict(BASE.parameters)
    parameters.update(updates)
    request = BASE.model_copy(update={"parameters": parameters})
    return evaluate_fabrication(request, CONTEXT)


@pytest.mark.parametrize(
    "nominal,tolerance,forming,corrosion,required,measured",
    [
        (0.010, 0.001, 0.10, 0.001, 0.007, 0.0085),
        (0.020, 0.002, 0.05, 0.003, 0.014, 0.017),
        (0.006, 0.001, 0.00, 0.001, 0.003, 0.004),
        (0.100, 0.010, 0.25, 0.005, 0.060, 0.070),
    ],
)
def test_nominal_loss_sequence_matches_independent_wall_balance(
    nominal, tolerance, forming, corrosion, required, measured
):
    result = evaluate(
        nominal_thickness_m=nominal,
        manufacturing_minus_tolerance_m=tolerance,
        forming_loss_fraction=forming,
        corrosion_allowance_m=corrosion,
        required_structural_thickness_m=required,
        measured_postfabrication_thickness_m=measured,
    )
    values = result.values
    supplied = nominal - tolerance
    formed = supplied * (1.0 - forming)
    structural = formed - corrosion
    measured_structural = measured - corrosion
    assert result.status == "computed"
    assert values["minimum_supplied_thickness_m"] == pytest.approx(supplied, abs=1e-15)
    assert values["minimum_formed_thickness_m"] == pytest.approx(formed, abs=1e-15)
    assert values["nominal_budget_structural_thickness_m"] == pytest.approx(structural, abs=1e-15)
    assert values["nominal_budget_margin_m"] == pytest.approx(structural - required, abs=1e-15)
    assert values["measured_structural_thickness_m"] == pytest.approx(measured_structural, abs=1e-15)
    assert values["measured_margin_m"] == pytest.approx(measured_structural - required, abs=1e-15)
    assert result.native_receipt.native.payload["model_id"] == "fabrication.thickness_budget"


def test_measured_postfabrication_wall_does_not_repeat_manufacturing_or_forming_losses():
    result = evaluate(
        nominal_thickness_m=0.010,
        manufacturing_minus_tolerance_m=0.002,
        forming_loss_fraction=0.20,
        corrosion_allowance_m=0.001,
        required_structural_thickness_m=0.005,
        measured_postfabrication_thickness_m=0.007,
    )
    # The measurement already includes tolerance and forming effects.
    assert result.values["measured_structural_thickness_m"] == pytest.approx(0.006)
    assert result.values["measured_margin_m"] == pytest.approx(0.001)


def test_absent_optional_postfabrication_measurement_stays_unknown():
    parameters = dict(BASE.parameters)
    parameters["measured_postfabrication_thickness_m"] = None
    request = BASE.model_copy(update={"parameters": parameters})
    result = evaluate_fabrication(request, CONTEXT)
    assert result.status == "computed"
    assert result.values["measured_structural_thickness_m"] is None
    assert result.values["measured_margin_m"] is None


@pytest.mark.parametrize(
    "nominal,tolerance,forming,corrosion",
    [(0.001, 0.001, 0.0, 0.0), (0.010, 0.0, 0.5, 0.005)],
)
def test_exhausted_positive_wall_is_withheld(nominal, tolerance, forming, corrosion):
    result = evaluate(
        nominal_thickness_m=nominal,
        manufacturing_minus_tolerance_m=tolerance,
        forming_loss_fraction=forming,
        corrosion_allowance_m=corrosion,
        required_structural_thickness_m=0.001,
        measured_postfabrication_thickness_m=None,
    )
    assert result.status == "out_of_scope"
    assert result.values == {}


def test_loss_sequence_conserves_declared_thickness_components():
    nominal, tolerance, forming, corrosion = 0.012, 0.0015, 0.125, 0.0008
    result = evaluate(
        nominal_thickness_m=nominal,
        manufacturing_minus_tolerance_m=tolerance,
        forming_loss_fraction=forming,
        corrosion_allowance_m=corrosion,
        required_structural_thickness_m=0.005,
        measured_postfabrication_thickness_m=None,
    )
    vals = result.values
    reconstructed = vals["minimum_supplied_thickness_m"] * (1.0 - forming) - corrosion
    assert math.isclose(vals["nominal_budget_structural_thickness_m"], reconstructed, rel_tol=0, abs_tol=1e-15)
