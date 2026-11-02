"""Sampled pressure relay with an actual causal pump/MOC torque-trip rerun.

The pilot contains the unchanged pre-trip drive. A first sampled threshold
event changes only its selected pump's subsequent finite torque ramp, then
the existing simultaneous rotor/characteristic solver is run again. Reported
pre-event pipe-end and rotor states are checked for causal identity. No arbitrary restart
state, postprocessed coastdown or implicit physical sampling clock is used.
"""
from __future__ import annotations
import math
from typing import Literal
from .contracts import Contract,Name,Positive,RunContext
from .transient import LiquidTransient,simulate_liquid_transient
from .pump_moc_coupling import PumpDrivePoint


class LiquidPressureTripRequest(Contract):
    specification: LiquidTransient
    target_pump_id: Name | None = None
    sensor_pipe_id: Name | None = None
    sensor_pipe_end: Literal['start','end'] = 'start'
    absolute_trip_pressure_pa: Positive | None = None
    trigger_direction: Literal['at_or_above','at_or_below'] = 'at_or_above'
    pressure_sampling_interval_s: Positive | None = None
    torque_removal_ramp_s: Positive | None = None
    post_trip_prescribed_torque_N_m: float | None = None
    detector_and_action_evidence_id: Name | None = None
    sampled_absolute_pressure_and_retained_mechanical_load_reviewed: bool = False
    pretrip_absolute_pressure_identity_tolerance_pa: Positive = 1e-6
    pretrip_volume_flow_identity_tolerance_m3_s: Positive = 1e-12
    pretrip_rotor_speed_identity_tolerance_rad_s: Positive = 1e-9
    pretrip_rotor_work_identity_tolerance_J: Positive = 1e-8


class LiquidPressureTripResult(Contract):
    state: Literal['computed','needs_data','out_of_scope']
    reason: str
    trip_detected: bool = False
    trip_time_s: float | None = None
    trip_sample_index: int | None = None
    sampled_pressure_at_trip_pa_abs: Positive | None = None
    previous_sample_pressure_pa_abs: Positive | None = None
    actual_transient_rerun_performed: bool = False
    pretrip_causal_identity_verified: bool = False
    native_timestep_s: Positive | None = None
    pressure_sampling_stride_steps: int | None = None
    action_ramp_completed_inside_horizon: bool = False
    maximum_pretrip_pressure_difference_pa: float | None = None
    maximum_pretrip_flow_difference_m3_s: float | None = None
    maximum_pretrip_rotor_speed_difference_rad_s: float | None = None
    maximum_pretrip_rotor_work_difference_J: float | None = None
    trip_drive_torque_history: tuple[PumpDrivePoint,...] = ()
    pilot_transient: dict | None = None
    operated_transient: dict | None = None
    continuous_unsampled_threshold_time_solved: bool = False
    field_or_standard_qualified: bool = False


def simulate_pressure_triggered_pump_trip(request:LiquidPressureTripRequest,context:RunContext) -> LiquidPressureTripResult:
    """Apply one sampled torque trip; preserve all actual rotor/MOC dynamics.

    Sampling interval is supplied physical/model data and must be an integer
    multiple of the actual native time step. Changing the numerical mesh does
    not silently change that sampling clock. The torque ramp starts exactly
    at the triggering native sample; previous drive knots are preserved.
    Nonzero active speed-feedback drive needs a separate disconnection model
    and is withheld, rather than retained as an invented post-trip brake.
    """
    def fail(state,reason,**kwargs):return LiquidPressureTripResult(state=state,reason=reason,**kwargs)
    q=request
    if any(v is None for v in (q.target_pump_id,q.sensor_pipe_id,q.absolute_trip_pressure_pa,q.pressure_sampling_interval_s,q.torque_removal_ramp_s,q.post_trip_prescribed_torque_N_m,q.detector_and_action_evidence_id)) or not q.sampled_absolute_pressure_and_retained_mechanical_load_reviewed:
        return fail('needs_data','actual pump/sensor/absolute threshold/sampling/ramp/action and evidence review are required')
    if not math.isfinite(q.post_trip_prescribed_torque_N_m):return fail('out_of_scope','post-trip prescribed torque must be finite')
    evidence=[e.id for e in context.evidence]
    if evidence.count(q.detector_and_action_evidence_id)!=1:raise ValueError('relay/action evidence must be unique in the actual context')
    pumps={p.id:p for p in q.specification.pumps};pipes={p.id:p for p in q.specification.pipes}
    if q.target_pump_id not in pumps or q.sensor_pipe_id not in pipes:raise ValueError('actual pump and pressure-sensor pipe must exist in the supplied native model')
    pump=pumps[q.target_pump_id]
    if pump.drive_speed_feedback_N_m_s!=0:return fail('out_of_scope','active speed feedback needs an explicit disconnection model; pure prescribed-torque trip only')
    pilot=simulate_liquid_transient(q.specification,context)
    if pilot['status']!='computed':return fail('out_of_scope','pilot leaves native liquid/rotor/characteristic support',pilot_transient=pilot)
    dt=float(pilot['timestep_s']);ratio=q.pressure_sampling_interval_s/dt;stride=round(ratio)
    if stride<1 or not math.isclose(ratio,stride,rel_tol=1e-10,abs_tol=1e-10):return fail('out_of_scope','supplied sampling clock must align with the actual native grid; refine input time step',pilot_transient=pilot,native_timestep_s=dt)
    times=pilot['time_s'];pipe=pipes[q.sensor_pipe_id]
    node_id=pipe.start_node if q.sensor_pipe_end=='start' else pipe.end_node
    elevation=next(n.elevation_m for n in q.specification.nodes if n.id==node_id)
    field=q.sensor_pipe_end+'_head_m'
    pressure=[q.specification.ambient_pressure_pa_abs+q.specification.density_kg_m3*q.specification.gravity_m_s2*(h-elevation) for h in pilot['pipes'][q.sensor_pipe_id][field]]
    indices=list(range(0,len(times),stride));trigger=None;previous=None
    for index in indices:
        passed=pressure[index]>=q.absolute_trip_pressure_pa if q.trigger_direction=='at_or_above' else pressure[index]<=q.absolute_trip_pressure_pa
        if passed:trigger=index;break
        previous=pressure[index]
    common=dict(native_timestep_s=dt,pressure_sampling_stride_steps=stride,pilot_transient=pilot)
    if trigger is None:return LiquidPressureTripResult(state='computed',reason='no threshold event at any supplied sampling instant',operated_transient=pilot,**common)
    event=float(times[trigger]);start_torque=pump.drive_at(event)
    points=[p for p in pump.drive_torque_history if p.time_s<event]
    if not points and event>0:points=[PumpDrivePoint(time_s=0.,torque_N_m=pump.drive_at(0.))]
    points.append(PumpDrivePoint(time_s=event,torque_N_m=start_torque))
    ramp_end=event+q.torque_removal_ramp_s
    if not math.isfinite(ramp_end) or ramp_end<=event:return fail('out_of_scope','torque-removal event/ramp time is not representable',**common)
    points.append(PumpDrivePoint(time_s=ramp_end,torque_N_m=q.post_trip_prescribed_torque_N_m))
    if ramp_end<q.specification.duration_s:points.append(PumpDrivePoint(time_s=q.specification.duration_s,torque_N_m=q.post_trip_prescribed_torque_N_m))
    actual_pump=pump.model_copy(update={'drive_torque_N_m':0.,'drive_torque_history':tuple(points)})
    specification=q.specification.model_copy(update={'pumps':tuple(actual_pump if p.id==actual_pump.id else p for p in q.specification.pumps)})
    operated=simulate_liquid_transient(specification,context)
    event_data=dict(trip_detected=True,trip_time_s=event,trip_sample_index=trigger//stride,sampled_pressure_at_trip_pa_abs=pressure[trigger],previous_sample_pressure_pa_abs=previous,actual_transient_rerun_performed=True,trip_drive_torque_history=tuple(points),operated_transient=operated,action_ramp_completed_inside_horizon=ramp_end<=q.specification.duration_s)
    if operated['status']!='computed':return fail('out_of_scope','actual trip rerun leaves native phase/rotor/map/balance support',**common,**event_data)
    if operated['time_s']!=times:raise ValueError('actual trip changed the native clock; pre-event causality cannot be asserted')
    pressure_difference=flow_difference=speed_difference=work_difference=0.
    for key,p in pipes.items():
        for end,node in [('start',p.start_node),('end',p.end_node)]:
            h0=pilot['pipes'][key][end+'_head_m'];h1=operated['pipes'][key][end+'_head_m']
            pressure_difference=max(pressure_difference,max(abs(a-b)*q.specification.density_kg_m3*q.specification.gravity_m_s2 for a,b in zip(h0[:trigger+1],h1[:trigger+1])))
            f0=pilot['pipes'][key][end+'_flow_m3_s'];f1=operated['pipes'][key][end+'_flow_m3_s']
            flow_difference=max(flow_difference,max(abs(a-b) for a,b in zip(f0[:trigger+1],f1[:trigger+1])))
    for key in pumps:
        for a,b in zip(pilot['pumps'][key][:trigger+1],operated['pumps'][key][:trigger+1]):
            speed_difference=max(speed_difference,abs(a['speed_rad_s']-b['speed_rad_s']))
            flow_difference=max(flow_difference,abs(a['volume_flow_m3_s']-b['volume_flow_m3_s']))
            pressure_difference=max(pressure_difference,abs(a['head_gain_m']-b['head_gain_m'])*q.specification.density_kg_m3*q.specification.gravity_m_s2)
            for field in ['drive_work_J','load_work_J','drag_work_J','hydraulic_work_J','map_loss_J','energy_residual_J','rotor_energy_J']:
                work_difference=max(work_difference,abs(a[field]-b[field]))
    verified=pressure_difference<=q.pretrip_absolute_pressure_identity_tolerance_pa and flow_difference<=q.pretrip_volume_flow_identity_tolerance_m3_s and speed_difference<=q.pretrip_rotor_speed_identity_tolerance_rad_s and work_difference<=q.pretrip_rotor_work_identity_tolerance_J
    errors=dict(maximum_pretrip_pressure_difference_pa=pressure_difference,maximum_pretrip_flow_difference_m3_s=flow_difference,maximum_pretrip_rotor_speed_difference_rad_s=speed_difference,maximum_pretrip_rotor_work_difference_J=work_difference,pretrip_causal_identity_verified=verified)
    if not verified:return fail('out_of_scope','actual native pre-trip trajectory differs from its causal pilot',**common,**event_data,**errors)
    return LiquidPressureTripResult(state='computed',reason='sampled pressure trip followed by actual coupled rotor/MOC rerun',**common,**event_data,**errors)
