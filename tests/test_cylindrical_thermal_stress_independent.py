"""Lamé limits, analytic thermal particular fields, equilibrium and axial force."""
import math
import numpy as np
import pytest
from scipy.integrate import quad,simpson
from pipeline_integrity.cylindrical_thermal_stress import CylindricalThermalStressRequest as Request,evaluate_cylindrical_thermal_stress as evaluate

def request(nu=.3,bc='prescribed_axial_force',nodes=2):
    r=np.linspace(.5,.8,nodes)
    return Request(radial_temperature_positions_m=tuple(r),temperature_k=tuple(np.full(nodes,313.)),stress_free_reference_temperature_k=293.,
        young_modulus_pa=200e9,poisson_ratio=nu,thermal_expansion_coefficient_per_k=12e-6,material_property_supported_temperature_k=(200.,500.),
        inner_pressure_traction_pa=1e6,outer_pressure_traction_pa=2e5,axial_boundary=bc,supplied_axial_strain=0.,supplied_axial_force_tension_positive_n=0.,
        query_radii_m=tuple(np.linspace(.5,.8,101)),maximum_sampled_absolute_small_strain=.01,relative_boundary_residual_tolerance=1e-9,
        geometry_temperature_traction_axial_and_material_basis_id='authored-isotropic-annulus',axisymmetric_constant_property_small_strain_thermoelasticity_reviewed=True)

def lame(q):
    ri=q.radial_temperature_positions_m[0];ro=q.radial_temperature_positions_m[-1];pi=q.inner_pressure_traction_pa;po=q.outer_pressure_traction_pa
    return (pi*ri**2-po*ro**2)/(ro**2-ri**2),(pi-po)*ri**2*ro**2/(ro**2-ri**2)

@pytest.mark.parametrize('nu',[-.2,.3,.45])
@pytest.mark.parametrize('bc',['prescribed_axial_force','closed_end_pressure_thrust','prescribed_axial_strain'])
def test_uniform_temperature_lame_pressure_and_axial_boundary_limit(nu,bc):
    q=request(nu,bc);r=evaluate(q);assert r['status']=='computed';A,B=lame(q);E=q.young_modulus_pa;thermal=q.thermal_expansion_coefficient_per_k*20
    sz=0. if bc=='prescribed_axial_force' else A if bc=='closed_end_pressure_thrust' else -E*thermal+2*nu*A
    for row in r['rows']:
        radius=row['radius_m'];u=radius*((1-nu)*A+(1+nu)*B/radius**2-nu*sz)/E+thermal*radius
        assert row['radial_stress_tension_positive_pa']==pytest.approx(A-B/radius**2,abs=.1)
        assert row['hoop_stress_tension_positive_pa']==pytest.approx(A+B/radius**2,abs=.1)
        assert row['axial_stress_tension_positive_pa']==pytest.approx(sz,abs=.1)
        assert row['radial_displacement_m']==pytest.approx(u,abs=1e-12)

@pytest.mark.parametrize('nu',[-.2,.3,.45])
def test_uniform_free_expansion_has_no_stress(nu):
    q=request(nu).model_copy(update={'inner_pressure_traction_pa':0.,'outer_pressure_traction_pa':0.});r=evaluate(q)
    assert r['status']=='computed'
    for row in r['rows']:
        assert row['radial_displacement_m']==pytest.approx(12e-6*20*row['radius_m'],rel=1e-10)
        assert max(abs(row[k]) for k in ('radial_stress_tension_positive_pa','hoop_stress_tension_positive_pa','axial_stress_tension_positive_pa'))<.02

def analytic_particular_solution(q,T,up,dup):
    """Close independent explicit particular u(r)+a*r+b/r using 3x3 traction/force algebra."""
    E=q.young_modulus_pa;nu=q.poisson_ratio;G=E/(2*(1+nu));lam=E*nu/((1+nu)*(1-2*nu));B=E/(1-2*nu)
    ri=q.radial_temperature_positions_m[0];ro=q.radial_temperature_positions_m[-1]
    def field(r,c):
        a,b,ez=c;u=a*r+b/r+up(r);du=a-b/r**2+dup(r)
        common=lam*(du+u/r+ez)-B*q.thermal_expansion_coefficient_per_k*(T(r)-q.stress_free_reference_temperature_k)
        return u,2*G*du+common,2*G*u/r+common,2*G*ez+common
    def observations(c):
        third=c[2] if q.axial_boundary=='prescribed_axial_strain' else quad(lambda r:2*math.pi*r*field(r,c)[3],ri,ro,epsabs=.0001)[0]/(E*ro**2)
        return np.array([field(ri,c)[1]/E,field(ro,c)[1]/E,third])
    zero=observations((0.,0.,0.));matrix=np.column_stack([observations(unit)-zero for unit in np.eye(3)])
    target=q.supplied_axial_strain if q.axial_boundary=='prescribed_axial_strain' else (
        math.pi*(ri**2*q.inner_pressure_traction_pa-ro**2*q.outer_pressure_traction_pa) if q.axial_boundary=='closed_end_pressure_thrust' else q.supplied_axial_force_tension_positive_n)/(E*ro**2)
    coeff=np.linalg.solve(matrix,np.array([-q.inner_pressure_traction_pa/E,-q.outer_pressure_traction_pa/E,target])-zero)
    return lambda r:field(r,coeff)

@pytest.mark.parametrize('bc',['prescribed_axial_force','closed_end_pressure_thrust','prescribed_axial_strain'])
def test_quadratic_thermal_particular_solution(bc):
    q=request(.3,bc,nodes=129);radii=np.array(q.radial_temperature_positions_m);slope=30.;T=lambda r:293+slope*r*r
    q=q.model_copy(update={'temperature_k':tuple(T(radii))});r=evaluate(q);assert r['status']=='computed'
    coefficient=(1+q.poisson_ratio)/(1-q.poisson_ratio)*q.thermal_expansion_coefficient_per_k*slope/4
    exact=analytic_particular_solution(q,T,lambda r:coefficient*r**3,lambda r:3*coefficient*r*r)
    for row in r['rows']:
        values=exact(row['radius_m'])
        assert row['radial_displacement_m']==pytest.approx(values[0],abs=2e-11)
        assert tuple(row[k] for k in ('radial_stress_tension_positive_pa','hoop_stress_tension_positive_pa','axial_stress_tension_positive_pa'))==pytest.approx(values[1:],abs=20.)

@pytest.mark.parametrize('nu',[0.,.3,.45])
def test_log_temperature_input_partition_refines_to_analytic_field(nu):
    errors=[]
    for nodes in (17,33,65):
        q=request(nu,nodes=nodes);radii=np.array(q.radial_temperature_positions_m);ri=radii[0];gradient=40.
        T=lambda r:293+gradient*np.log(r/ri);coefficient=(1+nu)/(1-nu)*q.thermal_expansion_coefficient_per_k*gradient/2
        q=q.model_copy(update={'temperature_k':tuple(T(radii))});r=evaluate(q);assert r['status']=='computed'
        exact=analytic_particular_solution(q,T,lambda r:coefficient*r*np.log(r/ri),lambda r:coefficient*(np.log(r/ri)+1))
        errors.append(max(abs(row['hoop_stress_tension_positive_pa']-exact(row['radius_m'])[2]) for row in r['rows']))
    assert errors[2]<errors[1]<errors[0] and errors[-1]<100.

@pytest.mark.parametrize('nu',[-.2,.3,.45])
def test_radial_equilibrium_traction_and_axial_integral(nu):
    q=request(nu,nodes=33);positions=np.array(q.radial_temperature_positions_m)
    q=q.model_copy(update={'temperature_k':tuple(293+50*np.log(positions/.5)),'query_radii_m':tuple(np.linspace(.5,.8,2001)),
        'supplied_axial_force_tension_positive_n':12345.})
    result=evaluate(q);assert result['status']=='computed';rows=result['rows'];r=np.array([v['radius_m'] for v in rows])
    sr=np.array([v['radial_stress_tension_positive_pa'] for v in rows]);sh=np.array([v['hoop_stress_tension_positive_pa'] for v in rows]);sz=np.array([v['axial_stress_tension_positive_pa'] for v in rows])
    residual=np.gradient(sr,r,edge_order=2)+(sr-sh)/r
    assert max(abs(residual[2:-2]))<100.
    assert sr[0]==pytest.approx(-q.inner_pressure_traction_pa,abs=.1) and sr[-1]==pytest.approx(-q.outer_pressure_traction_pa,abs=.1)
    assert simpson(2*math.pi*r*sz,x=r)==pytest.approx(q.supplied_axial_force_tension_positive_n,abs=.1)
    assert result['axial_force_tension_positive_n']==pytest.approx(q.supplied_axial_force_tension_positive_n,abs=.1)

def test_unknown_review_is_withheld():
    assert evaluate(request().model_copy(update={'axisymmetric_constant_property_small_strain_thermoelasticity_reviewed':False}))['status']=='needs_data'

def test_unsupported_material_temperature_is_withheld():
    assert evaluate(request().model_copy(update={'material_property_supported_temperature_k':(200.,300.)}))['status']=='out_of_scope'

def test_sampled_small_strain_support_is_enforced():
    assert evaluate(request().model_copy(update={'maximum_sampled_absolute_small_strain':1e-6}))['status']=='out_of_scope'

@pytest.mark.parametrize('nu',[0.,.3,.45])
def test_independent_stress_primary_bvp_matches_temperature_moment_solution(nu):
    from scipy.integrate import solve_bvp
    from scipy.interpolate import PchipInterpolator
    q=request(nu,nodes=9);radii=np.array(q.radial_temperature_positions_m);R=radii[-1];x0=radii[0]/R
    q=q.model_copy(update={'temperature_k':tuple(293+40*np.log(radii/.5)),'supplied_axial_force_tension_positive_n':12345.})
    result=evaluate(q);assert result['status']=='computed'
    temperature=PchipInterpolator(radii/R,q.temperature_k);E=q.young_modulus_pa;lam=nu/((1+nu)*(1-2*nu));twoG=1/(1+nu);A=lam+twoG
    def rhs(x,y,p):
        ez=p[0];thermal=q.thermal_expansion_coefficient_per_k*(temperature(x)-293)/(1-2*nu)
        radial=(y[1]-lam*y[0]/x-lam*ez+thermal)/A
        hoop=twoG*y[0]/x+lam*(radial+y[0]/x+ez)-thermal
        axial=twoG*ez+lam*(radial+y[0]/x+ez)-thermal
        return np.vstack((radial,(hoop-y[1])/x,2*math.pi*x*axial))
    def bc(a,b,p):return np.array([a[1]+q.inner_pressure_traction_pa/E,b[1]+q.outer_pressure_traction_pa/E,a[2],b[2]-12345/(E*R*R)])
    grid=np.linspace(x0,1,81);sol=solve_bvp(rhs,bc,grid,np.zeros((3,len(grid))),p=np.array([0.]),tol=1e-11,max_nodes=10000)
    assert sol.success
    for row in result['rows']:
        values=sol.sol(row['radius_m']/R)
        assert row['radial_displacement_m']==pytest.approx(R*values[0],abs=1e-11)
        assert row['radial_stress_tension_positive_pa']==pytest.approx(E*values[1],abs=2.)

@pytest.mark.parametrize('radius',[.5,.65,.8])
def test_constant_temperature_pressure_field_matches_selected_existing_lame_owner(radius):
    from pipeline_integrity.adapters import MaterialClient
    from pipeline_integrity.contracts import RunContext,EvidenceRef
    context=RunContext(asset_id='cylinder',scenario_id='uniform-temperature-lame-limit',clock_id='static',origin='synthetic',
        evidence=(EvidenceRef(id='authored',citation='Authored elastic annulus reference',origin='synthetic',license_id='MIT'),))
    q=request().model_copy(update={'query_radii_m':(radius,radius),'temperature_k':(293.,293.)});result=evaluate(q)
    native=MaterialClient().evaluate('mechanics.lame_cylinder',dict(inner_radius_m=.5,outer_radius_m=.8,
        internal_pressure_Pa=1e6,external_pressure_Pa=2e5,evaluation_radius_m=radius,closed_ends=True),context,location_id='annulus')
    assert native.status=='computed';values=native.payload['values'];row=result['rows'][0]
    assert row['radial_stress_tension_positive_pa']==pytest.approx(values['radial_stress_Pa'],abs=.1)
    assert row['hoop_stress_tension_positive_pa']==pytest.approx(values['hoop_stress_Pa'],abs=.1)
