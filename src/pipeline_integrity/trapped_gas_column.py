"""Fixed-inertance liquid column against one closed polytropic gas pocket.

Signed velocity is positive into the pocket. Gas pressure is absolute; liquid
acceleration follows the difference of two absolute pressures. A reversible
pressure-volume work potential is retained even for an isothermal pocket,
where this quantity is not the gas's change in internal energy.
"""
from __future__ import annotations

import math
import json
from pathlib import Path

from pydantic import Field,model_validator

from .contracts import Contract,Name,Nonnegative,Positive,RunContext,digest
from .datasets import file_digest
from .transient import History


class TrappedGasColumnRequest(Contract):
    id: Name
    asset_id: Name
    clock_id: Name
    liquid_density_kg_m3: Positive
    effective_column_length_m: Positive
    inner_diameter_m: Positive
    initial_gas_volume_m3: Positive
    initial_gas_pressure_Pa_abs: Positive
    polytropic_exponent: Positive
    initial_liquid_velocity_m_s: float = 0.0
    drive_pressure_Pa_abs: History
    darcy_friction_factor: Nonnegative = 0.0
    gas_volume_support_m3: tuple[Positive,Positive]
    sample_times_s: tuple[Nonnegative,...] = Field(min_length=2,max_length=100001)
    maximum_step_s: Positive
    relative_tolerance: Positive = Field(default=1e-8,le=1e-3)
    absolute_log_volume_tolerance: Positive = 1e-11
    absolute_velocity_tolerance_m_s: Positive = 1e-9
    absolute_work_integration_tolerance_J: Positive = 1e-8
    energy_budget_absolute_tolerance_J: Positive = 1e-5
    energy_budget_relative_tolerance: Positive = 1e-7
    maximum_rhs_evaluations: int = Field(default=200000,ge=1,le=5000000)
    fixed_inertance_single_pocket_and_polytropic_domain_reviewed: bool = False
    evidence_ids: tuple[Name,...] = Field(min_length=1)

    @model_validator(mode="after")
    def support(self):
        lo,hi=self.gas_volume_support_m3
        if not lo<self.initial_gas_volume_m3<hi:
            raise ValueError("initial pocket volume must be strictly inside its positive support")
        if self.sample_times_s[0]!=0 or any(b<=a for a,b in zip(self.sample_times_s,self.sample_times_s[1:])):
            raise ValueError("pocket sample times must start at zero and strictly increase")
        if self.drive_pressure_Pa_abs.time_s[-1]<self.sample_times_s[-1] and len(self.drive_pressure_Pa_abs.time_s)>1:
            raise ValueError("nonconstant drive history must cover the complete observation horizon")
        if any(p<=0 for p in self.drive_pressure_Pa_abs.values):
            raise ValueError("drive pressure must be positive absolute pressure")
        if len(set(self.evidence_ids))!=len(self.evidence_ids):
            raise ValueError("pocket evidence IDs must be unique")
        return self


def evaluate_trapped_gas_column(request:TrappedGasColumnRequest,context:RunContext):
    import numpy as np
    from scipy.integrate import solve_ivp

    q=TrappedGasColumnRequest.model_validate(request.model_dump(mode="python"))
    c=RunContext.model_validate(context.model_dump(mode="python"))
    if not q.fixed_inertance_single_pocket_and_polytropic_domain_reviewed:
        raise ValueError("fixed liquid inertance and single closed polytropic pocket require review")
    if (q.asset_id,q.clock_id)!=(c.asset_id,c.clock_id):
        raise ValueError("pocket asset/clock differs from context")
    ids=[e.id for e in c.evidence]
    if any(ids.count(e)!=1 for e in q.evidence_ids):
        raise ValueError("pocket evidence is absent or ambiguous")
    area=math.pi*q.inner_diameter_m**2/4
    mass=q.liquid_density_kg_m3*area*q.effective_column_length_m
    if not all(math.isfinite(x) and x>0 for x in (area,mass)):
        raise ValueError("pocket area/inertance exceeds finite positive arithmetic")
    lo,hi=[math.log(v)-math.log(q.initial_gas_volume_m3) for v in q.gas_volume_support_m3]
    kinetic0=0.5*mass*q.initial_liquid_velocity_m_s**2
    evaluations=0

    def state(w):
        volume=q.initial_gas_volume_m3*math.exp(w)
        pressure=q.initial_gas_pressure_Pa_abs*math.exp(-q.polytropic_exponent*w)
        # Stable isothermal limit; this is integral(-p dV), not steady-flow work.
        factor=1-q.polytropic_exponent
        potential=q.initial_gas_pressure_Pa_abs*q.initial_gas_volume_m3*(-w if factor==0 else -math.expm1(factor*w)/factor)
        if not all(math.isfinite(v) for v in (volume,pressure,potential)) or min(volume,pressure)<=0:
            raise ValueError("pocket state leaves finite positive arithmetic")
        return volume,pressure,potential

    def rhs(t,y):
        nonlocal evaluations
        evaluations+=1
        if evaluations>q.maximum_rhs_evaluations:
            raise ValueError("pocket RHS budget exhausted")
        volume,pressure,_=state(float(y[0]))
        velocity=float(y[1])
        drive=q.drive_pressure_Pa_abs.at(t)
        drag=q.darcy_friction_factor*velocity*abs(velocity)/(2*q.inner_diameter_m)
        return (-area*velocity/volume,
                (drive-pressure)/(q.liquid_density_kg_m3*q.effective_column_length_m)-drag,
                drive*area*velocity,mass*drag*velocity)

    def lower(t,y):return y[0]-lo
    def upper(t,y):return hi-y[0]
    lower.terminal=upper.terminal=True
    lower.direction=upper.direction=-1
    rows=[]
    issue=None
    termination=None
    maximum_budget_error=None
    try:
        solution=solve_ivp(rhs,(0,q.sample_times_s[-1]),(0,q.initial_liquid_velocity_m_s,0,0),
            method="DOP853",rtol=q.relative_tolerance,
            atol=(q.absolute_log_volume_tolerance,q.absolute_velocity_tolerance_m_s,
                  q.absolute_work_integration_tolerance_J,q.absolute_work_integration_tolerance_J),
            max_step=q.maximum_step_s,t_eval=q.sample_times_s,events=(lower,upper))
        if not solution.success:
            raise ValueError("pocket integrator failed: "+solution.message)
        samples=list(zip(solution.t,solution.y.T))
        events=[(float(t),values) for times,states in zip(solution.t_events,solution.y_events)
                for t,values in zip(times,states)]
        if events:
            event_time,event_state=events[0]
            if samples[-1][0]<event_time:
                samples.append((event_time,event_state))
            termination="gas_volume_support_reached"
        else:
            termination="requested_horizon_reached"
        for t,y in samples:
            volume,pressure,work=state(float(y[0]))
            velocity=float(y[1])
            kinetic=0.5*mass*velocity**2
            drive_work,dissipation=map(float,y[2:])
            error=kinetic-kinetic0+work-drive_work+dissipation
            tolerance=q.energy_budget_absolute_tolerance_J+q.energy_budget_relative_tolerance*max(
                abs(kinetic-kinetic0),abs(work),abs(drive_work),abs(dissipation))
            if abs(error)>tolerance or dissipation<-q.absolute_work_integration_tolerance_J:
                raise ValueError("pocket solution fails the declared mechanical work/energy budget")
            rows.append(dict(time_s=float(t),gas_volume_m3=volume,gas_pressure_Pa_abs=pressure,
                liquid_velocity_m_s=velocity,gas_compression_work_J=work,liquid_kinetic_energy_J=kinetic,
                drive_work_J=drive_work,friction_dissipation_J=dissipation,energy_budget_residual_J=error,
                polytropic_log_invariant_residual=float(math.log(pressure/q.initial_gas_pressure_Pa_abs)
                    +q.polytropic_exponent*math.log(volume/q.initial_gas_volume_m3))))
        maximum_budget_error=max(abs(r["energy_budget_residual_J"]) for r in rows)
    except (ValueError,ArithmeticError,RuntimeError) as error:
        issue=str(error)
        rows=[]
        termination=None
    body=dict(schema="pipeline_integrity.trapped_gas_column.v1",request=q.model_dump(mode="json"),
        context=c.model_dump(mode="json"),status="computed_diagnostic" if rows else "withheld",
        rows=rows,issue=issue,termination=termination,rhs_evaluations=evaluations,
        maximum_energy_budget_residual_J=maximum_budget_error,
        gas_work_is_internal_energy_change=False,
        implementation_sha256=file_digest(Path(__file__)),
        history_owner_sha256=file_digest(Path(__file__).with_name("transient.py")))
    body["record_digest"]=digest(body)
    return body


def verify_trapped_gas_column_record(record,*,request=None,context=None):
    if digest({k:v for k,v in record.items() if k!="record_digest"})!=record.get("record_digest"):
        raise ValueError("trapped gas column receipt changed")
    q=TrappedGasColumnRequest.model_validate_json(json.dumps(record["request"]))
    c=RunContext.model_validate_json(json.dumps(record["context"]))
    if request is not None and request.model_dump(mode="json")!=q.model_dump(mode="json"):
        raise ValueError("pocket caller request differs")
    if context is not None and context.model_dump(mode="json")!=c.model_dump(mode="json"):
        raise ValueError("pocket caller context differs")
    if evaluate_trapped_gas_column(q,c)!=record:
        raise ValueError("pocket replay differs")
    return True
