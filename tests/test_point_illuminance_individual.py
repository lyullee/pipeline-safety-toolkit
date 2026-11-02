"""Solid-angle flux and exact geometric photometry references."""
import math
import pytest
from scipy.integrate import quad
from pipeline_integrity.point_illuminance import PointLight as S,PointIlluminanceRequest as Q,evaluate_point_illuminance as solve

def make(**kw):return Q(**(dict(surface_point_m=(0.,0.,0.),outward_surface_normal=(0.,0.,1.),sources=(S(id='light',position_m=(0.,0.,2.),directional_luminous_intensity_cd=100.,transmission_fraction=1.),),point_source_far_field_and_directional_intensities_reviewed=True)|kw))

def test_normal_and_oblique_three_four_five_geometry():
 assert solve(make()).values['total_illuminance_lux']==25.
 q=make(sources=(S(id='light',position_m=(3.,0.,4.),directional_luminous_intensity_cd=100.,transmission_fraction=1.),));assert solve(q).values['total_illuminance_lux']==pytest.approx(3.2)

@pytest.mark.parametrize('scale',[.25,2.,10.])
def test_distance_square_and_intensity_scaling(scale):
 s=S(id='light',position_m=(0.,0.,2.*scale),directional_luminous_intensity_cd=100.,transmission_fraction=1.)
 assert solve(make(sources=(s,))).values['total_illuminance_lux']==pytest.approx(25/scale**2)

def test_disk_received_flux_against_independent_exact_solid_angle():
 I=75.;z=2.;radius=3.
 def ring(r):
  q=make(surface_point_m=(r,0.,z),outward_surface_normal=(0.,0.,-1.),sources=(S(id='light',position_m=(0.,0.,0.),directional_luminous_intensity_cd=I,transmission_fraction=1.),))
  return solve(q).values['total_illuminance_lux']*2*math.pi*r
 flux=quad(ring,0.,radius,epsabs=1e-9,epsrel=1e-12)[0]
 assert flux==pytest.approx(2*math.pi*I*(1-z/math.sqrt(z*z+radius*radius)),rel=3e-13)

def test_multiple_sources_transmission_and_back_face():
 ss=tuple(S(id=str(i),position_m=position,directional_luminous_intensity_cd=100.,transmission_fraction=t) for i,(position,t) in enumerate([((0.,0.,2.),.5),((0.,0.,2.),1.),((0.,0.,-2.),1.),((0.,0.,2.),0.)]))
 r=solve(make(sources=ss));assert r.values['total_illuminance_lux']==37.5
 assert [x['illuminance_lux'] for x in r.values['source_contributions']]==[12.5,25.,0.,0.]

def test_rigid_translation_and_normal_scale_preserve_observation():
 s=S(id='light',position_m=(1.,2.,5.),directional_luminous_intensity_cd=100.,transmission_fraction=1.)
 assert solve(make(surface_point_m=(1.,2.,3.),outward_surface_normal=(0.,0.,7.),sources=(s,))).values['total_illuminance_lux']==25.

@pytest.mark.parametrize('field',['directional_luminous_intensity_cd','transmission_fraction'])
def test_missing_photometry_is_not_filled(field):
 q=make();s=q.sources[0].model_copy(update={field:None});assert solve(q.model_copy(update={'sources':(s,)})).status=='needs_data'

@pytest.mark.parametrize('kw',[{'outward_surface_normal':(0.,0.,0.)},{'surface_point_m':(0.,0.,2.)}])
def test_singular_geometry_rejected(kw):
 with pytest.raises(ValueError):solve(make(**kw))
