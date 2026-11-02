"""Separate pressure-curve optimization, virtual work and incompressibility."""
import math
import pytest
from scipy.optimize import minimize_scalar
from pipeline_integrity.hardening_membrane_limit import HardeningMembraneLimitRequest,evaluate_hardening_membrane_limit

def calc(**kw):
    return evaluate_hardening_membrane_limit(HardeningMembraneLimitRequest(**(dict(transformation_model='j2_plane_strain',reference_diameter_m=.5,
        reference_wall_m=.005,strength_coefficient_pa=800e6,hardening_exponent=.2,calibration_basis_id='authored-power-law',
        maximum_supported_equivalent_strain=1.,maximum_reviewed_wall_diameter_ratio=.02,diameter_is_reference_membrane_coordinate=True,
        incompressible_plane_strain_power_hardening_reviewed=True)|kw)))

@pytest.mark.parametrize('n',[.08,.2,.45])
def test_pressure_turning_point_matches_independent_curve_optimizer(n):
    result=calc(hardening_exponent=n)
    f=2/math.sqrt(3)
    # Independently reconstruct loads from deformed geometry and constitutive work.
    def pressure(e):
        d=.5*math.exp(e);t=.005*math.exp(-e)
        equivalent_stress=800e6*(f*e)**n
        return 2*t*f*equivalent_stress/d
    optimum=minimize_scalar(lambda e:-pressure(e),bounds=(1e-12,1.),method='bounded',options={'xatol':1e-13})
    assert optimum.success
    assert optimum.x==pytest.approx(result.hoop_logarithmic_strain,abs=5e-9)
    assert -optimum.fun==pytest.approx(result.pressure_limit_pa,rel=2e-14)
    assert pressure(n/2*.9)<result.pressure_limit_pa
    assert pressure(n/2*1.1)<result.pressure_limit_pa

def test_work_conjugate_transformation_and_constant_length_material_volume():
    result=calc();f=result.transformation_factor;e=result.hoop_logarithmic_strain
    equivalent_stress=800e6*result.equivalent_strain**.2
    assert result.hoop_stress_pa*e==pytest.approx(equivalent_stress*result.equivalent_strain)
    assert result.current_diameter_m*result.current_wall_m==pytest.approx(.5*.005)

def test_supplied_legacy_transformation_matches_distinct_closed_expression():
    n=.2;k=800e6;t=.005;d=.5
    result=calc(transformation_model='supplied_work_conjugate_factor',supplied_transformation_factor=4/3,transformation_basis_id='authored-comparison')
    reference=2*k*t/d*math.exp(-n)*(n/(2*(3/4)**((1+n)/n)))**n
    assert result.pressure_limit_pa==pytest.approx(reference,rel=2e-14)
    assert result.pressure_limit_pa!=pytest.approx(calc().pressure_limit_pa,rel=.01)

@pytest.mark.parametrize('factor',[.5,2.])
def test_material_and_geometry_similarity(factor):
    assert calc(strength_coefficient_pa=800e6*factor).pressure_limit_pa==pytest.approx(calc().pressure_limit_pa*factor)
    assert calc(reference_wall_m=.005*factor,reference_diameter_m=.5*factor).pressure_limit_pa==pytest.approx(calc().pressure_limit_pa)

def test_unsupported_strain_is_not_extrapolated_to_a_pressure_limit():
    assert calc(maximum_supported_equivalent_strain=.05).status=='out_of_scope'

def test_thick_geometry_is_not_silently_treated_as_a_membrane():
    assert calc(reference_wall_m=.02).status=='out_of_scope'

def test_unreviewed_material_basis_is_not_selected():
    assert calc(calibration_basis_id=None).status=='needs_data'

def test_supplied_transformation_requires_an_explicit_basis():
    assert calc(transformation_model='supplied_work_conjugate_factor',supplied_transformation_factor=4/3).status=='needs_data'
