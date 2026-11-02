"""Uniform moving-medium vector ray references, with explicit fixed timing delay."""
import math
import pytest
from pipeline_integrity.paired_transit import PairedTransitRequest as Q,invert_paired_transit as solve

def make(u=25.,c=340.,beta=.8,**kw):
 L=.7;s=math.sqrt(c*c-u*u*(1-beta*beta));td=L/(s+u*beta);tu=L/(s-u*beta)
 return Q(**(dict(clock_id='common-duration-clock',path_length_m=L,signed_axial_projection_m=L*beta,forward_observed_time_s=td,reverse_observed_time_s=tu,forward_fixed_delay_s=0.,reverse_fixed_delay_s=0.,flow_area_m2=.03,chord_to_area_mean_factor=.9,uniform_axial_flow_isotropic_medium_and_stationary_paths_reviewed=True,area_mean_profile_conversion_reviewed=True)|kw))

@pytest.mark.parametrize('u,c,beta',[(25.,340.,1.),(-25.,340.,1.),(0.,340.,1.),(120.,330.,.6),(-100.,340.,.4),(25.,340.,-.8),(25.,1480.,.5)])
def test_independent_vector_ray_geometry_recovers_flow_and_medium_sound(u,c,beta):
 q=make(u,c,beta);r=solve(q);assert r.status=='computed'
 assert r.axial_flow_velocity_m_s==pytest.approx(u,rel=1e-12,abs=2e-11)
 assert r.uniform_medium_sound_speed_m_s==pytest.approx(c,rel=3e-13)
 assert r.signed_volume_flow_m3_s==pytest.approx(.03*.9*u,rel=1e-12,abs=1e-12)
 for sign,t in [(1,r.forward_propagation_time_s),(-1,r.reverse_propagation_time_s)]:
  nx=sign*beta;ny=math.sqrt(1-beta*beta)
  inferred=math.hypot(q.path_length_m*nx/t-u,q.path_length_m*ny/t)
  assert inferred==pytest.approx(c,rel=3e-13)
 assert not r.fluid_density_inferred

def test_oblique_half_sum_is_not_relabelled_true_sound_speed():
 r=solve(make(120.,330.,.6));assert r.effective_beam_speed_m_s<r.uniform_medium_sound_speed_m_s
 assert r.uniform_medium_sound_speed_m_s**2-r.effective_beam_speed_m_s**2==pytest.approx(120**2*(1-.6**2),rel=2e-13)

def test_asymmetric_fixed_delays_are_removed_in_same_clock():
 q=make();r=solve(q.model_copy(update={'forward_observed_time_s':q.forward_observed_time_s+2e-6,'forward_fixed_delay_s':2e-6,'reverse_observed_time_s':q.reverse_observed_time_s+7e-6,'reverse_fixed_delay_s':7e-6}))
 assert r.axial_flow_velocity_m_s==pytest.approx(25.,rel=2e-12)

@pytest.mark.parametrize('kw',[{'flow_area_m2':None},{'chord_to_area_mean_factor':None},{'area_mean_profile_conversion_reviewed':False}])
def test_area_profile_unknown_does_not_remove_valid_velocity(kw):
 r=solve(make(**kw));assert r.status=='computed' and r.signed_volume_flow_m3_s is None and r.axial_flow_velocity_m_s is not None

@pytest.mark.parametrize('field',['forward_observed_time_s','reverse_observed_time_s','forward_fixed_delay_s','reverse_fixed_delay_s'])
def test_unknown_timing_not_defaulted(field):assert solve(make(**{field:None})).status=='needs_data'

def test_perpendicular_geometry_is_not_identifiable():
 r=solve(make(25.,340.,0.));assert r.status=='non_identifiable' and r.uniform_medium_sound_speed_m_s is None and r.axial_flow_velocity_m_s is None

def test_impossible_delay_is_withheld():assert solve(make(forward_fixed_delay_s=.1)).status=='out_of_scope'

def test_invalid_projection_rejected():
 with pytest.raises(ValueError):solve(make(signed_axial_projection_m=.8))
