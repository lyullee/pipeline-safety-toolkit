"""Independent normalized-curve inverse roots and monotonic work coordinates."""
import pytest
from scipy.optimize import brentq
from scipy.integrate import quad
from pipeline_integrity.contracts import EvidenceRef,RunContext
from pipeline_integrity.normalized_constitutive_coordinate import NormalizedConstitutiveRequest,evaluate_normalized_constitutive_coordinate

def calc(**kw):
    c=RunContext(asset_id='tensile',scenario_id='normalized_curve',clock_id='load',origin='synthetic',
        evidence=(EvidenceRef(id='curve',citation='Authored normalized monotonic tensile coordinate',origin='synthetic'),))
    data=dict(asset_id=c.asset_id,location_id='specimen',clock_id=c.clock_id,time_s=0.,strain_measure='engineering',
        tensile_stress_pa=200e6,elastic_modulus_pa=200e9,reference_stress_pa=300e6,
        normalized_power_coefficient=3.,normalized_power_exponent=4.,maximum_supported_tensile_stress_pa=600e6,
        calibration_basis_id='authored-monotonic',calibration_evidence_id='curve',monotonic_stress_strain_coordinate_and_calibration_reviewed=True)
    return evaluate_normalized_constitutive_coordinate(NormalizedConstitutiveRequest(**(data|kw)),c)

@pytest.mark.parametrize('coefficient,exponent',[(1.,2.),(3.,4.),(.5,5.)])
@pytest.mark.parametrize('target_strain',[.0005,.003,.008])
def test_native_coordinate_recovers_independently_inverted_normalized_curve(coefficient,exponent,target_strain):
    modulus=200e9;reference=300e6
    stress=brentq(lambda s:s/reference+coefficient*(s/reference)**exponent-target_strain/(reference/modulus),0.,600e6,xtol=1e-5)
    result=calc(tensile_stress_pa=stress,normalized_power_coefficient=coefficient,normalized_power_exponent=exponent)
    assert result.status=='computed'
    assert result.total_strain==pytest.approx(target_strain,rel=5e-14)
    assert result.elastic_strain==pytest.approx(stress/modulus,rel=5e-14)
    assert result.total_strain==pytest.approx(result.elastic_strain+result.power_strain,rel=2e-15)
    assert result.native_amplitudes_are_virtual_monotonic_coordinates is True

def test_monotonic_work_coordinate_matches_independent_tangent_integral():
    # Integral sigma*d(epsilon), evaluated from independent normalized tangent.
    end=200e6;modulus=200e9;reference=300e6;alpha=3.;n=4.
    work=quad(lambda s:s/modulus*(1+alpha*n*(s/reference)**(n-1)),0.,end,epsabs=1e-7,epsrel=1e-12)[0]
    # Owner values evaluated along a stress grid also recover the conjugate
    # integral by epsilon(s)*ds integration by parts.
    integrated_strain=quad(lambda s:calc(tensile_stress_pa=s).total_strain,0.,end,epsabs=1e-7,epsrel=1e-11)[0]
    result=calc()
    assert end*result.total_strain-integrated_strain==pytest.approx(work,rel=1e-12)

def test_zero_stress_and_consistent_stress_unit_scaling():
    assert calc(tensile_stress_pa=0.).total_strain==0
    scaled=calc(tensile_stress_pa=400e6,elastic_modulus_pa=400e9,reference_stress_pa=600e6,maximum_supported_tensile_stress_pa=1200e6)
    assert scaled.total_strain==pytest.approx(calc().total_strain,rel=3e-14)

def test_curve_support_is_not_extrapolated():
    assert calc(tensile_stress_pa=700e6).status=='out_of_scope'

def test_unknown_calibration_is_not_replaced_by_a_material_default():
    assert calc(normalized_power_coefficient=None).status=='needs_data'
