"""Independent log-rate coordinates and threshold/critical limiting branches."""
import math
import pytest
from pipeline_integrity.crack_growth_modifiers import CrackGrowthModifierRequest,evaluate_crack_growth_modifiers

def calc(**kw):
    data=dict(baseline_rate_m_cycle=1e-8,intensity_range_pa_sqrt_m=20e6,maximum_intensity_pa_sqrt_m=30e6,
        threshold_range_pa_sqrt_m=4e6,critical_intensity_pa_sqrt_m=80e6,threshold_exponent=2.,critical_exponent=1.5,
        calibration_basis_id='authored-calibration',baseline_and_modifiers_same_calibration_reviewed=True)
    return evaluate_crack_growth_modifiers(CrackGrowthModifierRequest(**(data|kw)))

@pytest.mark.parametrize('p,q',[(.5,.5),(1.,1.),(2.,1.5),(3.,2.)])
def test_rate_matches_separate_log_coordinate_reference(p,q):
    c=2e-30;m=3.;dk=20e6;k=30e6;th=4e6;kc=80e6
    result=calc(baseline_rate_m_cycle=c*dk**m,threshold_exponent=p,critical_exponent=q)
    reference=math.exp(math.log(c)+m*math.log(dk)+p*(math.log(dk-th)-math.log(dk))-q*(math.log(kc-k)-math.log(kc)))
    assert result.modified_rate_m_cycle==pytest.approx(reference,rel=2e-14)

@pytest.mark.parametrize('dk',[0.,2e6,4e6])
def test_arrest_branch_and_exact_threshold_return_zero(dk):
    result=calc(intensity_range_pa_sqrt_m=dk)
    assert result.modified_rate_m_cycle==0
    assert result.at_or_below_threshold is True

@pytest.mark.parametrize('k',[80e6,90e6])
def test_critical_boundary_never_returns_divergent_growth(k):
    result=calc(maximum_intensity_pa_sqrt_m=k)
    assert result.status=='critical_boundary_reached'
    assert result.modified_rate_m_cycle is None
    assert result.fracture_event_or_remaining_life_inferred is False

def test_common_intensity_coordinate_scaling_preserves_modifiers():
    one=calc();two=calc(intensity_range_pa_sqrt_m=40e6,maximum_intensity_pa_sqrt_m=60e6,threshold_range_pa_sqrt_m=8e6,critical_intensity_pa_sqrt_m=160e6)
    assert two.modified_rate_m_cycle==pytest.approx(one.modified_rate_m_cycle)

def test_missing_calibration_is_not_assigned_material_constants():
    assert calc(calibration_basis_id=None).status=='needs_data'

def test_increasing_intensity_near_critical_accelerates_without_crossing_boundary():
    rates=[calc(maximum_intensity_pa_sqrt_m=k).modified_rate_m_cycle for k in (40e6,60e6,79e6)]
    assert rates[0]<rates[1]<rates[2]
