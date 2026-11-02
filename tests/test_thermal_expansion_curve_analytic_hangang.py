"""Analytic qualification cases for temperature-dependent expansion curves.

All inputs here are synthetic mathematical fixtures, not measured material data.
The oracle is the exact primitive of the supplied piecewise-linear coefficient.
"""

import pytest
import runpy

from pipeline_integrity.thermal_expansion import (
    CurveFrameRequest,
    ExpansionCurve,
    ExpansionKnot,
    evaluate_curve_thermal_frame,
    integrate_thermal_expansion,
)

FRAME_EXAMPLE = runpy.run_path("examples/frame_loading.py")
MATERIAL_SPY = runpy.run_path("tests/test_material_exchange.py")["ClientSpy"]


@pytest.fixture
def curve():
    return ExpansionCurve(
        id="synthetic-linear-alpha",
        material_id="synthetic-material",
        knots=(
            ExpansionKnot(temperature_K=300.0, tangent_expansion_per_K=10e-6),
            ExpansionKnot(temperature_K=500.0, tangent_expansion_per_K=20e-6),
            ExpansionKnot(temperature_K=800.0, tangent_expansion_per_K=35e-6),
        ),
        evidence_id="synthetic-fixture-only",
        tangent_small_engineering_strain_basis_reviewed=True,
    )


def test_piecewise_linear_primitive_matches_independent_exact_integrals(curve):
    first = integrate_thermal_expansion(curve, 300.0, 500.0)
    second = integrate_thermal_expansion(curve, 500.0, 800.0)
    whole = integrate_thermal_expansion(curve, 300.0, 800.0)

    # Independent trapezoid primitives: (10+20)e-6*200/2 and
    # (20+35)e-6*300/2, respectively.
    assert first["free_engineering_strain"] == pytest.approx(0.003, abs=1e-15)
    assert second["free_engineering_strain"] == pytest.approx(0.00825, abs=1e-15)
    assert whole["free_engineering_strain"] == pytest.approx(0.01125, abs=1e-15)
    assert whole["free_engineering_strain"] == pytest.approx(
        first["free_engineering_strain"] + second["free_engineering_strain"], abs=1e-15
    )
    assert len(whole["segments"]) == 2


def test_integral_is_path_reversible_and_additive_at_interior_temperature(curve):
    forward = integrate_thermal_expansion(curve, 300.0, 800.0)["free_engineering_strain"]
    reverse = integrate_thermal_expansion(curve, 800.0, 300.0)["free_engineering_strain"]
    left = integrate_thermal_expansion(curve, 300.0, 437.0)["free_engineering_strain"]
    right = integrate_thermal_expansion(curve, 437.0, 800.0)["free_engineering_strain"]

    assert reverse == pytest.approx(-forward, abs=1e-15)
    assert left + right == pytest.approx(forward, abs=1e-15)


def test_zero_temperature_increment_has_zero_strain(curve):
    result = integrate_thermal_expansion(curve, 437.0, 437.0)
    assert result["free_engineering_strain"] == 0.0
    assert result["equivalent_expansion_per_K"] == pytest.approx(10e-6)


def test_unreviewed_basis_and_out_of_support_are_rejected(curve):
    unreviewed = curve.model_copy(
        update={"tangent_small_engineering_strain_basis_reviewed": False}
    )
    with pytest.raises(ValueError, match="requires explicit review"):
        integrate_thermal_expansion(unreviewed, 300.0, 500.0)
    with pytest.raises(ValueError, match="exceed the supplied coefficient support"):
        integrate_thermal_expansion(curve, 300.0, 800.0001)
    with pytest.raises(ValueError, match="exceed the supplied coefficient support"):
        integrate_thermal_expansion(curve, 299.9999, 500.0)


def test_curve_owner_bridge_delivers_integrated_strain_to_frame_material_owner():
    frame, context = FRAME_EXAMPLE["reference_frame"]()
    spy = MATERIAL_SPY()
    spy.description["output_units"] = {
        "free_thermal_strain": "1",
        "restrained_uniaxial_stress_Pa": "Pa",
    }
    spy.values = {"free_thermal_strain": 0.0015, "restrained_uniaxial_stress_Pa": 0.0}
    curve = ExpansionCurve(
        id="synthetic-frame-curve",
        material_id=frame.members[0].material_id,
        knots=(
            ExpansionKnot(temperature_K=300.0, tangent_expansion_per_K=10e-6),
            ExpansionKnot(temperature_K=400.0, tangent_expansion_per_K=20e-6),
            ExpansionKnot(temperature_K=500.0, tangent_expansion_per_K=30e-6),
        ),
        evidence_id="reference",
        tangent_small_engineering_strain_basis_reviewed=True,
    )
    request = CurveFrameRequest(
        id="curve-thermal-frame",
        frame=frame,
        member_id="pipe",
        case_id="thermal",
        curve=curve,
        reference_temperature_K=300.0,
        temperature_K=400.0,
        uniform_member_temperature_reviewed=True,
        evidence_ids=("reference",),
    )

    record = evaluate_curve_thermal_frame(request, context, client=spy)
    assert record["status"] == "ready"
    assert record["integrated_expansion"]["free_engineering_strain"] == pytest.approx(
        0.0015, abs=1e-15
    )
    assert len(spy.calls) == 1
    assert spy.calls[0]["temperature_change_K"] == pytest.approx(100.0)
    assert spy.calls[0]["expansion_coefficient_per_K"] == pytest.approx(15e-6)
    assert record["contribution"]["initial_strains"][0]["value"] == pytest.approx(
        0.0015, abs=1e-15
    )
