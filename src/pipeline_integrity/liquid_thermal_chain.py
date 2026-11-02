"""Conservative liquid-advection/wall-storage thermal chain with imposed flow.

Fixed-density single-phase fluid cells exchange heat with lumped pipe-wall
cells. Backward Euler couples all temperatures in each step. Ambient is a
prescribed boundary; hydraulic pressure, solid phase fronts and soil moisture
transport remain separate models.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Literal
from pydantic import Field, model_validator
from .contracts import Contract, Name, Positive, Nonnegative, RunContext, digest
from .datasets import file_digest


class LiquidThermalCell(Contract):
    id: Name
    fluid_volume_m3: Positive
    wall_heat_capacity_J_K: Positive
    fluid_wall_conductance_W_K: Nonnegative
    wall_ambient_conductance_W_K: Nonnegative
    initial_fluid_temperature_K: Positive
    initial_wall_temperature_K: Positive
    evidence_id: Name


class LiquidThermalWindow(Contract):
    id: Name
    duration_s: Positive
    mass_flow_left_to_right_kg_s: float
    left_supply_temperature_K: Positive
    right_supply_temperature_K: Positive
    ambient_temperature_K: Positive
    fluid_heat_W: tuple[float, ...] = ()
    wall_heat_W: tuple[float, ...] = ()
    evidence_id: Name


def _check_chain_layout(cells,windows,maximum_step_s,maximum_cell_steps):
    if len({c.id for c in cells})!=len(cells) or len({w.id for w in windows})!=len(windows):
        raise ValueError("thermal cell and window IDs must be unique")
    for window in windows:
        if any(v and len(v)!=len(cells) for v in (window.fluid_heat_W,window.wall_heat_W)):
            raise ValueError("thermal source arrays must match the cell order")
    if sum(math.ceil(w.duration_s/maximum_step_s) for w in windows)*len(cells)>maximum_cell_steps:
        raise ValueError("liquid thermal campaign exceeds its cell-step budget")


class LiquidThermalRequest(Contract):
    id: Name
    asset_id: Name
    clock_id: Name
    start_time_s: Nonnegative = 0.0
    density_kg_m3: Positive
    heat_capacity_J_kg_K: Positive
    cells: tuple[LiquidThermalCell, ...] = Field(min_length=1,max_length=4000)
    windows: tuple[LiquidThermalWindow, ...] = Field(min_length=1,max_length=10000)
    maximum_step_s: Positive
    maximum_cell_steps: int = Field(default=2000000,ge=1,le=10000000)
    constant_properties_and_single_phase_reviewed: bool = False
    mechanical_energy_absent_or_in_supplied_heat_reviewed: bool = False
    evidence_ids: tuple[Name, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def aligned(self):
        _check_chain_layout(self.cells,self.windows,self.maximum_step_s,self.maximum_cell_steps)
        return self


def evaluate_liquid_thermal_chain(request: LiquidThermalRequest, context: RunContext):
    import numpy as np
    from scipy.sparse import bmat, diags
    from scipy.sparse.linalg import splu
    from importlib.metadata import version

    q=LiquidThermalRequest.model_validate(request.model_dump(mode="python"))
    c=RunContext.model_validate(context.model_dump(mode="python"))
    if not q.constant_properties_and_single_phase_reviewed:
        raise ValueError("constant-property single-phase thermal applicability review is required")
    if not q.mechanical_energy_absent_or_in_supplied_heat_reviewed:
        raise ValueError("pressure/kinetic work and dissipation must be absent or explicitly represented in supplied heat")
    if (q.asset_id,q.clock_id)!=(c.asset_id,c.clock_id):
        raise ValueError("liquid thermal asset/clock differs from context")
    required=set(q.evidence_ids)|{s.evidence_id for s in q.cells}|{w.evidence_id for w in q.windows}
    if not required <= {e.id for e in c.evidence}:
        raise ValueError("liquid thermal geometry/properties/boundaries lack context evidence")
    n=len(q.cells)
    cf=np.array([s.fluid_volume_m3*q.density_kg_m3*q.heat_capacity_J_kg_K for s in q.cells])
    cw=np.array([s.wall_heat_capacity_J_K for s in q.cells])
    fw=np.array([s.fluid_wall_conductance_W_K for s in q.cells])
    wa=np.array([s.wall_ambient_conductance_W_K for s in q.cells])
    fluid=np.array([s.initial_fluid_temperature_K for s in q.cells])
    wall=np.array([s.initial_wall_temperature_K for s in q.cells])
    if not all(np.isfinite(v).all() for v in (cf,cw,fw,wa)) or np.any(cf<=0):
        raise ValueError("thermal storage or conductance exceeds finite arithmetic")
    initial_energy=float(cf@fluid+cw@wall)
    time=q.start_time_s
    ledger=dict(inlet_enthalpy_J=0.0,outlet_enthalpy_J=0.0,ambient_heat_J=0.0,
                fluid_heating_J=0.0,wall_heating_J=0.0)
    states=[]
    def row(window_id):
        return dict(window_id=window_id,time_s=time,fluid_temperature_K=fluid.tolist(),
                    wall_temperature_K=wall.tolist(),stored_energy_J=float(cf@fluid+cw@wall))
    states.append(row(q.windows[0].id))
    for window in q.windows:
        steps=math.ceil(window.duration_s/q.maximum_step_s)
        dt=window.duration_s/steps
        adv=abs(window.mass_flow_left_to_right_kg_s)*q.heat_capacity_J_kg_K
        fluid_matrix=diags(cf/dt+fw+adv,format="csc")
        if n>1 and adv:
            offset=-1 if window.mass_flow_left_to_right_kg_s>0 else 1
            fluid_matrix=fluid_matrix+diags([-adv*np.ones(n-1)],[offset],shape=(n,n),format="csc")
        matrix=bmat([[fluid_matrix,diags(-fw)],
                     [diags(-fw),diags(cw/dt+fw+wa)]],format="csc")
        factor=splu(matrix)
        fh=np.array(window.fluid_heat_W or (0.0,)*n)
        wh=np.array(window.wall_heat_W or (0.0,)*n)
        supply=window.left_supply_temperature_K if window.mass_flow_left_to_right_kg_s>=0 else window.right_supply_temperature_K
        inlet_index=0 if window.mass_flow_left_to_right_kg_s>=0 else n-1
        outlet_index=n-1-inlet_index
        window_start=time
        for step in range(steps):
            rhs_fluid=cf/dt*fluid+fh
            rhs_fluid[inlet_index]+=adv*supply
            rhs_wall=cw/dt*wall+wa*window.ambient_temperature_K+wh
            previous=float(cf@fluid+cw@wall)
            solution=factor.solve(np.concatenate((rhs_fluid,rhs_wall)))
            if not np.isfinite(solution).all() or np.any(solution<=0):
                raise ValueError("liquid thermal solution left its positive-temperature domain")
            fluid,wall=solution[:n],solution[n:]
            increments=dict(inlet_enthalpy_J=dt*adv*supply,
                            outlet_enthalpy_J=-dt*adv*float(fluid[outlet_index]),
                            ambient_heat_J=dt*float(np.sum(wa*(window.ambient_temperature_K-wall))),
                            fluid_heating_J=dt*float(np.sum(fh)),wall_heating_J=dt*float(np.sum(wh)))
            for key,value in increments.items():
                ledger[key]+=value
            new_time=window_start+(step+1)*dt
            if new_time<=time:
                raise ValueError("thermal clock cannot represent the requested timestep")
            time=new_time
            state=row(window.id)
            state["step_energy_residual_J"]=state["stored_energy_J"]-previous-math.fsum(increments.values())
            states.append(state)
    body=dict(schema="pipeline_integrity.liquid_thermal_chain.v1",request=q.model_dump(mode="json"),
              status="computed",
              context=c.model_dump(mode="json"),cell_order=[s.id for s in q.cells],states=states,
              boundary_energy_ledger=ledger,energy_residual_J=float(cf@fluid+cw@wall)-initial_energy-math.fsum(ledger.values()),
              integration="fully_coupled_backward_euler",spatial_advection="first_order_upwind",
              prescribed_mass_flow=True,hydraulic_pressure_solved=False,numerical_validation_completed=False,
              implementation_sha256=file_digest(Path(__file__)),scipy_version=version("scipy"),numpy_version=version("numpy"))
    body["record_digest"]=digest(body)
    return body


def verify_liquid_thermal_chain_record(record, *, request=None, context=None):
    if digest({k:v for k,v in record.items() if k!="record_digest"})!=record.get("record_digest"):
        raise ValueError("liquid thermal chain receipt changed")
    q=LiquidThermalRequest.model_validate_json(json.dumps(record["request"]))
    c=RunContext.model_validate_json(json.dumps(record["context"]))
    if request is not None and request.model_dump(mode="json")!=q.model_dump(mode="json"):
        raise ValueError("liquid thermal caller request differs")
    if context is not None and context.model_dump(mode="json")!=c.model_dump(mode="json"):
        raise ValueError("liquid thermal caller context differs")
    if evaluate_liquid_thermal_chain(q,c)!=record:
        raise ValueError("liquid thermal chain replay differs")
    return True


class PropertyLiquidThermalRequest(Contract):
    id: Name
    asset_id: Name
    clock_id: Name
    start_time_s: Nonnegative = 0.0
    cells: tuple[LiquidThermalCell, ...] = Field(min_length=1,max_length=4000)
    windows: tuple[LiquidThermalWindow, ...] = Field(min_length=1,max_length=10000)
    maximum_step_s: Positive
    maximum_cell_steps: int = Field(default=2000000,ge=1,le=10000000)
    fluid: Name
    backend: Literal["HEOS","IF97","PR","SRK"] = "HEOS"
    reference_pressure_pa_abs: Positive
    reference_temperature_K: Positive
    property_temperature_bounds_K: tuple[Positive,Positive]
    property_probe_count: int = Field(default=9,ge=3,le=201)
    maximum_relative_density_variation: Nonnegative = Field(le=0.5)
    maximum_relative_heat_capacity_variation: Nonnegative = Field(le=0.5)
    frozen_reference_properties_reviewed: bool = False
    mechanical_energy_absent_or_in_supplied_heat_reviewed: bool = False
    property_evidence_id: Name
    evidence_ids: tuple[Name, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def support(self):
        _check_chain_layout(self.cells,self.windows,self.maximum_step_s,self.maximum_cell_steps)
        lower,upper=self.property_temperature_bounds_K
        if not lower<upper or not lower<=self.reference_temperature_K<=upper:
            raise ValueError("liquid thermal reference must lie inside ordered property-temperature support")
        temperatures=[s.initial_fluid_temperature_K for s in self.cells]
        temperatures.extend(t for w in self.windows for t in (w.left_supply_temperature_K,w.right_supply_temperature_K))
        if any(not lower<=t<=upper for t in temperatures):
            raise ValueError("initial/supply liquid temperatures exceed the reviewed property support")
        return self


def evaluate_property_liquid_thermal_chain(request: PropertyLiquidThermalRequest, context: RunContext, *, property_provider=None):
    import inspect
    from .thermo import StateRequest, ThermodynamicState, CheckedProvider, CoolPropProvider
    q=PropertyLiquidThermalRequest.model_validate(request.model_dump(mode="python"))
    c=RunContext.model_validate(context.model_dump(mode="python"))
    if not q.frozen_reference_properties_reviewed:
        raise ValueError("reference-pressure and frozen-property approximation requires review")
    if not q.mechanical_energy_absent_or_in_supplied_heat_reviewed:
        raise ValueError("thermal chain mechanical-energy exclusions require explicit review")
    evidence=[e for e in c.evidence if e.id==q.property_evidence_id]
    if len(evidence)!=1 or evidence[0].sha256 is None:
        raise ValueError("thermal property applicability needs source-hash-pinned context evidence")
    active=CoolPropProvider(use_cache=False) if property_provider is None else property_provider
    try:
        provider_source=Path(inspect.getfile(type(active))).resolve()
    except (TypeError,OSError) as error:
        raise ValueError("liquid thermal property provider implementation source is unavailable") from error
    provider_sha=file_digest(provider_source)
    checked=CheckedProvider(active)
    lower,upper=q.property_temperature_bounds_K
    probes=[q.reference_temperature_K]+[lower+(upper-lower)*i/(q.property_probe_count-1) for i in range(q.property_probe_count)]
    states=[]
    issues=[]
    for temperature in probes:
        state_request=StateRequest(fluid=q.fluid,backend=q.backend,inputs={"P":q.reference_pressure_pa_abs,"T":temperature},single_phase_required=True)
        state=checked.resolve(state_request)
        state=ThermodynamicState.model_validate(state.model_dump(mode="python"))
        if not math.isclose(state.pressure_pa_abs,q.reference_pressure_pa_abs,rel_tol=1e-10,abs_tol=1e-8) or not math.isclose(state.temperature_k,temperature,rel_tol=1e-10,abs_tol=1e-8):
            raise ValueError("liquid thermal property provider changed the requested pressure or temperature")
        if state.phase not in {"liquid","supercritical_liquid"} or state.cp_j_kg_k is None:
            issues.append("a property probe lacks a resolved liquid phase and heat capacity")
        states.append(state)
    reference=states[0]
    if len({(s.provider_version,s.provider_revision) for s in states})!=1:
        raise ValueError("liquid thermal property provider identity changed during probes")
    density_variation=max(abs(s.density_kg_m3/reference.density_kg_m3-1) for s in states)
    cp_variation=None if any(s.cp_j_kg_k is None for s in states) else max(abs(s.cp_j_kg_k/reference.cp_j_kg_k-1) for s in states)
    if density_variation>q.maximum_relative_density_variation:
        issues.append("sampled density variation exceeds the declared frozen-property allowance")
    if cp_variation is None or cp_variation>q.maximum_relative_heat_capacity_variation:
        issues.append("sampled heat-capacity variation exceeds the declared frozen-property allowance")
    chain=None
    derived=None
    if not issues:
        derived=LiquidThermalRequest(id=q.id,asset_id=q.asset_id,clock_id=q.clock_id,start_time_s=q.start_time_s,
            density_kg_m3=reference.density_kg_m3,heat_capacity_J_kg_K=reference.cp_j_kg_k,cells=q.cells,windows=q.windows,
            maximum_step_s=q.maximum_step_s,maximum_cell_steps=q.maximum_cell_steps,
            constant_properties_and_single_phase_reviewed=True,
            mechanical_energy_absent_or_in_supplied_heat_reviewed=q.mechanical_energy_absent_or_in_supplied_heat_reviewed,
            evidence_ids=tuple(dict.fromkeys((*q.evidence_ids,q.property_evidence_id))))
        chain=evaluate_liquid_thermal_chain(derived,c)
        if any(not lower<=t<=upper for row in chain["states"] for t in row["fluid_temperature_K"]):
            issues.append("computed liquid temperatures leave the reviewed property-temperature support")
    if file_digest(provider_source)!=provider_sha:
        raise ValueError("liquid thermal property implementation changed during evaluation")
    body=dict(schema="pipeline_integrity.property_liquid_thermal_chain.v1",request=q.model_dump(mode="json"),
              context=c.model_dump(mode="json"),status="computed_diagnostic" if not issues else "withheld",
              issues=issues,property_probes=[s.model_dump(mode="json") for s in states],
              relative_density_variation_sampled=density_variation,relative_heat_capacity_variation_sampled=cp_variation,
              probe_grid_proves_global_property_bound=False,reference_pressure_fixed=True,
              derived_request=None if derived is None else derived.model_dump(mode="json"),chain_result=chain,
              property_provider_source_sha256=provider_sha,implementation_sha256=file_digest(Path(__file__)),
              numerical_validation_completed=False)
    body["record_digest"]=digest(body)
    return body


def verify_property_liquid_thermal_chain_record(record, *, request=None, context=None, property_provider=None):
    if digest({k:v for k,v in record.items() if k!="record_digest"})!=record.get("record_digest"):
        raise ValueError("property-bound thermal chain receipt changed")
    q=PropertyLiquidThermalRequest.model_validate_json(json.dumps(record["request"]))
    c=RunContext.model_validate_json(json.dumps(record["context"]))
    if request is not None and request.model_dump(mode="json")!=q.model_dump(mode="json"):
        raise ValueError("property-bound thermal caller request differs")
    if context is not None and context.model_dump(mode="json")!=c.model_dump(mode="json"):
        raise ValueError("property-bound thermal caller context differs")
    if evaluate_property_liquid_thermal_chain(q,c,property_provider=property_provider)!=record:
        raise ValueError("property-bound thermal chain replay differs")
    return True
