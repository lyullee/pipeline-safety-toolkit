"""Analytical PDE, mixed storage, heterogeneous resistance and volume checks."""
from dataclasses import dataclass
import itertools,math
import numpy as np
import pytest
from pipeline_integrity.richards_fv import (
    RichardsMesh,RichardsSettings,InteriorDarcyFace,DarcyBoundaryFace,
    HydraulicState,LinearHydraulicLaw,solve_richards_fv)


def grid(n,dimension=1,elevation=False,boundary=()):
    shape=(n,)*dimension;coords=list(itertools.product(range(n),repeat=dimension));lookup={c:i for i,c in enumerate(coords)}
    faces=[];dx=1/n
    for c,i in lookup.items():
        for d in range(dimension):
            if c[d]+1<n:
                nextc=list(c);nextc[d]+=1
                faces.append(InteriorDarcyFace(cell_i=i,cell_j=lookup[tuple(nextc)],area_m2=dx**(dimension-1),center_distance_m=dx))
    z=tuple((c[-1]+.5)*dx if elevation else 0 for c in coords)
    return RichardsMesh(cell_volumes_m3=(dx**dimension,)*len(coords),cell_elevations_m=z,
        interior_faces=tuple(faces),boundary_faces=boundary,orthogonal_two_point_metrics_reviewed=True),np.array(coords,dtype=float)*dx+dx/2


def settings(duration=.1,dt=.01,**kw):
    return RichardsSettings(duration_s=duration,initial_step_s=dt,maximum_step_s=dt,
        nonlinear_volume_tolerance_m3=1e-13,nonlinear_relative_tolerance=1e-12,**kw)


@pytest.mark.parametrize('dimension',[1,2,3])
def test_uniform_preservation_generic_dimension(dimension):
    m,_=grid(3,dimension);law=LinearHydraulicLaw(.4,.2,.01)
    r=solve_richards_fv(m,settings(),(.1,)*len(m.cell_volumes_m3),law)
    assert r.final_head_m==pytest.approx((.1,)*len(m.cell_volumes_m3),abs=1e-14)
    assert abs(r.global_volume_residual_m3)<1e-14


@pytest.mark.parametrize('dimension',[1,2])
def test_diffusion_exact_neumann_mode_spatial_refinement(dimension):
    errors=[]
    for n in (5,10):
        m,x=grid(n,dimension);shape=np.prod(np.cos(math.pi*x),axis=1)
        h=.1*shape;duration=.3;dt=.02/n**2
        r=solve_richards_fv(m,settings(duration,dt),tuple(h),LinearHydraulicLaw(.4,.2,.01))
        exact=h*np.exp(-.05*dimension*math.pi**2*duration)
        errors.append(np.sqrt(np.mean((np.array(r.final_head_m)-exact)**2)))
        assert abs(r.global_volume_residual_m3)<3e-12
    assert errors[0]/errors[1]>3.8


def test_backward_euler_time_refinement_independent_discrete_mode():
    n=12;m,x=grid(n);h=.1*np.cos(math.pi*x[:,0]);duration=.4
    eigenvalue=.05*4*n*n*math.sin(math.pi/(2*n))**2
    errors=[]
    for dt in (.04,.02,.01):
        r=solve_richards_fv(m,settings(duration,dt),tuple(h),LinearHydraulicLaw(.4,.2,.01))
        errors.append(np.linalg.norm(np.array(r.final_head_m)-h*np.exp(-eigenvalue*duration)))
    assert errors[0]/errors[1]>1.98 and errors[1]/errors[2]>1.98


def test_nonuniform_3d_diffusion_against_analytic_discrete_eigenmode():
    n=4;m,x=grid(n,3);h=.1*np.prod(np.cos(math.pi*x),axis=1)
    r=solve_richards_fv(m,settings(.2,.02),tuple(h),LinearHydraulicLaw(.4,.2,.01))
    eigenvalue=.05*3*4*n*n*math.sin(math.pi/(2*n))**2
    amplitude=math.prod(1/(1+eigenvalue*(b-a)) for a,b in zip(r.accepted_step_times_s,r.accepted_step_times_s[1:]))
    assert np.array(r.final_head_m)==pytest.approx(h*amplitude,abs=2e-13)
    assert np.linalg.norm(np.array(r.final_head_m)-h)>.01
    assert abs(r.global_volume_residual_m3)<1e-13


@pytest.mark.parametrize('dimension',[1,2,3])
def test_hydrostatic_gravity_no_flow(dimension):
    m,_=grid(3,dimension,elevation=True);h=.6-np.array(m.cell_elevations_m)
    r=solve_richards_fv(m,settings(),tuple(h),LinearHydraulicLaw(.5,.1,.02))
    assert r.final_head_m==pytest.approx(tuple(h),abs=2e-14)
    assert abs(r.global_volume_residual_m3)<1e-14


@dataclass(frozen=True)
class NonlinearLaw:
    def evaluate(self,h,i):
        return HydraulicState(.4+.2*math.tanh(h),.2/math.cosh(h)**2,.01*math.exp(.3*h))


def test_actual_nonlinear_content_one_cell_flux_not_capacity_shortcut():
    m=RichardsMesh(cell_volumes_m3=(1.,),cell_elevations_m=(0.,),orthogonal_two_point_metrics_reviewed=True,
        boundary_faces=(DarcyBoundaryFace(cell=0,area_m2=2,center_to_face_m=.5,kind='inward_flux',value=.015),))
    h0=.8;theta0=NonlinearLaw().evaluate(h0,0).liquid_content
    r=solve_richards_fv(m,settings(1,.4,observation_times_s=(0,.33,1)),(h0,),NonlinearLaw())
    exact=math.atanh((theta0+.03-.4)/.2)
    assert r.final_head_m[0]==pytest.approx(exact,abs=3e-10)
    assert r.accumulated_boundary_inflow_m3==(pytest.approx(.03),)
    assert abs(r.global_volume_residual_m3)<1e-11
    assert abs(NonlinearLaw().evaluate(h0,0).capacity_per_m*(exact-h0)-.03)>1e-3
    assert tuple(s.time_s for s in r.snapshots)==(0,.33,1)


def test_closed_nonlinear_multicell_mass_conservation_and_time_variation():
    m,x=grid(9,2);h=.7*np.cos(math.pi*x[:,0])*np.cos(math.pi*x[:,1])
    r=solve_richards_fv(m,settings(.5,.05),tuple(h),NonlinearLaw())
    assert abs(r.global_volume_residual_m3)<2e-11
    assert np.linalg.norm(np.array(r.final_head_m)-h)>.01
    assert max(r.final_head_m)<max(h) and min(r.final_head_m)>min(h)


def test_nonlinear_pde_time_refinement():
    m,x=grid(10);h=.7*np.cos(math.pi*x[:,0])
    ref=np.array(solve_richards_fv(m,settings(.4,.001),tuple(h),NonlinearLaw()).final_head_m)
    errors=[]
    for dt in (.04,.02):
        r=solve_richards_fv(m,settings(.4,dt),tuple(h),NonlinearLaw())
        errors.append(np.linalg.norm(np.array(r.final_head_m)-ref))
    assert errors[0]/errors[1]>1.8


def test_nonlinear_conductivity_spatial_convergence_kirchhoff_exact_state():
    errors=[]
    for n in (8,16):
        dx=1/n
        bc=(DarcyBoundaryFace(cell=0,area_m2=1,center_to_face_m=dx/2,kind='pressure_head',value=.1),
            DarcyBoundaryFace(cell=n-1,area_m2=1,center_to_face_m=dx/2,kind='pressure_head',value=1.))
        m,x=grid(n,boundary=bc)
        exact=np.log(math.exp(.03)+(math.exp(.3)-math.exp(.03))*x[:,0])/.3
        r=solve_richards_fv(m,settings(200,20),tuple(exact),NonlinearLaw())
        errors.append(np.linalg.norm(np.array(r.final_head_m)-exact)/math.sqrt(n))
        assert abs(r.global_volume_residual_m3)<2e-9
    assert errors[0]/errors[1]>3.7


def test_successful_nonlinear_retry_keeps_actual_volume_budget():
    m,_=grid(1);law=NonlinearLaw();h0=.8
    # Many retries/accepted short steps can accumulate individual nonlinear
    # tolerances. Request a tighter per-step volume tolerance for this ledger.
    q=settings(.5,.5,max_nonlinear_iterations=3).model_copy(update={
        'nonlinear_volume_tolerance_m3':1e-14,'nonlinear_relative_tolerance':1e-14})
    r=solve_richards_fv(m,q,(h0,),law,(.03,))
    assert r.rejected_steps>0
    expected=math.atanh((law.evaluate(h0,0).liquid_content+.015-.4)/.2)
    assert r.final_head_m[0]==pytest.approx(expected,abs=2e-9)
    assert abs(r.global_volume_residual_m3)<2e-11


@dataclass(frozen=True)
class LayeredLaw:
    conductivities: tuple[float,...]
    def evaluate(self,h,i):return HydraulicState(.5,0.,self.conductivities[i])


def test_heterogeneous_series_darcy_resistance_steady_limit():
    n=8;dx=1/n;ks=(.01,)*4+(.001,)*4
    resistance=[dx/k for k in ks];total=sum(resistance);flux=1/total
    h=tuple(1-flux*(sum(resistance[:i])+.5*resistance[i]) for i in range(n))
    bc=(DarcyBoundaryFace(cell=0,area_m2=1,center_to_face_m=dx/2,kind='pressure_head',value=1),
        DarcyBoundaryFace(cell=n-1,area_m2=1,center_to_face_m=dx/2,kind='pressure_head',value=0))
    m,_=grid(n,boundary=bc)
    r=solve_richards_fv(m,settings(2,.5),(0.,)*n,LayeredLaw(ks))
    assert r.final_head_m==pytest.approx(h,abs=3e-13)
    assert r.accumulated_boundary_inflow_m3==(pytest.approx(2*flux),pytest.approx(-2*flux))
    assert abs(r.global_volume_residual_m3)<1e-13


def test_signed_cell_source_and_prescribed_boundary_ledger():
    m,_=grid(1,boundary=(DarcyBoundaryFace(cell=0,area_m2=2,center_to_face_m=.5,kind='inward_flux',value=-.01),))
    r=solve_richards_fv(m,settings(1,.1),(0.,),LinearHydraulicLaw(.5,.2,.1),(.03,))
    assert r.final_head_m==(pytest.approx(.05),)
    assert r.accumulated_cell_source_m3==(pytest.approx(.03),)
    assert r.accumulated_boundary_inflow_m3==(pytest.approx(-.02),)
    assert abs(r.global_volume_residual_m3)<1e-12


def test_all_neumann_zero_storage_and_disconnected_gauge_rejected():
    m,_=grid(3)
    with pytest.raises(ValueError,match='pressure component'):solve_richards_fv(m,settings(),(0,0,0),LayeredLaw((.1,.1,.1)))
    @dataclass
    class SplitLaw:
        def evaluate(self,h,i):return HydraulicState(.4,.2 if i==0 else 0.,0.)
    with pytest.raises(ValueError,match='pressure component'):solve_richards_fv(m,settings(),(0,0,0),SplitLaw())


@pytest.mark.parametrize('field,value',[('capacity_per_m',-1),('conductivity_m_s',-1),('reference_content',2)])
def test_invalid_or_unknown_closure(field,value):
    m,_=grid(1);args=dict(reference_content=.5,capacity_per_m=.2,conductivity_m_s=.01);args[field]=value
    with pytest.raises(ValueError):solve_richards_fv(m,settings(),(0.,),LinearHydraulicLaw(**args))


def test_invalid_mesh_and_clock():
    with pytest.raises(ValueError):RichardsMesh(cell_volumes_m3=(1,),cell_elevations_m=())
    with pytest.raises(ValueError):settings(observation_times_s=(.05,.04))
    with pytest.raises(ValueError):settings(observation_times_s=(.2,))
    m,_=grid(1)
    with pytest.raises(ValueError,match='review'):solve_richards_fv(m.model_copy(update={'orthogonal_two_point_metrics_reviewed':False}),settings(),(0.,),LinearHydraulicLaw(.5,.2,.01))


def test_retry_domain_failure_no_content_clipping():
    m,_=grid(1)
    with pytest.raises(RuntimeError,match='minimum step'):
        solve_richards_fv(m,settings(1,.5,minimum_step_s=.001),(0.,),LinearHydraulicLaw(.99,.2,.01),(1.,))
