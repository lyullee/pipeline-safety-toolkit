"""Fully reversed strain-life relation checked against a closed-form reduction.

The equal-exponent cases reduce the two-term Coffin-Basquin relation to one
power law, giving a reference independent of the owner's root finder.
"""

import runpy

import pytest

from pipeline_integrity.adapters import MaterialClient


REQUESTS, CONTEXT = runpy.run_path("examples/cyclic_fatigue.py")["make_cases"]()
REQUEST = REQUESTS["fatigue.strain_life"]
CLIENT = MaterialClient()


def evaluate(parameters):
    result = CLIENT.evaluate(
        "fatigue.strain_life", parameters, CONTEXT, location_id=REQUEST.location_id
    )
    return result


def amplitude_for_cycles(parameters, cycles):
    reversals = 2.0 * cycles
    exponent = parameters["fatigue_strength_exponent"]
    return (
        parameters["fatigue_strength_coefficient_Pa"]
        / parameters["elastic_modulus_Pa"]
        * reversals**exponent
        + parameters["fatigue_ductility_coefficient"]
        * reversals**exponent
    )


@pytest.mark.parametrize(
    "amplitude, modulus, strength, ductility, exponent",
    [
        (0.00105, 2.0e11, 1.0e9, 0.1, -0.5),
        (0.002, 2.1e11, 8.0e8, 0.08, -0.5),
        (0.0004, 1.9e11, 1.3e9, 0.025, -0.5),
        (0.0035, 7.0e10, 6.0e8, 0.2, -0.5),
    ],
)
def test_cycles_match_closed_form_equal_exponent_reduction(
    amplitude, modulus, strength, ductility, exponent
):
    p = REQUEST.parameters | {
        "strain_amplitude": amplitude,
        "elastic_modulus_Pa": modulus,
        "fatigue_strength_coefficient_Pa": strength,
        "fatigue_ductility_coefficient": ductility,
        "fatigue_strength_exponent": exponent,
        "fatigue_ductility_exponent": exponent,
    }
    expected = 0.5 * (
        (strength / modulus + ductility) / amplitude
    ) ** (1.0 / -exponent)
    result = evaluate(p)
    assert result.status == "computed"
    assert result.payload["values"]["cycles_to_failure"] == pytest.approx(expected, rel=2e-12)
    assert result.payload["values"]["reversals_to_failure"] == pytest.approx(2.0 * expected, rel=2e-12)


def test_uniform_stress_coefficient_scaling_preserves_life():
    p = REQUEST.parameters | {"fatigue_strength_exponent": -0.5, "fatigue_ductility_exponent": -0.5}
    base = evaluate(p)
    factor = 1000.0
    scaled = evaluate(
        p
        | {
            "elastic_modulus_Pa": p["elastic_modulus_Pa"] * factor,
            "fatigue_strength_coefficient_Pa": p["fatigue_strength_coefficient_Pa"] * factor,
        }
    )
    assert base.status == scaled.status == "computed"
    assert scaled.payload["values"]["cycles_to_failure"] == pytest.approx(
        base.payload["values"]["cycles_to_failure"], rel=2e-12
    )


def test_amplitude_scaling_obeys_closed_form_inverse_square():
    p = REQUEST.parameters | {"fatigue_strength_exponent": -0.5, "fatigue_ductility_exponent": -0.5}
    base = evaluate(p)
    doubled = evaluate(p | {"strain_amplitude": 2.0 * p["strain_amplitude"]})
    assert base.status == doubled.status == "computed"
    assert doubled.payload["values"]["cycles_to_failure"] == pytest.approx(
        base.payload["values"]["cycles_to_failure"] / 4.0, rel=2e-12
    )


@pytest.mark.parametrize("cycle_boundary", [50.0, 5.0e5])
def test_closed_form_root_at_declared_cycle_support_boundary(cycle_boundary):
    p = REQUEST.parameters | {
        "fatigue_strength_exponent": -0.5,
        "fatigue_ductility_exponent": -0.5,
        "strain_amplitude": amplitude_for_cycles(REQUEST.parameters, cycle_boundary),
    }
    result = evaluate(p)
    assert result.status == "computed"
    assert result.payload["values"]["cycles_to_failure"] == pytest.approx(cycle_boundary, rel=2e-10)


@pytest.mark.parametrize("amplitude_factor", [0.5, 1.5])
def test_root_outside_calibrated_cycle_support_is_not_extrapolated(amplitude_factor):
    p = REQUEST.parameters | {"fatigue_strength_exponent": -0.5, "fatigue_ductility_exponent": -0.5}
    if amplitude_factor == 0.5:
        # Lower amplitude implies a life beyond maximum_valid_cycles.
        p["strain_amplitude"] = amplitude_for_cycles(p, p["maximum_valid_cycles"]) * amplitude_factor
    else:
        # Higher amplitude implies a life below minimum_valid_cycles.
        p["strain_amplitude"] = amplitude_for_cycles(p, p["minimum_valid_cycles"]) * amplitude_factor
    assert evaluate(p).status == "out_of_scope"
