"""Thin observation versus an independent finite-wall radial elasticity BVP."""
import math,runpy
import numpy as np
import pytest
from scipy.integrate import solve_bvp,quad
from pipeline_integrity.pipe_thermoelastic_area import evaluate_thermoelastic_area,evaluate_closed_end_thermoelastic_geometry,ClosedEndThermoelasticRequest

OLD=runpy.run_path('tests/test_pipe_thermoelastic_area_independent.py')


def solid_wall(condition,nu,delta,pressure,eigenstrain):
    # x=r/a, U=u/a, S=sigma/E. Three-dimensional isotropic strain energy
    # supplies stresses; radial traction equilibrium closes the actual wall.
    young=2e11;mu=1/(2*(1+nu));lam=nu/((1+nu)*(1-2*nu));outer=1+delta
    def fields(x,y,epsz):
        ur=(y[1]-lam*y[0]/x-lam*epsz+(2*mu+3*lam)*eigenstrain)/(2*mu+lam)
        trace=ur+y[0]/x+epsz
        st=2*mu*(y[0]/x-eigenstrain)+lam*(trace-3*eigenstrain)
        sz=2*mu*(epsz-eigenstrain)+lam*(trace-3*eigenstrain)
        return ur,st,sz
    def ode(x,y,p):
        ur,st,sz=fields(x,y,p[0]);return np.array([ur,(st-y[1])/x])
    def boundary(left,right,p):
        axial=p[0] if condition=='fixed' else fields(1.,left,p[0])[2]-(pressure/young/(outer**2-1) if condition=='closed' else 0.)
        return np.array([left[1]+pressure/young,right[1],axial])
    mesh=np.linspace(1,outer,9)
    result=solve_bvp(ode,boundary,mesh,np.zeros((2,len(mesh))),p=np.zeros(1),tol=1e-11,bc_tol=1e-13,max_nodes=4000)
    assert result.success
    assert max(abs(boundary(result.y[:,0],result.y[:,-1],result.p)))<1e-11
    return result,fields,young


def observation(condition,nu,delta,pressure,temperature):
    q=OLD['request']('zero_axial_strain' if condition=='fixed' else 'zero_axial_stress',pressure,temperature,nu=nu)
    q=q.model_copy(update={'pressure':q.pressure.model_copy(update={'wall_thickness_m':.25*delta})})
    if condition=='closed':
        r=evaluate_closed_end_thermoelastic_geometry(ClosedEndThermoelasticRequest(unconstrained_base=q,
            maximum_absolute_mechanical_axial_strain=.005,maximum_absolute_total_axial_strain=.005,
            closed_end_pressure_force_and_no_external_axial_restraint_reviewed=True))
        assert r.status=='computed';return r,r.linear_area_fraction_increment,r.total_axial_strain
    r=evaluate_thermoelastic_area(q);assert r.status=='computed'
    return r,r.linear_total_area_fraction,None


@pytest.mark.parametrize('condition',['open','fixed','closed'])
@pytest.mark.parametrize('nu',[.3,-.15])
@pytest.mark.parametrize('temperature',[280.,320.])
def test_finite_wall_reference_retains_coarse_cases_and_converges_to_observed_area(condition,nu,temperature):
    errors=[];strain=1.2e-4;eigenstrain=1e-5*(temperature-300)
    for delta in [.16,.08,.04,.02,.01]:
        pressure=strain*2e11*delta
        reference,fields,young=solid_wall(condition,nu,delta,pressure,eigenstrain)
        r,actual,axial=observation(condition,nu,delta,pressure,temperature)
        solid_area=2*reference.sol(1.)[0]
        errors.append(abs(solid_area-actual))
        # Preserve coarse finite-thickness differences; this is explicitly
        # an asymptotic membrane comparison rather than exact coarse identity.
        assert errors[-1]<2*delta*strain
        if condition=='closed':
            assert abs(reference.p[0]-axial)<2*delta*strain
            volume=solid_area+reference.p[0]
            assert abs(volume-r.linear_volume_fraction_increment)<4*delta*strain
    assert all(b<a for a,b in zip(errors,errors[1:]))
    assert errors[-1]<.08*errors[0]


@pytest.mark.parametrize('condition',['open','fixed','closed'])
def test_actual_finite_wall_pressure_work_closes_three_dimensional_strain_energy(condition):
    nu=.3;delta=.12;pressure=2e6;a=.25
    result,fields,young=solid_wall(condition,nu,delta,pressure,0.)
    def energy(x):
        y=result.sol(x);ur,st,sz=fields(x,y,result.p[0])
        return .5*young*(y[1]*ur+st*y[0]/x+sz*result.p[0])*2*math.pi*a*a*x
    elastic=quad(energy,1,1+delta,epsabs=1e-10)[0]
    radial_work=.5*pressure*(2*math.pi*a*a*result.sol(1)[0])
    axial_force=pressure*math.pi*a*a if condition=='closed' else 0.
    external=radial_work+.5*axial_force*result.p[0]
    assert elastic==pytest.approx(external,rel=2e-7,abs=1e-9)


@pytest.mark.parametrize('condition',['open','fixed','closed'])
def test_thermal_boundary_condition_and_area_volume_are_not_a_shared_coefficient(condition):
    nu=.3;temperature=330.;eigenstrain=3e-4
    reference,fields,young=solid_wall(condition,nu,.1,0.,eigenstrain)
    r,actual,axial=observation(condition,nu,.1,0.,temperature)
    assert actual==pytest.approx(2*reference.sol(1)[0],rel=2e-10,abs=1e-13)
    if condition=='fixed':
        assert actual==pytest.approx(2*(1+nu)*eigenstrain)
        assert reference.p[0]==0.
    else:
        assert actual==pytest.approx(2*eigenstrain)
        assert reference.p[0]==pytest.approx(eigenstrain,abs=1e-12)
    if condition=='closed':
        assert r.linear_volume_fraction_increment==pytest.approx(3*eigenstrain)
        assert r.linear_volume_fraction_increment!=pytest.approx(r.linear_area_fraction_increment)
