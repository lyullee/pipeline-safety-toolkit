"""Independent inertia, wave impedance, storage, gravity and Darcy checks."""
import math
import numpy as np
import pytest
from pipeline_integrity.contracts import RunContext,digest
from pipeline_integrity.transient import History,LiquidNode,LiquidPipe,LiquidTransient
from pipeline_integrity.moc_characteristics import characteristic_invariants
from pipeline_integrity.liquid_virtual_mass import VirtualMassLiquidRequest,virtual_mass_coordinates,simulate_virtual_mass_liquid

CONTEXT=RunContext(asset_id='authored-inertia-line',scenario_id='uniform-alpha',clock_id='elapsed',origin='synthetic')


def request(alpha=1.44,friction=.02,velocity=.2,closure=True,elevations=(0.,0.)):
    rho=997.;g=9.80665;c=900.;length=90.;d=.2;area=math.pi*d*d/4
    dt=length/(30*c/math.sqrt(alpha));drop=friction*length/d*velocity*abs(velocity)/(2*g)
    flow=area*velocity
    endvalues=(flow,flow,0.) if closure else (flow,flow,flow)
    spec=LiquidTransient(nodes=(
        LiquidNode(id='in',elevation_m=elevations[0],initial_head_m=80.+drop,boundary='head',history=History(time_s=(0.,),values=(80.+drop,))),
        LiquidNode(id='out',elevation_m=elevations[1],initial_head_m=80.,boundary='withdrawal',history=History(time_s=(0.,dt,2*dt),values=endvalues))),
        pipes=(LiquidPipe(id='line',start_node='in',end_node='out',length_m=length,diameter_m=d,wave_speed_m_s=c,darcy_friction_factor=friction,initial_volume_flow_m3_s=flow),),
        duration_s=math.nextafter((2 if closure else 34)*dt,-math.inf),requested_timestep_s=dt,density_kg_m3=rho,gravity_m_s2=g,
        ambient_pressure_pa_abs=101325.,vapour_pressure_pa_abs=2000.,maximum_wave_speed_adjustment=1e-12,mass_balance_tolerance_m3=1e-8)
    return VirtualMassLiquidRequest(physical_specification=spec,uniform_inertia_factor=alpha,uniform_kinetic_correction_reviewed=True)


@pytest.mark.parametrize('alpha',[1.,1.21,1.69])
def test_coordinate_products_preserve_pressure_storage_and_steady_loss(alpha):
    q=request(alpha);s=q.physical_specification;m=virtual_mass_coordinates(q).owner_specification
    assert m.density_kg_m3*m.gravity_m_s2==pytest.approx(s.density_kg_m3*s.gravity_m_s2,rel=2e-16)
    assert m.density_kg_m3*m.pipes[0].wave_speed_m_s**2==pytest.approx(s.density_kg_m3*s.pipes[0].wave_speed_m_s**2,rel=4e-16)
    assert m.gravity_m_s2/m.pipes[0].wave_speed_m_s**2==pytest.approx(s.gravity_m_s2/s.pipes[0].wave_speed_m_s**2,rel=4e-16)
    assert m.pipes[0].darcy_friction_factor/m.gravity_m_s2==pytest.approx(s.pipes[0].darcy_friction_factor/s.gravity_m_s2)
    assert m.density_kg_m3==alpha*s.density_kg_m3
    assert q.physical_specification.density_kg_m3==997.


@pytest.mark.parametrize('alpha',[1.,1.21,1.69])
def test_closure_joukowsky_impedance_and_celerity_on_actual_moc(alpha):
    q=request(alpha);s=q.physical_specification;r=simulate_virtual_mass_liquid(q,CONTEXT);owner=r['owner_result']
    assert r['status']=='computed',owner['notes']
    pressure=r['physical_pressure_traces']['line']['end_pressure_pa_abs']
    expected=s.density_kg_m3*900.*math.sqrt(alpha)*.2
    assert pressure[2]-pressure[1]==pytest.approx(expected,rel=3e-12)
    assert owner['grid']['line']['wave_speed_m_s']==pytest.approx(900./math.sqrt(alpha),rel=2e-14)
    assert owner['all_volume_balances_within_tolerance']
    assert r['coordinate_meaning']['coordinate_density_is_fluid_property'] is False


@pytest.mark.parametrize('alpha',[1.,1.21,1.69])
def test_exact_head_step_first_arrival_at_slowed_wave_speed(alpha):
    q=request(alpha,friction=0.,velocity=0.,closure=False);s=q.physical_specification;dt=s.requested_timestep_s
    nodes=list(s.nodes);nodes[0]=nodes[0].model_copy(update={'history':History(time_s=(0.,dt,2*dt),values=(80.,80.,80.05))})
    s=s.model_copy(update={'nodes':tuple(nodes)})
    r=simulate_virtual_mass_liquid(q.model_copy(update={'physical_specification':s}),CONTEXT)['owner_result']
    assert r['status']=='computed'
    h=np.asarray(r['pipes']['line']['end_head_m']);first=int(np.flatnonzero(abs(h-80.)>1e-9)[0])
    assert first==32
    assert r['time_s'][first]-2*dt==pytest.approx(90.*math.sqrt(alpha)/900.,rel=3e-14)
    assert h[first]-80.==pytest.approx(.1,abs=1e-11)


@pytest.mark.parametrize('alpha,slope,velocity',[(1.21,.03,.4),(1.69,-.02,-.3),(1.44,0.,.5),(1.44,.03,0.)])
def test_shared_characteristic_step_matches_physical_gravity_and_darcy_inertia(alpha,slope,velocity):
    q=request(alpha);s=q.physical_specification;m=virtual_mass_coordinates(q).owner_specification
    d=.2;area=math.pi*d*d/4;dx=3.;dt=dx/m.pipes[0].wave_speed_m_s
    # Local uniform pressure means head_x=z_x=slope. This isolated stencil
    # checks acceleration; the graph API intentionally requires steady startup.
    h=80.+slope*np.arange(5)*dx;flow=np.full(5,area*velocity)
    b=m.pipes[0].wave_speed_m_s/(m.gravity_m_s2*area)
    resistance=m.pipes[0].darcy_friction_factor*dx/(2*m.gravity_m_s2*d*area**2)
    plus,minus,_,_=characteristic_invariants(h,flow,flow,b,resistance)
    actual=(plus-minus)/(2*b*area)
    expected=velocity-dt/alpha*(s.gravity_m_s2*slope+.02*velocity*abs(velocity)/(2*d))
    np.testing.assert_allclose(actual,expected,atol=8e-15,rtol=5e-13)


@pytest.mark.parametrize('velocity',[.5,-.5])
def test_homogeneous_darcy_decay_matches_independent_ode_and_temporal_refinement(velocity):
    q=request(1.69);m=virtual_mass_coordinates(q).owner_specification
    c=m.pipes[0].wave_speed_m_s;g=m.gravity_m_s2;d=.2;area=math.pi*d*d/4;duration=.4
    exact=velocity/(1+.02*abs(velocity)*duration/(2*d*1.69));errors=[]
    for count in (80,160):
        dt=duration/count;dx=c*dt;b=c/(g*area);resistance=m.pipes[0].darcy_friction_factor*dx/(2*g*d*area**2)
        v=velocity
        for _ in range(count):
            flow=np.full(3,area*v)
            plus,minus,_,_=characteristic_invariants(np.full(3,80.),flow,flow,b,resistance)
            v=float((plus[0]-minus[0])/(2*b*area))
        errors.append(abs(v-exact))
    assert errors[1]<.51*errors[0]
    assert errors[1]<2e-7


@pytest.mark.parametrize('elevations',[(0.,20.),(20.,0.),(-5.,10.)])
def test_hydrostatic_pressure_and_steady_darcy_balance_are_physical_not_coordinate_density(elevations):
    q=request(1.69,velocity=.2,closure=False,elevations=elevations)
    r=simulate_virtual_mass_liquid(q,CONTEXT);owner=r['owner_result'];s=q.physical_specification
    assert r['status']=='computed'
    p=r['physical_pressure_traces']['line']
    for side,index in (('start',0),('end',1)):
        expected=101325.+997.*9.80665*(s.nodes[index].initial_head_m-elevations[index])
        np.testing.assert_allclose(p[side+'_pressure_pa_abs'],expected,rtol=3e-14,atol=2e-8)
    expected_drop=.02*90/.2*.2**2/(2*9.80665)
    np.testing.assert_allclose(np.array(owner['pipes']['line']['start_head_m'])-np.array(owner['pipes']['line']['end_head_m']),expected_drop,atol=1e-11,rtol=0)


def test_replay_digest_and_alpha_one_keep_exact_owner_semantics():
    from pipeline_integrity.transient import simulate_liquid_transient
    q=request(1.);r=simulate_virtual_mass_liquid(q,CONTEXT);repeat=simulate_virtual_mass_liquid(q,CONTEXT)
    assert r==repeat
    assert r['specification_digest']==digest(q)
    assert r['owner_result']==simulate_liquid_transient(q.physical_specification,CONTEXT)
    assert r['coordinate_specification_digest']==r['physical_specification_digest']


@pytest.mark.parametrize('kind',['unknown','factor','review'])
def test_unknown_or_unreviewed_inertia_is_rejected(kind):
    q=request()
    with pytest.raises(ValueError):
        if kind=='unknown':VirtualMassLiquidRequest.model_validate(dict(q.model_dump(),profile_estimated_here=True))
        elif kind=='factor':VirtualMassLiquidRequest.model_validate(dict(q.model_dump(),uniform_inertia_factor=.9))
        else:virtual_mass_coordinates(q.model_copy(update={'uniform_kinetic_correction_reviewed':False}))
