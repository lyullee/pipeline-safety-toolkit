"""Independent acoustic Riemann solution and pressure-driven column motion."""
import math
import numpy as np
import pytest
from scipy.integrate import solve_ivp
from pipeline_integrity.liquid_column_collision import (LiquidAcousticColumnState as State,LiquidColumnCollisionRequest as Collision,
    evaluate_liquid_column_collision,ConstantHeadColumnReturnRequest as Return,evaluate_constant_head_column_return)
from pipeline_integrity.models.hydraulics import SurgePulse,surge_pulse


def collision(ul=1.,ur=0.,rho_l=970.,rho_r=970.,c_l=1100.,c_r=1100.,p_l=3e5,p_r=3e5,reference=3e5):
    return Collision(left=State(density_kg_m3=rho_l,wave_speed_m_s=c_l,pressure_pa_abs=p_l,signed_axial_velocity_m_s=ul),
        right=State(density_kg_m3=rho_r,wave_speed_m_s=c_r,pressure_pa_abs=p_r,signed_axial_velocity_m_s=ur),
        reference_pressure_pa_abs=reference,vapor_pressure_pa_abs=2500.,ordinary_liquid_impedance_and_local_linear_states_reviewed=True)


@pytest.mark.parametrize('ul,ur',[(1.,-1.),(1.,0.),(0.,-1.),(-.2,-.6),(.5,.5)])
def test_equal_medium_signed_approach_has_half_relative_joukowsky_increment(ul,ur):
    q=collision(ul,ur);r=evaluate_liquid_column_collision(q)
    assert r.status=='computed'
    assert r.interface_pressure_pa_abs-3e5==pytest.approx(970.*1100.*(ul-ur)/2,abs=3e-10)
    assert r.interface_signed_velocity_m_s==pytest.approx((ul+ur)/2,abs=3e-16)
    assert not r.vapor_collapse_kinetics_predicted and not r.nonlinear_total_energy_qualification_claimed


@pytest.mark.parametrize('ul,ur,rhol,rhor,cl,cr,pl,pr',[
    (.7,-.2,900.,1050.,800.,1200.,3.2e5,2.8e5),(-.1,-.6,980.,850.,1400.,950.,3e5,3e5),
    (.4,.1,950.,1000.,1100.,1100.,4e5,2.5e5),(.1,-.3,1000.,920.,1000.,1400.,2.5e5,3.8e5)])
def test_unequal_impedance_two_state_riemann_solution_and_independent_energy(ul,ur,rhol,rhor,cl,cr,pl,pr):
    q=collision(ul,ur,rhol,rhor,cl,cr,pl,pr);r=evaluate_liquid_column_collision(q)
    assert r.status=='computed'
    zl,zr=rhol*cl,rhor*cr
    # p*+Zl*u*=pl+Zl*ul; p*-Zr*u*=pr-Zr*ur.
    pressure,velocity=np.linalg.solve(np.array([[1.,zl],[1.,-zr]]),np.array([pl+zl*ul,pr-zr*ur]))
    assert r.interface_pressure_pa_abs==pytest.approx(pressure,rel=3e-16)
    assert r.interface_signed_velocity_m_s==pytest.approx(velocity,abs=4e-16)
    assert -cl*(pressure-pl)==pytest.approx(rhol*cl**2*(velocity-ul),rel=3e-15,abs=2e-7)
    assert cr*(pressure-pr)==pytest.approx(rhor*cr**2*(velocity-ur),rel=3e-15,abs=2e-7)
    energy=lambda rho,c,p,v:(p-3e5)**2/(2*rho*c*c)+rho*v*v/2
    time=.013
    initial=time*(cl*energy(rhol,cl,pl,ul)+cr*energy(rhor,cr,pr,ur))
    final=time*(cl*energy(rhol,cl,pressure,velocity)+cr*energy(rhor,cr,pressure,velocity))
    boundary=time*((pl-3e5)*ul-(pr-3e5)*ur)
    scale=max(1.,abs(initial),abs(final),abs(boundary))
    assert abs(final-initial-boundary)/scale<3e-15
    assert abs(r.quadratic_acoustic_energy_residual_W_m2)/max(1.,abs(r.energy_change_rate_per_area_W_m2),cl*rhol*ul**2,cr*rhor*ur**2)<4e-15


def test_stationary_liquid_is_not_a_rigid_wall_and_coherent_force_units_match_SI():
    q=collision(1.,0.);r=evaluate_liquid_column_collision(q)
    wall=surge_pulse(SurgePulse(density_kg_m3=970.,wave_speed_m_s=1100.,initial_velocity_m_s=1.,final_velocity_m_s=0.,length_m=80.,closure_time_s=.01))
    assert wall.values['fast_closure']
    assert r.left_pressure_increment_pa==pytest.approx(wall.values['instantaneous_pressure_pulse_pa']/2,rel=3e-16)
    inch=.0254;lbm=.45359237;lbf=4.4482216152605;psi=lbf/inch**2
    density_us=970.*inch**3/lbm;speed_us=1100./inch;velocity_us=1./inch
    force_conversion=lbf/(lbm*inch)
    pressure_us=density_us*speed_us*velocity_us/(2*force_conversion)
    assert r.left_pressure_increment_pa==pytest.approx(pressure_us*psi,rel=4e-16)


def test_reference_datum_change_does_not_change_physical_interface_state():
    q=collision(.7,-.3,p_l=3.2e5,p_r=2.8e5)
    first=evaluate_liquid_column_collision(q);second=evaluate_liquid_column_collision(q.model_copy(update={'reference_pressure_pa_abs':2.5e5}))
    assert second.interface_pressure_pa_abs==pytest.approx(first.interface_pressure_pa_abs,abs=2e-10)
    assert second.interface_signed_velocity_m_s==pytest.approx(first.interface_signed_velocity_m_s,abs=2e-16)


def test_two_actual_moc_incident_waves_meet_at_junction_with_the_same_star_state():
    from pipeline_integrity.contracts import RunContext
    from pipeline_integrity.transient import History,LiquidNode,LiquidPipe,LiquidTransient,simulate_liquid_transient
    rho=970.;c=1100.;g=9.80665;length=110.;dt=.01;head=100.;left_jump=.05;right_jump=.08;area=math.pi*.2**2/4
    nodes=(LiquidNode(id='left',elevation_m=0.,initial_head_m=head,boundary='head',history=History(time_s=(0.,dt,2*dt),values=(head,head,head+left_jump))),
        LiquidNode(id='middle',elevation_m=0.,initial_head_m=head,boundary='withdrawal',history=History(time_s=(0.,),values=(0.,))),
        LiquidNode(id='right',elevation_m=0.,initial_head_m=head,boundary='head',history=History(time_s=(0.,dt,2*dt),values=(head,head,head+right_jump))))
    spec=LiquidTransient(nodes=nodes,pipes=(LiquidPipe(id='left',start_node='left',end_node='middle',length_m=length,diameter_m=.2,wave_speed_m_s=c,darcy_friction_factor=0.,initial_volume_flow_m3_s=0.),
        LiquidPipe(id='right',start_node='middle',end_node='right',length_m=length,diameter_m=.2,wave_speed_m_s=c,darcy_friction_factor=0.,initial_volume_flow_m3_s=0.)),
        duration_s=math.nextafter(.14,-math.inf),requested_timestep_s=dt,density_kg_m3=rho,ambient_pressure_pa_abs=101325.,vapour_pressure_pa_abs=2500.,maximum_wave_speed_adjustment=1e-12,mass_balance_tolerance_m3=1e-8)
    context=RunContext(asset_id='authored-acoustic-junction',scenario_id='two-incident-waves',clock_id='elapsed',origin='synthetic')
    result=simulate_liquid_transient(spec,context);assert result['status']=='computed',result['notes']
    base=101325.+rho*g*head
    q=collision(g*left_jump/c,-g*right_jump/c,rho,rho,c,c,base+rho*g*left_jump,base+rho*g*right_jump,base)
    star=evaluate_liquid_column_collision(q);index=12
    actual_pressure=101325.+rho*g*result['pipes']['left']['end_head_m'][index]
    actual_velocity=result['pipes']['left']['end_flow_m3_s'][index]/area
    assert actual_pressure==pytest.approx(star.interface_pressure_pa_abs,abs=3e-8)
    assert actual_velocity==pytest.approx(star.interface_signed_velocity_m_s,abs=4e-15)


def return_request(**kw):
    data=dict(density_kg_m3=970.,wave_speed_m_s=1100.,downstream_column_length_m=120.,initial_velocity_away_from_closed_valve_m_s=.8,
        reservoir_pressure_pa_abs=42500.,vapor_pressure_pa_abs=2500.,observation_times_s=(0.,.5,1.,2.,3.,4.),constant_pressure_small_gap_rigid_column_reviewed=True)
    data.update(kw);return Return(**data)


def test_constant_head_reversal_return_and_work_against_independent_event_ode():
    q=return_request();r=evaluate_constant_head_column_return(q);assert r.status=='computed'
    pressure_force=q.reservoir_pressure_pa_abs-q.vapor_pressure_pa_abs;mass_per_area=q.density_kg_m3*q.downstream_column_length_m
    def rhs(time,y):return [y[1],-pressure_force/mass_per_area]
    def turning(time,y):return y[1]
    turning.direction=-1
    def contact(time,y):return y[0]
    contact.direction=-1;contact.terminal=True
    solved=solve_ivp(rhs,(0.,8.),[0.,.8],events=(turning,contact),rtol=1e-12,atol=1e-12,dense_output=True,max_step=.05)
    assert r.time_to_reverse_s==pytest.approx(solved.t_events[0][0],rel=2e-14)
    assert r.time_to_return_to_valve_s==pytest.approx(solved.t_events[1][0],rel=2e-14)
    assert r.time_to_return_to_valve_s==2*r.time_to_reverse_s
    assert r.signed_impact_velocity_m_s==pytest.approx(solved.y_events[1][0,1],rel=2e-14)
    for sample in r.observations:
        position,velocity=solved.sol(sample.time_s)
        assert sample.gap_displacement_m==pytest.approx(position,abs=2e-13)
        assert sample.signed_velocity_away_from_valve_m_s==pytest.approx(velocity,abs=2e-14)
        assert abs(sample.kinetic_work_residual_J_m2)/(.5*mass_per_area*.8**2)<4e-15
    wall=surge_pulse(SurgePulse(density_kg_m3=970.,wave_speed_m_s=1100.,initial_velocity_m_s=.8,final_velocity_m_s=0.,length_m=120.,closure_time_s=.01))
    assert r.instantaneous_rigid_wall_pressure_increment_pa==wall.values['instantaneous_pressure_pulse_pa']
    assert r.instantaneous_wall_pressure_reference_pa_abs==2500.+wall.values['instantaneous_pressure_pulse_pa']
    assert not r.condensation_or_phase_change_solved


@pytest.mark.parametrize('failure',['collision_review','mach','vapor','strain','unknown','return_review','gap','acoustic','pressure_drive','postimpact'])
def test_unreviewed_or_unsupported_collision_and_return_conditions(failure):
    if failure=='collision_review':assert evaluate_liquid_column_collision(collision().model_copy(update={'ordinary_liquid_impedance_and_local_linear_states_reviewed':False})).status=='needs_data'
    elif failure=='mach':assert evaluate_liquid_column_collision(collision(ul=100.)).status=='out_of_scope'
    elif failure=='vapor':assert evaluate_liquid_column_collision(collision(ul=-1.,ur=1.,p_l=3000.,p_r=3000.,reference=3000.)).status=='out_of_scope'
    elif failure=='strain':assert evaluate_liquid_column_collision(collision().model_copy(update={'maximum_linear_storage_strain':1e-6})).status=='out_of_scope'
    elif failure=='return_review':assert evaluate_constant_head_column_return(return_request(constant_pressure_small_gap_rigid_column_reviewed=False)).status=='needs_data'
    elif failure=='gap':assert evaluate_constant_head_column_return(return_request(maximum_gap_fraction_of_length=.001)).status=='out_of_scope'
    elif failure=='acoustic':assert evaluate_constant_head_column_return(return_request(maximum_acoustic_crossing_to_turn_time=.001)).status=='out_of_scope'
    else:
        with pytest.raises(ValueError):
            if failure=='unknown':Collision.model_validate(dict(collision().model_dump(),condensation_rate=1.))
            elif failure=='pressure_drive':return_request(reservoir_pressure_pa_abs=2500.)
            else:evaluate_constant_head_column_return(return_request(observation_times_s=(0.,10.)))
