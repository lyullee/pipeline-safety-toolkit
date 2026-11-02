"""Independent integrals, dimensions and limiting cases for ideal release laws."""
import math
import pytest
from scipy.integrate import quad,solve_ivp
from pipeline_integrity.porous_release import (
    SurfaceViscousSpillRequest as Surface, evaluate_surface_viscous_spill as surface,
    SphericalPorousFrontRequest as Sphere, evaluate_spherical_porous_front as sphere)


def srf(**kwargs):
    return Surface(feed_m3_s=.003,kinematic_viscosity_m2_s=2e-5,times_s=(0,1,4,100),
        flat_axisymmetric_viscous_regime_reviewed=True,**kwargs)


def sph(**kwargs):
    args=dict(source_radius_m=.01,permeability_m2=1e-11,dynamic_viscosity_pa_s=.02,
        porosity=.35,driving_pressure_pa=2e5,times_s=(0,1,20,1000),
        homogeneous_gravity_free_sharp_front_reviewed=True)
    args.update(kwargs)
    return Sphere(**args)


def test_surface_similarity_value_and_zero():
    r=surface(srf()); assert r.radius_m[0]==0
    expected=.715*(9.80665*.003**3/(3*2e-5))**.125
    assert r.radius_m[1]==pytest.approx(expected)
    assert r.radius_m[2]/r.radius_m[1]==pytest.approx(2)
    assert r.feed_volume_m3==(0,.003,.012,.3)


@pytest.mark.parametrize('scale,exponent',[(.1,3/8),(2,3/8),(100,3/8)])
def test_surface_feed_dimensional_scaling(scale,exponent):
    a=srf();b=a.model_copy(update={'feed_m3_s':a.feed_m3_s*scale})
    assert surface(b).radius_m[1]/surface(a).radius_m[1]==pytest.approx(scale**exponent)


def test_surface_drainage_exact_transition_and_input_volume_not_storage():
    q=srf(soil_permeability_m2=1e-8);r=surface(q);t=r.drainage_transition_s
    reference=.697*(6*q.feed_m3_s*q.kinematic_viscosity_m2_s**5/
        (math.pi*q.gravity_m_s2**5*q.soil_permeability_m2**4))**.25
    assert t==pytest.approx(reference)
    r=surface(q.model_copy(update={'times_s':(t*(1-1e-8),t,t*3)}))
    assert r.radius_m[0]<r.radius_m[1]==r.radius_m[2]
    assert r.feed_volume_m3[-1]==pytest.approx(3*r.feed_volume_m3[1])
    assert not r.post_transition_storage_partition_resolved


def test_surface_review_and_immutability():
    q=srf();assert surface(q.model_copy(update={'flat_axisymmetric_viscous_regime_reviewed':False})).status=='out_of_scope'
    with pytest.raises(Exception):q.feed_m3_s=1


@pytest.mark.parametrize('field,value',[('feed_m3_s',0),('kinematic_viscosity_m2_s',-1),('soil_permeability_m2',0),('times_s',(-1,))])
def test_surface_invalid(field,value):
    with pytest.raises(ValueError):Surface.model_validate({**srf().model_dump(),field:value})


@pytest.mark.parametrize('time',[1e-12,1e-5,1.,1e5,1e12])
def test_sphere_independent_time_integral(time):
    q=sph(times_s=(time,));r=sphere(q);y=r.radius_m[0]/q.source_radius_m-1
    # Integrate dt/dr directly from Darcy resistance and spherical pore storage.
    rs=q.source_radius_m
    integral=quad(lambda w:q.porosity*q.dynamic_viscosity_pa_s/(q.permeability_m2*q.driving_pressure_pa)
        *rs**2*w*(1+w),0,y,epsabs=max(1e-25,time*1e-10))[0]
    assert integral==pytest.approx(time,rel=3e-6 if time<1e-10 else 1e-10)


def test_sphere_radial_pressure_drop_and_mass_storage():
    q=sph();r=sphere(q)
    for radius,flow,volume in zip(r.radius_m[1:],r.instantaneous_feed_m3_s[1:],r.cumulative_intruded_liquid_m3[1:]):
        drop=quad(lambda x:q.dynamic_viscosity_pa_s*flow/(4*math.pi*q.permeability_m2*x*x),q.source_radius_m,radius)[0]
        assert drop==pytest.approx(q.driving_pressure_pa,rel=2e-12)
        pore_volume=quad(lambda x:4*math.pi*q.porosity*x*x,q.source_radius_m,radius)[0]
        assert volume==pytest.approx(pore_volume,rel=2e-12)
    assert r.radius_m[0]==q.source_radius_m
    assert r.instantaneous_feed_m3_s[0] is None
    assert r.cumulative_intruded_liquid_m3[0]==0


def test_sphere_mass_rate_by_refined_center_difference():
    time=3.;q=sph(times_s=(time,));reference=sphere(q).instantaneous_feed_m3_s[0]
    errors=[]
    for dt in (.03,.015,.0075):
        v=sphere(sph(times_s=(time-dt,time+dt))).cumulative_intruded_liquid_m3
        errors.append(abs((v[1]-v[0])/(2*dt)-reference))
    assert errors[0]/errors[1]>3.9 and errors[1]/errors[2]>3.9


def test_sphere_independent_differential_integration():
    q=sph(times_s=(1.,100.));r=sphere(q);rs=q.source_radius_m
    a=q.permeability_m2*q.driving_pressure_pa/(q.dynamic_viscosity_pa_s*q.porosity)
    numeric=solve_ivp(lambda t,y:[a/(y[0]**2*(1/rs-1/y[0]))],(1,100),[r.radius_m[0]],rtol=2e-10,atol=1e-12)
    assert numeric.success
    assert numeric.y[0,-1]==pytest.approx(r.radius_m[1],rel=3e-9)


def test_sphere_breakthrough_event_and_geometry():
    q=sph(source_center_depth_m=.8);r=sphere(q);t=r.exact_breakthrough_s
    r=sphere(q.model_copy(update={'times_s':(0,t/2,t,t*2)}))
    assert r.radius_m[2]==pytest.approx(.8,rel=2e-15)
    assert r.unperturbed_sphere_surface_intersection_m[:3]==(0,0,0)
    assert r.unperturbed_sphere_surface_intersection_m[3]**2==pytest.approx(r.radius_m[3]**2-.8**2)
    assert not r.post_breakthrough_flow_field_resolved
    assert r.exact_breakthrough_s<r.asymptotic_breakthrough_s


def test_sphere_long_time_limit_and_scaling():
    # At 1e14 s the exact finite-source correction is still ~1e-5;
    # move further into the declared asymptotic regime rather than relaxing it.
    q=sph(times_s=(1e18,));r=sphere(q)
    assert r.instantaneous_feed_m3_s[0]/r.asymptotic_feed_m3_s==pytest.approx(1,rel=3e-6)
    approx=(3*q.source_radius_m*q.permeability_m2*q.driving_pressure_pa*1e18/
        (q.dynamic_viscosity_pa_s*q.porosity))**(1/3)
    assert r.radius_m[0]==pytest.approx(approx,rel=3e-6)
    slower=sphere(sph(times_s=(1.,),dynamic_viscosity_pa_s=.04))
    assert slower.radius_m==sphere(sph(times_s=(.5,))).radius_m


@pytest.mark.parametrize('field,value',[('porosity',0),('porosity',1.1),('source_radius_m',0),('permeability_m2',0),('driving_pressure_pa',-1),('source_center_depth_m',.005),('times_s',(-1,))])
def test_sphere_invalid(field,value):
    with pytest.raises(ValueError):sph(**{field:value})


def test_sphere_unreviewed_and_unsorted_observation_order():
    assert sphere(sph(homogeneous_gravity_free_sharp_front_reviewed=False)).status=='out_of_scope'
    r=sphere(sph(times_s=(3,0,3)))
    assert r.radius_m[0]==r.radius_m[2]>r.radius_m[1]
