"""General-exponent solver checks supplement the independent special inverses."""
from decimal import Decimal,localcontext
import pytest
from pipeline_integrity.contracts import EvidenceRef,RunContext
from pipeline_integrity.material_exchange import prepare_material_job,evaluate_material_job
from pipeline_integrity.swt_endpoint_compatibility import evaluate_swt_cycle_job

CTX=RunContext(asset_id='general-exponent-curve',scenario_id='finite-supported-strain-life',clock_id='whole-cycles',origin='synthetic',
    evidence=(EvidenceRef(id='general-calibration',citation='Authored SI calibration-form parameters; numerical consistency supplement, no measured material fit',origin='synthetic'),))


@pytest.mark.parametrize('method',['morrow','swt'])
@pytest.mark.parametrize('exponents',[(-.08,-.55),(-.12,-.7)])
@pytest.mark.parametrize('cycles',[100.,1e5])
def test_general_negative_exponents_recover_independent_high_precision_curve_anchor(method,exponents,cycles):
    p=dict(elastic_modulus_Pa=190e9,fatigue_strength_coefficient_Pa=1100e6,fatigue_strength_exponent=exponents[0],
        fatigue_ductility_coefficient=.35,fatigue_ductility_exponent=exponents[1],minimum_valid_cycles=1.,maximum_valid_cycles=1e7,
        material_applicability_reviewed=True)
    with localcontext() as context:
        context.prec=75;d=lambda value:Decimal(str(value));x=2*d(cycles);sf=d(p['fatigue_strength_coefficient_Pa']);young=d(p['elastic_modulus_Pa']);ef=d(p['fatigue_ductility_coefficient']);b=d(exponents[0]);c=d(exponents[1])
        if method=='morrow':
            p.update(mean_stress_Pa=220e6,mean_stress_sign_convention='tension_positive',coefficients_and_mean_stress_basis_reviewed=True)
            strain=(sf-d(p['mean_stress_Pa']))/young*x**b+ef*x**c
        else:
            p.update(maximum_stress_Pa=450e6,stress_and_strain_measurement_basis_reviewed=True)
            strain=(sf*sf/young*x**(2*b)+sf*ef*x**(b+c))/d(p['maximum_stress_Pa'])
    p['strain_amplitude']=float(strain)
    job=prepare_material_job('fatigue.'+method+'_strain_life',p,CTX,location_id='reviewed-point',evidence_ids=('general-calibration',))
    if method=='morrow':
        result=evaluate_material_job(job,CTX);result.check_integrity();assert result.native.status=='computed'
        actual=result.native.payload['values']['cycles_to_failure']
    else:
        result=evaluate_swt_cycle_job(job,CTX);assert result.status=='computed';assert not result.endpoint_reconciled
        actual=result.cycles_to_failure
    assert actual==pytest.approx(cycles,rel=3e-11)
