"""Exact first-wave pump-boundary and torque-work references for an actual trip."""
import math
import pytest
from scipy.integrate import quad
from pipeline_integrity.contracts import EvidenceRef,RunContext
from pipeline_integrity.transient import LiquidNode,LiquidPipe,LiquidTransient,History
from pipeline_integrity.pump_moc_coupling import LiquidPump
from pipeline_integrity.rotating_dynamics import SignedRotatingMap
from pipeline_integrity.liquid_pressure_trip import LiquidPressureTripRequest as R,simulate_pressure_triggered_pump_trip as evaluate

RHO=998.;G=9.80665;A=math.pi*.1**2/4;C=1000.;B=C/(G*A)
ALPHA=.001;BETA=10000.;Q0=.001;W0=100.;INERTIA=.1
CTX=RunContext(asset_id='authored pump line',scenario_id='causal sampled trip',clock_id='elapsed',origin='synthetic',evidence=(EvidenceRef(id='e',citation='Authored affine pump/incident-wave reference and sensor clock',origin='synthetic'),))


def request(dt=.0005,direction=1,sample=.002):
    speeds=(90.,110.);flows=(.0008,.0012)
    pressure=tuple(tuple(RHO*G*(ALPHA*(w-W0)-BETA*(q-Q0)) for q in flows) for w in speeds)
    model=SignedRotatingMap(id='authored affine signed pump',speed_rad_s=speeds,volume_flow_m3_s=flows,resisting_torque_N_m=((1.,1.),(1.,1.)),pressure_rise_Pa=pressure,evidence_id='e',signed_axes_and_torque_pressure_conventions_reviewed=True)
    pump=LiquidPump(id='pump',start_node='reservoir',end_node='pump-outlet',shaft_id='rotor',performance_map=model,initial_speed_rad_s=W0,initial_volume_flow_m3_s=Q0,inertia_kg_m2=INERTIA,drive_torque_N_m=1.,rotor_and_drive_evidence_id='e',colocated_equal_velocity_head_ports_reviewed=True,initial_drive_equilibrium_required=True,torque_tolerance_N_m=1e-9,energy_tolerance_J=1e-5)
    constant=History(time_s=(0.,),values=(10.,))
    pulse=History(time_s=(0.,.01,.03,.037),values=(10.,10.,10.+direction*.001,10.+direction*.001))
    spec=LiquidTransient(nodes=(LiquidNode(id='reservoir',elevation_m=0.,initial_head_m=10.,boundary='head',history=constant),LiquidNode(id='pump-outlet',elevation_m=0.,initial_head_m=10.,boundary='withdrawal',history=History(time_s=(0.,),values=(0.,))),LiquidNode(id='terminus',elevation_m=0.,initial_head_m=10.,boundary='head',history=pulse)),pipes=(LiquidPipe(id='line',start_node='pump-outlet',end_node='terminus',length_m=10.,diameter_m=.1,wave_speed_m_s=C,darcy_friction_factor=0.,initial_volume_flow_m3_s=Q0),),pumps=(pump,),duration_s=.037,requested_timestep_s=dt,density_kg_m3=RHO,ambient_pressure_pa_abs=101325.,vapour_pressure_pa_abs=2000.,mass_balance_tolerance_m3=1e-8,valve_flow_tolerance_m3_s=1e-10,valve_head_tolerance_m=1e-9,maximum_wave_speed_adjustment=0.)
    return R(specification=spec,target_pump_id='pump',sensor_pipe_id='line',sensor_pipe_end='start',absolute_trip_pressure_pa=101325+RHO*G*(10.+direction*.0003),trigger_direction='at_or_above' if direction>0 else 'at_or_below',pressure_sampling_interval_s=sample,torque_removal_ramp_s=.002,post_trip_prescribed_torque_N_m=0.,detector_and_action_evidence_id='e',sampled_absolute_pressure_and_retained_mechanical_load_reviewed=True)


def exact_trip(sample):
    # The first incident characteristic at the pump is2*f. Its affine map
    # before the trip gives deltaH=2*BETA*f/(B+BETA).
    threshold_time=.02+.0003*(B+BETA)/(2*BETA)/.05
    return math.ceil(threshold_time/sample)*sample


def speed(time,event,ramp=.002):
    t=max(time-event,0.)
    if t<=ramp:return W0-t*t/(2*INERTIA*ramp)
    return W0-(t-ramp/2)/INERTIA


@pytest.mark.parametrize('dt',[.001,.0005,.00025])
@pytest.mark.parametrize('direction',[1,-1])
def test_first_characteristic_pressure_event_actual_rerun_rotor_and_pipe_reference(dt,direction):
    q=request(dt,direction);original=q.model_dump_json();out=evaluate(q,CTX)
    assert out.state=='computed',out.reason
    assert out.actual_transient_rerun_performed and out.pretrip_causal_identity_verified
    assert q.model_dump_json()==original
    event=exact_trip(.002);assert out.trip_time_s==pytest.approx(event,abs=2e-14)
    assert out.trip_sample_index==round(event/.002)
    if direction>0:assert out.previous_sample_pressure_pa_abs<q.absolute_trip_pressure_pa<=out.sampled_pressure_at_trip_pa_abs
    else:assert out.previous_sample_pressure_pa_abs>q.absolute_trip_pressure_pa>=out.sampled_pressure_at_trip_pa_abs
    native=out.operated_transient;times=native['time_s'];heads=native['pipes']['line']['start_head_m'];flows=native['pipes']['line']['start_flow_m3_s']
    assert native['all_volume_balances_within_tolerance']
    assert native['maximum_pipe_volume_residual_m3']<=q.specification.mass_balance_tolerance_m3
    assert native['maximum_junction_residual_m3_s']<=q.specification.valve_flow_tolerance_m3_s
    for t,h,flow,row in zip(times,heads,flows,native['pumps']['pump']):
        incident=direction*.05*min(max(t-.02,0.),.02)
        omega=speed(t,event)
        # Before the first return from the terminus (t=.04), incoming
        # characteristic plus the actual affine pump map closes head/flow.
        expected_h=10.+(B*ALPHA*(omega-W0)+2*BETA*incident)/(B+BETA)
        expected_q=Q0+(ALPHA*(omega-W0)-2*incident)/(B+BETA)
        assert row['speed_rad_s']==pytest.approx(omega,abs=2e-8)
        assert h==pytest.approx(expected_h,abs=3e-9)
        assert flow==pytest.approx(expected_q,abs=3e-12)
        assert abs(row['energy_residual_J'])<=q.specification.pumps[0].energy_tolerance_J
    # The actual pump trip must change the hydraulic trajectory after the
    # event, rather than only annotate the pilot or overwrite rotor output.
    assert abs(heads[-1]-out.pilot_transient['pipes']['line']['start_head_m'][-1])>1e-6
    end=times[-1]
    drive_work=quad(lambda t:(1. if t<=event else max(1-(t-event)/.002,0.))*speed(t,event),0,end,points=[event,event+.002],epsabs=1e-10)[0]
    resisting_work=quad(lambda t:speed(t,event),0,end,points=[event,event+.002],epsabs=1e-10)[0]
    kinetic=.5*INERTIA*(speed(end,event)**2-W0**2)
    assert drive_work-resisting_work==pytest.approx(kinetic,abs=2e-10)
    assert native['pumps']['pump'][-1]['rotor_energy_J']-.5*INERTIA*W0**2==pytest.approx(kinetic,abs=2e-7)


@pytest.mark.parametrize('sample',[.001,.002])
def test_sampling_clock_is_explicit_and_trip_time_is_not_an_unsampled_crossing(sample):
    q=request(sample=sample);out=evaluate(q,CTX);assert out.state=='computed'
    assert out.trip_time_s==pytest.approx(exact_trip(sample),abs=2e-14)
    assert out.pressure_sampling_stride_steps==round(sample/out.native_timestep_s)
    assert out.continuous_unsampled_threshold_time_solved is False


def test_initial_latched_trip_and_finite_torque_ramp():
    q=request();initial=101325+RHO*G*10
    out=evaluate(q.model_copy(update={'absolute_trip_pressure_pa':initial}),CTX)
    assert out.state=='computed';assert out.trip_detected and out.trip_time_s==0
    assert out.actual_transient_rerun_performed and out.pretrip_causal_identity_verified
    assert out.operated_transient['pumps']['pump'][-1]['speed_rad_s']==pytest.approx(speed(.037,0),abs=2e-8)


def test_no_event_is_not_reported_as_a_successful_pump_action():
    q=request().model_copy(update={'absolute_trip_pressure_pa':3e5})
    out=evaluate(q,CTX);assert out.state=='computed';assert not out.trip_detected
    assert not out.actual_transient_rerun_performed;assert out.trip_time_s is None


def test_misaligned_sampler_is_withheld_not_interpolated_or_retimed():
    out=evaluate(request(sample=.0013),CTX)
    assert out.state=='out_of_scope';assert not out.actual_transient_rerun_performed


def test_missing_controller_data_or_review_does_not_guess_a_trip():
    q=request()
    for key in ['target_pump_id','sensor_pipe_id','absolute_trip_pressure_pa','pressure_sampling_interval_s','torque_removal_ramp_s','post_trip_prescribed_torque_N_m','detector_and_action_evidence_id']:
        assert evaluate(q.model_copy(update={key:None}),CTX).state=='needs_data'
    assert evaluate(q.model_copy(update={'sampled_absolute_pressure_and_retained_mechanical_load_reviewed':False}),CTX).state=='needs_data'


def test_nonexistent_sensor_action_evidence_and_active_feedback_are_diagnostic():
    q=request()
    for update in [{'target_pump_id':'unknown'},{'sensor_pipe_id':'unknown'},{'detector_and_action_evidence_id':'unknown'}]:
        with pytest.raises(ValueError):evaluate(q.model_copy(update=update),CTX)
    pump=q.specification.pumps[0].model_copy(update={'drive_speed_feedback_N_m_s':1.})
    spec=q.specification.model_copy(update={'pumps':(pump,)})
    assert evaluate(q.model_copy(update={'specification':spec}),CTX).state=='out_of_scope'
