"""Radial Poisson BVP, independent wall work and geometric limiting references."""
from decimal import Decimal, localcontext
import math

import numpy as np
import pytest
from scipy.integrate import solve_bvp

from pipeline_integrity.annular_flow import ConcentricAnnulusLossRequest as Request, evaluate_concentric_annulus_loss as evaluate


def request(**changes):
    return Request(**(dict(outer_flow_radius_m=.05, inner_body_radius_m=.02, length_m=7.,
        volume_flow_m3_s=.0002, density_kg_m3=850., dynamic_viscosity_Pa_s=.02,
        closure="newtonian_laminar", maximum_reviewed_laminar_reynolds=1000.,
        fixed_concentric_steady_single_liquid_state_reviewed=True,
        fully_developed_no_slip_laminar_domain_reviewed=True) | changes))


def radial_bvp(inner, outer, gradient, mu):
    # Third state integrates actual annular volume flux. This reference solves
    # the differential field; it uses neither its logarithmic solution nor
    # the production conductance/profile expressions.
    radii=np.geomspace(inner, outer, 120)
    def ode(r,y):
        return np.vstack((y[1], -gradient/mu-y[1]/r, 2*math.pi*r*y[0]))
    def bc(left,right):return np.array((left[0],right[0],left[2]))
    result=solve_bvp(ode,bc,radii,np.zeros((3,len(radii))),tol=2e-10,max_nodes=20000)
    assert result.success,result.message
    return result


@pytest.mark.parametrize("inner", [.0005,.02,.04])
@pytest.mark.parametrize("gradient", [-2.,.5,3.])
def test_actual_annular_flow_and_wall_shears_match_independent_radial_poisson_bvp(inner,gradient):
    outer=.05;mu=.02;length=7.
    reference=radial_bvp(inner,outer,gradient,mu)
    flow=float(reference.y[2,-1])
    nodes,weights=np.polynomial.legendre.leggauss(96)
    radii=(outer+inner)/2+(outer-inner)*nodes/2
    q=request(inner_body_radius_m=inner,volume_flow_m3_s=flow,
              radial_positions_m=(inner,*map(float,radii),outer))
    r=evaluate(q);assert r.status=="computed"
    assert r.signed_pressure_gradient_Pa_m==pytest.approx(gradient,rel=2e-8)
    assert r.signed_friction_pressure_drop_Pa==pytest.approx(gradient*length,rel=2e-8)
    independent=reference.sol(np.array(q.radial_positions_m))
    velocity=np.array([p.axial_velocity_m_s for p in r.radial_profile])
    derivative=np.array([p.radial_velocity_gradient_s_inv for p in r.radial_profile])
    np.testing.assert_allclose(velocity,independent[0],rtol=2e-8,atol=1e-12)
    np.testing.assert_allclose(derivative,independent[1],rtol=2e-8,atol=1e-10)
    assert velocity[0]==velocity[-1]==0.
    area_flux=(outer-inner)/2*np.dot(weights,2*math.pi*radii*velocity[1:-1])
    assert area_flux==pytest.approx(flow,rel=2e-12)
    dissipation=length*mu*(outer-inner)/2*np.dot(weights,2*math.pi*radii*derivative[1:-1]**2)
    assert dissipation==pytest.approx(r.pressure_volume_dissipation_W,rel=2e-8)
    inner_traction=-mu*reference.y[1,0]
    outer_traction=mu*reference.y[1,-1]
    assert r.inner_wall_axial_traction_on_fluid_Pa==pytest.approx(inner_traction,rel=2e-8)
    assert r.outer_wall_axial_traction_on_fluid_Pa==pytest.approx(outer_traction,rel=2e-8)
    wall_force=length*(2*math.pi*inner*r.inner_wall_axial_traction_on_fluid_Pa
                      +2*math.pi*outer*r.outer_wall_axial_traction_on_fluid_Pa)
    assert wall_force+r.pressure_force_on_segment_N==pytest.approx(0.,abs=2e-13)
    assert r.pressure_volume_dissipation_W>0


@pytest.mark.parametrize("sign",[-1.,1.])
def test_thin_gap_converges_to_independent_plane_slit_pressure_and_parabola(sign):
    errors=[];outer=.05;mu=.02;mean=sign*1e-5
    for fraction in (.1,.01,.001,1e-6):
        inner=outer*(1-fraction);gap=outer-inner;area=math.pi*gap*(outer+inner)
        eta=np.array([0.,.1,.5,.9,1.]);radii=tuple(inner+gap*eta)
        r=evaluate(request(inner_body_radius_m=inner,volume_flow_m3_s=mean*area,radial_positions_m=radii))
        plane_gradient=12*mu*mean/gap**2
        errors.append(abs(r.signed_pressure_gradient_Pa_m/plane_gradient-1))
        if fraction==1e-6:
            assert errors[-1]<1e-8
            got=np.array([p.axial_velocity_m_s for p in r.radial_profile])
            np.testing.assert_allclose(got/mean,6*eta*(1-eta),atol=3e-7,rtol=3e-7)
    assert all(b<a for a,b in zip(errors,errors[1:]))


def test_vanishing_inner_rod_approaches_circular_poiseuille_limit_without_replacing_annular_area():
    errors=[];outer=.05;mean=.01;mu=.02
    for logarithmic_ratio in (20.,80.,300.,650.):
        inner=outer*math.exp(-logarithmic_ratio)
        area=math.pi*(outer-inner)*(outer+inner)
        r=evaluate(request(inner_body_radius_m=inner,volume_flow_m3_s=mean*area))
        circle=8*mu*mean/outer**2
        errors.append(abs(r.signed_pressure_gradient_Pa_m/circle-1))
        assert r.flow_area_m2==area
    assert all(b<a for a,b in zip(errors,errors[1:]))
    assert errors[-1]<.002


@pytest.mark.parametrize("fraction",[1e-6,1e-10,1e-12])
def test_thin_conductance_and_next_float_inside_wall_match_ninety_six_digit_reference(fraction):
    outer=.05;inner=outer*(1-fraction);area=math.pi*(outer-inner)*(outer+inner)
    radii=(inner,math.nextafter(inner,outer),(inner+outer)/2,math.nextafter(outer,inner),outer)
    q=request(inner_body_radius_m=inner,volume_flow_m3_s=area*1e-7,radial_positions_m=radii)
    r=evaluate(q)
    with localcontext() as ctx:
        ctx.prec=96;D=Decimal.from_float
        a,b,m,Q=D(inner),D(outer),D(.02),D(q.volume_flow_m3_s)
        logarithm=(b/a).ln();cross=b*b-a*a
        conductance=b**4-a**4-cross*cross/logarithm
        pi=Decimal('3.141592653589793238462643383279502884197169399375105820974944592307816406286208998628034825342117068')
        gradient=8*m*Q/(pi*conductance)
        assert r.signed_pressure_gradient_Pa_m==pytest.approx(float(gradient),rel=3e-14)
        for point in r.radial_profile[1:-1]:
            radius=D(point.radius_m)
            velocity=gradient/(4*m)*(b*b-radius*radius-cross*(b/radius).ln()/logarithm)
            assert point.axial_velocity_m_s>0
            assert point.axial_velocity_m_s==pytest.approx(float(velocity),rel=2e-12,abs=0.)


@pytest.mark.parametrize("sign",[-1.,1.])
def test_supplied_darcy_uses_actual_mean_velocity_and_native_pressure_loss(monkeypatch,sign):
    import fluids.core
    actual=fluids.core.dP_from_K;seen=[]
    def capture(K,rho,V):seen.append((K,rho,V));return actual(K,rho,V)
    monkeypatch.setattr(fluids.core,'dP_from_K',capture)
    q=request(closure='supplied_darcy',volume_flow_m3_s=sign*.0027,
        supplied_darcy_friction_factor=.024,factor_basis_id='authored-reviewed-annular-factor',
        supplied_factor_applicability_reviewed=True,radial_positions_m=(.02,.05))
    r=evaluate(q);area=math.pi*(.05**2-.02**2);velocity=q.volume_flow_m3_s/area;dh=4*area/(2*math.pi*(.05+.02))
    assert r.flow_area_m2==pytest.approx(area,rel=3e-16)
    assert r.hydraulic_diameter_m==pytest.approx(dh,rel=3e-16)
    assert r.mean_velocity_m_s==pytest.approx(velocity,rel=3e-16)
    assert all(v==pytest.approx(velocity) and rho==850. for _,rho,v in seen)
    assert r.signed_friction_pressure_drop_Pa==pytest.approx(sign*.024*7/dh*850*velocity**2/2)
    assert r.pressure_volume_dissipation_W>0 and r.profile_status=='out_of_scope'
    assert r.inner_wall_axial_traction_on_fluid_Pa is None
    # Historical force/mass conversion is checked as a coefficient interval,
    # using authored values, never a protected worked example or chart.
    psi=.45359237*9.80665/.0254**2;ft=.3048;rho_us=850* ft**3/.45359237
    coefficient=abs(r.signed_friction_pressure_drop_Pa)/psi*(r.hydraulic_radius_m/ft)/(.024*(7/ft)*rho_us*(velocity/ft)**2)
    assert .0000265<coefficient<.0000275
    assert velocity!=pytest.approx(q.volume_flow_m3_s/(math.pi*dh**2/4))


@pytest.mark.parametrize("closure",['supplied_darcy','newtonian_laminar'])
@pytest.mark.parametrize("zero",['flow','length'])
def test_zero_flow_or_length_preserves_zero_pressure_work(closure,zero):
    q=request(closure=closure,**({'volume_flow_m3_s':0.} if zero=='flow' else {'length_m':0.}))
    if closure=='supplied_darcy':q=q.model_copy(update={'supplied_darcy_friction_factor':.02,'factor_basis_id':'authored','supplied_factor_applicability_reviewed':True})
    r=evaluate(q);assert r.status=='computed'
    assert r.signed_friction_pressure_drop_Pa==r.pressure_volume_dissipation_W==0.
    if zero=='flow' and closure=='newtonian_laminar':
        assert r.darcy_friction_factor is None
        assert r.inner_wall_axial_traction_on_fluid_Pa==r.outer_wall_axial_traction_on_fluid_Pa==0.


@pytest.mark.parametrize("change",[{'volume_flow_m3_s':None},{'density_kg_m3':None},{'dynamic_viscosity_Pa_s':None},
    {'maximum_reviewed_laminar_reynolds':None},{'fully_developed_no_slip_laminar_domain_reviewed':False},
    {'fixed_concentric_steady_single_liquid_state_reviewed':False}])
def test_unknown_laminar_state_or_applicability_is_withheld(change):
    r=evaluate(request().model_copy(update=change))
    assert r.status=='needs_data' and r.signed_friction_pressure_drop_Pa is None


def test_laminar_support_and_supplied_factor_identity_are_not_invented():
    r=evaluate(request(maximum_reviewed_laminar_reynolds=.001))
    assert r.status=='out_of_scope' and r.signed_friction_pressure_drop_Pa is None
    r=evaluate(request(closure='supplied_darcy'))
    assert r.status=='needs_data'
    r=evaluate(request(closure='supplied_darcy',supplied_darcy_friction_factor=.02,
        factor_basis_id='authored',supplied_factor_applicability_reviewed=True,dynamic_viscosity_Pa_s=None))
    assert r.status=='computed' and r.reynolds_hydraulic_diameter is None


@pytest.mark.parametrize("change",[{'inner_body_radius_m':.05},{'inner_body_radius_m':0.},
    {'volume_flow_m3_s':float('nan')},{'density_kg_m3':True},{'dynamic_viscosity_Pa_s':0.},
    {'radial_positions_m':(.019,)},{'radial_positions_m':(.051,)},{'supplied_darcy_friction_factor':.02}])
def test_invalid_geometry_state_or_double_closure_is_revalidated(change):
    with pytest.raises(ValueError):evaluate(request().model_copy(update=change))


def test_sample_order_immutability_and_out_of_range_arithmetic_remain_explicit():
    q=request(radial_positions_m=(.05,.02,.035,.05));r=evaluate(q)
    assert tuple(p.radius_m for p in r.radial_profile)==q.radial_positions_m
    assert r.radial_profile[0].axial_velocity_m_s==r.radial_profile[-1].axial_velocity_m_s==0.
    with pytest.raises(ValueError):q.length_m=2.
    with pytest.raises(ValueError):evaluate(request(volume_flow_m3_s=1e308))
