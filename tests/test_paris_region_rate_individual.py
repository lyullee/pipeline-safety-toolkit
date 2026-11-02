"""Exact-arithmetic and independently integrated scalar Paris-region references."""
from decimal import Decimal,localcontext
import math
import runpy

import pytest
from scipy.integrate import solve_ivp

from pipeline_integrity.paris_region_rate import ParisRegionRateRequest as Request,evaluate_paris_region_rate as evaluate

BASE=runpy.run_path('examples/fracture_screen.py')['make_growth']().calibration


def request(**kw):
    calibration=BASE.model_copy(update={'coefficient_m_cycle_per_pa_sqrt_m_power':1e-22,'exponent':2.,
        'minimum_delta_k_pa_sqrt_m':1e-20,'maximum_delta_k_pa_sqrt_m':1e9})
    return Request(**(dict(id='authored-rate',material_condition_id=calibration.material_condition_id,
        intensity_range_pa_sqrt_m=3e6,temperature_k=293.,stress_ratio=.3,calibration=calibration,
        region_ii_mode_i_opening_and_same_calibration_reviewed=True)|kw))


@pytest.mark.parametrize('coefficient,exponent,intensity',[(2e-22,2.,3e6),(1e-30,3.,5e6),
    (4e-16,1.,1e7),(1e-25,1.5,2e6),(5e-40,4.,8e6),(1e-5,.5,100.)])
def test_scalar_rate_against_independent_seventy_five_digit_arithmetic(coefficient,exponent,intensity):
    q=request();q=q.model_copy(update={'intensity_range_pa_sqrt_m':intensity,
        'calibration':q.calibration.model_copy(update={'coefficient_m_cycle_per_pa_sqrt_m_power':coefficient,'exponent':exponent})})
    r=evaluate(q);assert r.status=='computed'
    with localcontext() as c:
        c.prec=75
        expected=Decimal(str(coefficient))*Decimal(str(intensity))**Decimal(str(exponent))
    assert r.growth_rate_m_per_whole_cycle==pytest.approx(float(expected),rel=3e-14)


@pytest.mark.parametrize('exponent',[1.5,2.,3.,4.])
def test_scalar_public_rate_integrates_to_independent_separated_depth_solution(exponent):
    initial=.001;factor=1.2*20e6*math.sqrt(math.pi);rate0=1e-9;cycles=2e5
    coefficient=rate0/(factor*math.sqrt(initial))**exponent
    q=request();q=q.model_copy(update={'calibration':q.calibration.model_copy(update={
        'coefficient_m_cycle_per_pa_sqrt_m_power':coefficient,'exponent':exponent})})
    def derivative(n,state):
        r=evaluate(q.model_copy(update={'intensity_range_pa_sqrt_m':factor*math.sqrt(float(state[0]))}))
        assert r.status=='computed';return [r.growth_rate_m_per_whole_cycle]
    sol=solve_ivp(derivative,(0.,cycles),[initial],method='DOP853',rtol=2e-12,atol=1e-14)
    assert sol.success
    lam=rate0/initial**(exponent/2);power=1-exponent/2
    expected=initial*math.exp(lam*cycles) if power==0 else (initial**power+power*lam*cycles)**(1/power)
    assert sol.y[0,-1]==pytest.approx(expected,rel=2e-10)


@pytest.mark.parametrize('field',['intensity_range_pa_sqrt_m','temperature_k','stress_ratio','calibration'])
def test_unknown_scalar_state_is_not_a_zero_rate(field):
    r=evaluate(request(**{field:None}));assert r.status=='needs_data' and r.growth_rate_m_per_whole_cycle is None


@pytest.mark.parametrize('field',['coefficient_m_cycle_per_pa_sqrt_m_power','exponent'])
def test_missing_calibration_scalar_is_not_inferred(field):
    q=request();assert evaluate(q.model_copy(update={'calibration':q.calibration.model_copy(update={field:None})})).status=='needs_data'


def test_review_and_material_identity_are_explicit():
    assert evaluate(request(region_ii_mode_i_opening_and_same_calibration_reviewed=False)).status=='needs_data'
    q=request();assert evaluate(q.model_copy(update={'calibration':q.calibration.model_copy(update={'applicability_reviewed':False})})).status=='needs_data'
    assert evaluate(request(material_condition_id='different-condition')).status=='out_of_scope'


@pytest.mark.parametrize('changes',[{'temperature_k':270.},{'temperature_k':330.},{'stress_ratio':.95},
    {'intensity_range_pa_sqrt_m':1e10},{'intensity_range_pa_sqrt_m':0.}])
def test_supported_region_exit_does_not_infer_arrest_or_fracture(changes):
    r=evaluate(request(**changes));assert r.status=='out_of_scope'
    assert r.growth_rate_m_per_whole_cycle is None and not r.threshold_arrest_or_fast_fracture_predicted


def test_declared_positive_threshold_is_not_turned_into_region_one_prediction():
    q=request();q=q.model_copy(update={'calibration':q.calibration.model_copy(update={'threshold_delta_k_pa_sqrt_m':4e6})})
    assert evaluate(q).status=='out_of_scope'


@pytest.mark.parametrize('coefficient,exponent,intensity',[(1e300,4.,1e6),(1e-300,1.,1e-10)])
def test_overflow_and_underflow_are_not_false_finite_or_zero_physics(coefficient,exponent,intensity):
    q=request(intensity_range_pa_sqrt_m=intensity);q=q.model_copy(update={'calibration':q.calibration.model_copy(update={
        'coefficient_m_cycle_per_pa_sqrt_m_power':coefficient,'exponent':exponent})})
    r=evaluate(q);assert r.status=='numerically_unrepresentable' and r.growth_rate_m_per_whole_cycle is None


@pytest.mark.parametrize('changes',[{'intensity_range_pa_sqrt_m':float('nan')},{'stress_ratio':1.},{'intensity_range_pa_sqrt_m':-1.}])
def test_cloned_invalid_input_is_revalidated(changes):
    with pytest.raises(ValueError):evaluate(request().model_copy(update=changes))
