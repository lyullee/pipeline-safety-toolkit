"""Independent shock-frame conservation, entropy, weak limit and air closed form."""
import math
import pytest
from pipeline_integrity.normal_shock_wall_reflection import NormalShockWallReflectionRequest as Request,evaluate_normal_shock_wall_reflection as evaluate

def req(gamma=1.4,ratio=1.):
    return Request(ambient_pressure_pa_abs=100000.,ambient_mass_density_kg_m3=1.2,heat_capacity_ratio=gamma,incident_overpressure_pa=100000.*ratio,
        maximum_reviewed_incident_absolute_pressure_ratio=20.,gas_state_planar_wave_and_wall_basis_id='authored-perfect-gas-wall',calorically_perfect_planar_shock_and_rigid_normal_wall_reviewed=True)

def conservation(g,pup,rhoup,uup,pdown,rhodown,udown,speed):
    a=uup-speed;b=udown-speed
    assert rhoup*a==pytest.approx(rhodown*b,rel=2e-11)
    assert pup+rhoup*a*a==pytest.approx(pdown+rhodown*b*b,rel=2e-11)
    assert g/(g-1)*pup/rhoup+a*a/2==pytest.approx(g/(g-1)*pdown/rhodown+b*b/2,rel=2e-11)
    assert math.log(pdown/pup)-g*math.log(rhodown/rhoup)>-1e-13

@pytest.mark.parametrize('gamma',[1.2,1.4,5/3])
@pytest.mark.parametrize('ratio',[.001,.1,1.,10.])
def test_both_shocks_conserve_fluxes_and_reflection_stops_the_gas(gamma,ratio):
    q=req(gamma,ratio);r=evaluate(q);assert r['status']=='computed'
    conservation(gamma,q.ambient_pressure_pa_abs,q.ambient_mass_density_kg_m3,0.,r['incident_pressure_pa_abs'],r['incident_mass_density_kg_m3'],r['incident_velocity_toward_wall_m_s'],r['incident_shock_speed_m_s'])
    conservation(gamma,r['incident_pressure_pa_abs'],r['incident_mass_density_kg_m3'],r['incident_velocity_toward_wall_m_s'],r['reflected_pressure_pa_abs'],r['reflected_mass_density_kg_m3'],0.,r['reflected_shock_speed_m_s'])
    assert r['reflected_shock_speed_m_s']<0 and abs(r['computed_wall_velocity_residual_m_s'])<1e-9
    assert r['actual_blast_field_or_target_response_qualified'] is False

@pytest.mark.parametrize('ratio',[.01,1.,10.])
def test_air_closed_form_reflection_overpressure(ratio):
    q=req(ratio=ratio);p=q.incident_overpressure_pa;p0=q.ambient_pressure_pa_abs
    expected=2*p*(7*p0+4*p)/(7*p0+p)
    assert evaluate(q)['reflected_overpressure_pa']==pytest.approx(expected,rel=2e-11)

def test_weak_wave_limit_and_zero_incident_wave():
    errors=[]
    for ratio in (1e-2,1e-3,1e-4):
        q=req(ratio=ratio);errors.append(abs(evaluate(q)['reflected_overpressure_pa']/(2*q.incident_overpressure_pa)-1))
    assert errors[2]<errors[1]<errors[0] and errors[-1]<1e-4
    assert evaluate(req(ratio=0.))['reflected_overpressure_pa']==0

def test_unknown_wall_or_reviewed_pressure_support_is_withheld():
    assert evaluate(req().model_copy(update={'calorically_perfect_planar_shock_and_rigid_normal_wall_reviewed':False}))['status']=='needs_data'
    assert evaluate(req().model_copy(update={'maximum_reviewed_incident_absolute_pressure_ratio':1.5}))['status']=='out_of_scope'
