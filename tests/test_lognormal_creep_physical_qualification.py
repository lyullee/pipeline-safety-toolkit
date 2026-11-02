"""Synthetic lognormal life-fit checks against independent likelihood identities."""

import math

import numpy as np
import pytest
from pydantic import ValidationError
from scipy.optimize import minimize
from scipy.stats import norm

from pipeline_integrity.adapters import MaterialClient
from pipeline_integrity.contracts import EvidenceRef, RunContext


CONTEXT = RunContext(
    asset_id="synthetic_creep_batch",
    scenario_id="known_lognormal_rupture_population",
    clock_id="elapsed_seconds_from_test_start",
    origin="synthetic",
    evidence=(
        EvidenceRef(
            id="authored_fixture",
            citation="Independently authored synthetic constant-condition specimen population",
            origin="synthetic",
            license_id="MIT",
        ),
    ),
)
CLIENT = MaterialClient()
STRESSES = [50e6] * 8 + [200e6] * 8
RESIDUALS = [-0.3, -0.1, 0.1, 0.3] * 4
REFERENCE_STRESS = 100e6
REFERENCE_MEDIAN = 1e6
STRESS_EXPONENT = 4.0


def fixture(*, censored=None, time_factor=1.0, stress_factor=1.0):
    residuals = list(RESIDUALS)
    latent = [
        REFERENCE_MEDIAN
        * (stress / REFERENCE_STRESS) ** (-STRESS_EXPONENT)
        * math.exp(error)
        for stress, error in zip(STRESSES, residuals, strict=True)
    ]
    right_censored = list(censored or [False] * len(latent))
    times = [value * time_factor for value in latent]
    # Censor only the latest latent failures at an earlier administrative endpoint.
    for i, is_censored in enumerate(right_censored):
        if is_censored:
            times[i] *= 0.72
    return {
        "material_condition_id": "synthetic_heat_treatment_A",
        "stress_basis": "initial_nominal_constant_load",
        "temperature_basis": "nominal_specified",
        "family": "stress_only",
        "specimen_id": [f"specimen-{i:02d}" for i in range(len(latent))],
        "initial_stress_Pa": [stress * stress_factor for stress in STRESSES],
        "temperature_K": [600.0] * len(latent),
        "endpoint_time_s": times,
        "right_censored": right_censored,
        "temperature_tolerance_K": 0.0,
        "independent_specimens_confirmed": True,
        "common_mechanism_and_condition_confirmed": True,
        "evidence": ({
            "source_id": "authored_fixture",
            "origin": "synthetic",
            "citation": "Independently authored synthetic constant-condition population",
            "license_id": "MIT",
            "sha256": None,
        },),
    }


def fit(parameters):
    result = CLIENT.evaluate(
        "creep.lognormal_life_fit", parameters, CONTEXT, location_id="creep_test_section"
    )
    return result


def model_of(result):
    assert result.status == "computed"
    return result.payload["values"]["model"]


def test_complete_population_recovers_known_median_stress_exponent_and_scatter():
    result = fit(fixture())
    model = model_of(result)
    assert model["reference_stress_Pa"] == pytest.approx(REFERENCE_STRESS, rel=2e-14)
    assert math.exp(model["log_reference_median_time_s"]) == pytest.approx(REFERENCE_MEDIAN, rel=2e-8)
    assert model["stress_exponent"] == pytest.approx(STRESS_EXPONENT, rel=2e-8)
    assert model["log_life_sigma"] == pytest.approx(math.sqrt(np.mean(np.square(RESIDUALS))), rel=2e-8)
    assert model["activation_energy_J_mol"] is None


def test_uniform_time_unit_scale_changes_only_median_and_density_jacobian():
    base = fit(fixture())
    scaled = fit(fixture(time_factor=60.0))
    left, right = model_of(base), model_of(scaled)
    assert right["log_reference_median_time_s"] - left["log_reference_median_time_s"] == pytest.approx(
        math.log(60.0), abs=2e-8
    )
    assert right["stress_exponent"] == pytest.approx(left["stress_exponent"], rel=2e-8)
    assert right["log_life_sigma"] == pytest.approx(left["log_life_sigma"], rel=2e-8)
    assert scaled.payload["values"]["negative_log_likelihood_seconds"] - base.payload["values"][
        "negative_log_likelihood_seconds"
    ] == pytest.approx(len(STRESSES) * math.log(60.0), abs=2e-7)


def test_uniform_stress_unit_scale_changes_reference_stress_but_not_life_law():
    base = model_of(fit(fixture()))
    scaled = model_of(fit(fixture(stress_factor=1e-6)))
    assert scaled["reference_stress_Pa"] == pytest.approx(base["reference_stress_Pa"] * 1e-6)
    assert scaled["stress_exponent"] == pytest.approx(base["stress_exponent"], rel=2e-8)
    assert scaled["log_reference_median_time_s"] == pytest.approx(base["log_reference_median_time_s"], abs=2e-8)
    assert scaled["log_life_sigma"] == pytest.approx(base["log_life_sigma"], rel=2e-8)


def test_right_censoring_fit_matches_independent_direct_survival_likelihood():
    censored = [i in {3, 7, 11, 15} for i in range(16)]
    parameters = fixture(censored=censored)
    result = fit(parameters)
    model = model_of(result)
    stress = np.log(np.asarray(parameters["initial_stress_Pa"]) / model["reference_stress_Pa"])
    y = np.log(parameters["endpoint_time_s"])
    mask = np.asarray(censored)

    def direct_nll(theta):
        mu = theta[0] - theta[1] * stress
        sigma = math.exp(theta[2])
        return float(-np.where(mask, norm.logsf(y, mu, sigma), norm.logpdf(y, mu, sigma)).sum())

    oracle = minimize(direct_nll, [math.log(REFERENCE_MEDIAN), STRESS_EXPONENT, math.log(0.3)], method="Nelder-Mead",
                      options={"xatol": 1e-11, "fatol": 1e-11, "maxiter": 20000})
    assert oracle.success
    # The reported likelihood is expressed on seconds, so add the failure-only log-time Jacobian.
    expected_seconds_nll = oracle.fun + y[~mask].sum()
    assert result.payload["values"]["negative_log_likelihood_seconds"] == pytest.approx(expected_seconds_nll, abs=2e-6)
    assert model["stress_exponent"] == pytest.approx(oracle.x[1], rel=2e-5)
    assert model["log_life_sigma"] == pytest.approx(math.exp(oracle.x[2]), rel=2e-5)


def test_single_stress_or_too_few_failures_is_withheld_as_insufficient_data():
    one_stress = fixture()
    one_stress["initial_stress_Pa"] = [100e6] * 16
    result = fit(one_stress)
    assert result.status == "needs_data"
    too_few = fixture(censored=[i >= 4 for i in range(16)])
    assert fit(too_few).status == "needs_data"


def test_duplicate_specimen_identity_is_rejected_at_contract_boundary():
    parameters = fixture()
    parameters["specimen_id"][1] = parameters["specimen_id"][0]
    with pytest.raises(ValidationError):
        CLIENT.evaluate(
            "creep.lognormal_life_fit", parameters, CONTEXT, location_id="creep_test_section"
        )
