"""Independent nodal resistor network, KCL cuts and full conversion-energy budget."""
import math
import numpy as np
import pytest
from pipeline_integrity.dc_tapped_cable import (
    DCCableTap as Tap,DCTappedCableRequest as Request,observe_dc_tapped_cable,
    DCOhmicDriveRequest,observe_dc_ohmic_drive,DCCollectedCurrentSegment,observe_dc_collected_current)


def request(**changes):
    q=dict(taps=(Tap(id='a',position_m=2.,current_draw_A=1.),Tap(id='b',position_m=5.,current_draw_A=2.),Tap(id='c',position_m=9.,current_draw_A=3.)),
        series_path_resistance_ohm_m=.1,source_voltage_to_common_return_V=50.,
        rectifier_input_requested=True,supplied_dc_real_output_over_ac_real_input_efficiency=.8,
        rectifier_efficiency_basis_id='given-DC-over-AC-real-power',unidirectional_rectifier_proportional_power_basis_reviewed=True,
        steady_currents_common_return_and_series_path_basis_reviewed=True,asset_id='feed',clock_id='steady',
        coordinate_frame_id='feed-chainage',potential_reference_id='common-return',cable_resistance_basis_id='supplied-single-path-resistance')
    q.update(changes);return Request(**q)


@pytest.mark.parametrize('loads',[(1.,2.,3.),(.1,3.,.2),(0.,0.,0.),(4.,0.,0.)])
def test_node_potentials_from_independent_conductance_matrix_not_suffix_drop_formula(loads):
    q=request(taps=tuple(Tap(id=str(i),position_m=x,current_draw_A=j) for i,(x,j) in enumerate(zip((2.,5.,9.),loads))))
    a=observe_dc_tapped_cable(q);assert a.status=='computed'
    resistance=np.array([.2,.3,.4]);G=1/resistance
    A=np.array([[G[0]+G[1],-G[1],0.],[-G[1],G[1]+G[2],-G[2]],[0.,-G[2],G[2]]])
    b=np.array([G[0]*50,0.,0.])-np.array(loads)
    voltages=np.linalg.solve(A,b)
    assert [s.tap_voltage_to_common_return_V for s in a.segments]==pytest.approx(voltages,rel=2e-14)
    v=np.r_[50.,voltages];link_current=(v[:-1]-v[1:])/resistance
    assert [s.series_current_A for s in a.segments]==pytest.approx(link_current,abs=1e-12)
    assert a.source_current_A==pytest.approx(sum(loads))
    for i,load in enumerate(loads):
        assert link_current[i]-(link_current[i+1] if i<2 else 0)==pytest.approx(load,abs=2e-12)


@pytest.mark.parametrize('duration',[.1,25.,3600.])
def test_independent_node_delivery_wire_heat_and_rectifier_conversion_energy(duration):
    q=request();a=observe_dc_tapped_cable(q)
    delivered=sum(s.tap_voltage_to_common_return_V*t.current_draw_A*duration for s,t in zip(a.segments,q.taps))
    wire=sum(s.segment_resistance_ohm*s.series_current_A**2*duration for s in a.segments)
    source=50*6*duration
    assert a.source_dc_real_power_W*duration==pytest.approx(source)
    assert delivered+wire==pytest.approx(source,rel=2e-14)
    conversion_heat=a.rectifier_loss_W*duration
    assert a.required_ac_real_input_power_W*duration==pytest.approx(delivered+wire+conversion_heat,rel=2e-14)
    assert source/(a.required_ac_real_input_power_W*duration)==pytest.approx(.8)
    assert abs(a.dc_power_balance_residual_W)<1e-12


def test_equal_taps_recover_geometric_current_sequence_and_known_drop():
    q=request(taps=tuple(Tap(id=str(i),position_m=2+5*i,current_draw_A=2.) for i in range(4)))
    a=observe_dc_tapped_cable(q)
    assert [s.series_current_A for s in a.segments]==[8.,6.,4.,2.]
    assert a.last_tap_cumulative_drop_V==pytest.approx(7.6)


def test_zero_resistance_and_at_bus_tap_are_exact_limits():
    a=observe_dc_tapped_cable(request(series_path_resistance_ohm_m=0.))
    assert a.cable_joule_power_W==0 and all(s.tap_voltage_to_common_return_V==50 for s in a.segments)
    q=request(taps=(Tap(id='at-bus',position_m=0,current_draw_A=1.),))
    b=observe_dc_tapped_cable(q);assert b.last_tap_cumulative_drop_V==0 and b.cable_joule_power_W==0


def test_no_source_voltage_preserves_cable_observation_but_withholds_rectifier_power():
    a=observe_dc_tapped_cable(request(source_voltage_to_common_return_V=None))
    assert a.status=='needs_data' and a.cable_status=='computed' and a.rectifier_status=='needs_data'
    assert a.last_tap_cumulative_drop_V>0 and a.required_ac_real_input_power_W is None


def test_omitted_rectifier_or_unknown_efficiency_never_uses_an_embedded_factor():
    a=observe_dc_tapped_cable(request(rectifier_input_requested=False))
    b=observe_dc_tapped_cable(request(supplied_dc_real_output_over_ac_real_input_efficiency=None))
    assert a.status=='computed' and a.rectifier_status=='not_requested' and a.required_ac_real_input_power_W is None
    assert b.status=='needs_data' and b.required_ac_real_input_power_W is None and b.cable_status=='computed'


def test_inadequate_source_voltage_is_not_clipped_to_passive_nodes():
    a=observe_dc_tapped_cable(request(source_voltage_to_common_return_V=.1))
    assert a.status=='out_of_scope' and a.segments[-1].tap_voltage_to_common_return_V<0
    assert a.required_ac_real_input_power_W is None


@pytest.mark.parametrize('scale',[.01,20.])
def test_length_and_resistance_density_preserve_physical_path(scale):
    q=request();a=observe_dc_tapped_cable(q)
    taps=tuple(t.model_copy(update={'position_m':t.position_m*scale}) for t in q.taps)
    b=observe_dc_tapped_cable(q.model_copy(update={'taps':taps,'series_path_resistance_ohm_m':.1/scale}))
    assert b.last_tap_cumulative_drop_V==pytest.approx(a.last_tap_cumulative_drop_V)
    assert b.cable_joule_power_W==pytest.approx(a.cable_joule_power_W)


@pytest.mark.parametrize('changes',[{'series_path_resistance_ohm_m':None},
    {'taps':(Tap(id='unknown',position_m=1,current_draw_A=None),)},
    {'steady_currents_common_return_and_series_path_basis_reviewed':False}])
def test_missing_cable_inputs_are_not_zero(changes):
    a=observe_dc_tapped_cable(request(**changes));assert a.status=='needs_data' and not a.segments


@pytest.mark.parametrize('changes',[{'rectifier_efficiency_basis_id':None},
    {'unidirectional_rectifier_proportional_power_basis_reviewed':False}])
def test_rectifier_reference_unknown_keeps_the_known_DC_budget(changes):
    a=observe_dc_tapped_cable(request(**changes));assert a.status=='needs_data' and a.source_dc_real_power_W==300.


@pytest.mark.parametrize('changes',[{'supplied_dc_real_output_over_ac_real_input_efficiency':1.1},
    {'supplied_dc_real_output_over_ac_real_input_efficiency':0},
    {'taps':(Tap(id='a',position_m=2,current_draw_A=1),Tap(id='a',position_m=3,current_draw_A=1))},
    {'taps':(Tap(id='a',position_m=2,current_draw_A=1),Tap(id='b',position_m=1,current_draw_A=1))}])
def test_invalid_efficiency_identity_or_directed_path_rejected(changes):
    with pytest.raises(ValueError):request(**changes)


@pytest.mark.parametrize('voltage',[-4.,0.,4.])
def test_ohmic_driver_from_independent_two_resistor_Kirchhoff_node(voltage):
    r1,r2=.4,.6
    # Nodal conductance equation solves the internal node without production I=E/R.
    internal=np.linalg.solve(np.array([[1/r1+1/r2]]),np.array([voltage/r1]))[0]
    expected=(voltage-internal)/r1
    q=DCOhmicDriveRequest(net_driving_potential_difference_V=voltage,complete_path_resistance_ohm=r1+r2,
        potential_reference_id='net-reconciled-driving-potential',resistance_basis_id='complete-series-path',
        common_reference_and_steady_complete_passive_resistive_path_reviewed=True)
    a=observe_dc_ohmic_drive(q)
    assert a.signed_current_A==pytest.approx(expected,abs=1e-14)
    assert a.dissipated_power_W==pytest.approx((voltage-internal)**2/r1+internal**2/r2,abs=1e-13)


def test_unknown_ohmic_driver_is_not_an_inferred_magnesium_material_curve():
    q=DCOhmicDriveRequest(potential_reference_id='unknown-drive',resistance_basis_id='unknown-soil')
    assert observe_dc_ohmic_drive(q).status=='needs_data'


@pytest.mark.parametrize('sign',[-1.,0.,1.])
def test_segment_pickup_matches_independent_distributed_current_flux_integral(sign):
    from scipy.integrate import quad
    length=80.;start=.3
    pickup=quad(lambda x:sign*(.01+.002*math.sin(math.pi*x/length)),0,length,epsabs=1e-12)[0]
    q=DCCollectedCurrentSegment(start_positive_x_axial_current_A=start,end_positive_x_axial_current_A=start+pickup,
        segment_length_m=length,coordinate_frame_id='same-chainage',steady_same_oriented_current_coordinate_reviewed=True)
    a=observe_dc_collected_current(q)
    assert a.signed_current_collected_into_pipe_A==pytest.approx(pickup,abs=1e-14)
    assert a.mean_current_collected_into_pipe_A_m==pytest.approx(pickup/length,abs=1e-14)


def test_unknown_collection_coordinate_not_positive_protection_demand():
    q=DCCollectedCurrentSegment(start_positive_x_axial_current_A=None,end_positive_x_axial_current_A=1.,
        segment_length_m=10.,coordinate_frame_id='unreviewed')
    assert observe_dc_collected_current(q).status=='needs_data'
