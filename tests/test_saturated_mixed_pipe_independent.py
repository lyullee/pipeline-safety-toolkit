"""Mass/phase observations and independent barotropic wave references."""
import math
from functools import lru_cache
import numpy as np
import pytest
from scipy.integrate import quad,solve_ivp
from scipy.optimize import brentq
from pipeline_integrity.gravity_free_surface_pipe import CircularFreeSurfaceSettings,FreeSurfaceBoundary,FreeSurfaceBoundaryStage
from pipeline_integrity.saturated_mixed_pipe import SaturatedMixedPipeSettings,mixed_pipe_closure,saturated_mixed_area_discharge,solve_saturated_mixed_pipe


def settings(duration=.02,length=4.,**kwargs):
    return SaturatedMixedPipeSettings(numerics=CircularFreeSurfaceSettings(length_m=length,inner_diameter_m=.5,duration_s=duration,**kwargs),
        saturation_pressure_pa_abs=2000.,barotropic_sound_speed_m_s=40.,maximum_acoustic_mach=.1,
        prismatic_barotropic_single_liquid_and_massless_saturation_reviewed=True)


def stages(kind='periodic'):
    b=FreeSurfaceBoundary(kind=kind)
    return (FreeSurfaceBoundaryStage(start_time_s=0.,left=b,right=b),)


def budgets(r):
    l=r.core.ledger
    scale=np.maximum(1.,abs(l.initial_mass_momentum)+abs(l.final_mass_momentum)+abs(l.integrated_boundary_inflow)+abs(l.integrated_distributed_sources))
    assert np.max(abs(l.residual)/scale)<3e-13


@pytest.mark.parametrize('head,velocity',[(.2,0.),(.8,0.),(.25,.4),(.7,-.4)])
def test_uniform_wet_or_filled_signed_translation(head,velocity):
    q=settings();u=saturated_mixed_area_discharge(np.full(32,head),velocity,q)
    r=solve_saturated_mixed_pipe(u,q,boundary_stages=stages())
    np.testing.assert_allclose(r.core.area_discharge,u,rtol=0,atol=2e-14)
    assert np.all(r.final.free_surface==(head<.5))
    budgets(r)


def test_resting_surface_crosses_crown_over_bed_without_numerical_motion():
    n=48;x=(np.arange(n)+.5)*4/n;bed=.04*np.sin(2*np.pi*x/4)
    q=settings(duration=.01).model_copy(update={'bed_elevations_m':tuple(bed)})
    u=saturated_mixed_area_discharge(.5-bed,0.,q)
    r=solve_saturated_mixed_pipe(u,q,boundary_stages=stages())
    np.testing.assert_allclose(r.core.area_discharge,u,rtol=0,atol=3e-13)
    assert r.final.free_surface.any() and (~r.final.free_surface).any()
    np.testing.assert_allclose(r.final.equivalent_head_m+bed,.5,atol=3e-11,rtol=0)
    budgets(r)


def test_filled_negative_gauge_is_distinct_from_massless_vapor_surface():
    q=settings();c=mixed_pipe_closure(q)
    head=.5+math.log1p((50000.-2000.)/(1000.*40**2))/c.kappa
    u=saturated_mixed_area_discharge(np.full(12,head),0.,q)
    r=solve_saturated_mixed_pipe(u,q,boundary_stages=stages('reflective'))
    assert not r.final.free_surface.any()
    np.testing.assert_allclose(r.final.crown_pressure_pa_abs,50000.,rtol=2e-13)
    assert r.final_geometric_vapor_volume_m3==0
    assert not r.vapor_mass_solved and not r.latent_or_thermal_energy_solved
    budgets(r)


@lru_cache(maxsize=8192)
def independent_section(head):
    # Independent vertical Cartesian quadrature, not the production angular rule.
    d=.5;g=9.80665;c=40.;top=min(head,d)
    width=lambda y:2*math.sqrt(max(0.,y*(d-y)))
    area=quad(lambda y:math.exp(g*(head-y)/c**2)*width(y),0.,top,epsabs=3e-12,epsrel=3e-11)[0]
    pressure=quad(lambda y:c**2*math.expm1(g*(head-y)/c**2)*width(y),0.,top,epsabs=3e-12,epsrel=3e-11)[0]
    topwidth=width(head) if head<d else 0.
    derivative=topwidth+g/c**2*area
    return area,pressure,math.sqrt(g*area/derivative),derivative


@lru_cache(maxsize=4)
def wave_primitive(hr,hl):
    # Solve the independent Riemann-invariant integral once, rather than nest
    # thousands of quadratures inside each characteristic root search.
    def integrand(h,f):
        a,_,c,da=independent_section(h)
        return [c*da/a]
    first=solve_ivp(integrand,(hr,.5),[0.],method='DOP853',rtol=8e-11,atol=2e-12,dense_output=True)
    assert first.success
    endpoint=float(first.y[0,-1])
    return lambda h:float(first.sol(h)[0]) if h<.5 else endpoint+9.80665/40.*(h-.5)


def exact_riemann(x,time,hl=.8,hr=.28):
    al,pl,cl,_=independent_section(hl);ar,pr,cr,_=independent_section(hr)
    primitive=wave_primitive(hr,hl)
    def curve(h,h0):
        a,p,_,_=independent_section(h);a0,p0,_,_=independent_section(h0)
        if h>h0:return math.sqrt((p-p0)*(a-a0)/(a*a0))
        return primitive(h)-primitive(h0)
    hs=brentq(lambda h:curve(h,hl)+curve(h,hr),hr,hl,xtol=2e-12)
    us=-curve(hs,hl);astar,_,cstar,_=independent_section(hs)
    shock=astar*us/(astar-ar)
    out=[]
    for xi in np.asarray(x)/time:
        if xi < -cl:h,v=hl,0.
        elif xi < us-cstar:
            h=brentq(lambda h:-curve(h,hl)-independent_section(h)[2]-xi,hs,hl,xtol=3e-11);v=-curve(h,hl)
        elif xi < shock:h,v=hs,us
        else:h,v=hr,0.
        out.append((independent_section(h)[0],v,h))
    return np.asarray(out)


def test_nonlinear_filled_to_partial_riemann_reference_and_mesh_convergence():
    errors=[]
    for n in (100,200):
        q=settings(duration=.015,length=2.)
        x=(np.arange(n)+.5)*2/n-1
        u=saturated_mixed_area_discharge(np.where(x<0,.8,.28),0.,q)
        r=solve_saturated_mixed_pipe(u,q)
        exact=exact_riemann(x,.015)
        errors.append(np.mean(abs(r.core.area_discharge[:,0]-exact[:,0])))
        budgets(r)
        assert r.final.free_surface.any() and (~r.final.free_surface).any()
        assert np.max(abs(r.final.velocity_m_s))<4.
        assert np.min(r.core.area_discharge[:,0])>0
    assert errors[1]<.82*errors[0]
    assert errors[1]/independent_section(.28)[0]<.035


def test_boundary_switch_creates_moving_partial_region_with_exact_observation_clock():
    q=settings(duration=.03,length=1.,observation_times_s=(0.,.005,.015,.03))
    u=saturated_mixed_area_discharge(np.full(60,.6),0.,q)
    closed=FreeSurfaceBoundary(kind='reflective')
    open_state=tuple(saturated_mixed_area_discharge(.3,0.,q).tolist())
    opened=FreeSurfaceBoundary(kind='prescribed',prescribed_area_discharge=open_state)
    schedule=(FreeSurfaceBoundaryStage(start_time_s=0.,left=closed,right=closed),
        FreeSurfaceBoundaryStage(start_time_s=.005,left=closed,right=opened),
        FreeSurfaceBoundaryStage(start_time_s=.015,left=closed,right=closed))
    r=solve_saturated_mixed_pipe(u,q,boundary_stages=schedule)
    assert r.core.event_times_s==(.005,.015)
    assert tuple(s.time_s for s in r.observations)==q.numerics.observation_times_s
    assert not r.observations[1].free_surface.any()
    assert r.observations[2].free_surface.any() and (~r.observations[2].free_surface).any()
    assert r.final_liquid_mass_kg<r.initial_liquid_mass_kg
    assert r.final_geometric_vapor_volume_m3>0
    assert not r.final.effective_area_mass_scaled_discharge.flags.writeable
    assert not np.shares_memory(r.final.effective_area_mass_scaled_discharge,r.core.area_discharge)
    budgets(r)


def test_diverging_filled_columns_create_finite_vapor_region_with_rarefaction_reference():
    # Both initial halves are liquid-filled. Opposite liquid velocities create
    # a finite free-surface section; no fixed point cavity is inserted.
    duration=.025;q=settings(duration=duration,length=2.);n=160
    x=(np.arange(n)+.5)*2/n-1.;initial_head=.55;speed=.6
    primitive=wave_primitive(.28,.8)
    center_head=brentq(lambda h:primitive(h)-primitive(initial_head)+speed,.28,.5)
    center_area,_,center_c,_=independent_section(center_head)
    u=saturated_mixed_area_discharge(np.full(n,initial_head),np.where(x<0,-speed,speed),q)
    r=solve_saturated_mixed_pipe(u,q)
    center=abs(x)<.4*center_c*duration
    assert center.any() and r.final.free_surface[center].all()
    np.testing.assert_allclose(r.core.area_discharge[center,0],center_area,rtol=.018)
    assert np.max(abs(r.final.velocity_m_s[center]))<.045
    assert r.final_geometric_vapor_volume_m3>0
    assert (~r.final.free_surface).any()
    assert r.core.ledger.integrated_boundary_inflow[0]<0
    budgets(r)


def test_pressurized_acoustic_travel_and_spatial_refinement():
    errors=[];c=40.;length=4.;duration=.01;amplitude=2e-7
    for n in (60,120):
        q=settings(duration=duration,length=length);closure=mixed_pipe_closure(q)
        x=(np.arange(n)+.5)*length/n;a0=float(closure.at_head(.8).area_m2)
        wave=amplitude*np.sin(2*np.pi*x/length)
        u=np.column_stack((a0+wave,c*wave))
        r=solve_saturated_mixed_pipe(u,q,boundary_stages=stages())
        exact=amplitude*np.sin(2*np.pi*(x-c*duration)/length)
        errors.append(np.mean(abs(r.core.area_discharge[:,0]-a0-exact)))
        budgets(r)
    assert errors[1]<.6*errors[0]
    assert errors[1]/amplitude<.025


@pytest.mark.parametrize('failure',['review','mach','dry','density','slope','unknown'])
def test_unreviewed_or_unsupported_state_is_rejected_without_inventory_clipping(failure):
    q=settings();u=saturated_mixed_area_discharge(np.full(12,.3),0.,q)
    with pytest.raises((ValueError,RuntimeError)):
        if failure=='review':solve_saturated_mixed_pipe(u,q.model_copy(update={'prismatic_barotropic_single_liquid_and_massless_saturation_reviewed':False}))
        elif failure=='mach':saturated_mixed_area_discharge(.3,5.,q)
        elif failure=='dry':solve_saturated_mixed_pipe(np.zeros((12,2)),q)
        elif failure=='density':saturated_mixed_area_discharge(20.,0.,q)
        elif failure=='slope':solve_saturated_mixed_pipe(u,q.model_copy(update={'bed_elevations_m':tuple(np.arange(12,dtype=float))}))
        else:SaturatedMixedPipeSettings.model_validate(dict(q.model_dump(),invented_latent_heat=True))
