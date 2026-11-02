"""Independent parcel impulse, pressure thrust and complete-CV distinctions."""
import pytest
from pipeline_integrity.port_momentum_load import MomentumPort as P,MomentumLoadRequest as Q,evaluate_port_momentum_load as solve

def outlet(sign=1.,**kw):return P(**(dict(id='out',outward_unit_normal=(sign,0.,0.),area_m2=.005,pressure_Pa_abs=121325.,outward_mass_flow_kg_s=2.,fluid_velocity_m_s=(sign*300.,0.,0.))|kw))
def make(**kw):return Q(**(dict(coordinate_frame_id='authored-XYZ',ambient_pressure_Pa_abs=101325.,ports=(outlet(),),uniform_port_states_and_common_frame_reviewed=True)|kw))
def elbow(**kw):
 ports=(P(id='in',outward_unit_normal=(-1.,0.,0.),area_m2=.1,pressure_Pa_abs=101325.,outward_mass_flow_kg_s=-2.,fluid_velocity_m_s=(10.,0.,0.)),P(id='out',outward_unit_normal=(0.,1.,0.),area_m2=.1,pressure_Pa_abs=101325.,outward_mass_flow_kg_s=2.,fluid_velocity_m_s=(0.,10.,0.)))
 return make(**(dict(ports=ports,complete_steady_control_volume_reviewed=True,body_force_on_fluid_N=(0.,0.,0.),absolute_mass_balance_tolerance_kg_s=0.)|kw))

@pytest.mark.parametrize('sign',[-1.,1.])
def test_outlet_pressure_plus_momentum_thrust(sign):
 r=solve(make(ports=(outlet(sign),))).values
 assert r['summed_open_port_contribution_N']==pytest.approx((-700*sign,0.,0.))
 assert r['port_contributions'][0]['pressure_contribution_N']==pytest.approx((-100*sign,0.,0.))
 assert r['port_contributions'][0]['momentum_contribution_N']==pytest.approx((-600*sign,0.,0.))
 assert r['whole_hardware_force_identified'] is False and 'fluid_force_on_hardware_N' not in r

def test_opposed_outlets_cancel_only_their_known_contributions():
 a=outlet(1.);b=outlet(-1.,id='other');r=solve(make(ports=(a,b))).values
 assert r['summed_open_port_contribution_N']==pytest.approx((0.,0.,0.))
 assert r['signed_outward_mass_balance_kg_s']==4.
 assert not r['whole_hardware_force_identified']

def test_complete_elbow_force_from_independent_parcel_impulse():
 r=solve(elbow()).values;duration=.25;mass=2*duration
 fluid_momentum_change=(-mass*10.,mass*10.,0.)
 hardware=tuple(-x/duration for x in fluid_momentum_change)
 assert r['fluid_force_on_hardware_N']==pytest.approx(hardware)
 assert r['whole_hardware_force_identified'] and r['signed_outward_mass_balance_kg_s']==0.

def test_explicit_fluid_body_force_is_transferred_to_hardware():
 r=solve(elbow(body_force_on_fluid_N=(0.,0.,-5.))).values
 assert r['fluid_force_on_hardware_N']==pytest.approx((20.,-20.,-5.))

def test_uniform_absolute_pressure_reference_shift_does_not_change_thrust():
 q=make();p=q.ports[0];r=solve(q).values
 qq=q.model_copy(update={'ambient_pressure_Pa_abs':201325.,'ports':(p.model_copy(update={'pressure_Pa_abs':221325.}),)})
 assert solve(qq).values['summed_open_port_contribution_N']==r['summed_open_port_contribution_N']

def test_orthogonal_frame_rotation_rotates_the_load():
 q=elbow();rot=lambda v:(-v[1],v[0],v[2])
 pp=tuple(p.model_copy(update={'outward_unit_normal':rot(p.outward_unit_normal),'fluid_velocity_m_s':rot(p.fluid_velocity_m_s)}) for p in q.ports)
 r=solve(q.model_copy(update={'coordinate_frame_id':'rotated','ports':pp})).values
 assert r['fluid_force_on_hardware_N']==pytest.approx((20.,20.,0.))

def test_unbalanced_whole_cv_is_not_a_steady_hardware_force():
 q=elbow();pp=(q.ports[0],q.ports[1].model_copy(update={'outward_mass_flow_kg_s':3.}))
 r=solve(q.model_copy(update={'ports':pp}));assert r.status=='out_of_scope' and not r.values['whole_hardware_force_identified']

@pytest.mark.parametrize('kw',[{'body_force_on_fluid_N':None},{'absolute_mass_balance_tolerance_kg_s':None}])
def test_complete_cv_unknown_body_or_budget_is_withheld(kw):assert solve(elbow(**kw)).status=='needs_data'

@pytest.mark.parametrize('field',['pressure_Pa_abs','outward_mass_flow_kg_s','fluid_velocity_m_s'])
def test_missing_uniform_port_state_withheld(field):
 p=outlet().model_copy(update={field:None});assert solve(make(ports=(p,))).status=='needs_data'

@pytest.mark.parametrize('kw',[{'outward_unit_normal':(2.,0.,0.)},{'outward_mass_flow_kg_s':-2.}])
def test_inconsistent_normal_or_flow_direction_rejected(kw):
 with pytest.raises(ValueError):solve(make(ports=(outlet(**kw),)))
