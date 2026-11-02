"""Separated analytic 2D heat mode on a rectangle with two polygon cavities."""
import math,runpy
from pathlib import Path
import numpy as np
from pipeline_integrity.soil_fem import evaluate_soil_fem,boundary_metadata,verify_soil_fem_record

make=runpy.run_path(str(Path(__file__).resolve().parents[1]/'examples/soil_fem.py'))['make_case']


def solve_mode(refinement,dt):
    q,c=make(mode='uniform',refinement=refinement,reviewed=True,maximum_step_s=dt)
    # All outer and cavity boundary line coordinates are integers: this exact
    # mode vanishes on them. Only prescribed ground/cavity temperature is used;
    # no nonexistent field-source input is manufactured.
    amplitude=tuple(2*math.sin(math.pi*x)*math.sin(math.pi*y) for x,y in q.mesh.vertices_m)
    initial=tuple(300+a for a in amplitude)
    boundaries=tuple(b.model_copy(update={'nodal_temperature_K':tuple(
        tuple(300. for _ in b.node_ids) for _ in q.time_s)}) for b in q.boundaries)
    q=q.model_copy(update={'initial_node_temperature_K':initial,'boundaries':boundaries})
    c=c.model_copy(update={'metadata':{'soil_fem_boundary':boundary_metadata(q)}})
    r=evaluate_soil_fem(q,c)
    assert r['status']=='computed'
    assert verify_soil_fem_record(r,q,c)['valid']
    actual=np.array(r['native']['rows'][-1]['node_temperature_K'])
    exact=300+np.array(amplitude)*math.exp(-2*math.pi**2*q.conductivity_W_m_K/q.volumetric_heat_capacity_J_m3_K*q.time_s[-1])
    return q,r,actual,exact


def test_2d_heat_equation_analytic_mode_spatial_convergence():
    errors=[]
    for refinement in (2,4):
        q,r,actual,exact=solve_mode(refinement,.1/refinement**2)
        errors.append(np.sqrt(np.mean((actual-exact)**2)))
        for row in r['native']['rows'][1:]:
            assert abs(row['step_energy_residual_J'])<1e-7
    assert errors[0]/errors[1]>3.0


def test_transient_heat_time_refinement_fixed_mesh():
    _,_,reference,_=solve_mode(3,.0005)
    errors=[]
    for dt in (.02,.01,.005):
        _,_,actual,_=solve_mode(3,dt)
        errors.append(np.linalg.norm(actual-reference))
    assert errors[0]/errors[1]>1.9 and errors[1]/errors[2]>1.9


def test_steady_linear_2d_heat_flux_and_constant_temperature_preservation():
    q,c=make(mode='linear',reviewed=True,shear=.3)
    r=evaluate_soil_fem(q,c);assert r['status']=='computed'
    row=r['native']['rows'][0]
    assert max(abs(t-(300+2*p[1])) for t,p in zip(row['node_temperature_K'],q.mesh.vertices_m))<1e-10
    assert abs(row['boundary_physical_gradient_power_into_soil_W']['ground']-24)<1e-9
    q,c=make(mode='uniform',reviewed=True)
    r=evaluate_soil_fem(q,c);assert r['status']=='computed'
    assert max(abs(t-300) for row in r['native']['rows'] for t in row['node_temperature_K'])<1e-10


def test_signed_heat_flux_integrated_storage_and_zero_flux_boundary_ledger():
    for sign in (-1,1):
        q,c=make(mode='flux',reviewed=True,shear=0)
        boundaries=tuple(b.model_copy(update={'heat_flux_into_soil_W_m2':tuple(
            tuple(sign*v for v in row) for row in b.heat_flux_into_soil_W_m2)}) if b.kind=='heat_flux' else b for b in q.boundaries)
        q=q.model_copy(update={'boundaries':boundaries})
        c=c.model_copy(update={'metadata':{'soil_fem_boundary':boundary_metadata(q)}})
        r=evaluate_soil_fem(q,c);assert r['status']=='computed'
        final=r['native']['rows'][-1]
        expected=sign*10*4*q.mesh.axial_extrusion_length_m*.2
        assert abs(final['stored_energy_change_J']-expected)<1e-8
