"""Independent Newtonian trajectory integration and geometric observation limits."""
import pytest
from scipy.integrate import solve_ivp
from pipeline_integrity.horizontal_jet import HorizontalJetObservation as Q,invert_horizontal_jet as solve

def make(x=.6,y=.1962,g=9.81,**kw):return Q(**(dict(horizontal_distance_m=x,vertical_drop_m=y,gravity_m_s2=g,actual_normal_jet_area_m2=.01,tracked_to_section_mean_velocity_factor=1.,horizontal_launch_constant_gravity_and_ballistic_path_reviewed=True,actual_area_and_velocity_profile_conversion_reviewed=True)|kw))

@pytest.mark.parametrize('speed,time,g',[(3.,.2,9.81),(4.,2.,1.62),(20.,1.,20.)])
def test_independent_time_integrated_trajectory_inverse(speed,time,g):
 ode=solve_ivp(lambda t,z:[z[2],z[3],0.,g],(0.,time),[0.,0.,speed,0.],rtol=1e-11,atol=1e-12,method='DOP853');x,y,_,vy=ode.y[:,-1]
 r=solve(make(float(x),float(y),g));assert r.status=='computed';v=r.values
 assert v['parcel_flight_time_s']==pytest.approx(time,rel=2e-12)
 assert v['tracked_horizontal_velocity_m_s']==pytest.approx(speed,rel=2e-12)
 assert v['downward_velocity_at_observation_m_s']==pytest.approx(vy,rel=2e-12)
 assert v['volume_flow_m3_s']==pytest.approx(.01*speed,rel=2e-12)

def test_actual_jet_area_and_tracked_mean_mapping_are_separate():
 r=solve(make(actual_normal_jet_area_m2=.005,tracked_to_section_mean_velocity_factor=.5)).values
 assert r['tracked_horizontal_velocity_m_s']==pytest.approx(3.)
 assert r['section_mean_exit_velocity_m_s']==pytest.approx(1.5)
 assert r['volume_flow_m3_s']==pytest.approx(.0075)

@pytest.mark.parametrize('kw',[{'actual_normal_jet_area_m2':None},{'tracked_to_section_mean_velocity_factor':None},{'actual_area_and_velocity_profile_conversion_reviewed':False}])
def test_unknown_flux_mapping_preserves_velocity_only(kw):
 r=solve(make(**kw));assert r.status=='computed' and 'volume_flow_m3_s' not in r.values
 assert r.values['tracked_horizontal_velocity_m_s']==pytest.approx(3.)

@pytest.mark.parametrize('field',['horizontal_distance_m','vertical_drop_m'])
def test_unknown_trajectory_does_not_default(field):assert solve(make(**{field:None})).status=='needs_data'

@pytest.mark.parametrize('x,y',[(0.,.2),(.6,0.)])
def test_degenerate_observation_is_not_fitted(x,y):assert solve(make(x,y)).status=='out_of_scope'

def test_gravity_and_horizontal_geometric_similarity():
 a=solve(make()).values;b=solve(make(g=4*9.81)).values;c=solve(make(x=1.2)).values
 assert b['parcel_flight_time_s']==pytest.approx(a['parcel_flight_time_s']/2)
 assert b['tracked_horizontal_velocity_m_s']==pytest.approx(2*a['tracked_horizontal_velocity_m_s'])
 assert c['tracked_horizontal_velocity_m_s']==pytest.approx(2*a['tracked_horizontal_velocity_m_s'])
