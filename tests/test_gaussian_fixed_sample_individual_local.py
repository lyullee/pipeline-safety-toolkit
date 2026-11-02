"""Independent Gaussian-tail probabilities and mean/noise scaling references."""

import math

import pytest
from pydantic import ValidationError

from pipeline_integrity.leak_signal_statistics import (
    GaussianFixedSampleRequest, design_gaussian_fixed_sample_test,
)


def case(**updates):
    fields=dict(noise_standard_deviation=4.0,sample_count=16,
        false_alarm_probability=0.15865525393145707,miss_probability=0.15865525393145707,
        signal_unit="kg/s",null_mean=3.0,iid_gaussian_known_scale_reviewed=True)
    fields.update(updates)
    return GaussianFixedSampleRequest(**fields)


def test_known_one_sigma_gaussian_reference_has_independent_threshold_and_shift():
    r=design_gaussian_fixed_sample_test(case())
    assert r.mean_standard_error == 1.0
    assert r.alarm_threshold == pytest.approx(4.0,abs=2e-15)
    assert r.detectable_positive_mean_shift == pytest.approx(2.0,abs=2e-15)
    assert r.signal_unit == "kg/s"
    assert not r.noise_parameters_fitted and not r.observed_detection_performance_established


@pytest.mark.parametrize("alpha,beta,n,sigma", [(0.05,0.1,25,2.0),(0.01,0.02,100,0.8),(1e-20,1e-12,4,3.0)])
def test_requested_error_rates_match_independent_erfc_tail_integration(alpha,beta,n,sigma):
    q=case(false_alarm_probability=alpha,miss_probability=beta,sample_count=n,noise_standard_deviation=sigma)
    r=design_gaussian_fixed_sample_test(q)
    se=sigma/math.sqrt(n)
    null_distance=(r.alarm_threshold-q.null_mean)/se
    alternate_mean=q.null_mean+r.detectable_positive_mean_shift
    missed_distance=(alternate_mean-r.alarm_threshold)/se
    assert 0.5*math.erfc(null_distance/math.sqrt(2)) == pytest.approx(alpha,rel=3e-13)
    assert 0.5*math.erfc(missed_distance/math.sqrt(2)) == pytest.approx(beta,rel=3e-13)


def test_fourfold_sample_count_halves_shift_and_threshold_offset():
    a=design_gaussian_fixed_sample_test(case())
    b=design_gaussian_fixed_sample_test(case(sample_count=64))
    assert b.detectable_positive_mean_shift == pytest.approx(a.detectable_positive_mean_shift/2)
    assert b.alarm_threshold-3 == pytest.approx((a.alarm_threshold-3)/2)


def test_consistent_positive_affine_signal_transform_preserves_design_probability():
    a=design_gaussian_fixed_sample_test(case())
    b=design_gaussian_fixed_sample_test(case(noise_standard_deviation=12.0,null_mean=16.0))
    assert b.alarm_threshold == pytest.approx(3*a.alarm_threshold+7)
    assert b.detectable_positive_mean_shift == pytest.approx(3*a.detectable_positive_mean_shift)


@pytest.mark.parametrize("field", ["noise_standard_deviation","sample_count","false_alarm_probability","miss_probability"])
def test_unknown_design_input_remains_unknown(field):
    r=design_gaussian_fixed_sample_test(case(**{field:None}))
    assert r.status == "needs_data" and r.alarm_threshold is None
    assert r.detectable_positive_mean_shift is None


def test_unreviewed_sampling_assumptions_withhold_design():
    assert design_gaussian_fixed_sample_test(case(iid_gaussian_known_scale_reviewed=False)).status == "needs_data"


@pytest.mark.parametrize("updates", [
    {"sample_count":0},{"sample_count":2.5},{"sample_count":True},
    {"false_alarm_probability":0.0},{"miss_probability":0.5},
    {"false_alarm_probability":float("nan")},{"noise_standard_deviation":float("inf")},
    {"noise_standard_deviation":-1.0},
])
def test_invalid_modified_requests_revalidate_at_execution_boundary(updates):
    with pytest.raises(ValidationError):
        design_gaussian_fixed_sample_test(case().model_copy(update=updates))
