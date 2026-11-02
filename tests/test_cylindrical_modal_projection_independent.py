"""Independent separated ring/rod fields and axial finite-element convergence."""
import math
import numpy as np
import pytest
from scipy.linalg import eigh
from pipeline_integrity.cylindrical_modal_projection import CylindricalModalProjectionRequest as Request,evaluate_cylindrical_modal_projection as evaluate
def request(radius=.5,thickness=.005,modulus=200e9,density=7800.,nu=.3,plane='plane_strain_axially_uniform_shell'):
    return Request(mean_radius_m=radius,wall_thickness_m=thickness,axial_length_m=4.,elastic_modulus_pa=modulus,
        poisson_ratio=nu,material_density_kg_m3=density,radial_axial_constraint=plane,
        isotropic_thin_uniform_shell_modal_fields_and_free_axial_mode_reviewed=True)

@pytest.mark.parametrize('plane',['plane_stress_ring','plane_strain_axially_uniform_shell'])
@pytest.mark.parametrize('radius,thickness,modulus,density,nu',[(.5,.005,200e9,7800.,.3),(.15,.003,70e9,2700.,.33),(1.,.01,50e9,2000.,.25)])
def test_ring_energy_projection_matches_independent_membrane_and_inextensional_fields(plane,radius,thickness,modulus,density,nu):
    q=request(radius,thickness,modulus,density,nu,plane);r=evaluate(q);assert r['status']=='computed'
    effective=modulus/(1-nu**2) if plane.startswith('plane_strain') else modulus
    radial=r['modes'][0]
    assert radial['frequency_hz']==pytest.approx(math.sqrt(effective/density)/(2*math.pi*radius),rel=2e-10)
    assert radial['modal_mass_kg']==pytest.approx(2*math.pi*radius*thickness*q.axial_length_m*density,rel=2e-13)
    assert radial['generalized_uniform_pressure_area_m2']==pytest.approx(2*math.pi*radius*q.axial_length_m)
    for n,mode in zip((2,3),r['modes'][1:3]):
        expected=math.sqrt(effective*thickness**2/(12*density*radius**4)*n**2*(n*n-1)**2/(n*n+1))/(2*math.pi)
        assert mode['frequency_hz']==pytest.approx(expected,rel=2e-8)
        assert mode['modal_mass_kg']==pytest.approx(math.pi*radius*thickness*q.axial_length_m*density*(1+1/n**2),rel=2e-13)
        assert mode['generalized_uniform_pressure_area_m2']==0

def test_free_free_axial_frequency_matches_independent_rod_fe_convergence():
    q=request();r=evaluate(q);exact=r['modes'][-1]['frequency_hz'];errors=[]
    area=2*math.pi*q.mean_radius_m*q.wall_thickness_m
    for elements in (8,16,32):
        h=q.axial_length_m/elements;k=np.zeros((elements+1,elements+1));m=k.copy()
        for i in range(elements):
            k[i:i+2,i:i+2]+=q.elastic_modulus_pa*area/h*np.array([[1.,-1.],[-1.,1.]])
            m[i:i+2,i:i+2]+=q.material_density_kg_m3*area*h/6*np.array([[2.,1.],[1.,2.]])
        values=eigh(k,m,eigvals_only=True)
        assert abs(values[0])<1e-4
        frequency=math.sqrt(values[1])/(2*math.pi);errors.append(abs(frequency/exact-1))
    assert errors[2]<errors[1]<errors[0] and errors[-1]<.0005

def test_thickness_scaling_separates_membrane_and_flexural_frequencies():
    q=request();a=evaluate(q);b=evaluate(q.model_copy(update={'wall_thickness_m':.01}))
    assert b['modes'][0]['frequency_hz']==pytest.approx(a['modes'][0]['frequency_hz'],rel=2e-10)
    for i in (1,2):assert b['modes'][i]['frequency_hz']==pytest.approx(2*a['modes'][i]['frequency_hz'],rel=2e-8)

def test_unknown_density_withheld():
    assert evaluate(request(density=None))['status']=='needs_data'

def test_thick_wall_not_silently_extended():
    assert evaluate(request(thickness=.05))['status']=='out_of_scope'
