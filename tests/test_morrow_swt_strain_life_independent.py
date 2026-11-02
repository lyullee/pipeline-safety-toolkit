"""Selected existing mean-sensitive strain-life owners, not a new solver."""
from pathlib import Path
from decimal import Decimal,localcontext
import math,os
import matintegrity
import pytest
from pipeline_integrity.contracts import EvidenceRef,RunContext
from pipeline_integrity.material_exchange import prepare_material_job,evaluate_material_job

CTX=RunContext(asset_id='uniaxial-calibration',scenario_id='mean-sensitive-strain-life',clock_id='whole-cycles',origin='synthetic',
    evidence=(EvidenceRef(id='calibration',citation='Authored calibrated-form parameters for independent algebraic special cases; no experimental material fit claimed',origin='synthetic'),))


def parameters():
    return dict(strain_amplitude=.005,elastic_modulus_Pa=200e9,fatigue_strength_coefficient_Pa=900e6,
        fatigue_strength_exponent=-.5,fatigue_ductility_coefficient=.6,fatigue_ductility_exponent=-1.,
        minimum_valid_cycles=1.,maximum_valid_cycles=1e8)


def run(kind,**changes):
    q=parameters()
    if kind=='morrow':q.update(mean_stress_Pa=100e6,mean_stress_sign_convention='tension_positive',coefficients_and_mean_stress_basis_reviewed=True,material_applicability_reviewed=True)
    if kind=='swt':q.update(maximum_stress_Pa=400e6,stress_and_strain_measurement_basis_reviewed=True,material_applicability_reviewed=True)
    q.update(changes)
    job=prepare_material_job('fatigue.'+kind+'_strain_life',q,CTX,location_id='calibrated-point',evidence_ids=('calibration',))
    receipt=evaluate_material_job(job,CTX);receipt.check_integrity()
    return receipt.native,q


def test_morrow_selected_owner_import_is_actual_immutable_version():
    assert Path(matintegrity.__file__).resolve().is_relative_to(Path(os.environ['PIPELINE_SELECTED_MATERIAL_SOURCE_ROOT']).resolve())


def test_swt_selected_owner_import_is_actual_immutable_version():
    assert Path(matintegrity.__file__).resolve().is_relative_to(Path(os.environ['PIPELINE_SELECTED_MATERIAL_SOURCE_ROOT']).resolve())


@pytest.mark.parametrize('mean',[-150e6,0.,250e6])
@pytest.mark.parametrize('strain',[.003,.025])
def test_morrow_inverse_matches_independent_high_precision_quadratic(mean,strain):
    result,p=run('morrow',mean_stress_Pa=mean,strain_amplitude=strain);assert result.status=='computed'
    with localcontext() as context:
        context.prec=75
        a=(Decimal(str(p['fatigue_strength_coefficient_Pa']))-Decimal(str(mean)))/Decimal(str(p['elastic_modulus_Pa']))
        b=Decimal(str(p['fatigue_ductility_coefficient']));target=Decimal(str(strain))
        x=2*target/(a+(a*a+4*b*target).sqrt());cycles=1/(2*x*x)
    actual=result.payload['values'];assert actual['cycles_to_failure']==pytest.approx(float(cycles),rel=3e-11)
    assert actual['reversals_to_failure']==2*actual['cycles_to_failure']


def test_morrow_mean_effect_continuously_weakens_as_plastic_term_dominates():
    penalties=[]
    for strain in [.002,.008,.032]:
        zero,_=run('morrow',mean_stress_Pa=0.,strain_amplitude=strain)
        tensile,_=run('morrow',mean_stress_Pa=250e6,strain_amplitude=strain)
        n0=zero.payload['values']['cycles_to_failure'];nt=tensile.payload['values']['cycles_to_failure']
        assert 0<nt<n0;penalties.append(1-nt/n0)
    assert all(b<a for a,b in zip(penalties,penalties[1:]))


def test_morrow_zero_mean_reduces_to_existing_fully_reversed_strain_life():
    actual,p=run('morrow',mean_stress_Pa=0.);base=parameters()
    job=prepare_material_job('fatigue.strain_life',base,CTX,location_id='calibrated-point',evidence_ids=('calibration',))
    expected=evaluate_material_job(job,CTX).native
    assert actual.payload['values']['cycles_to_failure']==pytest.approx(expected.payload['values']['cycles_to_failure'],rel=3e-11)


@pytest.mark.parametrize('mean',[900e6,1e9])
def test_morrow_nonpositive_elastic_term_is_withheld(mean):
    result,_=run('morrow',mean_stress_Pa=mean);assert result.status=='out_of_scope'


@pytest.mark.parametrize('field',['coefficients_and_mean_stress_basis_reviewed','material_applicability_reviewed'])
def test_morrow_unknown_basis_is_not_a_calibration(field):
    result,_=run('morrow',**{field:False});assert result.status=='needs_data'


def test_morrow_root_outside_supplied_cycle_support_is_not_clipped():
    result,_=run('morrow',strain_amplitude=1.);assert result.status=='out_of_scope'


@pytest.mark.parametrize('cycles',[1.,1e8])
def test_morrow_declared_cycle_endpoint_remains_an_actual_supported_state(cycles):
    p=parameters();strain=(p['fatigue_strength_coefficient_Pa']-100e6)/p['elastic_modulus_Pa']*(2*cycles)**p['fatigue_strength_exponent']+p['fatigue_ductility_coefficient']*(2*cycles)**p['fatigue_ductility_exponent']
    result,_=run('morrow',strain_amplitude=strain)
    assert result.status=='computed'
    assert result.payload['values']['cycles_to_failure']==pytest.approx(cycles,rel=3e-11)


@pytest.mark.parametrize('maximum',[200e6,500e6])
@pytest.mark.parametrize('strain',[.003,.015])
def test_swt_inverse_matches_independent_high_precision_monotone_cubic(maximum,strain):
    result,p=run('swt',maximum_stress_Pa=maximum,strain_amplitude=strain);assert result.status=='computed'
    with localcontext() as context:
        context.prec=75
        sf=Decimal(str(p['fatigue_strength_coefficient_Pa']));a=sf*sf/Decimal(str(p['elastic_modulus_Pa']));b=sf*Decimal(str(p['fatigue_ductility_coefficient']));target=Decimal(str(maximum))*Decimal(str(strain))
        low=Decimal(0);high=Decimal(1)
        for _ in range(270):
            mid=(low+high)/2
            if a*mid**2+b*mid**3<target:low=mid
            else:high=mid
        x=(low+high)/2;cycles=1/(2*x*x)
    v=result.payload['values'];assert v['cycles_to_failure']==pytest.approx(float(cycles),rel=3e-11)
    assert v['swt_parameter_Pa']==maximum*strain
    assert v['reversals_to_failure']==2*v['cycles_to_failure']


def test_swt_zero_mean_consistent_stress_strain_anchor_recovers_baseline_cycles():
    n=125.;p=parameters();stress=p['fatigue_strength_coefficient_Pa']*(2*n)**p['fatigue_strength_exponent']
    strain=stress/p['elastic_modulus_Pa']+p['fatigue_ductility_coefficient']*(2*n)**p['fatigue_ductility_exponent']
    result,_=run('swt',maximum_stress_Pa=stress,strain_amplitude=strain)
    assert result.payload['values']['cycles_to_failure']==pytest.approx(n,rel=3e-11)


def test_swt_common_stress_modulus_scaling_preserves_life_and_scales_parameter():
    first,_=run('swt');second,_=run('swt',maximum_stress_Pa=800e6,elastic_modulus_Pa=400e9,fatigue_strength_coefficient_Pa=1800e6)
    assert first.payload['values']['cycles_to_failure']==pytest.approx(second.payload['values']['cycles_to_failure'],rel=3e-11)
    assert second.payload['values']['swt_parameter_Pa']==2*first.payload['values']['swt_parameter_Pa']


@pytest.mark.parametrize('maximum',[0.,-100e6])
def test_swt_nonpositive_maximum_does_not_infer_no_material_fatigue(maximum):
    result,_=run('swt',maximum_stress_Pa=maximum);assert result.status=='out_of_scope'


@pytest.mark.parametrize('field',['stress_and_strain_measurement_basis_reviewed','material_applicability_reviewed'])
def test_swt_unknown_measurement_or_material_basis_is_withheld(field):
    result,_=run('swt',**{field:False});assert result.status=='needs_data'


def test_swt_outside_cycle_support_is_not_clipped():
    result,_=run('swt',strain_amplitude=1.);assert result.status=='out_of_scope'


@pytest.mark.parametrize('cycles',[1.,1e8])
def test_swt_declared_cycle_endpoint_remains_an_actual_supported_state(cycles):
    p=parameters();maximum=400e6
    parameter=p['fatigue_strength_coefficient_Pa']**2/p['elastic_modulus_Pa']*(2*cycles)**(2*p['fatigue_strength_exponent'])+p['fatigue_strength_coefficient_Pa']*p['fatigue_ductility_coefficient']*(2*cycles)**(p['fatigue_strength_exponent']+p['fatigue_ductility_exponent'])
    result,_=run('swt',maximum_stress_Pa=maximum,strain_amplitude=parameter/maximum)
    # Check the selected owner's raw status separately from the public endpoint
    # compatibility path. The receipt preserves any native bracket rejection.
    from pipeline_integrity.swt_endpoint_compatibility import evaluate_swt_cycle_job
    inputs={**p,'maximum_stress_Pa':maximum,'strain_amplitude':parameter/maximum,
        'stress_and_strain_measurement_basis_reviewed':True,'material_applicability_reviewed':True}
    job=prepare_material_job('fatigue.swt_strain_life',inputs,CTX,
        location_id='calibrated-point',evidence_ids=('calibration',))
    endpoint=evaluate_swt_cycle_job(job,CTX)
    assert endpoint.status=='computed'
    assert endpoint.native_material_receipt.native.status == result.status
    assert endpoint.cycles_to_failure==pytest.approx(cycles,rel=3e-11)
    if result.status != 'computed':
        assert result.status=='out_of_scope'
        assert endpoint.endpoint_reconciled
        assert abs(endpoint.endpoint_parameter_residual_pa)<=endpoint.allowed_parameter_roundoff_pa
        assert endpoint.physical_cycle_support_unchanged
    else:
        assert result.payload['values']['cycles_to_failure']==pytest.approx(cycles,rel=3e-11)
