"""Discrete supplied gas/vapor cavities coupled to the shared liquid MOC.

A reservoir, one surveyed liquid pipe and a prescribed downstream liquid flow
form the supported path. Each nonreservoir grid section has a supplied positive
noncondensable gas inventory. Its pressure is vapor pressure plus a polytropic
gas partial pressure. Cavity continuity and both incident characteristics are
solved together; pressure is never clipped to vapor pressure and a disappearing
gas seed is never replaced by an artificial zero-volume collapse.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from pydantic import Field, model_validator

from .contracts import Contract, Name, Positive, Nonnegative, RunContext, SignalFrame, digest
from .datasets import file_digest
from .moc_characteristics import characteristic_invariants
from .transient import LiquidTransient


class CavityMOCRequest(Contract):
    id: Name
    asset_id: Name
    clock_id: Name
    start_time_s: Nonnegative = 0.0
    liquid: LiquidTransient
    initial_cavity_volume_m3: tuple[Positive, ...] = Field(min_length=2,max_length=10000)
    initial_gas_temperature_K: tuple[Positive, ...] = Field(min_length=2,max_length=10000)
    polytropic_exponent: tuple[Positive, ...] = Field(min_length=2,max_length=10000)
    noncondensable_specific_gas_constant_J_kg_K: Positive
    minimum_cavity_volume_m3: Positive
    maximum_local_void_fraction: Positive = Field(default=0.1,le=0.1)
    maximum_supported_absolute_pressure_Pa: Positive
    absolute_cavity_volume_tolerance_m3: Positive
    relative_cavity_volume_tolerance: Positive = Field(default=1e-9,le=1e-3)
    relative_fixed_gas_mass_tolerance: Positive = Field(default=1e-10,le=1e-3)
    absolute_global_liquid_volume_tolerance_m3: Positive
    relative_global_liquid_volume_tolerance: Positive = Field(default=1e-7,le=1e-3)
    root_log_volume_absolute_tolerance: Positive = Field(default=1e-12,le=1e-4)
    maximum_root_iterations: int = Field(default=100,ge=1,le=1000)
    maximum_root_evaluations: int = Field(default=1000000,ge=1,le=100000000)
    maximum_retained_values: int = Field(default=2000000,ge=100,le=20000000)
    maximum_result_bytes: int = Field(default=64000000,ge=1000,le=256000000)
    parameter_evidence_id: Name
    gas_inventory_evidence_id: Name
    closure_review_evidence_id: Name
    supplied_fixed_gas_locations_amount_and_polytropic_response_reviewed: bool = False
    constant_liquid_vapor_pressure_and_negligible_vapor_mass_reviewed: bool = False
    small_void_fraction_acoustic_liquid_and_omitted_slip_reviewed: bool = False

    @model_validator(mode="after")
    def scope(self):
        s = self.liquid
        if len(s.pipes) != 1 or len(s.nodes) != 2 or s.valves or s.pumps:
            raise ValueError("discrete cavity MOC requires exactly one pipe, two boundary nodes and no separate valves/pumps")
        pipe = s.pipes[0]
        nodes = {n.id:n for n in s.nodes}
        if nodes[pipe.start_node].boundary != "head" or nodes[pipe.end_node].boundary != "withdrawal":
            raise ValueError("cavity path requires upstream reservoir head and downstream prescribed liquid withdrawal")
        if any(any(v != 0 for v in node.atmospheric_emitter.values) for node in s.nodes):
            raise ValueError("cavity MOC does not infer atmospheric gas ingress through emitters")
        ratio=s.duration_s/s.requested_timestep_s
        if not math.isfinite(ratio) or ratio>s.maximum_steps:
            raise ValueError("cavity requested clock exceeds its finite step budget")
        count = max(1,math.ceil(ratio))
        dt = s.duration_s/count
        travel=pipe.wave_speed_m_s*dt
        if not math.isfinite(travel) or travel<=0:
            raise ValueError("cavity characteristic travel distance is not positively representable")
        segments_ratio=pipe.length_m/travel
        if not math.isfinite(segments_ratio) or segments_ratio>s.maximum_total_segments+0.5:
            raise ValueError("cavity characteristic segment count exceeds its finite mesh budget")
        sections = round(segments_ratio)
        if sections < 2 or sections > s.maximum_total_segments:
            raise ValueError("cavity CFL mesh is outside the supplied segment budget")
        if any(len(values) != sections for values in (self.initial_cavity_volume_m3,self.initial_gas_temperature_K,self.polytropic_exponent)):
            raise ValueError("cavity inputs must identify each downstream grid section, excluding the reservoir")
        if any(not 1 <= exponent <= 2 for exponent in self.polytropic_exponent):
            raise ValueError("each supplied polytropic exponent must lie between one and two")
        if self.maximum_supported_absolute_pressure_Pa <= s.vapour_pressure_pa_abs:
            raise ValueError("cavity pressure support must exceed the supplied vapor pressure")
        if (count+1)*(13*sections+20) > self.maximum_retained_values or count > s.maximum_steps:
            raise ValueError("discrete cavity history/step budget exceeded")
        return self


def cavity_moc_metadata():
    return {name:file_digest(Path(__file__).with_name(name+".py"))
            for name in ("cavity_moc","moc_characteristics","transient")}


def evaluate_cavity_moc(request: CavityMOCRequest,context: RunContext):
    import numpy as np
    import scipy
    from scipy.optimize import brentq
    q = CavityMOCRequest.model_validate(request.model_dump(mode="python"))
    c = RunContext.model_validate(context.model_dump(mode="python"))
    if (q.asset_id,q.clock_id) != (c.asset_id,c.clock_id):
        raise ValueError("cavity MOC asset/clock differs from context")
    if not all((q.supplied_fixed_gas_locations_amount_and_polytropic_response_reviewed,
                q.constant_liquid_vapor_pressure_and_negligible_vapor_mass_reviewed,
                q.small_void_fraction_acoustic_liquid_and_omitted_slip_reviewed)):
        raise ValueError("supplied gas inventory, vapor-pressure/mass and acoustic/void-fraction reviews are required")
    ids = [e.id for e in c.evidence]
    required = {q.parameter_evidence_id,q.gas_inventory_evidence_id,q.closure_review_evidence_id}
    evidence = {e.id:e for e in c.evidence}
    if len(ids) != len(set(ids)) or not required <= set(ids) or any(evidence[key].sha256 is None for key in required):
        raise ValueError("cavity geometry/gas/closure evidence requires unambiguous source SHA-256 pins")
    pins,q_pin,c_pin = cavity_moc_metadata(),digest(q),digest(c)
    s = q.liquid
    pipe = s.pipes[0]
    nodes = {n.id:n for n in s.nodes}
    reservoir,outlet = nodes[pipe.start_node],nodes[pipe.end_node]
    count = max(1,math.ceil(s.duration_s/s.requested_timestep_s))
    dt = s.duration_s/count
    for history in (reservoir.history,outlet.history):
        for knot in history.time_s:
            if knot<=s.duration_s:
                tick=knot/dt
                if not math.isfinite(tick) or abs(tick-round(tick))>128*np.finfo(float).eps*max(1.0,abs(tick)):
                    raise ValueError("cavity boundary-history knots must lie on the actual MOC clock; refine its step")
    sections = round(pipe.length_m/(pipe.wave_speed_m_s*dt))
    dx = pipe.length_m/sections
    wave = dx/dt
    wave_adjustment = abs(wave/pipe.wave_speed_m_s-1)
    if wave_adjustment > s.maximum_wave_speed_adjustment+1e-14:
        raise ValueError("cavity MOC wave-speed adjustment exceeds the reviewed bound")
    area = (math.pi/4)*pipe.diameter_m*pipe.diameter_m
    impedance_denominator=s.gravity_m_s2*area
    resistance_denominator=2*s.gravity_m_s2*pipe.diameter_m*area*area
    if not all(math.isfinite(value) and value>0 for value in (area,impedance_denominator,resistance_denominator)):
        raise ValueError("cavity pipe area or characteristic denominators exceed finite positive arithmetic")
    b = wave/impedance_denominator
    resistance = pipe.darcy_friction_factor*dx/resistance_denominator
    head = np.linspace(reservoir.initial_head_m,outlet.initial_head_m,sections+1)
    initial_head=head.copy()
    if abs(head[0]-head[-1]-resistance*sections*pipe.initial_volume_flow_m3_s*abs(pipe.initial_volume_flow_m3_s)) > s.initial_head_tolerance_m:
        raise ValueError("initial cavity liquid head/flow gradient is not in Darcy equilibrium")
    if abs(outlet.history.at(0)-pipe.initial_volume_flow_m3_s) > s.initial_flow_tolerance_m3_s:
        raise ValueError("initial cavity outlet withdrawal and pipe flow differ")
    chainage = np.linspace(0,pipe.length_m,sections+1)
    elevation = (np.interp(chainage,[p.chainage_m for p in pipe.elevation_profile],[p.elevation_m for p in pipe.elevation_profile])
                 if pipe.elevation_profile else np.linspace(reservoir.elevation_m,outlet.elevation_m,sections+1))
    density_gravity = s.density_kg_m3*s.gravity_m_s2
    if not all(math.isfinite(value) and value>0 for value in (dt,dx,wave,area,b,density_gravity)) or not math.isfinite(resistance) or resistance<0:
        raise ValueError("cavity mesh, impedance, resistance and pressure-head scales exceed finite positive arithmetic")
    for time in (*[t for t in reservoir.history.time_s if t<=s.duration_s],s.duration_s):
        boundary_pressure=s.ambient_pressure_pa_abs+density_gravity*(reservoir.history.at(time)-reservoir.elevation_m)
        if not s.vapour_pressure_pa_abs<boundary_pressure<=q.maximum_supported_absolute_pressure_Pa:
            raise ValueError("a supplied reservoir history knot leaves the supported single-phase pressure domain")
    pressure0 = s.ambient_pressure_pa_abs+density_gravity*(head-elevation)
    gas_pressure0 = pressure0[1:]-s.vapour_pressure_pa_abs
    if np.any(pressure0 <= s.vapour_pressure_pa_abs) or np.any(pressure0 > q.maximum_supported_absolute_pressure_Pa):
        raise ValueError("every initial cavity requires a positive gas partial pressure inside support")
    gas_v0 = np.asarray(q.initial_cavity_volume_m3)
    gas_temperature0 = np.asarray(q.initial_gas_temperature_K)
    exponents = np.asarray(q.polytropic_exponent)
    volume = gas_v0.copy()
    dual_volume = np.full(sections,area*dx)
    dual_volume[-1] *= 0.5
    upper_volume = q.maximum_local_void_fraction*dual_volume
    log_v0 = np.log(gas_v0)
    log_pg0 = np.log(gas_pressure0)
    pressure_limited_minimum = np.exp(log_v0+(log_pg0-math.log(q.maximum_supported_absolute_pressure_Pa-s.vapour_pressure_pa_abs))/exponents)
    lower_volume = np.maximum(q.minimum_cavity_volume_m3,pressure_limited_minimum)
    if np.any(gas_v0 < lower_volume) or np.any(gas_v0 > upper_volume) or np.any(lower_volume >= upper_volume):
        raise ValueError("initial cavity inventory/pressure is outside the positive-volume and void-fraction support")
    initial_gas_mass = gas_pressure0*gas_v0/(q.noncondensable_specific_gas_constant_J_kg_K*gas_temperature0)
    if not np.all(np.isfinite(initial_gas_mass)) or np.any(initial_gas_mass <= 0):
        raise ValueError("initial cavity gas mass is outside finite positive arithmetic")
    flow_left = np.full(sections+1,pipe.initial_volume_flow_m3_s)
    flow_right = flow_left.copy()
    rows = []
    accepted_steps = []
    root_evaluations = 0
    integrated_exchange = 0.0
    maximum_mach = maximum_strain = 0.0
    if abs(pipe.initial_volume_flow_m3_s)/(area*wave)>s.maximum_acoustic_mach:
        raise ValueError("initial cavity flow exceeds the supplied acoustic Mach domain")
    retained_bytes = 0
    failure = None

    def gas_state(volumes):
        pg = np.exp(log_pg0+exponents*(log_v0-np.log(volumes)))
        gas_t = gas_temperature0*np.exp((exponents-1)*(log_v0-np.log(volumes)))
        gas_mass = pg*volumes/(q.noncondensable_specific_gas_constant_J_kg_K*gas_t)
        if (not np.all(np.isfinite(pg)) or not np.all(np.isfinite(gas_t)) or not np.all(np.isfinite(gas_mass))
                or np.any(gas_t <= 0) or np.any(gas_mass <= 0)):
            raise ValueError("cavity gas law left finite arithmetic")
        if np.any(np.abs(gas_mass-initial_gas_mass) > q.relative_fixed_gas_mass_tolerance*initial_gas_mass):
            raise ValueError("cavity gas law failed the fixed noncondensable gas-mass budget")
        log_ratio = np.log(volumes)-log_v0
        work = np.empty(sections)
        isothermal = exponents == 1
        work[isothermal] = gas_pressure0[isothermal]*gas_v0[isothermal]*log_ratio[isothermal]
        other = ~isothermal
        work[other] = (gas_pressure0[other]*gas_v0[other]
                       *np.expm1((1-exponents[other])*log_ratio[other])/(1-exponents[other]))
        if not np.all(np.isfinite(work)):
            raise ValueError("cavity gas pressure work exceeds finite arithmetic")
        return pg,gas_t,gas_mass,work

    def state(time):
        nonlocal maximum_mach,maximum_strain
        partial,gas_t,gas_mass,work = gas_state(volume)
        pressure = s.ambient_pressure_pa_abs+density_gravity*(head-elevation)
        liquid_storage = s.gravity_m_s2*area/wave**2*float(np.trapezoid(head-initial_head,dx=dx))
        cavity_change = float(np.sum(volume-gas_v0))
        residual = liquid_storage-cavity_change-integrated_exchange
        maximum_mach = max(maximum_mach,float(max(np.max(np.abs(flow_left)),np.max(np.abs(flow_right)))/(area*wave)))
        maximum_strain = max(maximum_strain,float(s.gravity_m_s2*np.max(np.abs(head-initial_head))/wave**2))
        return dict(time_s=q.start_time_s+time,head_m=head.tolist(),absolute_pressure_Pa=pressure.tolist(),
                    flow_from_left_m3_s=flow_left.tolist(),flow_to_right_m3_s=flow_right.tolist(),
                    cavity_volume_m3=volume.tolist(),gas_partial_pressure_Pa=partial.tolist(),
                    gas_temperature_K=gas_t.tolist(),gas_mass_residual_kg=(gas_mass-initial_gas_mass).tolist(),
                    gas_partial_pressure_expansion_work_J=work.tolist(),
                    fixed_vapor_pressure_expansion_work_J=(s.vapour_pressure_pa_abs*(volume-gas_v0)).tolist(),
                    local_void_fraction=(volume/dual_volume).tolist(),
                    linearized_liquid_pressure_storage_change_m3=liquid_storage,
                    total_cavity_volume_change_m3=cavity_change,integrated_external_liquid_inflow_m3=integrated_exchange,
                    liquid_volume_residual_m3=residual)

    rows.append(state(0.0))
    retained_bytes = len(json.dumps(rows).encode("utf-8"))
    try:
        for step in range(1,count+1):
            time = step*dt
            if q.start_time_s+time <= rows[-1]["time_s"]:
                raise ValueError("cavity MOC step is not representable on the declared clock")
            if resistance*max(float(np.max(np.abs(flow_left))),float(np.max(np.abs(flow_right))))/b >= 0.5:
                raise ValueError("cavity friction source is too stiff for the characteristic step")
            plus,minus,start_incident,end_incident = characteristic_invariants(head,flow_right,flow_left,b,resistance)
            new_head = head.copy()
            new_left,new_right = flow_left.copy(),flow_right.copy()
            new_volume = volume.copy()
            old_jump = flow_right[1:]-flow_left[1:]
            closure_residuals = []
            for j in range(sections):
                grid_index = j+1
                cp = float(plus[j]) if j < sections-1 else float(end_incident)
                cm = float(minus[j]) if j < sections-1 else None
                outgoing = outlet.history.at(time) if cm is None else None

                def continuity(log_v):
                    nonlocal root_evaluations
                    root_evaluations += 1
                    if root_evaluations > q.maximum_root_evaluations:
                        raise ValueError("cavity scalar-root evaluation budget exhausted")
                    trial_volume = math.exp(log_v)
                    partial = math.exp(float(log_pg0[j]+exponents[j]*(log_v0[j]-log_v)))
                    h = elevation[grid_index]+(s.vapour_pressure_pa_abs+partial-s.ambient_pressure_pa_abs)/density_gravity
                    incoming_flow = (cp-h)/b
                    outgoing_flow = (h-cm)/b if cm is not None else outgoing
                    # Trapezoidal cavity continuity uses the same time weights
                    # as the independently audited external liquid exchange.
                    return (trial_volume-volume[j]-0.5*dt*(old_jump[j]+outgoing_flow-incoming_flow))/gas_v0[j]

                lo,hi = math.log(float(lower_volume[j])),math.log(float(upper_volume[j]))
                flo,fhi = continuity(lo),continuity(hi)
                if flo > 0 or fhi < 0:
                    raise ValueError("cavity continuity root leaves pressure/positive-volume/void-fraction support")
                root = (lo if flo == 0 else hi if fhi == 0 else brentq(continuity,lo,hi,
                        xtol=q.root_log_volume_absolute_tolerance,rtol=4*np.finfo(float).eps,
                        maxiter=q.maximum_root_iterations))
                v = math.exp(root)
                if not math.isfinite(v) or v<=0 or v<q.minimum_cavity_volume_m3 or v>float(upper_volume[j]):
                    raise ValueError("recovered cavity root leaves the actual positive-volume/void-fraction support")
                pg = math.exp(float(log_pg0[j]+exponents[j]*(log_v0[j]-root)))
                h = elevation[grid_index]+(s.vapour_pressure_pa_abs+pg-s.ambient_pressure_pa_abs)/density_gravity
                qi = (cp-h)/b
                qo = (h-cm)/b if cm is not None else outgoing
                residual_volume = v-volume[j]-0.5*dt*(old_jump[j]+qo-qi)
                tolerance = q.absolute_cavity_volume_tolerance_m3+q.relative_cavity_volume_tolerance*max(
                    abs(v-volume[j]),0.5*dt*(abs(old_jump[j])+abs(qo)+abs(qi)))
                if not math.isfinite(residual_volume) or abs(residual_volume) > tolerance:
                    raise ValueError("cavity root did not satisfy its independently recovered volume balance")
                new_volume[j],new_head[grid_index],new_left[grid_index],new_right[grid_index] = v,h,qi,qo
                closure_residuals.append(float(residual_volume))
            new_head[0] = reservoir.history.at(time)
            new_left[0] = new_right[0] = (new_head[0]-start_incident)/b
            new_pressure = s.ambient_pressure_pa_abs+density_gravity*(new_head-elevation)
            if np.any(new_pressure <= s.vapour_pressure_pa_abs) or np.any(new_pressure > q.maximum_supported_absolute_pressure_Pa):
                raise ValueError("cavity/reservoir pressure leaves the reviewed positive-gas and maximum-pressure support")
            new_exchange = integrated_exchange+0.5*dt*((flow_right[0]-flow_right[-1])+(new_right[0]-new_right[-1]))
            new_storage = s.gravity_m_s2*area/wave**2*float(np.trapezoid(new_head-initial_head,dx=dx))
            new_cavity_change = float(np.sum(new_volume-gas_v0))
            global_residual = new_storage-new_cavity_change-new_exchange
            global_tolerance = q.absolute_global_liquid_volume_tolerance_m3+q.relative_global_liquid_volume_tolerance*max(
                abs(new_storage),abs(new_cavity_change),abs(new_exchange))
            if not math.isfinite(global_residual) or abs(global_residual) > global_tolerance:
                raise ValueError("cavity MOC failed the global linearized-liquid storage/void/exchange budget")
            mach = float(max(np.max(np.abs(new_left)),np.max(np.abs(new_right)))/(area*wave))
            strain = float(s.gravity_m_s2*np.max(np.abs(new_head-initial_head))/wave**2)
            if mach > s.maximum_acoustic_mach or strain > s.maximum_linearized_storage_strain:
                raise ValueError("cavity MOC exceeded the reviewed acoustic Mach/storage-strain domain")
            gas_state(new_volume)
            step_receipt = dict(time_s=q.start_time_s+time,cavity_volume_residual_m3=closure_residuals,
                                global_liquid_volume_residual_m3=global_residual,
                                cavity_root_time_rule="implicit_endpoint_pressure_with_trapezoidal_volume_continuity")
            estimate = len(json.dumps(step_receipt).encode("utf-8"))+len(json.dumps(rows[-1]).encode("utf-8"))+200*sections
            if retained_bytes+estimate > q.maximum_result_bytes:
                raise ValueError("cavity retained trajectory exceeds the result-byte budget")
            head,flow_left,flow_right,volume = new_head,new_left,new_right,new_volume
            integrated_exchange = new_exchange
            rows.append(state(time))
            accepted_steps.append(step_receipt)
            retained_bytes += estimate
    except (ValueError,RuntimeError,ArithmeticError) as error:
        failure = type(error).__name__+": "+str(error)
    if cavity_moc_metadata() != pins or digest(q) != q_pin or digest(c) != c_pin:
        raise ValueError("cavity MOC source/request/context changed during evaluation")
    body = dict(schema="pipeline_integrity.cavity_moc.v1",request=q.model_dump(mode="json"),
                context=c.model_dump(mode="json"),status="withheld" if failure else "computed_diagnostic",
                failure=failure,implementation=pins,states=rows,steps=accepted_steps,
                grid=dict(chainage_m=chainage.tolist(),elevation_m=elevation.tolist(),segments=sections,
                          actual_step_s=dt,adjusted_wave_speed_m_s=wave,relative_wave_speed_adjustment=wave_adjustment),
                initial_noncondensable_gas_mass_kg=initial_gas_mass.tolist(),root_evaluations=root_evaluations,
                maximum_acoustic_mach=maximum_mach,maximum_linearized_storage_strain=maximum_strain,
                final_liquid_volume_residual_m3=rows[-1]["liquid_volume_residual_m3"],
                gas_mass_fixed_at_each_grid_section=True,gas_migration_or_dissolution_solved=False,
                exact_zero_volume_collapse_solved=False,phase_slip_or_two_fluid_momentum_solved=False,
                vapor_mass_or_latent_energy_conservation_solved=False,
                mechanical_work_meaning="integral_of_each_gas_partial_pressure_dV_and_separate_fixed_vapor_pressure_dV",
                gas_liquid_heat_and_total_energy_balance_solved=False,
                theoretical_sources=["https://doi.org/10.1016/j.jfluidstructs.2005.08.008",
                                     "https://hdl.handle.net/2440/77276"],
                scipy_version=scipy.__version__,numpy_version=np.__version__,
                polytropic_work_is_not_independent_heat_balance=True,numerical_validation_completed=False)
    if len(json.dumps(body).encode("utf-8")) > q.maximum_result_bytes:
        raise ValueError("complete cavity MOC receipt exceeds result-byte budget")
    body["record_digest"] = digest(body)
    return body


def verify_cavity_moc_record(record, *, request=None, context=None):
    if digest({k:v for k,v in record.items() if k != "record_digest"}) != record.get("record_digest"):
        raise ValueError("cavity MOC receipt changed")
    q = CavityMOCRequest.model_validate_json(json.dumps(record["request"]))
    c = RunContext.model_validate_json(json.dumps(record["context"]))
    if request is not None and request.model_dump(mode="json") != q.model_dump(mode="json"):
        raise ValueError("cavity MOC caller request differs")
    if context is not None and context.model_dump(mode="json") != c.model_dump(mode="json"):
        raise ValueError("cavity MOC caller context differs")
    if record.get("status") == "withheld":
        raise ValueError("withheld cavity MOC states do not establish a completed replay")
    if evaluate_cavity_moc(q,c) != record:
        raise ValueError("cavity MOC replay differs")
    return True


def cavity_moc_signal_frames(record, *, section_index: int, stream_id: str):
    verify_cavity_moc_record(record)
    q = CavityMOCRequest.model_validate_json(json.dumps(record["request"]))
    if type(section_index) is not int or not 1 <= section_index <= record["grid"]["segments"]:
        raise ValueError("cavity signal section must identify a nonreservoir cavity grid point")
    channels = {"absolute_pressure_Pa":"Pa","head_m":"m","cavity_volume_m3":"m^3",
                "gas_partial_pressure_Pa":"Pa","gas_temperature_K":"K"}
    frames = tuple(SignalFrame(stream_id=stream_id,clock_id=q.clock_id,time_s=row["time_s"],origin="simulation",
        values={key:row[key][section_index if key in {"absolute_pressure_Pa","head_m"} else section_index-1]
                for key in channels},units=channels,valid={key:True for key in channels},
        operating_mode="supplied_discrete_gas_cavity") for row in record["states"])
    receipt = dict(schema="pipeline_integrity.cavity_moc_frames.v1",source_record_digest=record["record_digest"],
                   section_index=section_index,stream_id=stream_id,clock_id=q.clock_id,asset_id=q.asset_id,
                   frames_digest=digest(frames),origin="simulation",pressure_reference="absolute")
    receipt["record_digest"] = digest(receipt)
    return frames,receipt
