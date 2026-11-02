"""Bounded independent references for conditional lognormal creep-life prediction."""

import math

import pytest

from pipeline_integrity.adapters import MaterialClient
from pipeline_integrity.contracts import EvidenceRef, RunContext
from matintegrity.contracts import Evidence


SIGMA = math.log(2.0)
MEDIAN = 1.0e6
REFERENCE_STRESS = 1.0e8
REFERENCE_TEMPERATURE = 600.0
EXPONENT = 2.0


def context():
    return RunContext(
        asset_id="synthetic-creep-coupon",
        scenario_id="constant-condition-life-reference",
        clock_id="seconds-from-fresh-material",
        origin="synthetic",
        evidence=(
            EvidenceRef(
                id="authored-reference",
                citation="Independently authored synthetic analytic cases",
                origin="synthetic",
                license_id="MIT",
            ),
        ),
    )


def model(*, family="stress_only", activation_energy=None):
    return {
        "family": family,
        "material_condition_id": "synthetic-condition",
        "stress_basis": "initial_nominal_constant_load",
        "temperature_basis": "nominal_specified",
        "reference_stress_Pa": REFERENCE_STRESS,
        "reference_temperature_K": REFERENCE_TEMPERATURE,
        "log_reference_median_time_s": math.log(MEDIAN),
        "stress_exponent": EXPONENT,
        "activation_energy_J_mol": activation_energy,
        "log_life_sigma": SIGMA,
        "temperature_tolerance_K": 1.0,
        "support_stress_Pa": [0.5e8, 1.0e8, 2.0e8],
        "support_temperature_K": [600.0, 600.0, 600.0],
        "longest_observed_failure_time_s": 1.0e7,
        "training_digest": "a" * 64,
        "observations": 8,
        "failures": 8,
        "evidence": (
            Evidence(
                source_id="authored-creep-model",
                origin="synthetic",
                citation="Independently authored synthetic life-distribution parameters",
                license_id="MIT",
            ),
        ),
    }


def evaluate(
    *,
    model_data=None,
    stress=REFERENCE_STRESS,
    temperature=REFERENCE_TEMPERATURE,
    age=0.0,
    horizon=MEDIAN,
    reviewed=True,
    constant=True,
):
    params = {
        "model": model_data or model(),
        "material_condition_id": "synthetic-condition",
        "stress_basis": "initial_nominal_constant_load",
        "temperature_basis": "nominal_specified",
        "initial_stress_Pa": stress,
        "temperature_K": temperature,
        "current_age_s": age,
        "horizon_s": horizon,
        "maximum_time_extrapolation_factor": 1.0,
        "constant_conditions_since_fresh_material_confirmed": constant,
        "applicability_reviewed": reviewed,
    }
    return MaterialClient().evaluate(
        "creep.conditional_life", params, context(), location_id="wall-reference-point"
    )


def values(result):
    assert result.status == "computed"
    return result.payload["values"]


@pytest.mark.parametrize("stress,multiplier", [(0.5e8, 4.0), (1e8, 1.0), (2e8, 0.25)])
def test_stress_life_median_matches_power_law(stress, multiplier):
    result = evaluate(stress=stress)
    output = values(result)
    expected = MEDIAN * (stress / REFERENCE_STRESS) ** (-EXPONENT)
    assert output["median_life_s"] == pytest.approx(expected, rel=2e-14)
    assert output["log_median_life_s"] == pytest.approx(math.log(expected), abs=2e-14)
    assert output["median_life_s"] == pytest.approx(MEDIAN * multiplier, rel=2e-14)
    assert output["parameter_uncertainty_included"] is False


def test_fresh_material_period_probability_is_half_at_the_median():
    output = values(evaluate(age=0.0, horizon=MEDIAN))
    assert output["probability_given_survival"] == pytest.approx(0.5, abs=2e-15)


def test_conditional_period_probability_matches_closed_form_normal_survival_ratio():
    age = MEDIAN
    horizon = MEDIAN
    result = evaluate(age=age, horizon=horizon)
    output = values(result)
    z0 = math.log(age / MEDIAN) / SIGMA
    z1 = math.log((age + horizon) / MEDIAN) / SIGMA
    phi = lambda z: 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))
    expected = (phi(z1) - phi(z0)) / (1.0 - phi(z0))
    assert output["probability_given_survival"] == pytest.approx(expected, rel=2e-13)
    assert output["probability_given_survival"] == pytest.approx(2.0 * phi(1.0) - 1.0, rel=2e-13)


def test_arrhenius_temperature_shift_has_the_declared_signed_inverse_temperature_form():
    activation = 80_000.0
    temperature = 650.0
    data = model(family="arrhenius_stress", activation_energy=activation)
    data["support_stress_Pa"] = [0.5e8, 1.0e8, 2.0e8, 1.0e8]
    data["support_temperature_K"] = [550.0, 600.0, 600.0, 650.0]
    output = values(evaluate(model_data=data, temperature=temperature))
    gas_constant = 8.31446261815324
    expected_log_median = math.log(MEDIAN) + activation / gas_constant * (
        1.0 / temperature - 1.0 / REFERENCE_TEMPERATURE
    )
    assert output["log_median_life_s"] == pytest.approx(expected_log_median, rel=2e-14)
    assert output["median_life_s"] == pytest.approx(math.exp(expected_log_median), rel=2e-14)


def test_applicability_and_fresh_age_origin_gates_withhold_a_probability():
    for result in (evaluate(reviewed=False), evaluate(constant=False)):
        assert result.status == "needs_data"
        assert result.payload["values"] == {}
        assert result.payload["probability_semantics"] == {
            "probability_given_survival": "conditional_first_failure_in_period"
        }


def test_prediction_outside_observed_stress_support_is_withheld():
    result = evaluate(stress=2.01e8)
    assert result.status == "out_of_scope"
    assert result.payload["values"] == {}


def test_interval_beyond_declared_life_support_is_withheld():
    result = evaluate(age=9.0e6, horizon=2.0e6)
    assert result.status == "out_of_scope"
    assert result.payload["values"] == {}
