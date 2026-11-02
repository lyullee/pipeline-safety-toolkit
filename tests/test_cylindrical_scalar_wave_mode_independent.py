"""Independent annular finite-volume modes and scalar wave-energy conservation."""
import math
import numpy as np
import pytest
from scipy.linalg import eigh_tridiagonal
from scipy.integrate import quad
from pipeline_integrity.cylindrical_scalar_wave_mode import CylindricalScalarWaveModeRequest as Request,evaluate_cylindrical_scalar_wave_mode as evaluate

def req(mode=1,R=2.,c=300.):
    return Request(outer_zero_displacement_radius_m=R,wave_speed_m_s=c,mode_number=mode,initial_axis_velocity_m_s=2.,
        query_radii_m=(0.,R),query_times_s=(0.,.01),scalar_wave_geometry_and_boundary_basis_id='authored-cylindrical-wave',regular_axis_scalar_wave_and_outer_zero_boundary_reviewed=True)

@pytest.mark.parametrize('mode',[1,2,3])
@pytest.mark.parametrize('geometry',[(1.,1.),(2.,300.),(.2,1500.)])
def test_independent_annular_finite_volume_eigenvalue_convergence(mode,geometry):
    R,c=geometry;expected=evaluate(req(mode,R,c))['angular_frequency_rad_s'];errors=[]
    for n in (40,80,160):
        h=R/n;r=(np.arange(n)+.5)*h;mass=r*h
        faces=np.arange(1,n)*h
        edge=faces/h
        diagonal=np.zeros(n);diagonal[:-1]+=edge;diagonal[1:]+=edge;diagonal[-1]+=2*R/h
        eigen=eigh_tridiagonal(diagonal/mass,-edge/np.sqrt(mass[:-1]*mass[1:]),select='i',select_range=(mode-1,mode-1),eigvals_only=True)[0]
        errors.append(abs(c*math.sqrt(eigen)/expected-1))
    assert errors[-1]<errors[1]<errors[0] and errors[-1]<.00025

@pytest.mark.parametrize('mode',[1,2,3])
def test_independent_scalar_wave_energy_integral_and_boundaries(mode):
    q=req(mode);omega=evaluate(q)['angular_frequency_rad_s'];energies=[]
    for phase in (0.,.7,1.4,2.2):
        t=phase/omega
        def density(r):
            s=evaluate(q.model_copy(update={'query_radii_m':(r,),'query_times_s':(t,)}))['rows'][0]
            return .5*r*(s['scalar_velocity_m_s']**2+q.wave_speed_m_s**2*s['scalar_radial_displacement_gradient']**2)
        energies.append(quad(density,0,q.outer_zero_displacement_radius_m,epsabs=1e-10)[0])
    assert all(e==pytest.approx(energies[0],rel=2e-10) for e in energies)
    for row in evaluate(q)['rows']:
        if row['radius_m']==q.outer_zero_displacement_radius_m:
            assert abs(row['scalar_displacement_m'])<1e-15 and abs(row['scalar_velocity_m_s'])<1e-14
        if row['time_s']==0:assert row['scalar_displacement_m']==0

def test_unknown_boundary_support_and_output_budget():
    assert evaluate(req().model_copy(update={'regular_axis_scalar_wave_and_outer_zero_boundary_reviewed':False}))['status']=='needs_data'
    with pytest.raises(ValueError,match='budget'):evaluate(req().model_copy(update={'maximum_output_points':1}))
