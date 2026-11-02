"""Independent identities for bounded one-exposure spatial extreme-value calculations."""

import math

import numpy as np
import pytest
from pydantic import ValidationError

from pipeline_integrity.adapters import MaterialClient
from pipeline_integrity.contracts import EvidenceRef, RunContext


CONTEXT = RunContext(
    asset_id="synthetic_surface_population",
    scenario_id="equal_area_block_maxima",
    clock_id="single_declared_exposure",
    origin="synthetic",
    evidence=(
        EvidenceRef(
            id="authored",
            citation="Independently authored deterministic extreme-value calculation cases",
            origin="synthetic",
            license_id="MIT",
        ),
    ),
)
CLIENT = MaterialClient()


def parameters(**overrides):
    rng = np.random.default_rng(7341)
    maxima = (0.003 + rng.gumbel(loc=0.0, scale=0.00045, size=24)).tolist()
    values = {
        "extreme_value_family": "gumbel",
        "population_id": "synthetic-population-A",
        "exposure_condition_id": "single-inspection-A",
        "block_ids": [f"block-{i:02d}" for i in range(len(maxima))],
        "maximum_depth_m": maxima,
        "block_area_m2": 0.25,
        "target_block_count": 120,
        "independent_equivalent_blocks": True,
        "same_exposure_and_surface_population": True,
        "complete_uncensored_block_maxima": True,
        "maximum_target_to_sampled_area_ratio": 8.0,
        "depth_threshold_m": 0.004,
        "quantile_probability": 0.975,
        "confidence_level": 0.90,
        "diagnostic_resamples": 99,
        "parameter_resamples": 99,
        "seed": 84,
        "minimum_goodness_of_fit_pvalue": 0.0,
        "maximum_negative_depth_probability": 1.0,
    }
    values.update(overrides)
    return values


def evaluate(args):
    return CLIENT.evaluate("corrosion.gumbel_area_maximum", args, CONTEXT, location_id="surface-A")


def test_gumbel_area_quantile_and_exceedance_match_closed_form_from_fitted_parameters():
    result = evaluate(parameters())
    assert result.status == "computed"
    values = result.payload["values"]

    # Gumbel maximum CDF: F(x)=exp(-exp(-(x-mu)/beta)); target maximum CDF is F(x)^N.
    q_single_block = 0.975 ** (1.0 / 120)
    expected_target_quantile = values["fitted_block_location_m"] - values["fitted_block_scale_m"] * math.log(
        -math.log(q_single_block)
    )
    fitted_block_cdf_at_threshold = math.exp(
        -math.exp(
            -(0.004 - values["fitted_block_location_m"]) / values["fitted_block_scale_m"]
        )
    )
    expected_area_exceedance = 1.0 - fitted_block_cdf_at_threshold**120

    assert values["observed_area_m2"] == pytest.approx(24 * 0.25)
    assert values["target_area_m2"] == pytest.approx(120 * 0.25)
    assert values["target_maximum_depth_quantile_m"] == pytest.approx(expected_target_quantile, rel=2e-12)
    assert values["area_depth_exceedance_probability"] == pytest.approx(expected_area_exceedance, abs=2e-12)
    assert values["fitted_block_scale_m"] > 0
    assert values["gof_monte_carlo_resamples"] == 99
    assert len(values["depth_quantile_parameter_interval_m"]) == 2


@pytest.mark.parametrize(
    "declaration",
    [
        {"independent_equivalent_blocks": False},
        {"same_exposure_and_surface_population": False},
        {"complete_uncensored_block_maxima": False},
    ],
)
def test_unsubstantiated_sampling_contract_withholds_estimate(declaration):
    result = evaluate(parameters(**declaration))
    assert result.status == "needs_data"
    assert result.payload["values"] == {}


def test_target_area_ratio_above_declared_bound_is_rejected_as_out_of_scope():
    result = evaluate(parameters(maximum_target_to_sampled_area_ratio=4.99))
    assert result.status == "out_of_scope"
    assert result.payload["values"] == {}


def test_constant_maxima_do_not_claim_an_identified_extreme_value_scale():
    result = evaluate(parameters(maximum_depth_m=[0.003] * 24))
    assert result.status == "needs_data"
    assert result.payload["values"] == {}


def test_block_ids_are_unique_and_aligned_with_observations():
    duplicate_ids = ["same"] * 24
    with pytest.raises(ValidationError):
        evaluate(parameters(block_ids=duplicate_ids))
    with pytest.raises(ValidationError):
        evaluate(parameters(maximum_depth_m=[0.002] * 7, block_ids=[f"b{i}" for i in range(7)]))
