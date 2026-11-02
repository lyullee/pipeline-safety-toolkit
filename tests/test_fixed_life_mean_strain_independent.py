import math
import pytest
from pipeline_integrity.contracts import EvidenceRef,RunContext
from pipeline_integrity.fixed_life_mean_strain import FixedLifeMeanStrainRequest,evaluate_fixed_life_mean_strain

CTX=RunContext(asset_id='mean-sensitive-curve',scenario_id='fixed-life-observation',clock_id='whole-cycles',origin='synthetic',
    evidence=(EvidenceRef(id='curve-calibration',citation='Authored uniaxial strain-life calibration form; actual mean/maximum stress and finite support reviewed as synthetic',origin='synthetic'),))


def parameters(method):
    p=dict(elastic_modulus_Pa=200e9,fatigue_strength_coefficient_Pa=900e6,fatigue_strength_exponent=-.5,
        fatigue_ductility_coefficient=.6,fatigue_ductility_exponent=-1.,minimum_valid_cycles=1.,maximum_valid_cycles=1e8,
        material_applicability_reviewed=True)
    if method=='morrow':p.update(mean_stress_Pa=100e6,mean_stress_sign_convention='tension_positive',coefficients_and_mean_stress_basis_reviewed=True)
    else:p.update(maximum_stress_Pa=400e6,stress_and_strain_measurement_basis_reviewed=True)
    return p


def forward_reference(method,p,cycles):
    sf=p['fatigue_strength_coefficient_Pa'];ef=p['fatigue_ductility_coefficient'];young=p['elastic_modulus_Pa'];x=1/math.sqrt(2*cycles)
    if method=='morrow':return (sf-p['mean_stress_Pa'])/young*x+ef*x*x
    return (sf*sf/young*x*x+sf*ef*x*x*x)/p['maximum_stress_Pa']


def request(method='morrow',cycles=25.,**changes):
    p=parameters(method)
    return FixedLifeMeanStrainRequest(**dict(dict(asset_id=CTX.asset_id,location_id='material-point',clock_id=CTX.clock_id,
        method=method,target_whole_cycles=cycles,native_parameters_without_strain=p,
        reviewed_strain_amplitude_bracket=(forward_reference(method,p,cycles*4),forward_reference(method,p,cycles/4)),
        evidence_ids=('curve-calibration',),fixed_life_inverse_and_strain_bracket_reviewed=True),**changes))


@pytest.mark.parametrize('method',['morrow','swt'])
@pytest.mark.parametrize('cycles',[25.,2500.])
def test_existing_owner_fixed_life_inverse_matches_independent_special_polynomial(method,cycles):
    q=request(method,cycles);r=evaluate_fixed_life_mean_strain(q,CTX);assert r['status']=='computed'
    expected=forward_reference(method,q.native_parameters_without_strain,cycles)
    assert r['calculated_strain_amplitude']==pytest.approx(expected,rel=3e-9)
    assert r['native_predicted_whole_cycles']==pytest.approx(cycles,rel=3e-8)
    assert r['elastic_calculated_pseudo_stress_amplitude_pa']==q.native_parameters_without_strain['elastic_modulus_Pa']*r['calculated_strain_amplitude']
    assert not r['elastic_calculated_stress_is_actual_stress'] and not r['actual_stress_evolution_predicted']


def test_continuous_plastic_dominance_does_not_become_a_hard_mean_insensitivity_rule():
    effects=[]
    for cycles in [25.,25000.]:
        q=request('morrow',cycles);p=dict(q.native_parameters_without_strain);p['mean_stress_Pa']=0.
        zero=evaluate_fixed_life_mean_strain(q.model_copy(update={'native_parameters_without_strain':p}),CTX)
        mean=evaluate_fixed_life_mean_strain(q,CTX)
        effects.append(1-mean['calculated_strain_amplitude']/zero['calculated_strain_amplitude'])
    assert 0<effects[0]<effects[1]


@pytest.mark.parametrize('method',['morrow','swt'])
def test_valid_native_strains_must_actually_bracket_the_target_life(method):
    q=request(method);p=q.native_parameters_without_strain
    q=q.model_copy(update={'reviewed_strain_amplitude_bracket':(forward_reference(method,p,100.),forward_reference(method,p,50.))})
    r=evaluate_fixed_life_mean_strain(q,CTX)
    assert r['status']=='bracket_does_not_bound_fixed_life' and 'calculated_strain_amplitude' not in r


@pytest.mark.parametrize('method',['morrow','swt'])
def test_unknown_bracket_review_is_not_a_fatigue_prediction(method):
    assert evaluate_fixed_life_mean_strain(request(method,fixed_life_inverse_and_strain_bracket_reviewed=False),CTX)['status']=='needs_data'


@pytest.mark.parametrize('method',['morrow','swt'])
def test_target_beyond_calibration_is_not_reached_by_expanding_support(method):
    q=request(method).model_copy(update={'target_whole_cycles':2e8})
    assert evaluate_fixed_life_mean_strain(q,CTX)['status']=='out_of_scope'


def test_foreign_context_and_given_unknown_strain_are_rejected():
    with pytest.raises(ValueError):evaluate_fixed_life_mean_strain(request(asset_id='another'),CTX)
    p=parameters('morrow');p['strain_amplitude']=.01
    with pytest.raises(ValueError):request(native_parameters_without_strain=p)


def test_native_rejection_of_a_trial_does_not_extend_calibration_to_find_a_root():
    q=request(reviewed_strain_amplitude_bracket=(.7,.8))
    r=evaluate_fixed_life_mean_strain(q,CTX)
    assert r['status']=='solver_withheld' and 'calculated_strain_amplitude' not in r
