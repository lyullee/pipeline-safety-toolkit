"""Independent finite-sum, likelihood and information-balance references."""
import math

import pytest

from pipeline_integrity.leak_signal_statistics import (
    FiniteWindowMeanRequest, GaussianOnsetRequest, GaussianSPRTRequest,
    SpatialIncidentRateRequest, SymmetricGaussianWaldRequest,
    approximate_symmetric_gaussian_wald_count, estimate_gaussian_onset,
    evaluate_finite_window_mean, evaluate_gaussian_sprt, integrate_spatial_incident_rate,
)


def mean_case(**updates):
    values=dict(samples=(1.,5.,10.),signal_unit="kg/s",clock_id="plant_s",
        window_start_s=0.,window_finish_s=3.,support_and_weight_meaning_reviewed=True)
    return FiniteWindowMeanRequest(**(values|updates))


@pytest.mark.parametrize("weights,expected", [(None,16/3),((1.,2.,3.),41/6),((0.,0.,4.),10.)])
def test_finite_mean_independent_weighted_sum(weights,expected):
    result=evaluate_finite_window_mean(mean_case(weights=weights))
    assert result.mean==pytest.approx(expected,rel=2e-15)
    assert result.sample_count==3
    assert result.signal_unit=="kg/s" and not result.variance_reduction_inferred


def test_finite_mean_paired_permutation_and_positive_weight_scale_do_not_change_mean():
    a=evaluate_finite_window_mean(mean_case(weights=(1.,2.,3.)))
    b=evaluate_finite_window_mean(mean_case(samples=(10.,1.,5.),weights=(30.,10.,20.)))
    assert b.mean==pytest.approx(a.mean,rel=2e-15)
    c=evaluate_finite_window_mean(mean_case(samples=(9.,21.,36.),weights=(1.,2.,3.)))
    assert c.mean==pytest.approx(3*a.mean+6,rel=2e-15)


@pytest.mark.parametrize("updates", [dict(samples=(1.,None,3.)),
    dict(samples=(1.,None,3.),weights=(1.,0.,1.)),dict(weights=(1.,None,1.)),
    dict(support_and_weight_meaning_reviewed=False)])
def test_finite_mean_unknown_inputs_are_not_silently_dropped(updates):
    result=evaluate_finite_window_mean(mean_case(**updates))
    assert result.status=="needs_data" and result.mean is None


@pytest.mark.parametrize("updates", [dict(weights=(0.,0.,0.)),dict(weights=(1.,2.)),
    dict(weights=(1.,-1.,1.)),dict(window_start_s=-1e308,window_finish_s=1e308)])
def test_finite_mean_invalid_weight_or_window_rejected(updates):
    with pytest.raises(ValueError): evaluate_finite_window_mean(mean_case(**updates))


def rate_case(**updates):
    values=dict(segment_lengths_m=(100.,250.,50.),segment_intensities_per_m_year=(1e-4,2e-4,0.),
                piecewise_constant_intensity_and_exposure_reviewed=True)
    return SpatialIncidentRateRequest(**(values|updates))


def test_spatial_frequency_integrates_supplied_exposure_without_probability_conversion():
    result=integrate_spatial_incident_rate(rate_case())
    assert result.incident_rate_per_year==pytest.approx(.06,rel=2e-15)
    assert not result.occurrence_probability_inferred and not result.intensity_fitted


def test_spatial_frequency_uniform_limit_and_partition_additivity():
    a=integrate_spatial_incident_rate(rate_case(segment_intensities_per_m_year=(1e-4,)*3))
    b=integrate_spatial_incident_rate(rate_case(segment_lengths_m=(400.,),
                                            segment_intensities_per_m_year=(1e-4,)))
    assert a.incident_rate_per_year==pytest.approx(400e-4)
    assert b.incident_rate_per_year==pytest.approx(a.incident_rate_per_year)


def test_spatial_frequency_zero_is_distinct_from_unknown_even_on_zero_length():
    zero=integrate_spatial_incident_rate(rate_case(segment_intensities_per_m_year=(0.,0.,0.)))
    unknown=integrate_spatial_incident_rate(rate_case(segment_lengths_m=(0.,),
        segment_intensities_per_m_year=(None,)))
    assert zero.status=="computed" and zero.incident_rate_per_year==0
    assert unknown.status=="needs_data" and unknown.incident_rate_per_year is None


@pytest.mark.parametrize("updates", [dict(segment_lengths_m=(-1.,250.,50.)),
    dict(segment_intensities_per_m_year=(1e-4,)),
    dict(segment_lengths_m=(1e308,),segment_intensities_per_m_year=(1e308,))])
def test_spatial_frequency_invalid_or_overflow_is_not_a_finite_rate(updates):
    with pytest.raises(ValueError): integrate_spatial_incident_rate(rate_case(**updates))


def sequence_values(samples=(2.,2.,2.),**updates):
    values=dict(clock_id="plant_s",window_start_s=10.,window_finish_s=20.,
        sample_times_s=tuple(11.+i for i in range(len(samples))),samples=samples,
        signal_unit="kg/s",null_mean=0.,alternative_mean=1.,noise_standard_deviation=1.,
        iid_gaussian_known_scale_reviewed=True)
    return values|updates


def sprt_case(samples=(2.,2.,2.),**updates):
    return GaussianSPRTRequest(**(sequence_values(samples)|dict(false_alarm_probability=.1,
        miss_probability=.1)|updates))


def density_ratio(x,null,alternate,sigma):
    # Independent density evaluation; chosen test coordinates avoid tail underflow.
    a=math.exp(-.5*((x-alternate)/sigma)**2)/(math.sqrt(2*math.pi)*sigma)
    b=math.exp(-.5*((x-null)/sigma)**2)/(math.sqrt(2*math.pi)*sigma)
    return math.log(a/b)


@pytest.mark.parametrize("samples,status,index", [((2.,2.,2.),"accepted_alternative",1),
    ((0.,)*7,"accepted_null",4),((.5,.5,.5),"continue",None)])
def test_sprt_sequence_matches_independent_normal_density_ratios(samples,status,index):
    q=sprt_case(samples)
    result=evaluate_gaussian_sprt(q)
    consumed=len(samples) if index is None else index+1
    expected=tuple(math.fsum(density_ratio(x,0.,1.,1.) for x in samples[:i+1])
                   for i in range(consumed))
    assert result.score_history==pytest.approx(expected,abs=2e-15)
    assert result.status==status and result.stopping_index_zero_based==index
    assert result.consumed_samples==consumed
    assert result.unconsumed_samples==len(samples)-consumed


def test_sprt_sequence_boundary_equality_continues_until_strict_crossing():
    alpha=1/(1+math.exp(2))
    boundary=math.log1p(-alpha)-math.log(alpha)
    sample=boundary+.5
    assert sample-.5==boundary
    result=evaluate_gaussian_sprt(sprt_case((sample,.5,2.),
        false_alarm_probability=alpha,miss_probability=alpha))
    assert result.score_history[0]==result.upper_boundary
    assert result.stopping_index_zero_based==2


def test_sprt_sequence_missing_before_stop_withholds_but_unconsumed_tail_is_not_read():
    missing=evaluate_gaussian_sprt(sprt_case((.5,None,10.)))
    stopped=evaluate_gaussian_sprt(sprt_case((4.,None,10.)))
    assert missing.status=="needs_data" and missing.consumed_samples==1
    assert missing.unconsumed_samples==2
    assert stopped.status=="accepted_alternative" and stopped.unconsumed_samples==2


def test_sprt_sequence_affine_signal_and_clock_translation_preserve_decision():
    a=evaluate_gaussian_sprt(sprt_case())
    b=evaluate_gaussian_sprt(sprt_case((13.,13.,13.),null_mean=7.,alternative_mean=10.,
        noise_standard_deviation=3.,window_start_s=110.,window_finish_s=120.,
        sample_times_s=(111.,112.,113.)))
    assert b.score_history==pytest.approx(a.score_history,abs=2e-15)
    assert b.stopping_time_s==a.stopping_time_s+100


@pytest.mark.parametrize("updates", [dict(alternative_mean=None),dict(noise_standard_deviation=None),
    dict(false_alarm_probability=None),dict(miss_probability=None),dict(iid_gaussian_known_scale_reviewed=False)])
def test_sprt_sequence_unknown_hypothesis_or_unreviewed_model_withholds(updates):
    assert evaluate_gaussian_sprt(sprt_case(**updates)).status=="needs_data"


@pytest.mark.parametrize("updates", [dict(sample_times_s=(12.,11.,13.)),
    dict(sample_times_s=(11.,11.,13.)),dict(sample_times_s=(9.,12.,13.)),
    dict(sample_times_s=(11.,12.)),dict(alternative_mean=0.),dict(window_start_s=-1e308,window_finish_s=1e308)])
def test_sprt_sequence_invalid_clock_or_hypothesis_not_silently_repaired(updates):
    with pytest.raises(ValueError): evaluate_gaussian_sprt(sprt_case(**updates))


def wald_case(**updates):
    return SymmetricGaussianWaldRequest(**(dict(positive_mean_shift=1.,noise_standard_deviation=2.,
        symmetric_error_probability=.1,iid_gaussian_known_scale_and_symmetric_errors_reviewed=True)|updates))


@pytest.mark.parametrize("alpha", [.2,.05,.001])
def test_wald_count_matches_separate_expected_boundary_over_gaussian_information(alpha):
    q=wald_case(symmetric_error_probability=alpha)
    upper=math.log((1-alpha)/alpha)
    lower=math.log(alpha/(1-alpha))
    expected_terminal_score=(1-alpha)*upper+alpha*lower
    # H1 expectation of log(f1/f0), obtained from E[X]=mu1 and E[X^2].
    mu0,mu1,sigma=0.,q.positive_mean_shift,q.noise_standard_deviation
    expected_x2=sigma*sigma+mu1*mu1
    expected_null_square=expected_x2-2*mu0*mu1+mu0*mu0
    expected_alt_square=expected_x2-2*mu1*mu1+mu1*mu1
    information=(expected_null_square-expected_alt_square)/(2*sigma*sigma)
    result=approximate_symmetric_gaussian_wald_count(q)
    assert result.approximate_expected_observations==pytest.approx(expected_terminal_score/information,rel=3e-14)
    assert not result.exact_stopping_time_claimed


def test_wald_count_noise_and_shift_scaling_and_subsample_flag():
    a=approximate_symmetric_gaussian_wald_count(wald_case())
    b=approximate_symmetric_gaussian_wald_count(wald_case(positive_mean_shift=2.))
    c=approximate_symmetric_gaussian_wald_count(wald_case(noise_standard_deviation=4.))
    tiny=approximate_symmetric_gaussian_wald_count(wald_case(positive_mean_shift=100.))
    assert b.approximate_expected_observations==pytest.approx(a.approximate_expected_observations/4)
    assert c.approximate_expected_observations==pytest.approx(a.approximate_expected_observations*4)
    assert tiny.below_one_observation and 0<tiny.approximate_expected_observations<1


@pytest.mark.parametrize("updates", [dict(positive_mean_shift=None),dict(noise_standard_deviation=None),
    dict(symmetric_error_probability=None),dict(iid_gaussian_known_scale_and_symmetric_errors_reviewed=False)])
def test_wald_count_unknown_model_parameters_are_not_fitted(updates):
    assert approximate_symmetric_gaussian_wald_count(wald_case(**updates)).status=="needs_data"


def onset_case(samples=(0.,0.,2.,2.),**updates):
    return GaussianOnsetRequest(**(sequence_values(samples)|dict(tie_policy="earliest")|updates))


@pytest.mark.parametrize("samples", [(0.,0.,2.,2.),(0.,0.,0.,0.),(1.,3.,0.,2.)])
def test_likelihood_onset_matches_exhaustive_suffix_density_product(samples):
    q=onset_case(samples)
    result=estimate_gaussian_onset(q)
    expected=tuple(math.log(math.prod(math.exp(density_ratio(x,0.,1.,1.)) for x in samples[i:]))
                   for i in range(len(samples)))
    assert result.candidate_suffix_scores==pytest.approx(expected,abs=2e-15)
    assert result.onset_index_zero_based==max(range(len(samples)),key=lambda i:expected[i])
    assert not result.field_leak_presence_established


@pytest.mark.parametrize("policy,expected", [("earliest",0),("latest",3)])
def test_likelihood_onset_exact_ties_keep_all_candidates_and_explicit_policy(policy,expected):
    result=estimate_gaussian_onset(onset_case((.5,.5,.5,.5),tie_policy=policy))
    assert result.maximizer_indices_zero_based==(0,1,2,3)
    assert result.onset_index_zero_based==expected


def test_likelihood_onset_clock_translation_and_missing_full_window():
    a=estimate_gaussian_onset(onset_case())
    b=estimate_gaussian_onset(onset_case(window_start_s=110.,window_finish_s=120.,
        sample_times_s=(111.,112.,113.,114.)))
    missing=estimate_gaussian_onset(onset_case((0.,None,2.,2.)))
    assert b.onset_time_s==a.onset_time_s+100
    assert b.candidate_suffix_scores==a.candidate_suffix_scores
    assert missing.status=="needs_data" and missing.onset_index_zero_based is None
