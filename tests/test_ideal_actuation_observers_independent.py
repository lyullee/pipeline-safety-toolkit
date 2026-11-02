"""Independent surface traction, swept-volume work and nominal shear references."""
import math
import pytest
from scipy.integrate import quad
from pipeline_integrity.ideal_actuation_observers import (
    uniform_pressure_face_force,IdealPistonRequest,evaluate_ideal_piston,
    IdealMotorRequest,evaluate_ideal_motor,UniformShearCutRequest,evaluate_uniform_shear_cut)


def piston(**kw):
    d=dict(id='authored',bore_diameter_m=.1,rod_diameter_m=.04,cap_pressure_pa_abs=5e6,
        rod_chamber_pressure_pa_abs=2e6,exterior_pressure_pa_abs=1e5,signed_extension_m=.3,
        measured_chamber='cap',signed_volume_inflow_m3_s=.001,positive_extension_axis=(1.,0.,0.),
        load_observation_direction=(1.,0.,0.),uniform_pressure_rigid_piston_incompressible_kinematics_reviewed=True)
    d.update(kw);return IdealPistonRequest(**d)

@pytest.mark.parametrize('r,p,p0',[(.04,3e6,1e5),(.2,1e5,4e5),(.003,1e5,1e5)])
def test_integrated_planar_traction(r,p,p0):
    expected=quad(lambda radius:2*math.pi*radius*(p-p0),0,r)[0]
    assert uniform_pressure_face_force(p,p0,math.pi*r*r)==pytest.approx(expected)

@pytest.mark.parametrize('rod,cap,back',[(0.,5e6,2e6),(.04,1e6,4e6),(.09,3e6,3e6)])
def test_piston_surface_integral_and_pressure_volume_work(rod,cap,back):
    q=piston(rod_diameter_m=rod,cap_pressure_pa_abs=cap,rod_chamber_pressure_pa_abs=back)
    v=evaluate_ideal_piston(q)['values'];a=math.pi*q.bore_diameter_m**2/4;b=math.pi*rod**2/4
    # Absolute work on all three exposed axial faces: cap, annulus, outer rod.
    work=cap*a*q.signed_extension_m-back*(a-b)*q.signed_extension_m-q.exterior_pressure_pa_abs*b*q.signed_extension_m
    assert v['net_pressure_work_j']==pytest.approx(work)
    assert v['net_extension_force_n']==pytest.approx(work/q.signed_extension_m)

@pytest.mark.parametrize('chamber,sign',[('cap',1),('rod',-1)])
def test_incompressible_chamber_volume_change(chamber,sign):
    q=piston(measured_chamber=chamber);v=evaluate_ideal_piston(q)['values']
    area=v['cap_area_m2'] if sign==1 else v['rod_chamber_area_m2']
    assert sign*area*v['extension_velocity_m_s']==pytest.approx(q.signed_volume_inflow_m3_s)
    assert v['net_pressure_power_w']==pytest.approx(v['net_extension_force_n']*v['extension_velocity_m_s'])

def test_common_absolute_pressure_datum_and_force_projection():
    q=piston(load_observation_direction=(.6,.8,0.));a=evaluate_ideal_piston(q)['values']
    b=evaluate_ideal_piston(piston(load_observation_direction=(.6,.8,0.),cap_pressure_pa_abs=q.cap_pressure_pa_abs+7e5,rod_chamber_pressure_pa_abs=q.rod_chamber_pressure_pa_abs+7e5,exterior_pressure_pa_abs=q.exterior_pressure_pa_abs+7e5))['values']
    assert a==pytest.approx(b);assert a['load_direction_force_n']==pytest.approx(.6*a['net_extension_force_n'])

@pytest.mark.parametrize('dp,disp,speed',[(4e6,1e-5,12.),(-2e6,4e-4,3.),(3e5,2e-5,-4.)])
def test_motor_rotational_work_integral_and_flow_energy(dp,disp,speed):
    q=IdealMotorRequest(id='authored',signed_pressure_drop_pa=dp,displacement_m3_per_positive_revolution=disp,signed_revolutions_per_s=speed,ideal_incompressible_pressure_work_reviewed=True)
    v=evaluate_ideal_motor(q)['values']
    angular_work=quad(lambda theta:v['positive_rotation_torque_nm'],0,2*math.pi)[0]
    hydraulic_work=quad(lambda volume:dp,0,disp)[0]
    assert angular_work==pytest.approx(hydraulic_work)
    assert v['transferred_mechanical_power_w']==pytest.approx(dp*v['signed_volume_flow_m3_s'])
    assert v['transferred_mechanical_power_w']==pytest.approx(v['positive_rotation_torque_nm']*v['signed_angular_velocity_rad_s'])

@pytest.mark.parametrize('perimeter,thickness,strength',[(.12,.002,2e8),(.8,.01,5e7)])
def test_uniform_shear_area_integral(perimeter,thickness,strength):
    q=UniformShearCutRequest(id='authored',actual_cut_perimeter_m=perimeter,uniform_sheet_thickness_m=thickness,supplied_uniform_shear_strength_pa=strength,material_basis_id='caller-material',uniform_simultaneous_shear_area_basis_reviewed=True)
    v=evaluate_uniform_shear_cut(q)['values']
    assert v['nominal_uniform_shear_force_n']==pytest.approx(quad(lambda s:strength*thickness,0,perimeter)[0])


def test_unknown_review_is_not_a_zero_force():
    assert evaluate_ideal_piston(piston(uniform_pressure_rigid_piston_incompressible_kinematics_reviewed=False))['status']=='needs_data'
    assert evaluate_ideal_motor(IdealMotorRequest(id='unknown',signed_pressure_drop_pa=1,displacement_m3_per_positive_revolution=1,signed_revolutions_per_s=1))['status']=='needs_data'
    assert evaluate_uniform_shear_cut(UniformShearCutRequest(id='unknown',actual_cut_perimeter_m=1,uniform_sheet_thickness_m=1,supplied_uniform_shear_strength_pa=1,material_basis_id='caller'))['status']=='needs_data'


def test_direction_geometry_and_nonfinite_rejected():
    with pytest.raises(ValueError):piston(positive_extension_axis=(2,0,0))
    with pytest.raises(ValueError):piston(rod_diameter_m=.1)
    with pytest.raises(ValueError):uniform_pressure_face_force(float('nan'),1,1)
