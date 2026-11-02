"""Independent integrated potentials, current flux and average-bus references."""
import math
import numpy as np
import pytest
from scipy.integrate import quad
from pipeline_integrity.earth_electrode import SlenderEarthElectrode as Rod,EarthElectrodeRequest as Request,evaluate_earth_electrodes as evaluate
from pipeline_integrity.ground_halfspace import GroundObservationPoint as Point

def rod(id='a',x=0.,length=2.,radius=.01,depth=0.,axis='vertical',current=None):
    return Rod(id=id,start_xyz_depth_m=(x,0.,depth),axis=axis,length_m=length,radius_m=radius,signed_current_into_ground_A=current)

def solve(rods=None,**changes):
    args=dict(uniform_resistivity_ohm_m=100.,electrodes=rods or (rod(),),mode='rod_average_common_bus',total_signed_current_A=1.,
        coordinate_frame_id='authored-soil',uniform_leakage_slender_line_and_homogeneous_soil_reviewed=True,current_return_at_infinity_reviewed=True)
    return evaluate(Request(**(args|changes)))

def reference_vertical_self(L,a,h,rho):
    def phi(z):
        return math.asinh((L-z)/a)+math.asinh(z/a)+math.asinh((2*h+z+L)/a)-math.asinh((2*h+z)/a)
    return rho/(4*math.pi*L**2)*quad(phi,0,L,epsabs=1e-11,epsrel=1e-12)[0]

@pytest.mark.parametrize('depth',[0.,.5,100.])
def test_vertical_average_self_resistance_matches_independent_line_potential_integral(depth):
    r=solve((rod(depth=depth),))
    assert r.status=='computed'
    assert r.common_bus_resistance_ohm==pytest.approx(reference_vertical_self(2.,.01,depth,100.),rel=2e-11)
    assert r.exact_finite_metal_surface_equipotential is False

@pytest.mark.parametrize('depth',[.1,1.,100.])
def test_horizontal_buried_self_matches_independent_separation_convolution(depth):
    r=solve((rod(depth=depth,axis='horizontal'),))
    L=2.;a=.01
    exact=100/(4*math.pi*L**2)*quad(lambda u:2*(L-u)*(1/math.hypot(u,a)+1/math.sqrt(u*u+4*depth*depth+a*a)),0,L,epsabs=1e-10,epsrel=1e-12)[0]
    assert r.status=='computed'
    assert r.common_bus_resistance_ohm==pytest.approx(exact,rel=2e-12)

def test_surface_vertical_slender_limit_has_logarithmic_diameter_not_radius_confusion():
    errors=[]
    for a in (.02,.002,.0002):
        r=solve((rod(radius=a),));dimensionless=r.common_bus_resistance_ohm*(2*math.pi*2)/100
        diameter=2*a
        limit=math.log(8*2/diameter)-1
        errors.append(abs(dimensionless-limit))
    assert errors[1]<.102*errors[0]
    assert errors[2]<.102*errors[1]
    assert errors[-1]<.0001

def mutual_reference(a,b,spacing,rho=100.):
    L=a.length_m;M=b.length_m;ha=a.start_xyz_depth_m[2];hb=b.start_xyz_depth_m[2]
    def potential(z):
        z+=ha
        return math.asinh((z-hb)/spacing)-math.asinh((z-hb-M)/spacing)+math.asinh((z+hb+M)/spacing)-math.asinh((z+hb)/spacing)
    return rho/(4*math.pi*L*M)*quad(potential,0,L,epsabs=1e-11,epsrel=1e-12)[0]

def test_unequal_vertical_mutual_resistance_and_reciprocity_match_independent_integral():
    a=rod(length=2.,depth=.2);b=rod('b',x=1.3,length=3.,depth=.7,radius=.02)
    r=solve((a,b));R=np.array(r.resistance_matrix_ohm)
    assert r.status=='computed'
    assert R[0,1]==pytest.approx(mutual_reference(a,b,1.3),rel=2e-10)
    assert R==pytest.approx(R.T,rel=0,abs=0)
    reverse=solve((b,a))
    assert reverse.signed_currents_A==pytest.approx(r.signed_currents_A[::-1],rel=2e-12)

def test_two_equal_rods_common_bus_and_imposed_current_are_different_conditions():
    common=solve((rod(),rod('b',x=3.)))
    R=np.array(common.resistance_matrix_ohm)
    assert common.signed_currents_A==pytest.approx((.5,.5),rel=2e-14)
    assert common.common_bus_resistance_ohm==pytest.approx((R[0,0]+R[0,1])/2,rel=2e-14)
    imposed=solve((rod(current=1.),rod('b',x=3.,current=0.)),mode='prescribed_currents',total_signed_current_A=None)
    assert imposed.common_bus_resistance_ohm is None
    assert imposed.rod_average_potentials_V==pytest.approx((R[0,0],R[0,1]),rel=2e-14)

def test_three_rod_average_bus_obeys_current_balance_and_minimum_quadratic_dissipation():
    r=solve((rod(),rod('b',x=3.),rod('c',x=6.)),total_signed_current_A=2.)
    R=np.array(r.resistance_matrix_ohm);I=np.array(r.signed_currents_A)
    assert sum(I)==pytest.approx(2.,abs=2e-14)
    assert r.rod_average_potentials_V==pytest.approx(np.full(3,r.common_bus_potential_V),rel=3e-14)
    assert I[1]<I[0] # Interior rod experiences stronger mutual crowding.
    for perturbation in (np.array([.1,-.2,.1]),np.array([.1,0.,-.1])):
        altered=I+perturbation
        assert altered@R@altered>r.model_ohmic_power_W
    assert r.model_ohmic_power_W==pytest.approx(2*r.common_bus_potential_V,rel=3e-14)

def test_infinite_spacing_limit_is_parallel_single_resistance_and_surface_green_mutual():
    r=solve((rod(),rod('b',x=2000.)))
    R=np.array(r.resistance_matrix_ohm)
    assert R[0,1]==pytest.approx(100/(2*math.pi*2000),rel=2e-6)
    assert r.common_bus_resistance_ohm==pytest.approx(R[0,0]/2,rel=.001)

def test_length_and_soil_resistivity_similarity_and_signed_current_power():
    a=solve((rod(),rod('b',x=3.)))
    b=solve((rod(length=20.,radius=.1),rod('b',x=30.,length=20.,radius=.1)))
    c=solve((rod(),rod('b',x=3.)),uniform_resistivity_ohm_m=1000.,total_signed_current_A=-2.)
    assert b.common_bus_resistance_ohm==pytest.approx(a.common_bus_resistance_ohm/10,rel=3e-14)
    assert c.common_bus_resistance_ohm==pytest.approx(10*a.common_bus_resistance_ohm,rel=3e-14)
    assert c.common_bus_potential_V==pytest.approx(-20*a.common_bus_potential_V,rel=3e-14)
    assert c.model_ohmic_power_W==pytest.approx(40*a.model_ohmic_power_W,rel=3e-14)

def test_far_field_current_flux_and_insulating_plane_from_independent_surface_quadrature():
    radius=50.;x,w=np.polynomial.legendre.leggauss(20);mu=(x+1)/2;weights=w/2
    points=[];flux_weights=[]
    for m,weight in zip(mu,weights):
        for k in range(32):
            angle=2*math.pi*k/32
            p=(radius*math.sqrt(1-m*m)*math.cos(angle),radius*math.sqrt(1-m*m)*math.sin(angle),radius*m)
            points.append(Point(id=str(len(points)),position_xyz_depth_m=p));flux_weights.append(weight*2*math.pi/32*radius**2)
    points.append(Point(id='insulating',position_xyz_depth_m=(4.,2.,0.)))
    r=solve((rod(),rod('b',x=3.)),total_signed_current_A=3.,observations=tuple(points))
    assert r.status=='computed'
    flux=sum(np.dot(np.array(p.position_xyz_depth_m)/radius,f.current_density_xyz_depth_A_m2)*weight for p,f,weight in zip(points,r.fields,flux_weights))
    assert flux==pytest.approx(3.,rel=2e-11)
    assert r.fields[-1].current_density_xyz_depth_A_m2[2]==pytest.approx(0.,abs=1e-14)

def test_electric_field_is_negative_independent_potential_gradient_and_j_equals_e_over_rho():
    center=np.array([3.,2.,1.]);h=1e-4;points=[Point(id='center',position_xyz_depth_m=tuple(center))]
    for axis in range(3):
        delta=np.eye(3)[axis]*h
        points.extend([Point(id=f'{axis}+',position_xyz_depth_m=tuple(center+delta)),Point(id=f'{axis}-',position_xyz_depth_m=tuple(center-delta))])
    r=solve(observations=tuple(points));f=r.fields
    gradient=np.array([(f[1+2*i].potential_V_relative_infinity-f[2+2*i].potential_V_relative_infinity)/(2*h) for i in range(3)])
    assert f[0].electric_field_xyz_depth_V_m==pytest.approx(-gradient,rel=2e-8)
    assert f[0].current_density_xyz_depth_A_m2==pytest.approx(-gradient/100,rel=2e-8)

def test_close_spacing_requires_quadrature_refinement_and_matches_independent_reference():
    rods=(rod(),rod('b',x=.08))
    coarse=solve(rods,maximum_quadrature_order=16,quadrature_relative_tolerance=1e-9)
    assert coarse.status=='out_of_scope'
    fine=solve(rods,maximum_quadrature_order=256,quadrature_relative_tolerance=1e-9)
    assert fine.status=='computed'
    assert fine.mutual_quadrature_order>16
    assert fine.resistance_matrix_ohm[0][1]==pytest.approx(mutual_reference(*rods,.08),rel=2e-10)

@pytest.mark.parametrize('changes',[{'uniform_resistivity_ohm_m':None},{'uniform_leakage_slender_line_and_homogeneous_soil_reviewed':False},{'current_return_at_infinity_reviewed':False},{'total_signed_current_A':None}])
def test_missing_state_review_or_return_does_not_produce_a_resistance_claim(changes):
    assert solve(**changes).status=='needs_data'

def test_invalid_geometry_observation_and_ambiguous_controls_are_rejected():
    with pytest.raises(ValueError,match='intersect'):solve((rod(),rod('b',x=.01)))
    with pytest.raises(ValueError):rod(depth=-1.)
    with pytest.raises(ValueError):rod(radius=.3)
    with pytest.raises(ValueError,match='outside electrode cores'):solve(observations=(Point(id='inside',position_xyz_depth_m=(0.,0.,1.)),))
    with pytest.raises(ValueError,match='must not be mixed'):solve((rod(current=1.),),mode='prescribed_currents')
