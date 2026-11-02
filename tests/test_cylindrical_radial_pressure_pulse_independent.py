"""Geometry-linked shell pulse versus independent membrane dynamic/energy limits."""
import math
import pytest
from pipeline_integrity.cylindrical_modal_projection import CylindricalModalProjectionRequest as Shell
from pipeline_integrity.cylindrical_radial_pressure_pulse import RadialPressureStage as Stage,CylindricalRadialPulseRequest as Request,evaluate_cylindrical_radial_pressure_pulse as evaluate

@pytest.mark.parametrize('plane',['plane_stress_ring','plane_strain_axially_uniform_shell'])
@pytest.mark.parametrize('duration_fraction',[.025,.125,.5])
def test_uniform_pressure_pulse_radial_peak_hoop_coordinate_and_work_balance(plane,duration_fraction):
    radius=.5;thickness=.005;modulus=200e9;nu=.3;density=7800.;pressure=1e5
    shell=Shell(mean_radius_m=radius,wall_thickness_m=thickness,axial_length_m=4.,elastic_modulus_pa=modulus,
        poisson_ratio=nu,material_density_kg_m3=density,radial_axial_constraint=plane,
        isotropic_thin_uniform_shell_modal_fields_and_free_axial_mode_reviewed=True)
    effective=modulus/(1-nu**2) if plane.startswith('plane_strain') else modulus
    omega=math.sqrt(effective/density)/radius;period=2*math.pi/omega;duration=duration_fraction*period
    peak_time=duration/2+math.pi/(2*omega)
    times=(0.,duration,peak_time) if peak_time>duration else (0.,duration/2,duration)
    q=Request(shell=shell,pressure_stages=(Stage(start_s=0.,internal_minus_external_pressure_pa=pressure),Stage(start_s=duration,internal_minus_external_pressure_pa=0.)),
        output_times_s=times,modal_damping_ratio=0.,maximum_reviewed_absolute_radial_strain=.005,
        pressure_and_uniform_radial_linear_mode_basis_id='authored-axisymmetric-pressure',held_uniform_pressure_radial_mode_and_elastic_strain_support_reviewed=True)
    result=evaluate(q);assert result['status']=='computed'
    peak=result['response']['points'][-1]
    expected=2*pressure*radius**2/(effective*thickness)*math.sin(omega*duration/2)
    assert peak['displacement_m']==pytest.approx(expected,rel=3e-11)
    assert abs(peak['velocity_m_s'])<1e-10
    assert effective*peak['displacement_m']/radius==pytest.approx(2*pressure*radius/thickness*math.sin(omega*duration/2),rel=3e-11)
    mode=result['modal_projection']['modes'][0]
    at_switch=next(p for p in result['response']['points'] if p['time_s']==duration)
    energy=.5*mode['modal_mass_kg']*at_switch['velocity_m_s']**2+.5*mode['modal_stiffness_n_m']*at_switch['displacement_m']**2
    work=pressure*mode['generalized_uniform_pressure_area_m2']*at_switch['displacement_m']
    assert energy==pytest.approx(work,rel=3e-11)
