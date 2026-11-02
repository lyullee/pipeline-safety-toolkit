"""Quasi-steady liquid drainage using tank geometry and the existing Darcy owner.

The liquid is incompressible and gas/backpressure boundaries are prescribed.
An implicit-midpoint volume balance advances storage; pipe inertia, flashing,
vortex entrainment and gas-pressure feedback are outside this closure.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Literal
from pydantic import Field
from .contracts import Contract, Name, Positive, Nonnegative, RunContext, digest
from .datasets import file_digest
from .tanks import TankGeometry, _shape
from .models.hydraulics import LiquidPressureLoss, liquid_pressure_loss


class DrainageRequest(Contract):
    id: Name
    asset_id: Name
    clock_id: Name
    geometry: TankGeometry
    initial_volume_m3: Positive
    stop_volume_m3: Positive
    start_time_s: Nonnegative = 0.0
    duration_s: Positive
    maximum_step_s: Positive
    maximum_steps: int = Field(default=10000,ge=1,le=100000)
    maximum_flow_evaluations: int = Field(default=1000000,ge=1,le=10000000)
    tank_gas_pressure_pa_abs: Positive
    downstream_pressure_pa_abs: Positive
    tank_bottom_minus_outlet_elevation_m: float
    pipe_length_m: Positive
    pipe_diameter_m: Positive
    roughness_m: Nonnegative
    minor_loss_coefficient: Nonnegative
    density_kg_m3: Positive
    viscosity_pa_s: Positive
    maximum_volume_flow_m3_s: Positive
    gravity_m_s2: Positive = 9.80665
    flow_absolute_tolerance_m3_s: Positive = 1e-12
    volume_absolute_tolerance_m3: Positive = 1e-10
    transition_policy: Literal["withhold","use_declared_correlation"]
    friction_method: Literal["Clamond","Colebrook","Haaland","Swamee_Jain_1976"] = "Clamond"
    quasi_steady_nonflashing_single_phase_reviewed: bool = False
    evidence_ids: tuple[Name, ...] = Field(min_length=1)


def drainage_outflow(request: DrainageRequest, *, level_m: float, gas_pressure_pa_abs: float, evaluation_budget: dict):
    """Existing Darcy-root path with a caller-supplied instantaneous gas pressure."""
    from scipy.optimize import brentq
    q=request
    if not math.isfinite(level_m) or level_m<0 or not math.isfinite(gas_pressure_pa_abs) or gas_pressure_pa_abs<=0:
        raise ValueError("drainage level and absolute gas pressure are outside their physical domain")
    drive=(gas_pressure_pa_abs-q.downstream_pressure_pa_abs
           +q.density_kg_m3*q.gravity_m_s2*(level_m+q.tank_bottom_minus_outlet_elevation_m))
    if drive<=0:
        raise ValueError("drainage left its forward-flow domain; reverse flow is not inferred")
    def residual(rate):
        evaluation_budget["count"]+=1
        if evaluation_budget["count"]>q.maximum_flow_evaluations:
            raise ValueError("drainage exceeded its hydraulic-evaluation budget")
        result=liquid_pressure_loss(LiquidPressureLoss(
            diameter_m=q.pipe_diameter_m,length_m=q.pipe_length_m,roughness_m=q.roughness_m,
            volume_flow_m3_s=rate,density_kg_m3=q.density_kg_m3,viscosity_pa_s=q.viscosity_pa_s,
            minor_loss_coefficient=q.minor_loss_coefficient,gravity_m_s2=q.gravity_m_s2,
            transition_policy=q.transition_policy,friction_method=q.friction_method))
        if result.status!="computed":
            raise ValueError("drainage hydraulic owner withheld a flow trial: "+"; ".join(result.notes))
        return result.values["required_inlet_minus_outlet_pa"]-drive
    if residual(q.maximum_volume_flow_m3_s)<0:
        raise ValueError("drainage flow exceeds the supplied root bracket")
    return float(brentq(residual,0.0,q.maximum_volume_flow_m3_s,xtol=q.flow_absolute_tolerance_m3_s,rtol=1e-12))


def evaluate_tank_drainage(request: DrainageRequest, context: RunContext):
    from scipy.optimize import brentq
    q=DrainageRequest.model_validate(request.model_dump(mode="python"))
    c=RunContext.model_validate(context.model_dump(mode="python"))
    if not q.quasi_steady_nonflashing_single_phase_reviewed:
        raise ValueError("quasi-steady, nonflashing single-phase review is required")
    if (q.asset_id,q.clock_id)!=(c.asset_id,c.clock_id):
        raise ValueError("drainage asset or clock differs from context")
    if not (set(q.evidence_ids)|{q.geometry.evidence_id}) <= {e.id for e in c.evidence}:
        raise ValueError("drainage evidence is absent")
    height,capacity,volume_at,level_at,owner=_shape(q.geometry,"brenth")
    if not 0<q.stop_volume_m3<q.initial_volume_m3<=capacity:
        raise ValueError("drainage volumes must satisfy 0 < stop < initial <= capacity")
    if math.ceil(q.duration_s/q.maximum_step_s)>q.maximum_steps:
        raise ValueError("drainage time grid exceeds the step budget")
    evaluation_budget={"count":0}

    def available(volume):
        return (q.tank_gas_pressure_pa_abs-q.downstream_pressure_pa_abs
                +q.density_kg_m3*q.gravity_m_s2*(level_at(volume)+q.tank_bottom_minus_outlet_elevation_m))

    if available(q.stop_volume_m3)<=0:
        raise ValueError("stop volume must remain above hydraulic equilibrium; asymptotic stopping needs a separate criterion")

    def flow(volume):
        return drainage_outflow(q,level_m=level_at(volume),gas_pressure_pa_abs=q.tank_gas_pressure_pa_abs,
                                evaluation_budget=evaluation_budget)

    time=q.start_time_s
    end=time+q.duration_s
    volume=q.initial_volume_m3
    discharged=0.0
    states=[dict(time_s=time,volume_m3=volume,level_m=level_at(volume),outflow_m3_s=flow(volume))]
    stopped=False
    for _ in range(q.maximum_steps):
        if time>=end or stopped:
            break
        dt=min(q.maximum_step_s,end-time)
        previous=volume
        midpoint_to_stop=(previous+q.stop_volume_m3)/2
        to_stop=previous-q.stop_volume_m3
        stop_flow=flow(midpoint_to_stop)
        if dt*stop_flow>=to_stop:
            dt=to_stop/stop_flow
            volume=q.stop_volume_m3
            stopped=True
        else:
            volume=float(brentq(lambda v:v-previous+dt*flow((previous+v)/2),
                                q.stop_volume_m3,previous,xtol=q.volume_absolute_tolerance_m3,rtol=1e-12))
        midpoint_flow=flow((previous+volume)/2)
        discharged+=dt*midpoint_flow
        if time+dt<=time:
            raise ValueError("drainage clock cannot represent the requested timestep")
        time+=dt
        states.append(dict(time_s=time,volume_m3=volume,level_m=level_at(volume),
                           outflow_m3_s=flow(volume),step_mean_outflow_m3_s=midpoint_flow,
                           step_volume_residual_m3=volume-previous+dt*midpoint_flow))
    if not stopped and time<end:
        raise ValueError("drainage did not complete its requested time window")
    body=dict(schema="pipeline_integrity.tank_drainage.v1",request=q.model_dump(mode="json"),
              status="computed",
              context=c.model_dump(mode="json"),states=states,geometry_owner=owner,
              discharged_volume_m3=discharged,discharged_mass_kg=discharged*q.density_kg_m3,
              volume_residual_m3=volume-q.initial_volume_m3+discharged,
              stop_volume_reached=stopped,flow_evaluations=evaluation_budget["count"],
              integration="implicit_midpoint",numerical_validation_completed=False,
              implementation_sha256=file_digest(Path(__file__)),
              hydraulic_owner_sha256=file_digest(Path(__file__).parent/"models"/"hydraulics.py"))
    body["record_digest"]=digest(body)
    return body


def verify_tank_drainage_record(record, *, request=None, context=None):
    if digest({k:v for k,v in record.items() if k!="record_digest"})!=record.get("record_digest"):
        raise ValueError("drainage receipt changed")
    q=DrainageRequest.model_validate_json(json.dumps(record["request"]))
    c=RunContext.model_validate_json(json.dumps(record["context"]))
    if request is not None and request.model_dump(mode="json")!=q.model_dump(mode="json"):
        raise ValueError("drainage caller request differs")
    if context is not None and context.model_dump(mode="json")!=c.model_dump(mode="json"):
        raise ValueError("drainage caller context differs")
    if evaluate_tank_drainage(q,c)!=record:
        raise ValueError("drainage replay differs")
    return True
