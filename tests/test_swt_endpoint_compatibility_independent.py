"""Selected existing mean-sensitive strain-life owners, not a new solver."""
from pathlib import Path
from decimal import Decimal,localcontext
import math,os
from types import SimpleNamespace
from pipeline_integrity.swt_endpoint_compatibility import evaluate_swt_cycle_job
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
    observed=evaluate_swt_cycle_job(job,CTX)
    values=dict(cycles_to_failure=observed.cycles_to_failure,reversals_to_failure=observed.reversals_to_failure,swt_parameter_Pa=observed.swt_parameter_pa)
    return SimpleNamespace(status=observed.status,payload={'values':values}),q




def test_swt_selected_owner_import_is_actual_immutable_version():
    assert Path(matintegrity.__file__).resolve().is_relative_to(Path(os.environ['PIPELINE_SELECTED_MATERIAL_SOURCE_ROOT']).resolve())

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
    assert result.status=='computed'
    assert result.payload['values']['cycles_to_failure']==pytest.approx(cycles,rel=3e-11)


def prepared_endpoint(which='maximum',factor=1.):
    p=parameters();cycles=p['maximum_valid_cycles'] if which=='maximum' else p['minimum_valid_cycles'];maximum=400e6
    parameter=p['fatigue_strength_coefficient_Pa']**2/p['elastic_modulus_Pa']*(2*cycles)**(2*p['fatigue_strength_exponent'])+p['fatigue_strength_coefficient_Pa']*p['fatigue_ductility_coefficient']*(2*cycles)**(p['fatigue_strength_exponent']+p['fatigue_ductility_exponent'])
    p.update(strain_amplitude=parameter/maximum*factor,maximum_stress_Pa=maximum,
        stress_and_strain_measurement_basis_reviewed=True,material_applicability_reviewed=True)
    return prepare_material_job('fatigue.swt_strain_life',p,CTX,location_id='calibrated-point',evidence_ids=('calibration',))


def test_swt_original_endpoint_rejection_is_preserved_with_explicit_reconciliation():
    job=prepared_endpoint();before=job.model_dump();r=evaluate_swt_cycle_job(job,CTX)
    assert r.status=='computed' and r.endpoint_reconciled
    assert r.reconciled_endpoint=='maximum' and r.cycles_to_failure==1e8
    assert r.native_material_receipt.native.status=='out_of_scope'
    r.native_material_receipt.check_integrity()
    assert abs(r.endpoint_parameter_residual_pa)<=r.allowed_parameter_roundoff_pa
    assert r.physical_cycle_support_unchanged and job.model_dump()==before


@pytest.mark.parametrize('which,factor',[('maximum',1-1e-8),('minimum',1+1e-8)])
def test_swt_real_outside_support_is_not_snapped_into_calibration(which,factor):
    r=evaluate_swt_cycle_job(prepared_endpoint(which,factor),CTX)
    assert r.status=='out_of_scope' and not r.endpoint_reconciled
    assert r.cycles_to_failure is None


def test_swt_ambiguous_narrow_interval_is_not_arbitrarily_assigned_an_endpoint():
    job=prepared_endpoint();p=dict(job.parameters);p['minimum_valid_cycles']=math.nextafter(p['maximum_valid_cycles'],0.)
    altered=prepare_material_job('fatigue.swt_strain_life',p,CTX,location_id='calibrated-point',evidence_ids=('calibration',))
    r=evaluate_swt_cycle_job(altered,CTX)
    # An interior native result may stand; a native rejection cannot be
    # reconciled by choosing one of two indistinguishable endpoints.
    if r.native_material_receipt.native.status!='computed':
        assert r.status=='out_of_scope' and not r.endpoint_reconciled


def test_swt_other_model_job_is_never_relabelled_as_swt():
    job=prepare_material_job('fatigue.goodman',dict(alternating_stress_Pa=10e6,mean_stress_Pa=0.,ultimate_tensile_strength_Pa=900e6),CTX,location_id='calibrated-point',evidence_ids=('calibration',))
    with pytest.raises(ValueError):evaluate_swt_cycle_job(job,CTX)


@pytest.mark.parametrize('ulps',[0,65])
def test_swt_roundoff_budget_is_explicit_and_cannot_become_a_material_margin(ulps):
    with pytest.raises(ValueError):evaluate_swt_cycle_job(prepared_endpoint(),CTX,maximum_endpoint_roundoff_ulps=ulps)
