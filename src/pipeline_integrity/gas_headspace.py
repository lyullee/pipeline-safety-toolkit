"""Well-mixed ideal-gas headspace with prescribed flows and volume displacement.

Heat capacities and gas constant are caller supplied for one fixed composition.
Boundary enthalpy uses the same zero as U=m*cv*T. Composition changes,
condensation and pressure-driven valve flows require a coupled provider.
"""

from __future__ import annotations

import math
import json
from pathlib import Path
from .datasets import file_digest

from pydantic import Field, model_validator

from .contracts import Contract, Name, Positive, Nonnegative, RunContext, SignalFrame, digest, json_value


class HeadspaceWindow(Contract):
    id: Name
    duration_s: Positive
    inlet_mass_flow_kg_s: Nonnegative = 0.0
    outlet_mass_flow_kg_s: Nonnegative = 0.0
    inlet_temperature_K: Positive
    heat_input_W: float = 0.0
    ambient_temperature_K: Positive
    ambient_conductance_W_K: Nonnegative = 0.0
    volume_change_m3_s: float = 0.0


class HeadspaceRequest(Contract):
    id: Name
    asset_id: Name
    clock_id: Name
    start_time_s: Nonnegative = 0.0
    volume_m3: Positive
    gas_constant_J_kg_K: Positive
    cv_J_kg_K: Positive
    initial_mass_kg: Positive
    initial_temperature_K: Positive
    maximum_step_s: Positive
    maximum_steps: int = Field(default=100000, ge=1, le=1000000)
    windows: tuple[HeadspaceWindow, ...] = Field(min_length=1, max_length=1024)
    evidence_ids: tuple[Name, ...] = Field(min_length=1)
    operating_pressure_bounds_pa_abs: tuple[Positive, Positive] | None = None

    @model_validator(mode="after")
    def unique_windows(self):
        if self.operating_pressure_bounds_pa_abs is not None and self.operating_pressure_bounds_pa_abs[0]>=self.operating_pressure_bounds_pa_abs[1]:
            raise ValueError("headspace operating pressure bounds must be ordered")
        if len({w.id for w in self.windows}) != len(self.windows):
            raise ValueError("headspace window IDs must be unique")
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("headspace evidence IDs must be unique")
        if sum(math.ceil(w.duration_s / self.maximum_step_s) for w in self.windows) > self.maximum_steps:
            raise ValueError("headspace integration exceeds the declared step budget")
        volume = self.volume_m3
        mass = self.initial_mass_kg
        for window in self.windows:
            volume += window.volume_change_m3_s * window.duration_s
            mass += (window.inlet_mass_flow_kg_s-window.outlet_mass_flow_kg_s)*window.duration_s
            if volume <= 0 or mass <= 0:
                raise ValueError("prescribed displacement or outflow exhausts the headspace")
        return self


def headspace_thermodynamic_state(*, mass_kg, internal_energy_J, volume_m3, cv_J_kg_K, gas_constant_J_kg_K):
    """Shared fixed-composition ideal-gas state for prescribed or coupled volumes."""
    if not all(math.isfinite(x) and x>0 for x in (mass_kg,internal_energy_J,volume_m3,cv_J_kg_K,gas_constant_J_kg_K)):
        raise ValueError("headspace state requires positive finite mass, energy, volume and gas properties")
    temperature=internal_energy_J/(mass_kg*cv_J_kg_K)
    pressure=mass_kg*gas_constant_J_kg_K*temperature/volume_m3
    if not all(math.isfinite(x) and x>0 for x in (temperature,pressure)):
        raise ValueError("headspace pressure or temperature is not positively representable")
    return dict(temperature_K=temperature,pressure_pa_abs=pressure)


def headspace_boundary_energy_rates(request: HeadspaceRequest, window: HeadspaceWindow, *,
                                    mass_kg, internal_energy_J, volume_m3, volume_change_m3_s=None):
    """Shared open-system energy rates; displacement may come from a coupled liquid state."""
    state=headspace_thermodynamic_state(mass_kg=mass_kg,internal_energy_J=internal_energy_J,volume_m3=volume_m3,
        cv_J_kg_K=request.cv_J_kg_K,gas_constant_J_kg_K=request.gas_constant_J_kg_K)
    dv=window.volume_change_m3_s if volume_change_m3_s is None else volume_change_m3_s
    if not math.isfinite(dv):
        raise ValueError("headspace displacement rate must be finite")
    cp=request.cv_J_kg_K+request.gas_constant_J_kg_K
    temperature=state["temperature_K"]
    return dict(direct_heat_W=window.heat_input_W,
        ambient_heat_W=window.ambient_conductance_W_K*(window.ambient_temperature_K-temperature),
        inlet_enthalpy_W=window.inlet_mass_flow_kg_s*cp*window.inlet_temperature_K,
        outlet_enthalpy_W=-window.outlet_mass_flow_kg_s*cp*temperature,
        volume_work_into_gas_W=-state["pressure_pa_abs"]*dv)


def evaluate_headspace(request: HeadspaceRequest, context: RunContext | None = None) -> dict:
    """Integrate mass and internal energy with RK4 boundary-energy quadrature."""
    q = HeadspaceRequest.model_validate(request.model_dump(mode="python"))
    if context is not None:
        context = RunContext.model_validate(context.model_dump(mode="python"))
        if context.asset_id != q.asset_id or context.clock_id != q.clock_id:
            raise ValueError("headspace asset or clock differs from context")
        if not set(q.evidence_ids) <= {e.id for e in context.evidence}:
            raise ValueError("headspace evidence is absent from context")
    mass = q.initial_mass_kg
    energy = mass * q.cv_J_kg_K * q.initial_temperature_K
    initial_energy = energy
    time = q.start_time_s
    volume = q.volume_m3
    net_mass = net_energy = 0.0
    ledger = dict(direct_heat_J=0.0,ambient_heat_J=0.0,inlet_enthalpy_J=0.0,
                  outlet_enthalpy_J=0.0,volume_work_into_gas_J=0.0)
    rows = []

    def state_row(window_id):
        state=headspace_thermodynamic_state(mass_kg=mass,internal_energy_J=energy,volume_m3=volume,
            cv_J_kg_K=q.cv_J_kg_K,gas_constant_J_kg_K=q.gas_constant_J_kg_K)
        return dict(window_id=window_id, time_s=time, mass_kg=mass,
                    internal_energy_J=energy, **state, volume_m3=volume)

    rows.append(state_row(q.windows[0].id))
    for window in q.windows:
        steps = math.ceil(window.duration_s / q.maximum_step_s)
        dt = window.duration_s / steps
        dm = window.inlet_mass_flow_kg_s - window.outlet_mass_flow_kg_s

        def components(m, u, v):
            return tuple(headspace_boundary_energy_rates(q,window,mass_kg=m,internal_energy_J=u,volume_m3=v).values())

        for _ in range(steps):
            dv = window.volume_change_m3_s*dt
            a = components(mass,energy,volume)
            k1 = math.fsum(a)
            b = components(mass+dm*dt/2,energy+k1*dt/2,volume+dv/2)
            k2 = math.fsum(b)
            c = components(mass+dm*dt/2,energy+k2*dt/2,volume+dv/2)
            k3 = math.fsum(c)
            d = components(mass+dm*dt,energy+k3*dt,volume+dv)
            k4 = math.fsum(d)
            for index,key in enumerate(ledger):
                ledger[key] += dt*(a[index]+2*b[index]+2*c[index]+d[index])/6
            delta_energy = dt * (k1 + 2 * k2 + 2 * k3 + k4) / 6
            mass += dm * dt
            energy += delta_energy
            volume += dv
            net_mass += dm * dt
            net_energy += delta_energy
            if time+dt<=time:
                raise ValueError("headspace clock cannot represent the requested timestep")
            time += dt
            rows.append(state_row(window.id))
    result = dict(schema="pipeline_integrity.gas_headspace.v1", request=q.model_dump(mode="json"),
                  status="computed",
                  implementation_sha256=file_digest(Path(__file__)),
                  states=rows, mass_residual_kg=mass-q.initial_mass_kg-net_mass,
                  energy_residual_J=energy-initial_energy-net_energy,
                  boundary_energy_ledger=ledger,
                  ledger_energy_residual_J=energy-initial_energy-math.fsum(ledger.values()),
                  context=None if context is None else context.model_dump(mode="json"),
                  model="prescribed_volume_well_mixed_fixed_composition_ideal_gas",
                  boundary_energy_integration="same_RK4_stages_as_state",
                  numerical_validation_completed=False)
    events = []
    if q.operating_pressure_bounds_pa_abs is not None:
        lower,upper = q.operating_pressure_bounds_pa_abs
        def pressure_region(row):
            p = row["pressure_pa_abs"]
            return "below" if p<lower else "above" if p>upper else "within"
        if pressure_region(rows[0])!="within":
            events.append(dict(kind="initial_pressure_outside_bounds",time_s=rows[0]["time_s"],
                               region=pressure_region(rows[0])))
        for before,after in zip(rows,rows[1:]):
            a,b = pressure_region(before),pressure_region(after)
            if a!=b:
                events.append(dict(kind="sampled_pressure_region_transition",from_region=a,to_region=b,
                                   time_bracket_s=(before["time_s"],after["time_s"]),
                                   substep_crossings_resolved=False))
    result["pressure_events"] = events
    result = json_value(result)
    result["record_digest"] = digest(result)
    return result


def verify_headspace_record(record: dict, request: HeadspaceRequest | None = None,
                           context: RunContext | None = None) -> bool:
    body = {k: v for k, v in record.items() if k != "record_digest"}
    if digest(body) != record.get("record_digest"):
        raise ValueError("headspace record digest differs")
    retained = HeadspaceRequest.model_validate_json(json.dumps(record["request"]))
    stored_context = None if record.get("context") is None else RunContext.model_validate_json(json.dumps(record["context"]))
    if context is not None and context.model_dump(mode="json") != record.get("context"):
        raise ValueError("headspace caller context differs")
    if request is not None and request.model_dump(mode="json") != retained.model_dump(mode="json"):
        raise ValueError("headspace caller request differs")
    if evaluate_headspace(retained, stored_context) != record:
        raise ValueError("headspace replay differs")
    return True


def headspace_signal_frames(record: dict, *, stream_id: str):
    """Expose replayed model states to the existing monitoring/response interfaces."""
    verify_headspace_record(record)
    q = HeadspaceRequest.model_validate_json(json.dumps(record["request"]))
    channels = {"pressure_pa_abs":"Pa","temperature_K":"K","mass_kg":"kg",
                "internal_energy_J":"J","volume_m3":"m^3"}
    frames = tuple(SignalFrame(stream_id=stream_id,clock_id=q.clock_id,time_s=row["time_s"],
                               values={key:row[key] for key in channels},units=channels,
                               valid={key:True for key in channels},origin="simulation",
                               operating_mode=row["window_id"]) for row in record["states"])
    body = dict(schema="pipeline_integrity.headspace_frames.v1",source_digest=record["record_digest"],
                stream_id=stream_id,clock_id=q.clock_id,asset_id=q.asset_id,
                frames_digest=digest(frames),frame_count=len(frames),origin="simulation",
                pressure_reference="absolute",evidence_ids=q.evidence_ids)
    body = json_value(body)
    body["record_digest"] = digest(body)
    return frames,body
