"""Selected hollow circular-section torsion through the existing material owner."""
import math
import pytest
m=pytest.importorskip('matintegrity.models.fatigue')
from scipy.integrate import quad

def request(a,b,torque=0.,amplitude=0.):
 return m.PressurizedAnnulusLoadState(reference_section_id='authored-annulus',load_case_id='static-torsion',inner_radius_m=a,outer_radius_m=b,
  pressure_evaluation_radius_m=b,external_axial_force_mean_N=0.,external_axial_force_amplitude_N=0.,torque_mean_N_m=torque,torque_amplitude_N_m=amplitude,
  axial_torsion_phase_rad=0.,internal_pressure_Pa=0.,external_pressure_Pa=0.,closed_ends=False)

@pytest.mark.parametrize('a,b',[(.01,.03),(.035,.05),(.099,.1)])
@pytest.mark.parametrize('gradient',[-2e6,0.,3e6])
def test_outer_shear_from_independent_area_moment_integral(a,b,gradient):
 torque=2*math.pi*quad(lambda radius:gradient*radius**3,a,b,epsabs=1e-12,epsrel=1e-13)[0]
 r=m.pressurized_annulus_load_state(request(a,b,torque)).values
 assert r['torsional_shear_mean_Pa']==pytest.approx(gradient*b,rel=3e-13,abs=1e-10)
 assert r['pressure_hoop_mean_Pa']==0 and r['pressure_axial_mean_Pa']==0
 assert r['external_axial_mean_Pa']==0
 assert r['torsional_shear_amplitude_Pa']==0

@pytest.mark.parametrize('scale',[.2,5.])
def test_geometric_similarity_and_torque_cube_scale(scale):
 a=m.pressurized_annulus_load_state(request(.02,.05,400.)).values
 b=m.pressurized_annulus_load_state(request(.02*scale,.05*scale,400.*scale**3)).values
 assert b['torsional_shear_mean_Pa']==pytest.approx(a['torsional_shear_mean_Pa'],rel=3e-13)
 assert b['polar_second_moment_m4']==pytest.approx(a['polar_second_moment_m4']*scale**4,rel=3e-13)

def test_mean_amplitude_are_linear_section_coordinates_not_a_life_prediction():
 r=m.pressurized_annulus_load_state(request(.02,.05,400.,100.)).values
 assert r['torsional_shear_amplitude_Pa']==pytest.approx(r['torsional_shear_mean_Pa']/4,rel=1e-13)
 assert r['torsional_shear_minimum_Pa']==pytest.approx(r['torsional_shear_mean_Pa']*.75)
 assert r['torsional_shear_maximum_Pa']==pytest.approx(r['torsional_shear_mean_Pa']*1.25)
 assert 'cycles_to_failure' not in r

def test_solid_shaft_is_explicitly_not_hidden_as_a_small_inner_radius():
 with pytest.raises(ValueError):request(0.,.05,400.)
