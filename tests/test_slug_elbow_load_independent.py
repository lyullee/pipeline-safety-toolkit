"""Independent vector momentum ledger, rotations and supplied pressure snapshots."""
import math
import numpy as np
import pytest
from pipeline_integrity.slug_elbow_load import SlugElbowLoadRequest as Request,evaluate_slug_elbow_load
from pipeline_integrity.liquid_column_collision import LiquidAcousticColumnState, LiquidColumnCollisionRequest,evaluate_liquid_column_collision


def request(**kwargs):
    data=dict(coordinate_frame_id='laboratory',slug_density_kg_m3=980.,flow_area_m2=.03,
        signed_path_velocity_m_s=2.,incoming_path_unit_direction=(1.,0.,0.),outgoing_path_unit_direction=(0.,1.,0.),
        incoming_pressure_Pa_abs=1e5,outgoing_pressure_Pa_abs=1e5,ambient_pressure_Pa_abs=1e5,
        uniform_full_bore_slug_port_states_reviewed=True)
    data.update(kwargs)
    return Request(**data)


@pytest.mark.parametrize('velocity',[-3.,-.1,0.,.1,3.])
def test_right_angle_components_and_reversal_match_parcel_momentum(velocity):
    q=request(signed_path_velocity_m_s=velocity);r=evaluate_slug_elbow_load(q)
    force=q.slug_density_kg_m3*q.flow_area_m2*velocity**2
    assert r.status=='computed'
    assert r.values['summed_convective_contribution_N']==pytest.approx((force,-force,0.))
    assert r.values['summed_pressure_contribution_N']==pytest.approx((0.,0.,0.),abs=0.)
    # A parcel traversing the bend changes velocity; equal reversed flow has
    # the same reaction because both parcel travel and mass-flow signs reverse.
    dt=.017
    if velocity:
        vi=np.array([velocity,0.,0.]);vo=np.array([0.,velocity,0.])
        parcel_mass=abs(velocity)*q.slug_density_kg_m3*q.flow_area_m2*dt
        change=(vo-vi) if velocity>0 else (vi-vo)
        assert np.array(r.values['summed_convective_contribution_N'])*dt==pytest.approx(-parcel_mass*change)
    assert not r.values['whole_hardware_force_identified']


@pytest.mark.parametrize('angle',[0.,.2,math.pi/2,math.pi])
def test_general_bend_rotation_and_control_volume_momentum(angle):
    incoming=np.array([1.,0.,0.]);outgoing=np.array([math.cos(angle),math.sin(angle),0.])
    axis=np.array([1.,2.,3.]);axis/=np.linalg.norm(axis);phi=.63
    cross=np.array([[0,-axis[2],axis[1]],[axis[2],0,-axis[0]],[-axis[1],axis[0],0]])
    rot=np.eye(3)+math.sin(phi)*cross+(1-math.cos(phi))*cross@cross
    q=request(incoming_path_unit_direction=tuple(rot@incoming),outgoing_path_unit_direction=tuple(rot@outgoing))
    r=evaluate_slug_elbow_load(q)
    expected=q.slug_density_kg_m3*q.flow_area_m2*q.signed_path_velocity_m_s**2*(incoming-outgoing)
    assert r.values['summed_convective_contribution_N']==pytest.approx(rot@expected,rel=3e-15,abs=3e-14)
    assert r.values['signed_outward_mass_balance_kg_s']==0.


def test_supplied_pressure_traction_and_explicit_steady_body_force_are_separate():
    q=request(incoming_pressure_Pa_abs=2e5,outgoing_pressure_Pa_abs=1.4e5,
        complete_steady_control_volume_reviewed=True,body_force_on_fluid_N=(0.,0.,-12.),absolute_mass_balance_tolerance_kg_s=0.)
    r=evaluate_slug_elbow_load(q)
    pressure=np.array([3000.,-1200.,0.]);conv=np.array([117.6,-117.6,0.])
    assert r.values['summed_pressure_contribution_N']==pytest.approx(pressure)
    assert r.values['fluid_force_on_hardware_N']==pytest.approx(pressure+conv+[0,0,-12])
    assert r.values['whole_hardware_force_identified']


def test_external_incident_acoustic_state_is_an_explicit_pressure_input_not_an_extra_force():
    left=LiquidAcousticColumnState(density_kg_m3=980.,wave_speed_m_s=1000.,pressure_pa_abs=1e5,signed_axial_velocity_m_s=.1)
    right=left.model_copy(update={'signed_axial_velocity_m_s':0.})
    state=evaluate_liquid_column_collision(LiquidColumnCollisionRequest(left=left,right=right,
        reference_pressure_pa_abs=1e5,vapor_pressure_pa_abs=2000.,ordinary_liquid_impedance_and_local_linear_states_reviewed=True))
    q=request(incoming_pressure_Pa_abs=state.interface_pressure_pa_abs,outgoing_pressure_Pa_abs=state.interface_pressure_pa_abs,
        signed_path_velocity_m_s=state.interface_signed_velocity_m_s)
    r=evaluate_slug_elbow_load(q);p=state.interface_pressure_pa_abs-1e5
    assert r.values['summed_pressure_contribution_N']==pytest.approx((q.flow_area_m2*p,-q.flow_area_m2*p,0.))
    assert not r.values['acoustic_pressure_predicted'] and not r.values['whole_hardware_force_identified']


@pytest.mark.parametrize('case',['review','unit','unknown','missing_body','nonfinite','underflow'])
def test_missing_states_and_unsupported_arithmetic_are_not_filled(case):
    if case=='review':assert evaluate_slug_elbow_load(request(uniform_full_bore_slug_port_states_reviewed=False)).status=='needs_data'
    elif case=='missing_body':assert evaluate_slug_elbow_load(request(complete_steady_control_volume_reviewed=True)).status=='needs_data'
    else:
        data={'unit':dict(incoming_path_unit_direction=(2.,0.,0.)), 'unknown':dict(invented_acoustic_force=123.),
            'nonfinite':dict(signed_path_velocity_m_s=float('nan')),
            'underflow':dict(slug_density_kg_m3=1e-200,flow_area_m2=1e-200)}[case]
        with pytest.raises(ValueError):evaluate_slug_elbow_load(request(**data))
