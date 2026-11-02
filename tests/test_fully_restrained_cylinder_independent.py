"""Independent axisymmetric displacement BVP with zero axial strain."""
import math,runpy
import numpy as np
import pytest
from scipy.integrate import solve_bvp
from pipeline_integrity.fully_restrained_cylinder import RestrainedCylinderRequest as Request,evaluate_fully_restrained_cylinder as evaluate
C=runpy.run_path('examples/cyclic_fatigue.py')['make_cases']()[1]
def request(nu=.3,temperature=40.,outer=.65):
    return Request(asset_id=C.asset_id,clock_id=C.clock_id,location_id='straight-cylinder',inner_radius_m=.5,outer_radius_m=outer,
        internal_pressure_relative_zero_exterior_pa=2e6,elastic_modulus_pa=200e9,poisson_ratio=nu,expansion_coefficient_per_k=12e-6,
        uniform_temperature_change_k=temperature,material_evidence_id='authored',isotropic_elastic_uniform_temperature_zero_axial_strain_reviewed=True)

@pytest.mark.parametrize('nu,temperature',[(0.,40.),(.3,40.),(.45,-30.),(-.2,10.)])
def test_displacement_boundary_solution_axial_compatibility_and_end_force(nu,temperature):
    q=request(nu,temperature);result=evaluate(q,C);assert result['status']=='computed'
    e=q.elastic_modulus_pa;mu=e/(2*(1+nu));lame=e*nu/((1+nu)*(1-2*nu));thermal=(3*lame+2*mu)*q.expansion_coefficient_per_k*temperature
    ri=q.inner_radius_m;ro=q.outer_radius_m;pressure=q.internal_pressure_relative_zero_exterior_pa
    def fun(r,y):return np.vstack((y[1],-y[1]/r+y[0]/r**2))
    def bc(a,b):return np.array([((lame+2*mu)*a[1]+lame*a[0]/ri-thermal+pressure)/e,
        ((lame+2*mu)*b[1]+lame*b[0]/ro-thermal)/e])
    grid=np.linspace(ri,ro,60);sol=solve_bvp(fun,bc,grid,np.zeros((2,len(grid))),tol=1e-10,max_nodes=10000)
    assert sol.success
    r=np.linspace(ri,ro,21);u,du=sol.sol(r)
    axial=lame*(du+u/r)-thermal
    assert axial==pytest.approx(np.full(len(r),result['axial_stress_tension_positive_pa']),rel=3e-9,abs=.02)
    material_force=float(np.mean(axial))*math.pi*(ro*ro-ri*ri)
    assert material_force==pytest.approx(result['pressure_end_thrust_n']+result['required_positive_end_restraint_force_n'],rel=3e-9,abs=.02)
    assert result['axial_stress_compression_positive_pa']==-result['axial_stress_tension_positive_pa']

@pytest.mark.parametrize('nu',[.2,.4])
def test_exact_annulus_pressure_component_converges_to_od_membrane_source_coordinate(nu):
    errors=[]
    for ratio in (.1,.01,.001):
        q=request(nu,temperature=0.,outer=.5*(1+ratio));r=evaluate(q,C)
        source_thin=nu*q.internal_pressure_relative_zero_exterior_pa*q.outer_radius_m/(q.outer_radius_m-q.inner_radius_m)
        errors.append(abs(r['pressure_poisson_tension_component_pa']/source_thin-1))
    assert errors[2]<errors[1]<errors[0] and errors[-1]<.0016

def test_unknown_temperature_withheld():
    assert evaluate(request().model_copy(update={'uniform_temperature_change_k':None}),C)['status']=='needs_data'

def test_no_material_review_no_result():
    assert evaluate(request().model_copy(update={'isotropic_elastic_uniform_temperature_zero_axial_strain_reviewed':False}),C)['status']=='needs_data'
