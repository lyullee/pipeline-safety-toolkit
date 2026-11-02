"""Independent charge-flux, Laplace, boundary and reference-potential tests."""
import math
import pytest
from scipy.integrate import quad
from pipeline_integrity.ground_halfspace import (
    SurfacePointCurrent,GroundObservationPoint,GroundHalfspaceRequest,evaluate_ground_halfspace)


def source(name='injection',position=(0.,0.),current=2.):
    return SurfacePointCurrent(id=name,horizontal_position_m=position,signed_current_into_ground_A=current)


def request(**changes):
    d=dict(uniform_resistivity_ohm_m=50.,coordinate_frame_id='ground-frame',sources=(source(),),
        observations=(GroundObservationPoint(id='observation',position_xyz_depth_m=(3.,4.,2.)),),
        homogeneous_unbounded_ohmic_halfspace_and_surface_point_sources_reviewed=True,
        net_current_return_at_infinity_reviewed=True)
    d.update(changes);return GroundHalfspaceRequest(**d)


def at(x,y,z,**changes):
    return evaluate_ground_halfspace(request(observations=(GroundObservationPoint(id='p',position_xyz_depth_m=(x,y,z)),),**changes)).fields[0]


@pytest.mark.parametrize('radius',[.2,7.,500.])
def test_integrated_hemispherical_charge_flux(radius):
    # Use field components in the hemisphere normal rather than potential formula.
    def ring(theta):
        x,z=radius*math.sin(theta),radius*math.cos(theta)
        field=at(x,0,z).current_density_xyz_depth_A_m2
        normal=field[0]*math.sin(theta)+field[2]*math.cos(theta)
        return 2*math.pi*radius**2*normal*math.sin(theta)
    outward=quad(ring,0,math.pi/2,epsabs=1e-10)[0]
    assert outward==pytest.approx(2.,rel=3e-12)


@pytest.mark.parametrize('xy',[(3.,4.),(-2.,1.),(.001,.03)])
def test_insulating_surface_no_normal_current(xy):
    f=at(*xy,0.)
    assert f.current_density_xyz_depth_A_m2[2]==0


def test_potential_gradient_and_laplace_convergence():
    location=(3.,4.,2.);field=at(*location);v0=field.potential_V_relative_infinity;laplacians=[]
    for step in (.08,.04,.02):
        curvature=0.
        for axis in range(3):
            plus=list(location);minus=list(location);plus[axis]+=step;minus[axis]-=step
            vp=at(*plus).potential_V_relative_infinity;vm=at(*minus).potential_V_relative_infinity
            gradient=(vp-vm)/(2*step)
            if step==.02:assert gradient==pytest.approx(-field.electric_field_xyz_depth_V_m[axis],rel=4e-5)
            curvature+=(vp-2*v0+vm)/step**2
        laplacians.append(abs(curvature))
    assert laplacians[2]<laplacians[1]/3.8<laplacians[0]/14.


def test_finite_return_dipole_and_polarity_superposition():
    sources=(source('anode',(-1.,0.),2.),source('return',(1.,0.),-2.))
    a=at(4.,0.,0.,sources=sources,net_current_return_at_infinity_reviewed=False)
    individual=at(4.,0.,0.,sources=(sources[0],)).potential_V_relative_infinity+at(4.,0.,0.,sources=(sources[1],)).potential_V_relative_infinity
    assert a.potential_V_relative_infinity==pytest.approx(individual)
    reverse=tuple(s.model_copy(update={'signed_current_into_ground_A':-s.signed_current_into_ground_A}) for s in sources)
    b=at(4.,0.,0.,sources=reverse,net_current_return_at_infinity_reviewed=False)
    assert b.potential_V_relative_infinity==pytest.approx(-a.potential_V_relative_infinity)
    near=at(100.,0.,0.,sources=sources).potential_V_relative_infinity
    far=at(200.,0.,0.,sources=sources).potential_V_relative_infinity
    assert far/near==pytest.approx(.25,rel=1e-4)


def test_ground_coordinate_translation_and_rotation():
    a=at(3.,4.,2.)
    translated=at(13.,-3.,2.,sources=(source(position=(10.,-7.)),))
    assert translated.potential_V_relative_infinity==pytest.approx(a.potential_V_relative_infinity)
    rotated=at(-4.,3.,2.)
    assert rotated.electric_field_xyz_depth_V_m==pytest.approx((-a.electric_field_xyz_depth_V_m[1],a.electric_field_xyz_depth_V_m[0],a.electric_field_xyz_depth_V_m[2]))


@pytest.mark.parametrize('scale',[.01,13.])
def test_resistivity_scales_voltage_not_prescribed_current_density(scale):
    a=at(3.,4.,2.);b=at(3.,4.,2.,uniform_resistivity_ohm_m=50*scale)
    assert b.potential_V_relative_infinity==pytest.approx(a.potential_V_relative_infinity*scale)
    assert b.current_density_xyz_depth_A_m2==pytest.approx(a.current_density_xyz_depth_A_m2)


@pytest.mark.parametrize('change',[dict(uniform_resistivity_ohm_m=None),dict(sources=(source(current=None),)),
    dict(homogeneous_unbounded_ohmic_halfspace_and_surface_point_sources_reviewed=False),
    dict(net_current_return_at_infinity_reviewed=False)])
def test_unknown_property_or_return_not_silently_closed(change):
    r=evaluate_ground_halfspace(request(**change));assert r.status=='needs_data' and r.fields==()


def test_active_point_singularity_not_finite_electrode_resistance():
    r=evaluate_ground_halfspace(request(observations=(GroundObservationPoint(id='at-electrode',position_xyz_depth_m=(0,0,0)),)))
    assert r.status=='out_of_scope' and not r.fields


def test_zero_source_at_observation_is_known_zero():
    r=evaluate_ground_halfspace(request(sources=(source(current=0),),observations=(GroundObservationPoint(id='at-zero-source',position_xyz_depth_m=(0,0,0)),)))
    assert r.status=='computed' and r.fields[0].potential_V_relative_infinity==0


@pytest.mark.parametrize('change',[dict(sources=(source(),source())),dict(observations=(GroundObservationPoint(id='air',position_xyz_depth_m=(1,2,-1)),))])
def test_invalid_identity_and_halfspace(change):
    with pytest.raises(ValueError):evaluate_ground_halfspace(request(**change))
