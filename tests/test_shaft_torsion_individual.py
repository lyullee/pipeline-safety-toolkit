import math
import pytest
from scipy.integrate import quad
from pipeline_integrity.shaft_torsion_observer import ShaftTorsionRequest,evaluate_shaft_torsion


def request(**changes):
    return ShaftTorsionRequest(**dict(dict(id='authored-shaft',outside_diameter_m=.08,inside_diameter_m=0.,
        signed_torque_nm=120.,signed_angular_velocity_rad_s=25.,length_m=1.5,shear_modulus_pa=79e9,
        supplied_shear_stress_ceiling_pa=55e6,ceiling_evidence_id='caller-reviewed-ceiling',
        homogeneous_circular_saint_venant_torsion_reviewed=True,supplied_ceiling_basis_reviewed=True),**changes))


@pytest.mark.parametrize('inside',[0.,.02,.06])
@pytest.mark.parametrize('torque',[-120.,0.,180.])
def test_independent_twist_field_section_traction_and_energy_integrals(inside,torque):
    q=request(inside_diameter_m=inside,signed_torque_nm=torque)
    v=evaluate_shaft_torsion(q)['values'];a=inside/2;b=q.outside_diameter_m/2
    rate=v['signed_twist_rad']/q.length_m
    # Actual shear strain r*dtheta/dz supplies traction and energy densities.
    t=quad(lambda r:2*math.pi*r*r*(q.shear_modulus_pa*r*rate),a,b,epsabs=1e-12)[0]
    energy=quad(lambda r:math.pi*r*q.shear_modulus_pa*(r*rate)**2*q.length_m,a,b,epsabs=1e-12)[0]
    assert t == pytest.approx(torque,rel=2e-13,abs=1e-12)
    assert v['elastic_strain_energy_j']==pytest.approx(energy,rel=2e-13,abs=1e-14)
    assert v['signed_surface_shear_stress_pa']==pytest.approx(q.shear_modulus_pa*b*rate,rel=2e-13,abs=1e-10)
    assert v['signed_transmitted_power_w']==pytest.approx(t*q.signed_angular_velocity_rad_s,abs=1e-9)


@pytest.mark.parametrize('inside',[0.,.04])
def test_allowable_inverse_is_independently_recovered_from_traction(inside):
    q=request(inside_diameter_m=inside);v=evaluate_shaft_torsion(q)['values'];b=q.outside_diameter_m/2;a=inside/2
    maximum=quad(lambda r:2*math.pi*r*r*q.supplied_shear_stress_ceiling_pa*r/b,a,b)[0]
    assert v['maximum_absolute_nominal_torque_nm']==pytest.approx(maximum,rel=2e-13)
    for sign in (-1,1):
        response=evaluate_shaft_torsion(request(inside_diameter_m=inside,signed_torque_nm=sign*maximum))
        assert abs(response['values']['supplied_shear_ceiling_residual_pa'])<1e-7


def test_rotation_work_and_energy_gradient_have_distinct_factor_two():
    q=request();v=evaluate_shaft_torsion(q)['values'];h=.001
    def energy(t):return evaluate_shaft_torsion(request(signed_torque_nm=t))['values']['elastic_strain_energy_j']
    assert (energy(120+h)-energy(120-h))/(2*h)==pytest.approx(v['signed_twist_rad'],rel=2e-10)
    assert 2*v['elastic_strain_energy_j']==pytest.approx(120*v['signed_twist_rad'])


@pytest.mark.parametrize('speed',[-30.,0.,30.])
def test_speed_sign_and_zero_speed_do_not_change_torque_capacity(speed):
    v=evaluate_shaft_torsion(request(signed_angular_velocity_rad_s=speed))['values']
    assert v['signed_transmitted_power_w']==120*speed
    assert v['maximum_absolute_nominal_torque_nm']>0
    assert v['maximum_absolute_transmitted_power_w']==v['maximum_absolute_nominal_torque_nm']*abs(speed)


def test_unknown_ceiling_preserves_physical_demand_not_a_material_prediction():
    r=evaluate_shaft_torsion(request(supplied_shear_stress_ceiling_pa=None,ceiling_evidence_id=None))
    assert r['ceiling_status']=='needs_data' and r['values']['maximum_absolute_nominal_torque_nm'] is None
    assert r['values']['signed_surface_shear_stress_pa']>0 and not r['material_strength_predicted']


def test_unreviewed_support_and_unknown_compliance_are_explicit():
    assert evaluate_shaft_torsion(request(homogeneous_circular_saint_venant_torsion_reviewed=False))['status']=='needs_data'
    r=evaluate_shaft_torsion(request(length_m=None,shear_modulus_pa=None))
    assert 'signed_twist_rad' not in r['values']


@pytest.mark.parametrize('changes',[dict(inside_diameter_m=.08),dict(shear_modulus_pa=None),dict(outside_diameter_m=0),dict(signed_torque_nm=float('nan'))])
def test_invalid_domains_have_no_fabricated_response(changes):
    with pytest.raises(ValueError):evaluate_shaft_torsion(request(**changes))
