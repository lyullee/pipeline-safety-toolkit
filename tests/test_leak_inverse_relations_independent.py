"""Authored network split, independent path geometry and acoustic modes."""
import math
import pytest
from pipeline_integrity.leak_inverse_relations import (
    DiscrepancyLocationRequest as D,locate_small_leak_from_discrepancies as discrepancy,
    ReflectionLocationRequest as R,locate_from_known_interface_reflection as reflection,
    DielectricCableRangingRequest as E,locate_dielectric_cable as dielectric,
    DirectionalAcousticSignalRequest as A,combine_directional_acoustic_signal as acoustic)


@pytest.mark.parametrize('fraction',[0.,.02,.3,.7,1.])
def test_discrepancy_from_independent_linear_hydraulic_resistances(fraction):
    resistance=25.;pressure_drop=100.;loss=.001
    left=resistance*fraction;right=resistance-left
    original=pressure_drop/resistance
    actual_up=(pressure_drop+right*loss)/resistance
    actual_down=(pressure_drop-left*loss)/resistance
    r=discrepancy(D(upstream_discrepancy_m3_s=actual_up-original,
        downstream_discrepancy_m3_s=original-actual_down,section_length_m=1200,
        small_leak_fully_propagated_linear_resistance_reviewed=True))
    assert r.fraction_from_upstream==pytest.approx(fraction,abs=6e-13)
    assert r.distance_from_upstream_m==pytest.approx(1200*fraction,abs=1e-9)
    assert r.inferred_loss_m3_s==pytest.approx(loss,rel=1e-11)


def test_discrepancy_zero_unknown_and_sign_rejection():
    args=dict(upstream_discrepancy_m3_s=0,downstream_discrepancy_m3_s=0,section_length_m=10,
        small_leak_fully_propagated_linear_resistance_reviewed=True)
    assert discrepancy(D(**args)).status=='unidentifiable'
    assert discrepancy(D(**{**args,'upstream_discrepancy_m3_s':None})).status=='needs_data'
    assert discrepancy(D(**{**args,'small_leak_fully_propagated_linear_resistance_reviewed':False})).status=='needs_data'
    with pytest.raises(ValueError):D(**{**args,'upstream_discrepancy_m3_s':-1})


@pytest.mark.parametrize('interface_to_source',[0.,25.,600.,1000.])
@pytest.mark.parametrize('time_origin',[-1234.,1000.])
def test_reflection_independent_direct_and_reflected_paths(interface_to_source,time_origin):
    L=1000.;a=1100.;x=interface_to_source
    direct=time_origin+(L-x)/a;reflected=time_origin+(L+x)/a
    r=reflection(R(sensor_to_interface_m=L,direct_arrival_s=direct,reflected_arrival_s=reflected,
        wave_speed_m_s=a,known_constant_pressure_interface_and_uniform_speed_reviewed=True))
    assert r.distance_upstream_from_sensor_m==pytest.approx(L-x,abs=2e-10)
    assert r.interface_to_source_m==pytest.approx(x,abs=2e-10)


def test_reflection_outside_and_unknown_and_wrong_order():
    args=dict(sensor_to_interface_m=1000.,direct_arrival_s=0.,reflected_arrival_s=3.,wave_speed_m_s=1000.,
        known_constant_pressure_interface_and_uniform_speed_reviewed=True)
    r=reflection(R(**args));assert r.status=='outside_sensor_interface_segment'
    assert r.distance_upstream_from_sensor_m==-500
    assert reflection(R(**{**args,'sensor_to_interface_m':None})).status=='needs_data'
    assert reflection(R(**{**args,'known_constant_pressure_interface_and_uniform_speed_reviewed':False})).status=='needs_data'
    with pytest.raises(ValueError):reflection(R(**{**args,'reflected_arrival_s':-1}))


@pytest.mark.parametrize('epsilon',[1.,2.25,4.,16.])
def test_dielectric_wave_time_and_existing_range_latency(epsilon):
    distance=123.;delay=2e-8
    elapsed=2*distance*math.sqrt(epsilon)/299792458.0+delay
    r=dielectric(E(relative_permittivity=epsilon,elapsed_round_trip_s=elapsed,fixed_latency_s=delay,
        cable_length_m=200.,nonmagnetic_nondispersive_dielectric_reviewed=True))
    assert r.position_m==pytest.approx(distance,abs=5e-14)
    assert r.within_declared_cable


def test_dielectric_unknown_regime_range_and_invalid():
    q=E(relative_permittivity=4.,elapsed_round_trip_s=1e-5,cable_length_m=1,
        nonmagnetic_nondispersive_dielectric_reviewed=True)
    assert dielectric(q).status=='outside_declared_cable'
    assert dielectric(q.model_copy(update={'relative_permittivity':None})).status=='needs_data'
    assert dielectric(q.model_copy(update={'elapsed_round_trip_s':None})).status=='needs_data'
    assert dielectric(q.model_copy(update={'nonmagnetic_nondispersive_dielectric_reviewed':False})).status=='needs_data'
    with pytest.raises(ValueError):E(relative_permittivity=.5,elapsed_round_trip_s=1.)


@pytest.mark.parametrize('right_pressure,left_pressure',[(15.,0.),(0.,-20.),(-10.,17.),(0.,0.)])
@pytest.mark.parametrize('direction',[-1,1])
def test_directional_pressure_flow_riemann_mode_reconstruction(right_pressure,left_pressure,direction):
    rho=700.;speed=900.;area=.2
    pressure=right_pressure+left_pressure
    flow=area*(right_pressure-left_pressure)/(rho*speed)
    r=acoustic(A(pressure_change_pa=pressure,volume_flow_change_m3_s=flow,density_kg_m3=rho,
        wave_speed_m_s=speed,flow_area_m2=area,direction=direction,
        linear_single_phase_acoustic_impedance_reviewed=True))
    assert r.combined_pressure_signal_pa==pytest.approx(2*(right_pressure if direction==1 else left_pressure),abs=1e-14)


def test_directional_unknown_and_invalid_direction():
    q=A(pressure_change_pa=1.,volume_flow_change_m3_s=1.,density_kg_m3=1.,wave_speed_m_s=1.,flow_area_m2=1.,direction=1)
    assert acoustic(q).status=='needs_data'
    with pytest.raises(ValueError):A.model_validate({**q.model_dump(),'direction':0})
