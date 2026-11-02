"""Constitutive displacement derivatives, force conservation and footprint limits."""
import math
import numpy as np
import pytest
from scipy.integrate import quad
from pipeline_integrity.surface_point_load_transfer import SurfacePointLoadTransferRequest as Request,evaluate_surface_point_load_transfer as evaluate

def req(force=10000.,depth=2.,offset=1.):
    return Request(downward_surface_force_n=force,depth_below_plane_m=depth,horizontal_offset_m=offset,
        elastic_halfspace_load_geometry_basis_id='authored-elastic-halfspace',homogeneous_isotropic_halfspace_and_point_load_reviewed=True)

@pytest.mark.parametrize('nu',[-.2,.0,.35])
@pytest.mark.parametrize('offset',[0.,1.7])
def test_independent_boussinesq_displacement_constitutive_stress(nu,offset):
    q=req(offset=offset);G=30e6;lam=2*G*nu/(1-2*nu);point=np.array([offset,0.,q.depth_below_plane_m]);step=1e-4
    def displacement(p):
        x,y,z=p;radius=math.hypot(x,y);R=np.linalg.norm(p);factor=q.downward_surface_force_n/(4*math.pi*G)
        radial=factor*(z/R**3-(1-2*nu)/(R*(R+z)))
        return np.array([x*radial,y*radial,factor*(z*z/R**3+2*(1-nu)/R)])
    gradient=np.column_stack([(displacement(point+step*np.eye(3)[i])-displacement(point-step*np.eye(3)[i]))/(2*step) for i in range(3)])
    sigma_zz=lam*np.trace(gradient)+2*G*gradient[2,2]
    assert evaluate(q)['vertical_compressive_continuum_stress_pa']==pytest.approx(-sigma_zz,rel=1e-7)

def test_horizontal_plane_integrated_stress_transmits_full_vertical_force():
    q=req();z=q.depth_below_plane_m
    result=quad(lambda u:evaluate(q.model_copy(update={'horizontal_offset_m':z*u}))['vertical_compressive_continuum_stress_pa']*2*math.pi*z*z*u,0,np.inf)[0]
    assert result==pytest.approx(q.downward_surface_force_n,rel=1e-9)

def test_integrated_uniform_circular_footprint_matches_independent_axis_solution():
    depth=2.;radius=3.;pressure=20000.
    stress=quad(lambda r:evaluate(req(force=pressure,depth=depth,offset=r))['vertical_compressive_continuum_stress_pa']*2*math.pi*r,0,radius)[0]
    assert stress==pytest.approx(pressure*(1-(depth/math.hypot(depth,radius))**3),rel=1e-10)

def test_force_length_similarity_and_zero_load():
    q=req();r=evaluate(q)
    s=evaluate(req(force=40000.,depth=4.,offset=2.))
    assert s['vertical_compressive_continuum_stress_pa']==pytest.approx(r['vertical_compressive_continuum_stress_pa'])
    assert evaluate(req(force=0.))['vertical_compressive_continuum_stress_pa']==0
    assert r['pipe_contact_or_ovalization_qualified'] is False

def test_unknown_halfspace_applicability_is_not_assumed():
    assert evaluate(req().model_copy(update={'homogeneous_isotropic_halfspace_and_point_load_reviewed':False}))['status']=='needs_data'
