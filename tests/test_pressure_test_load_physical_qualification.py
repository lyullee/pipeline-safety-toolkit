"""Independent gauge-reference and hydrostatic-head pressure checks."""

import runpy

import pytest

from pipeline_integrity.pressure_tests import evaluate_pressure_test


REQUESTS, _, _, CONTEXT = runpy.run_path("examples/pressure_tests.py")["make_cases"]()
BASE = REQUESTS["inspection.pressure_test_load"]
G = 9.80665


def evaluate(**updates):
    parameters = dict(BASE.parameters)
    parameters.update(updates)
    return evaluate_pressure_test(BASE.model_copy(update={"parameters": parameters}), CONTEXT)


@pytest.mark.parametrize(
    "design,factor,allow_test,allow_design,density,elevation",
    [
        (1.0e6, 1.5, 150.0e6, 100.0e6, 1000.0, 10.0),
        (4.0e6, 1.25, 180.0e6, 120.0e6, 998.0, 5.5),
        (750e3, 1.6, 105e6, 140e6, 1100.0, 0.0),
        (2.3e6, 1.0, 210e6, 105e6, 0.0, 17.0),
    ],
)
def test_reference_and_local_test_pressure_match_independent_balance(
    design, factor, allow_test, allow_design, density, elevation
):
    result = evaluate(
        design_pressure_Pa=design,
        declared_test_factor=factor,
        allowable_at_test_Pa=allow_test,
        allowable_at_design_Pa=allow_design,
        fluid_density_kg_m3=density,
        elevation_below_gauge_m=elevation,
    )
    reference = design * factor * allow_test / allow_design
    hydrostatic_head = density * G * elevation
    assert result["status"] == "computed"
    assert result["values"]["reference_test_pressure_Pa"] == pytest.approx(reference, rel=2e-15)
    assert result["values"]["local_test_pressure_Pa"] == pytest.approx(reference + hydrostatic_head, rel=2e-15)
    assert result["native_receipt"]["native"]["payload"]["model_id"] == "inspection.pressure_test_load"


@pytest.mark.parametrize("scale", [0.25, 2.0, 13.0])
def test_design_pressure_and_test_factor_scale_reference_linearly(scale):
    base = evaluate()["values"]
    design_scaled = evaluate(
        design_pressure_Pa=BASE.parameters["design_pressure_Pa"] * scale,
    )["values"]
    factor_scaled = evaluate(
        declared_test_factor=BASE.parameters["declared_test_factor"] * scale,
    )["values"]
    assert design_scaled["reference_test_pressure_Pa"] == pytest.approx(
        base["reference_test_pressure_Pa"] * scale, rel=2e-15
    )
    assert factor_scaled["reference_test_pressure_Pa"] == pytest.approx(
        base["reference_test_pressure_Pa"] * scale, rel=2e-15
    )


def test_local_head_changes_with_density_and_elevation_but_not_reference_pressure():
    baseline = evaluate(fluid_density_kg_m3=0.0, elevation_below_gauge_m=0.0)["values"]
    loaded = evaluate(fluid_density_kg_m3=850.0, elevation_below_gauge_m=12.0)["values"]
    expected_head = 850.0 * G * 12.0
    assert loaded["local_test_pressure_Pa"] - baseline["local_test_pressure_Pa"] == pytest.approx(expected_head)
    assert loaded["reference_test_pressure_Pa"] == pytest.approx(baseline["reference_test_pressure_Pa"])


def test_zero_elevation_and_zero_density_reduce_local_pressure_to_reference():
    for density, elevation in ((0.0, 10.0), (1000.0, 0.0)):
        values = evaluate(fluid_density_kg_m3=density, elevation_below_gauge_m=elevation)["values"]
        assert values["local_test_pressure_Pa"] == pytest.approx(values["reference_test_pressure_Pa"])
