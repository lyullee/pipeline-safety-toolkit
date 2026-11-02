"""Simultaneous characteristic-node, valve and signed inertial-pump closure."""
from __future__ import annotations

import math

import numpy as np
from pydantic import Field, model_validator

from .contracts import Contract, Name, Positive, Nonnegative
from .rotating_dynamics import SignedRotatingMap, rotating_map_point


class PumpDrivePoint(Contract):
    time_s: Nonnegative
    torque_N_m: float


class LiquidPump(Contract):
    id: Name
    start_node: Name
    end_node: Name
    shaft_id: Name
    performance_map: SignedRotatingMap
    initial_speed_rad_s: float
    initial_volume_flow_m3_s: float
    inertia_kg_m2: Positive
    drive_torque_N_m: float = 0.0
    drive_torque_history: tuple[PumpDrivePoint,...] = Field(default=(),max_length=10000)
    drive_speed_feedback_N_m_s: Nonnegative = 0.0
    viscous_drag_N_m_s: Nonnegative = 0.0
    rotor_and_drive_evidence_id: Name
    colocated_equal_velocity_head_ports_reviewed: bool = False
    initial_drive_equilibrium_required: bool = False
    torque_tolerance_N_m: Positive = 1e-7
    energy_tolerance_J: Positive = 1e-5
    map_power_loss_tolerance_W: Nonnegative = 1e-6

    @model_validator(mode="after")
    def drive_history(self):
        if self.drive_torque_history:
            if self.drive_torque_N_m!=0 or self.drive_torque_history[0].time_s!=0 or any(
                b.time_s<=a.time_s for a,b in zip(self.drive_torque_history,self.drive_torque_history[1:])):
                raise ValueError("use either constant drive torque or an ordered zero-start torque history")
        return self

    def drive_at(self,time_s):
        if not self.drive_torque_history:
            return self.drive_torque_N_m
        return float(np.interp(time_s,[p.time_s for p in self.drive_torque_history],
                               [p.torque_N_m for p in self.drive_torque_history]))


def pump_initial_state(pump, spec, nodes, context):
    if pump.start_node==pump.end_node:
        raise ValueError("pump ports must be distinct nodes")
    if not pump.colocated_equal_velocity_head_ports_reviewed or not pump.performance_map.signed_axes_and_torque_pressure_conventions_reviewed:
        raise ValueError("pump colocated equal-velocity ports and signed map need explicit review")
    if abs(nodes[pump.start_node].elevation_m-nodes[pump.end_node].elevation_m)>spec.initial_head_tolerance_m:
        raise ValueError("this pump boundary requires colocated port elevations")
    evidence=[e.id for e in context.evidence]
    if len(evidence)!=len(set(evidence)) or not {pump.rotor_and_drive_evidence_id,pump.performance_map.evidence_id}<=set(evidence):
        raise ValueError("pump signed map and rotor/drive evidence must be actual context members")
    point=rotating_map_point(pump.performance_map,pump.initial_speed_rad_s,pump.initial_volume_flow_m3_s)
    head=point["pressure_rise_Pa"]/(spec.density_kg_m3*spec.gravity_m_s2)
    if abs(nodes[pump.end_node].initial_head_m-nodes[pump.start_node].initial_head_m-head)>spec.initial_head_tolerance_m:
        raise ValueError("initial pump head gain does not match its actual map operating point")
    loss=point["resisting_torque_N_m"]*pump.initial_speed_rad_s-point["pressure_rise_Pa"]*pump.initial_volume_flow_m3_s
    if loss < -pump.map_power_loss_tolerance_W:
        raise ValueError("initial pump map produces fluid power beyond shaft power")
    if pump.drive_torque_history and pump.drive_torque_history[-1].time_s<spec.duration_s:
        raise ValueError("pump torque history must explicitly cover the complete transient clock")
    torque=pump.drive_at(0.0)-(pump.drive_speed_feedback_N_m_s+pump.viscous_drag_N_m_s)*pump.initial_speed_rad_s-point["resisting_torque_N_m"]
    if pump.initial_drive_equilibrium_required and abs(torque)>pump.torque_tolerance_N_m:
        raise ValueError("initial pump drive torque is not at the requested rotor equilibrium")
    return dict(speed_rad_s=pump.initial_speed_rad_s,volume_flow_m3_s=pump.initial_volume_flow_m3_s,
                head_gain_m=head,drive_work_J=0.0,load_work_J=0.0,drag_work_J=0.0,hydraulic_work_J=0.0,
                map_loss_J=0.0,initial_rotor_J=0.5*pump.inertia_kg_m2*pump.initial_speed_rad_s**2,
                energy_residual_J=0.0)


def solve_pump_nodes(spec,nodes,free_heads,coefficients,forcings,time_s,dt,previous_states):
    """Close every active valve and pump against the same incoming characteristics.

    Endpoint head/flow satisfy the pump map at the new speed. Rotor torque/work
    use midpoint speed and flow, so angular momentum and kinetic work share a
    time rule. The map is evaluated at both states without extrapolation.
    """
    try:
        from scipy.optimize import least_squares
    except ImportError as exc:
        raise ImportError("Pump-node coupling requires the engineering extra (scipy)") from exc

    pumps=spec.pumps
    active=[(v,v.effective_area_m2.at(time_s)) for v in spec.valves if v.effective_area_m2.at(time_s)>0]
    names=sorted({key for component in (*pumps,*[v for v,_ in active]) for key in (component.start_node,component.end_node)
                  if nodes[key].boundary=="withdrawal"})
    indices={name:i for i,name in enumerate(names)}
    nv,npump=len(active),len(pumps)
    size=len(names)+nv+2*npump
    if size>spec.maximum_valve_unknowns:
        raise ValueError("simultaneous pump/valve/junction unknown budget exceeded")
    head_scale=max(1.0,*[abs(v) for v in free_heads.values()])
    flow_scale=max(1e-10,*[abs(v) for v in forcings.values()],*[abs(p.initial_volume_flow_m3_s) for p in spec.pipes],
                   *[abs(p.initial_volume_flow_m3_s) for p in pumps])
    flow_scale=2.0**math.floor(math.log2(flow_scale))
    speed_scales=[2.0**math.floor(math.log2(max(1.0,abs(p.performance_map.speed_rad_s[0]),abs(p.performance_map.speed_rad_s[-1])))) for p in pumps]
    torque_scales=[max(1.0,abs(p.drive_at(time_s-0.5*dt)),*[abs(v) for row in p.performance_map.resisting_torque_N_m for v in row],
                       p.inertia_kg_m2*speed_scales[i]/dt) for i,p in enumerate(pumps)]
    lower=[-np.inf]*size
    upper=[np.inf]*size
    x0=[free_heads[name]/head_scale for name in names]
    # Valve flow is scaled by one shared SI flow scale, including at zero head.
    x0.extend(v.initial_volume_flow_m3_s/flow_scale for v,_ in active)
    qoffset=len(names)+nv
    soffset=qoffset+npump
    for i,pump in enumerate(pumps):
        x0.append(previous_states[pump.id]["volume_flow_m3_s"]/flow_scale)
        lower[qoffset+i]=pump.performance_map.volume_flow_m3_s[0]/flow_scale
        upper[qoffset+i]=pump.performance_map.volume_flow_m3_s[-1]/flow_scale
    for i,pump in enumerate(pumps):
        x0.append(previous_states[pump.id]["speed_rad_s"]/speed_scales[i])
        lower[soffset+i]=pump.performance_map.speed_rad_s[0]/speed_scales[i]
        upper[soffset+i]=pump.performance_map.speed_rad_s[-1]/speed_scales[i]
    def unpack(x):
        heads=free_heads.copy()
        heads.update({name:float(x[i]*head_scale) for name,i in indices.items()})
        valves=[float(x[len(names)+i]*flow_scale) for i in range(nv)]
        flows=[float(x[qoffset+i]*flow_scale) for i in range(npump)]
        speeds=[float(x[soffset+i]*speed_scales[i]) for i in range(npump)]
        return heads,valves,flows,speeds
    evaluations=0
    def residual(x):
        nonlocal evaluations
        evaluations+=1
        if evaluations>spec.maximum_pump_residual_evaluations:
            raise ValueError("simultaneous pump residual-evaluation budget exceeded")
        heads,valves,flows,speeds=unpack(x)
        values=np.zeros(size)
        for name,i in indices.items():
            node=nodes[name]
            emission=node.atmospheric_emitter.at(time_s)*math.sqrt(max(heads[name]-node.elevation_m,0.0))
            values[i]=(coefficients[name]*heads[name]-forcings[name]+node.history.at(time_s)+emission)/flow_scale
        for component,flow in zip((*[v for v,_ in active],*pumps),(*valves,*flows)):
            for name,sign in ((component.start_node,1),(component.end_node,-1)):
                if name in indices:
                    values[indices[name]]+=sign*flow/flow_scale
        for i,((valve,area),flow) in enumerate(zip(active,valves)):
            values[len(names)+i]=(heads[valve.start_node]-heads[valve.end_node]
                -flow*abs(flow)/(2*spec.gravity_m_s2*area**2))/head_scale
        for i,(pump,flow,speed) in enumerate(zip(pumps,flows,speeds)):
            old=previous_states[pump.id]
            midpoint=0.5*(old["speed_rad_s"]+speed)
            midflow=0.5*(old["volume_flow_m3_s"]+flow)
            endpoint=rotating_map_point(pump.performance_map,speed,flow)
            point=rotating_map_point(pump.performance_map,midpoint,midflow)
            values[qoffset+i]=(heads[pump.end_node]-heads[pump.start_node]
                -endpoint["pressure_rise_Pa"]/(spec.density_kg_m3*spec.gravity_m_s2))/head_scale
            values[soffset+i]=(pump.inertia_kg_m2*(speed-old["speed_rad_s"])/dt-pump.drive_at(time_s-0.5*dt)
                +(pump.drive_speed_feedback_N_m_s+pump.viscous_drag_N_m_s)*midpoint+point["resisting_torque_N_m"])/torque_scales[i]
        return values
    solution=least_squares(residual,np.asarray(x0),bounds=(np.asarray(lower),np.asarray(upper)),
        ftol=1e-12,xtol=1e-12,gtol=1e-12,max_nfev=spec.maximum_valve_solver_evaluations,
        method="trf",x_scale="jac")
    root_x=solution.x.copy()
    errors=residual(root_x)
    def physical_budget_ratio(values):
        ratios=[float(np.max(np.abs(values[:len(names)]),initial=0.0)*flow_scale)/spec.valve_flow_tolerance_m3_s,
                float(np.max(np.abs(values[len(names):soffset]),initial=0.0)*head_scale)/spec.valve_head_tolerance_m]
        ratios.extend(abs(float(values[soffset+i]*torque_scales[i]))/p.torque_tolerance_N_m for i,p in enumerate(pumps))
        return max(ratios)
    initial_ratio=physical_budget_ratio(errors)
    ratio=initial_ratio
    refinement_steps=refinement_evaluations=0
    # Optimizer gradient tolerances are dimensionless. At a fine time step
    # inertia can make their torque scale much larger than the requested
    # physical torque error budget. Correct only an actually unqualified
    # residual; leave every already-qualified iterate numerically unchanged.
    if ratio>1.0 and np.isfinite(root_x).all():
        jacobian=np.asarray(solution.jac,dtype=float)
        for _ in range(4):
            try:correction,_,rank,_=np.linalg.lstsq(jacobian,-errors,rcond=None)
            except np.linalg.LinAlgError:break
            if rank<size or not np.isfinite(correction).all():break
            accepted=False
            fraction=1.0
            for _ in range(16):
                trial=root_x+fraction*correction
                if np.any(trial<lower) or np.any(trial>upper):
                    fraction*=0.5
                    continue
                trial_errors=residual(trial)
                refinement_evaluations+=1
                trial_ratio=physical_budget_ratio(trial_errors)
                if trial_ratio<ratio:
                    root_x,errors,ratio=trial,trial_errors,trial_ratio
                    refinement_steps+=1
                    accepted=True
                    break
                fraction*=0.5
            if not accepted or ratio<=1.0:break
    heads,valve_values,flows,speeds=unpack(root_x)
    flow_error=float(np.max(np.abs(errors[:len(names)]),initial=0.0)*flow_scale)
    head_error=float(np.max(np.abs(errors[len(names):soffset]),initial=0.0)*head_scale)
    qualified=bool(np.isfinite(root_x).all() and flow_error<=spec.valve_flow_tolerance_m3_s
                   and head_error<=spec.valve_head_tolerance_m)
    states={}
    step_audits={}
    for i,(pump,flow,speed) in enumerate(zip(pumps,flows,speeds)):
        old=previous_states[pump.id]
        midpoint=0.5*(old["speed_rad_s"]+speed)
        midflow=0.5*(old["volume_flow_m3_s"]+flow)
        point=rotating_map_point(pump.performance_map,midpoint,midflow)
        endpoint=rotating_map_point(pump.performance_map,speed,flow)
        drive=(pump.drive_at(time_s-0.5*dt)-pump.drive_speed_feedback_N_m_s*midpoint)*midpoint*dt
        load=point["resisting_torque_N_m"]*midpoint*dt
        drag=pump.viscous_drag_N_m_s*midpoint**2*dt
        hydraulic=point["pressure_rise_Pa"]*midflow*dt
        delta=0.5*pump.inertia_kg_m2*(speed-old["speed_rad_s"])*(speed+old["speed_rad_s"])
        error=math.fsum((drive,-load,-drag,-delta))
        torque_error=abs(float(errors[soffset+i]*torque_scales[i]))
        passive=(load-hydraulic)/dt>=-pump.map_power_loss_tolerance_W and (
            endpoint["resisting_torque_N_m"]*speed-endpoint["pressure_rise_Pa"]*flow>=-pump.map_power_loss_tolerance_W)
        accumulated={key:old[key]+value for key,value in (("drive_work_J",drive),("load_work_J",load),
            ("drag_work_J",drag),("hydraulic_work_J",hydraulic),("map_loss_J",load-hydraulic))}
        cumulative_error=math.fsum((old["initial_rotor_J"],accumulated["drive_work_J"],-accumulated["load_work_J"],
            -accumulated["drag_work_J"],-0.5*pump.inertia_kg_m2*speed**2))
        qualified=qualified and torque_error<=pump.torque_tolerance_N_m and abs(error)<=pump.energy_tolerance_J and abs(cumulative_error)<=pump.energy_tolerance_J and passive
        states[pump.id]=dict(speed_rad_s=speed,volume_flow_m3_s=flow,head_gain_m=heads[pump.end_node]-heads[pump.start_node],
            initial_rotor_J=old["initial_rotor_J"],**accumulated,energy_residual_J=cumulative_error)
        step_audits[pump.id]=dict(midpoint_speed_rad_s=midpoint,midpoint_flow_m3_s=midflow,torque_residual_N_m=torque_error,
            energy_residual_J=error,cumulative_energy_residual_J=cumulative_error,map_passive=passive,
            drive_work_J=drive,load_work_J=load,drag_work_J=drag,hydraulic_work_J=hydraulic)
    valve_flows=dict.fromkeys((v.id for v in spec.valves),0.0)
    valve_flows.update({v.id:flow for (v,_),flow in zip(active,valve_values)})
    return heads,valve_flows,states,dict(time_s=time_s,root_qualified=bool(qualified),
        method="scipy_trf_simultaneous_pump_rotor_valve_characteristics",function_evaluations=int(solution.nfev)+refinement_evaluations,
        residual_evaluations=evaluations,
        jacobian_evaluations=int(solution.njev or 0),optimizer_success=bool(solution.success),
        maximum_flow_residual_m3_s=flow_error,maximum_head_residual_m=head_error,unknowns=size,pumps=step_audits,
        physical_residual_refinement=dict(accepted_steps=refinement_steps,residual_evaluations=refinement_evaluations,
            initial_maximum_budget_ratio=initial_ratio if math.isfinite(initial_ratio) else None,
            final_maximum_budget_ratio=ratio if math.isfinite(ratio) else None))
