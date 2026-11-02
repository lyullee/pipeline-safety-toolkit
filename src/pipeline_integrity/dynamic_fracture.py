"""Energy-coupled one-coordinate fracture and native HEM depressurization.

The reviewed coordinate changes storage volume, opening area and recoverable
elastic energy. Native hydrogen EOS, nozzle flow and fluid/wall balances are
reused. Generalized crack inertia and rate/temperature-dependent irreversible
resistance are supplied separately; no static toughness is converted to speed.
This reduced system does not solve a shell, a J-integral field or rupture waves.
"""
from __future__ import annotations

import bisect
import json
import math
from dataclasses import asdict
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .contracts import Contract, Name, Nonnegative, Positive, RunContext, SignalFrame, digest, json_value
from .datasets import file_digest
from .phase_change_exchange import native_backend_metadata
from .thermo import StateRequest
from .fracture_arrest import FractureArrestRequest


class CrackGeometryEnergy(Contract):
    id: Name
    advance_m: tuple[Nonnegative,...] = Field(min_length=2,max_length=500)
    fluid_volume_m3: tuple[Positive,...]
    new_crack_area_m2: tuple[Nonnegative,...]
    opening_area_m2: tuple[Nonnegative,...]
    recoverable_elastic_energy_J: tuple[Nonnegative,...]
    new_crack_area_basis_id: Name
    evidence_id: Name

    @model_validator(mode="after")
    def tables(self):
        n=len(self.advance_m)
        if any(len(values)!=n for values in (self.fluid_volume_m3,self.new_crack_area_m2,
                                             self.opening_area_m2,self.recoverable_elastic_energy_J)):
            raise ValueError("crack geometry and stored-energy tables must share the exact advance knots")
        for values in (self.advance_m,self.fluid_volume_m3,self.new_crack_area_m2):
            if any(b<=a for a,b in zip(values,values[1:])):
                raise ValueError("advance, displaced fluid volume and new fracture area must strictly increase")
        if max(self.opening_area_m2)<=0 or any(b<a for a,b in zip(self.opening_area_m2,self.opening_area_m2[1:])):
            raise ValueError("reviewed crack opening area must be nondecreasing and eventually positive")
        if any(b>a for a,b in zip(self.recoverable_elastic_energy_J,self.recoverable_elastic_energy_J[1:])):
            raise ValueError("this reduced model requires a nonincreasing recoverable elastic potential")
        return self


class DynamicCrackResistance(Contract):
    id: Name
    speed_m_s: tuple[Nonnegative,...] = Field(min_length=2,max_length=200)
    material_temperature_K: tuple[Positive,...] = Field(min_length=2,max_length=200)
    resistance_J_m2: tuple[tuple[Positive,...],...]
    material_condition_id: Name
    material_orientation_id: Name
    evidence_id: Name
    definition: Literal["dynamic_energy_per_new_crack_area_excluding_explicit_front_inertia"]

    @model_validator(mode="after")
    def table(self):
        if self.speed_m_s[0]!=0 or any(b<=a for values in (self.speed_m_s,self.material_temperature_K)
                                                   for a,b in zip(values,values[1:])):
            raise ValueError("dynamic resistance requires increasing speed/temperature support including zero speed")
        if (len(self.resistance_J_m2)!=len(self.speed_m_s)
                or any(len(row)!=len(self.material_temperature_K) for row in self.resistance_J_m2)):
            raise ValueError("dynamic fracture resistance is a speed-major temperature-minor table")
        return self


class DynamicFractureRequest(Contract):
    id: Name
    asset_id: Name
    event_id: Name
    clock_id: Name
    location_id: Name
    flaw_id: Name
    coordinate_frame_id: Name
    crack_path_id: Name
    path_position_at_zero_advance_m: float = 0.0
    propagation_direction: Literal["increasing_path","decreasing_path"] = "increasing_path"
    pipe_outer_diameter_m: Positive
    pipe_wall_thickness_m: Positive
    start_time_s: Nonnegative = 0.0
    duration_s: Positive
    fluid: Literal["Hydrogen","ParaHydrogen"]
    property_basis_id: Name
    initial_fluid_state: StateRequest
    external_fluid_state: StateRequest
    initial_wall_temperature_K: Positive
    wall_heat_capacity_J_K: Positive
    fluid_wall_UA_W_K: Nonnegative
    ambient_wall_UA_W_K: Nonnegative
    ambient_wall_temperature_K: Positive
    geometry: CrackGeometryEnergy
    resistance: DynamicCrackResistance
    effective_front_inertia_kg: Positive
    initial_advance_m: Nonnegative
    initial_speed_m_s: Nonnegative
    discharge_coefficient: Positive = Field(le=1)
    throat_pressure_samples: int = Field(default=60,ge=12,le=500)
    pressure_support_Pa_abs: tuple[Positive,Positive]
    fluid_temperature_support_K: tuple[Positive,Positive]
    minimum_inventory_kg: Positive
    maximum_step_s: Positive
    relative_ODE_tolerance: Positive = Field(default=1e-8,lt=0.01)
    absolute_mass_ODE_tolerance_kg: Positive = 1e-10
    absolute_energy_ODE_tolerance_J: Positive = 1e-5
    absolute_temperature_ODE_tolerance_K: Positive = 1e-7
    absolute_advance_ODE_tolerance_m: Positive = 1e-10
    absolute_speed_ODE_tolerance_m_s: Positive = 1e-8
    restart_force_tolerance_N: Positive
    stop_speed_projection_tolerance_m_s: Positive
    maximum_velocity_projection_energy_J: Nonnegative
    absolute_mass_balance_tolerance_kg: Positive
    absolute_energy_balance_tolerance_J: Positive
    relative_balance_tolerance: Nonnegative = Field(default=1e-7,le=0.001)
    maximum_RHS_calls: int = Field(default=100000,ge=1,le=1000000)
    maximum_native_state_conversions: int = Field(default=1000000,ge=1,le=10000000)
    maximum_points: int = Field(default=10000,ge=2,le=100000)
    maximum_mode_events: int = Field(default=1000,ge=1,le=10000)
    maximum_result_bytes: int = Field(default=64000000,ge=4096,le=256000000)
    evidence_ids: tuple[Name,...] = Field(min_length=1)
    coupling_review_evidence_id: Name
    variable_volume_pressure_work_and_elastic_potential_reviewed: bool = False
    dynamic_resistance_and_explicit_inertia_do_not_double_count_energy_reviewed: bool = False
    lumped_wall_temperature_represents_resistance_temperature_reviewed: bool = False
    irreversible_fracture_energy_not_recycled_to_lumped_wall_reviewed: bool = False
    homogeneous_equilibrium_restriction_and_pure_fluid_external_boundary_reviewed: bool = False
    owner_pins: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def scope(self):
        if 2*self.pipe_wall_thickness_m>=self.pipe_outer_diameter_m:
            raise ValueError("fracture pipe requires a strictly positive inner diameter")
        if (not math.isfinite(self.start_time_s+self.duration_s)
                or self.start_time_s+self.duration_s<=self.start_time_s):
            raise ValueError("dynamic fracture horizon must be representable on its actual source clock")
        if not self.geometry.advance_m[0]<=self.initial_advance_m<self.geometry.advance_m[-1]:
            raise ValueError("initial crack coordinate must lie inside the supported geometry")
        if self.initial_speed_m_s>self.resistance.speed_m_s[-1]:
            raise ValueError("initial crack speed exceeds the reviewed dynamic resistance")
        for limits in (self.pressure_support_Pa_abs,self.fluid_temperature_support_K):
            if limits[0]>=limits[1]:
                raise ValueError("native fracture EOS support bounds must increase")
        for state in (self.initial_fluid_state,self.external_fluid_state):
            if (state.fluid!=self.fluid or state.backend!="HEOS"
                    or set(state.inputs) not in ({"P","T"},{"P","Hmass"},{"P","Smass"})):
                raise ValueError("fracture initial and exterior states need a common pure HEOS P/T, P/H or P/S basis")
        return self


def dynamic_fracture_metadata():
    from importlib.metadata import version
    return dict(owner=native_backend_metadata(),scipy_version=version("scipy"),
                source_sha256={name:file_digest(Path(__file__).with_name(name+".py"))
                               for name in ("dynamic_fracture","contracts","datasets","thermo",
                                            "phase_change_exchange","fracture_arrest","fracture_exchange")})


def dynamic_fracture_boundary(q):
    return dict(asset_id=q.asset_id,event_id=q.event_id,clock_id=q.clock_id,
                request_digest_without_pins=digest(q.model_dump(mode="json",exclude={"owner_pins"})),
                energy_scope="fluid_U_wall_energy_elastic_potential_front_kinetic_fracture_dissipation_external_PdV")


def bind_dynamic_fracture_context(request: DynamicFractureRequest,context: RunContext):
    q=DynamicFractureRequest.model_validate(request.model_dump(mode="python"))
    c=RunContext.model_validate(context.model_dump(mode="python"))
    if (q.asset_id,q.event_id,q.clock_id)!=(c.asset_id,c.scenario_id,c.clock_id):
        raise ValueError("fracture context must identify the actual asset, event and clock")
    payload=c.model_dump(mode="python")
    payload["metadata"]={**c.metadata,"dynamic_fracture_boundary":dynamic_fracture_boundary(q)}
    return RunContext.model_validate(payload)


def _geometry(table,a):
    if not table.advance_m[0]<=a<=table.advance_m[-1]:
        raise ValueError("dynamic crack coordinate leaves the reviewed geometry/elastic-energy support")
    index=min(len(table.advance_m)-2,max(0,bisect.bisect_right(table.advance_m,a)-1))
    width=table.advance_m[index+1]-table.advance_m[index]
    fraction=(a-table.advance_m[index])/width
    result={}
    for name in ("fluid_volume_m3","new_crack_area_m2","opening_area_m2","recoverable_elastic_energy_J"):
        values=getattr(table,name)
        slope=(values[index+1]-values[index])/width
        result[name]=values[index]+fraction*(values[index+1]-values[index])
        result[name+"_per_m"]=slope
    if not all(math.isfinite(value) for value in result.values()):
        raise ValueError("crack geometry or elastic-potential gradient exceeds finite arithmetic")
    return result


def _native_run(q):
    import numpy as np
    from scipy.integrate import solve_ivp
    from scipy.interpolate import RegularGridInterpolator
    from lh2dt.dynamic_pipe import DynamicHEMPipe,DynamicHEMPipeState
    from lh2dt.properties import HydrogenProperties
    from lh2dt.streams import MassEnergyFlow
    from lh2dt.valve import Valve

    class DomainError(ArithmeticError):
        pass

    class Properties:
        def __init__(self):
            self.native=HydrogenProperties(q.fluid)
            self.calls=0;self.failures=0

        def __getattr__(self,name):
            method=getattr(self.native,name)
            if not callable(method) or not name.startswith("from_"):
                return method
            def call(*args,**kwargs):
                self.calls+=1
                if self.calls>q.maximum_native_state_conversions:
                    raise DomainError("dynamic fracture native state-conversion budget exceeded")
                try:
                    state=method(*args,**kwargs)
                except Exception:
                    self.failures+=1
                    raise
                if (not q.pressure_support_Pa_abs[0]<=state.pressure_Pa<=q.pressure_support_Pa_abs[1]
                        or not q.fluid_temperature_support_K[0]<=state.temperature_K<=q.fluid_temperature_support_K[1]):
                    raise DomainError("native fluid/throat state leaves the reviewed EOS support")
                return state
            return call

    props=Properties()
    def resolve(state):
        pair=set(state.inputs)
        method,key={frozenset(("P","T")):("from_pT","T"),frozenset(("P","Hmass")):("from_ph","Hmass"),
                    frozenset(("P","Smass")):("from_ps","Smass")}[frozenset(pair)]
        fluid=getattr(props,method)(state.inputs["P"],state.inputs[key])
        if state.single_phase_required and fluid.quality is not None:
            raise ValueError("fracture initial/exterior state requested single phase but resolved in coexistence")
        return fluid
    initial_fluid=resolve(q.initial_fluid_state)
    exterior=resolve(q.external_fluid_state)
    p_external=exterior.pressure_Pa
    if initial_fluid.pressure_Pa<=p_external:
        raise ValueError("outward HEM fracture model requires initial source pressure above the actual exterior")
    initial_geometry=_geometry(q.geometry,q.initial_advance_m)
    inner_diameter=q.pipe_outer_diameter_m-2*q.pipe_wall_thickness_m
    equivalent_storage_length=initial_geometry["fluid_volume_m3"]/(math.pi*inner_diameter**2/4)
    pipe=DynamicHEMPipe(equivalent_storage_length,inner_diameter,0.0,q.wall_heat_capacity_J_K,
        fluid_volume_m3=initial_geometry["fluid_volume_m3"],fluid_wall_UA_W_K=q.fluid_wall_UA_W_K,
        ambient_UA_W_K=q.ambient_wall_UA_W_K,ambient_temperature_K=q.ambient_wall_temperature_K,properties=props)
    native_initial=pipe.initialize(initial_fluid,wall_temperature_K=q.initial_wall_temperature_K)
    if native_initial.mass_kg<q.minimum_inventory_kg:
        raise ValueError("initial fracture fluid inventory is below the reviewed positive mass floor")
    full_area=max(q.geometry.opening_area_m2)
    valve=Valve(full_area,q.discharge_coefficient,properties=props,
                pressure_samples=q.throat_pressure_samples,allow_reverse=False)
    resistance=RegularGridInterpolator((q.resistance.speed_m_s,q.resistance.material_temperature_K),
                                       np.asarray(q.resistance.resistance_J_m2),bounds_error=True)
    # m,U,Twall,a,v,cumulative_m_out,cumulative_h_out,cumulative_Qambient,Dfracture,Wpressure
    y=np.array([native_initial.mass_kg,native_initial.internal_energy_J,native_initial.wall_temperature_K,
                q.initial_advance_m,q.initial_speed_m_s,0.0,0.0,0.0,0.0,0.0])
    rhs_calls=0;projection_energy=0.0;events=[];rows=[]

    def description(state,*,trial=False):
        if not np.isfinite(state).all():
            raise ValueError("dynamic fracture state or integrated ledger is nonfinite")
        m,u,tw,a,v=map(float,state[:5])
        if m<q.minimum_inventory_kg or tw<=0:
            raise ValueError("dynamic fracture fluid/wall state leaves its positive inventory domain")
        geometry=_geometry(q.geometry,a)
        # This assignment is an idempotent geometry projection of the current
        # trial state, never a history-dependent controller or accepted-state update.
        pipe.fluid_volume=geometry["fluid_volume_m3"]
        native=DynamicHEMPipeState(m,u,tw)
        fluid=pipe.thermo(native)
        if fluid.pressure_Pa<p_external:
            raise ValueError("fracture model reached exterior-pressure contact; air ingestion is not represented")
        speed=max(v,0.0) if trial else v
        if speed<0:
            raise ValueError("an accepted crack trajectory cannot heal or move backwards")
        r=float(np.asarray(resistance((speed,tw))).item())
        area_gradient=geometry["new_crack_area_m2_per_m"]
        driving_force=((fluid.pressure_Pa-p_external)*geometry["fluid_volume_m3_per_m"]
                       -geometry["recoverable_elastic_energy_J_per_m"])
        fracture_force=r*area_gradient
        if not all(math.isfinite(value) for value in (r,driving_force,fracture_force)):
            raise ValueError("dynamic crack driving force or resistance exceeds finite arithmetic")
        return geometry,native,fluid,r,driving_force,fracture_force

    def stored(state,geometry):
        return (float(state[1])+q.wall_heat_capacity_J_K*float(state[2])
                +geometry["recoverable_elastic_energy_J"]+0.5*q.effective_front_inertia_kg*float(state[4])**2
                +float(state[8])+p_external*(geometry["fluid_volume_m3"]-initial_geometry["fluid_volume_m3"]))

    initial_total=stored(y,initial_geometry)
    initial_mechanical=(initial_geometry["recoverable_elastic_energy_J"]
                        +0.5*q.effective_front_inertia_kg*q.initial_speed_m_s**2)
    if not all(math.isfinite(value) for value in (initial_total,initial_mechanical)):
        raise ValueError("dynamic fracture initial stored energy exceeds finite arithmetic")

    def retain(time,state,mode):
        if rows and time<=rows[-1]["elapsed_time_s"]:
            raise ValueError("dynamic fracture output clock must strictly advance")
        if rows and q.start_time_s+time<=rows[-1]["time_s"]:
            raise ValueError("dynamic fracture accepted step is below source-clock resolution")
        geometry,native,fluid,r,driving,fracture=description(state)
        mass_residual=float(state[0])+float(state[5])-native_initial.mass_kg
        energy_residual=stored(state,geometry)-initial_total+float(state[6])-float(state[7])-projection_energy
        mechanical_residual=(0.5*q.effective_front_inertia_kg*float(state[4])**2
            +geometry["recoverable_elastic_energy_J"]+float(state[8])-initial_mechanical-float(state[9])-projection_energy)
        if not all(math.isfinite(value) for value in (mass_residual,energy_residual,mechanical_residual)):
            raise ValueError("dynamic fracture integrated conservation audit is nonfinite")
        if float(state[8])<0:
            raise ValueError("accepted irreversible fracture dissipation cannot be negative")
        mass_scale=max(native_initial.mass_kg,abs(float(state[5])),1.0)
        # CoolProp's internal-energy reference is not an error allowance. Scale
        # relative tolerance by actual changes and exchanged physical work.
        energy_scale=max(abs(stored(state,geometry)-initial_total),abs(float(state[6])),
                         abs(float(state[7])),abs(float(state[9])),1.0)
        if (abs(mass_residual)>q.absolute_mass_balance_tolerance_kg+q.relative_balance_tolerance*mass_scale
                or abs(energy_residual)>q.absolute_energy_balance_tolerance_J+q.relative_balance_tolerance*energy_scale
                or abs(mechanical_residual)>q.absolute_energy_balance_tolerance_J
                   +q.relative_balance_tolerance*max(abs(initial_mechanical),abs(float(state[9])),1.0)):
            raise ValueError("dynamic fracture integrated fluid/solid mass or energy budget did not qualify")
        if rows and (float(state[3])<rows[-1]["advance_m"]-q.absolute_advance_ODE_tolerance_m
                     or float(state[8])<rows[-1]["fracture_dissipation_J"]-q.absolute_energy_ODE_tolerance_J):
            raise ValueError("accepted crack growth or irreversible fracture energy moved backwards")
        area_gradient=geometry["new_crack_area_m2_per_m"]
        envelope=p_external+(fracture+geometry["recoverable_elastic_energy_J_per_m"])/geometry["fluid_volume_m3_per_m"]
        driving_energy=driving/area_gradient
        if not math.isfinite(envelope) or not math.isfinite(driving_energy):
            raise ValueError("dynamic fracture energy-per-area or instantaneous pressure threshold is nonfinite")
        rows.append(dict(elapsed_time_s=float(time),time_s=q.start_time_s+float(time),advance_m=float(state[3]),
            crack_path_position_m=q.path_position_at_zero_advance_m
                +(1 if q.propagation_direction=="increasing_path" else -1)*float(state[3]),
            speed_m_s=float(state[4]),mode=mode,fluid_state=asdict(fluid),wall_temperature_K=float(state[2]),
            mass_kg=float(state[0]),fluid_internal_energy_J=float(state[1]),geometry=geometry,
            driving_energy_J_m2=driving_energy,dynamic_resistance_J_m2=r,
            instantaneous_force_equality_pressure_Pa_abs=envelope if envelope>0 else None,
            front_kinetic_energy_J=0.5*q.effective_front_inertia_kg*float(state[4])**2,
            irreversibility_reaction_N=max(fracture-driving,0.0) if mode=="stuck" else 0.0,
            unresolved_positive_stick_force_N=max(driving-fracture,0.0) if mode=="stuck" else 0.0,
            force_state_side="incoming_branch_at_event; mode_after_event_identifies_the_outgoing_branch",
            fracture_dissipation_J=float(state[8]),cumulative_external_displacement_work_J=
                p_external*(geometry["fluid_volume_m3"]-initial_geometry["fluid_volume_m3"]),
            cumulative_mass_out_kg=float(state[5]),cumulative_enthalpy_out_J=float(state[6]),
            cumulative_ambient_heat_J=float(state[7]),cumulative_net_pressure_crack_work_J=float(state[9]),
            mass_residual_kg=mass_residual,total_energy_residual_J=energy_residual,
            mechanical_energy_residual_J=mechanical_residual,numerical_velocity_projection_energy_J=projection_energy))
        if len(rows)>q.maximum_points:
            raise ValueError("dynamic fracture accepted-state output budget exceeded")

    def force(state):
        values=description(state,trial=True)
        return values[4]-values[5]

    mode="moving" if y[4]>0 or force(y)>=q.restart_force_tolerance_N else "stuck"
    retain(0.0,y,mode)
    time=0.0
    while time<q.duration_s:
        if len(events)>=q.maximum_mode_events:
            raise ValueError("dynamic fracture stick/restart event budget exhausted")
        episode_start=time
        def rhs(_time,state):
            nonlocal rhs_calls
            rhs_calls+=1
            if rhs_calls>q.maximum_RHS_calls:
                raise ValueError("dynamic fracture RHS work budget exhausted")
            geometry,native,fluid,r,driving,fracture=description(state,trial=True)
            failures=props.failures
            outlet=valve.evaluate(fluid,exterior,opening=geometry["opening_area_m2"]/full_area)
            if props.failures!=failures:
                raise DomainError("native HEM throat search contained an unresolved EOS sample")
            rate=float(outlet.mass_flow_kg_s)
            h=float(outlet.outlet_specific_enthalpy_J_kg)
            native_derivative=pipe.derivative_from_flows(native,(MassEnergyFlow(-rate,h,source=q.id+"/crack"),))
            advance_rate=float(state[4]) if mode=="moving" else 0.0
            volume_rate=geometry["fluid_volume_m3_per_m"]*advance_rate
            acceleration=(driving-fracture)/q.effective_front_inertia_kg if mode=="moving" else 0.0
            return np.array([native_derivative.mass_kg_s,native_derivative.internal_energy_W-fluid.pressure_Pa*volume_rate,
                native_derivative.wall_temperature_K_s,advance_rate,acceleration,rate,rate*h,
                native_derivative.heat_from_ambient_W,fracture*advance_rate,
                (fluid.pressure_Pa-p_external)*volume_rate])

        def transition(_time,state):
            if mode=="moving" and _time==episode_start and float(state[4])==0:
                # Exclude the departure root at zero speed, preserving the
                # unmodified zero-velocity state and its positive acceleration.
                return q.absolute_speed_ODE_tolerance_m_s
            return float(state[4]) if mode=="moving" else force(state)-q.restart_force_tolerance_N
        transition.terminal=True
        transition.direction=-1 if mode=="moving" else 1
        tolerances=np.array([q.absolute_mass_ODE_tolerance_kg,q.absolute_energy_ODE_tolerance_J,
            q.absolute_temperature_ODE_tolerance_K,q.absolute_advance_ODE_tolerance_m,
            q.absolute_speed_ODE_tolerance_m_s,q.absolute_mass_ODE_tolerance_kg,
            q.absolute_energy_ODE_tolerance_J,q.absolute_energy_ODE_tolerance_J,
            q.absolute_energy_ODE_tolerance_J,q.absolute_energy_ODE_tolerance_J])
        result=solve_ivp(rhs,(time,q.duration_s),y,method="DOP853",events=transition,
                         max_step=q.maximum_step_s,rtol=q.relative_ODE_tolerance,atol=tolerances)
        if not result.success or len(result.t)<2 or float(result.t[-1])<=time:
            raise ValueError("dynamic fracture native integration failed or its event clock did not advance")
        for index in range(1,len(result.t)):
            state=result.y[:,index].copy()
            if index+1==len(result.t) and result.status==1 and mode=="moving":
                if abs(float(state[4]))>q.stop_speed_projection_tolerance_m_s:
                    raise ValueError("crack stop event root exceeds the reviewed velocity tolerance")
                adjustment=-0.5*q.effective_front_inertia_kg*float(state[4])**2
                projection_energy+=adjustment
                if abs(projection_energy)>q.maximum_velocity_projection_energy_J:
                    raise ValueError("numerical crack-stop kinetic-energy projection exceeds caller budget")
                state[4]=0.0
                if force(state)>q.restart_force_tolerance_N:
                    raise ValueError("crack stop root contradicts its positive restart driving force")
            retain(float(result.t[index]),state,mode)
        y=state
        time=float(result.t[-1])
        if result.status==1:
            previous=mode
            mode="stuck" if mode=="moving" else "moving"
            events.append(dict(time_s=q.start_time_s+time,from_mode=previous,to_mode=mode,
                               advance_m=float(y[3]),net_driving_minus_resistance_force_N=force(y)))
            rows[-1]["mode_after_event"]=mode
    if q.start_time_s+q.duration_s<=q.start_time_s:
        raise ValueError("dynamic fracture absolute clock cannot represent the requested duration")
    return dict(history=rows,events=events,RHS_calls=rhs_calls,native_state_conversions=props.calls,
                native_EOS_sample_failures=props.failures,actual_duration_s=time,
                irreversibility="v_nonnegative_with_velocity_zero_events_and_reviewed_force_restart_tolerance",
                dynamic_crack_force_balance="M*dv/dt=(p-p_ext)*dV/da-dU_elastic/da-R(v,Twall)*dS/da",
                fluid_mechanical_power_into_crack_W="p*dV/dt; external displacement work p_ext*dV/dt retained separately",
                negative_velocity_trial_extension="R_at_zero_speed_for_event_location_only; negative_accepted_speed_rejected",
                full_scale_pipeline_arrest_proven=False,static_toughness_used_as_dynamic_resistance=False)


def evaluate_dynamic_fracture(request: DynamicFractureRequest,context: RunContext):
    q=DynamicFractureRequest.model_validate(request.model_dump(mode="python"))
    c=RunContext.model_validate(context.model_dump(mode="python"))
    if (q.asset_id,q.event_id,q.clock_id)!=(c.asset_id,c.scenario_id,c.clock_id):
        raise ValueError("dynamic fracture asset/event/clock differs from context")
    if c.metadata.get("dynamic_fracture_boundary")!=dynamic_fracture_boundary(q):
        raise ValueError("dynamic fracture context must bind actual fluid, geometry, resistance and energy coupling")
    reviews=(q.variable_volume_pressure_work_and_elastic_potential_reviewed,
        q.dynamic_resistance_and_explicit_inertia_do_not_double_count_energy_reviewed,
        q.lumped_wall_temperature_represents_resistance_temperature_reviewed,
        q.irreversible_fracture_energy_not_recycled_to_lumped_wall_reviewed,
        q.homogeneous_equilibrium_restriction_and_pure_fluid_external_boundary_reviewed)
    evidence={item.id:item for item in c.evidence}
    needed={q.geometry.evidence_id,q.resistance.evidence_id,q.coupling_review_evidence_id}
    if (not all(reviews) or len(evidence)!=len(c.evidence) or not needed<=set(q.evidence_ids)<=set(evidence)
            or any(evidence[key].sha256 is None for key in q.evidence_ids)):
        raise ValueError("dynamic fracture geometry, true dynamic resistance, inertia and energy coupling require source-pinned review")
    before=dynamic_fracture_metadata()
    if before!=q.owner_pins:
        raise ValueError("dynamic fracture native source/runtime/EOS reference differs from reviewed pins")
    body=dict(schema="pipeline_integrity.dynamic_fracture.v1",request=q.model_dump(mode="json"),
        context=c.model_dump(mode="json"),provider=before,status="withheld",field_validation_claim=False,
        limitations=["One generalized crack coordinate with caller-reviewed recoverable potential, variable volume, inertia and rate/temperature resistance.",
                     "No shell field, J-integral solution, crack branching, slip or acoustic decompression-wave propagation.",
                     "Opening restriction owns discharge impedance; no axial pipe-friction or distributed pressure field is solved.",
                     "Exterior is the same pure fluid; air ingestion or a state outside reviewed geometry/EOS/resistance is withheld."])
    try:
        body.update(status="computed_diagnostic",**_native_run(q))
    except (ValueError,RuntimeError,ArithmeticError,KeyError) as error:
        body.update(status="withheld",issues=[str(error)],error_type=type(error).__name__)
    if (before!=dynamic_fracture_metadata() or digest(request)!=digest(q) or digest(context)!=digest(c)):
        raise ValueError("dynamic fracture source, reference or caller inputs changed during evaluation")
    body=json_value(body)
    body["record_digest"]=digest(body)
    if len(json.dumps(body,allow_nan=False).encode("utf-8"))>q.maximum_result_bytes:
        raise ValueError("dynamic fracture receipt exceeds retained-byte budget")
    return body


def verify_dynamic_fracture_record(record,*,request=None,context=None):
    if not isinstance(record,dict) or record.get("record_digest")!=digest({k:v for k,v in record.items() if k!="record_digest"}):
        raise ValueError("dynamic fracture receipt changed")
    q=DynamicFractureRequest.model_validate_json(json.dumps(record["request"]))
    c=RunContext.model_validate_json(json.dumps(record["context"]))
    if request is not None and digest(request)!=digest(q) or context is not None and digest(context)!=digest(c):
        raise ValueError("dynamic fracture caller boundary differs")
    if record.get("status")!="computed_diagnostic":
        raise ValueError("withheld dynamic fracture requires diagnosis rather than matching error replay")
    if evaluate_dynamic_fracture(q,c)!=record:
        raise ValueError("dynamic fracture HEM/mechanical/thermal replay differs")
    return True


PRESSURE_ENVELOPE_DEFINITION="dynamic_fracture.instantaneous_generalized_force_equality_not_proven_arrest"


def dynamic_fracture_provider_identity(request: DynamicFractureRequest):
    q=DynamicFractureRequest.model_validate(request.model_dump(mode="python"))
    return dict(provider_id="pipeline_integrity.energy_coupled_dynamic_fracture",provider_version="1",
                provider_source_sha256=file_digest(Path(__file__)),provider_configuration_sha256=digest(q),
                decompression_source_sha256=digest(q.owner_pins.get("owner")),
                pressure_envelope_definition_id=PRESSURE_ENVELOPE_DEFINITION)


class DynamicFractureProvider:
    """Run this actual reduced solver through the existing ArrestProvider API."""

    def __init__(self,request: DynamicFractureRequest):
        self.request=DynamicFractureRequest.model_validate(request.model_dump(mode="python"))
        self.identity=dynamic_fracture_provider_identity(self.request)
        self.last_record=None

    def __call__(self,request,context):
        from .fracture_arrest import ArrestPoint,ArrestTrajectory,trajectory_binding
        q=self.request
        if request.source_mode!="provider":
            raise ValueError("native dynamic fracture cannot replace a preselected reviewed trajectory")
        if dynamic_fracture_provider_identity(q)!=self.identity:
            raise ValueError("dynamic fracture provider source/configuration changed")
        if context.origin not in {"simulation","synthetic"} or request.trajectory_origin!=context.origin:
            raise ValueError("a computed dynamic fracture trajectory requires its actual simulation/synthetic origin")
        identity_fields=("provider_id","provider_version","provider_source_sha256",
                         "provider_configuration_sha256","decompression_source_sha256","pressure_envelope_definition_id")
        if any(getattr(request,key)!=self.identity[key] for key in identity_fields):
            raise ValueError("arrest request must bind the actual reduced dynamic/decompression provider identity")
        if ((request.asset_id,request.clock_id,request.event_id,request.coordinate_frame_id,
             request.crack_path_id,request.location_id,request.flaw_id)
                !=(q.asset_id,q.clock_id,q.event_id,q.coordinate_frame_id,q.crack_path_id,q.location_id,q.flaw_id)
                or request.material_condition_id!=q.resistance.material_condition_id
                or request.material_orientation_id!=q.resistance.material_orientation_id
                or request.new_crack_area_basis_id!=q.geometry.new_crack_area_basis_id
                or request.propagation_direction!=q.propagation_direction
                or request.gas_composition_id!=q.fluid or request.eos_basis_id!=q.property_basis_id
                or request.pipe_diameter_m!=q.pipe_outer_diameter_m
                or request.wall_thickness_m!=q.pipe_wall_thickness_m
                or request.time_limits_s!=(q.start_time_s,q.start_time_s+q.duration_s)):
            raise ValueError("arrest and forward dynamic fracture physical/material/clock bases differ")
        if not set(q.evidence_ids)<=set(request.evidence_ids):
            raise ValueError("arrest request must retain the actual dynamic geometry/resistance/coupling evidence")
        record=evaluate_dynamic_fracture(q,context)
        self.last_record=record
        if record["status"]!="computed_diagnostic":
            raise ValueError("actual reduced dynamic fracture solver withheld its trajectory")
        if len(record["history"])>request.maximum_points:
            raise ValueError("actual dynamic trajectory exceeds the requested arrest comparison budget")
        points=tuple(ArrestPoint(id=q.id+"/state/"+str(index),time_s=row["time_s"],
            crack_path_position_m=row["crack_path_position_m"],local_pressure_pa_abs=row["fluid_state"]["pressure_Pa"],
            temperature_k=row["wall_temperature_K"],propagation_velocity_m_s=row["speed_m_s"],
            driving_energy_release_j_m2=row["driving_energy_J_m2"],
            dynamic_resistance_j_m2=row["dynamic_resistance_J_m2"],
            arrest_pressure_envelope_pa_abs=row["instantaneous_force_equality_pressure_Pa_abs"],
            valid=row["instantaneous_force_equality_pressure_Pa_abs"] is not None)
            for index,row in enumerate(record["history"]))
        native_receipt=dict(schema="pipeline_integrity.dynamic_fracture_arrest_provider.v1",
            dynamic_record_digest=record["record_digest"],dynamic_request=q.model_dump(mode="json"),
            actual_history_digest=digest(record["history"]),actual_duration_s=record["actual_duration_s"],
            events=record["events"],RHS_calls=record["RHS_calls"],
            maximum_mass_residual_kg=max(abs(row["mass_residual_kg"]) for row in record["history"]),
            maximum_total_energy_residual_J=max(abs(row["total_energy_residual_J"]) for row in record["history"]),
            maximum_mechanical_energy_residual_J=max(abs(row["mechanical_energy_residual_J"]) for row in record["history"]),
            full_scale_pipeline_arrest_proven=False,continuous_arrest_hold_proven=False,
            pressure_envelope_meaning=PRESSURE_ENVELOPE_DEFINITION,
            full_native_record_access="provider.last_record; source request retained here for deterministic replay")
        trajectory=ArrestTrajectory(status="computed",origin=context.origin,asset_id=q.asset_id,clock_id=q.clock_id,
            event_id=q.event_id,coordinate_frame_id=q.coordinate_frame_id,crack_path_id=q.crack_path_id,
            material_condition_id=q.resistance.material_condition_id,material_orientation_id=q.resistance.material_orientation_id,
            provider_id=self.identity["provider_id"],provider_version=self.identity["provider_version"],
            provider_source_sha256=self.identity["provider_source_sha256"],
            provider_configuration_sha256=self.identity["provider_configuration_sha256"],
            decompression_source_sha256=self.identity["decompression_source_sha256"],
            resistance_definition="dynamic_energy_per_new_crack_area",
            new_crack_area_basis_id=q.geometry.new_crack_area_basis_id,
            pressure_envelope_definition_id=PRESSURE_ENVELOPE_DEFINITION,points=points,native_receipt=native_receipt,
            evaluation_sha256="0"*64)
        result=trajectory.model_copy(update={"evaluation_sha256":trajectory_binding(request,context,trajectory)})
        if dynamic_fracture_provider_identity(q)!=self.identity:
            raise ValueError("dynamic fracture provider changed during arrest projection")
        return result


def dynamic_fracture_signal_frames(record,*,stream_id):
    verify_dynamic_fracture_record(record)
    if record["status"]!="computed_diagnostic":
        raise ValueError("fracture process frames require the completed actual coupled forward calculation")
    q=DynamicFractureRequest.model_validate_json(json.dumps(record["request"]))
    c=RunContext.model_validate_json(json.dumps(record["context"]))
    units=dict(pressure_pa_abs="Pa",fluid_temperature_K="K",wall_temperature_K="K",mass_kg="kg",
        fluid_internal_energy_J="J",advance_m="m",crack_path_position_m="m",speed_m_s="m/s",
        driving_energy_J_m2="J/m^2",dynamic_resistance_J_m2="J/m^2",fracture_dissipation_J="J",
        front_kinetic_energy_J="J",instantaneous_force_equality_pressure_pa_abs="Pa")
    frames=[]
    for row in record["history"]:
        values={key:row[key] for key in ("wall_temperature_K","mass_kg","fluid_internal_energy_J","advance_m",
            "crack_path_position_m","speed_m_s","driving_energy_J_m2","dynamic_resistance_J_m2",
            "fracture_dissipation_J","front_kinetic_energy_J")}
        values.update(pressure_pa_abs=row["fluid_state"]["pressure_Pa"],fluid_temperature_K=row["fluid_state"]["temperature_K"],
            instantaneous_force_equality_pressure_pa_abs=row["instantaneous_force_equality_pressure_Pa_abs"])
        frames.append(SignalFrame(stream_id=stream_id,clock_id=q.clock_id,time_s=row["time_s"],values=values,
            units=units,valid={key:value is not None for key,value in values.items()},
            origin="synthetic" if c.origin=="synthetic" else "simulation",
            operating_mode="energy_coupled_dynamic_fracture_"+row.get("mode_after_event",row["mode"])))
    receipt=dict(schema="pipeline_integrity.dynamic_fracture_frames.v1",source_record_digest=record["record_digest"],
        source_context_digest=digest(c),stream_id=stream_id,clock_id=q.clock_id,coordinate_frame_id=q.coordinate_frame_id,
        crack_path_id=q.crack_path_id,material_condition_id=q.resistance.material_condition_id,
        material_orientation_id=q.resistance.material_orientation_id,new_crack_area_basis_id=q.geometry.new_crack_area_basis_id,
        frame_count=len(frames),frames_digest=digest(frames),proven_arrest_inferred=False)
    receipt["record_digest"]=digest(receipt)
    return tuple(frames),receipt


def bind_dynamic_fracture_arrest(request: DynamicFractureRequest,template: FractureArrestRequest,
                                context: RunContext):
    """Bind actual forward physics to the original sampled arrest comparison API."""
    from .fracture_arrest import boundary_metadata
    q=DynamicFractureRequest.model_validate(request.model_dump(mode="python"))
    base=FractureArrestRequest.model_validate(template.model_dump(mode="python"))
    c=RunContext.model_validate(context.model_dump(mode="python"))
    if c.origin not in {"simulation","synthetic"}:
        raise ValueError("computed dynamic fracture needs explicit simulation or synthetic origin")
    if (base.asset_id,base.event_id,base.clock_id,base.location_id,base.flaw_id)!=(
            q.asset_id,q.event_id,q.clock_id,q.location_id,q.flaw_id):
        raise ValueError("arrest template must identify the same actual asset, event, clock, location and flaw")
    if base.source_mode!="provider":
        raise ValueError("dynamic solver binding requires an explicitly selected provider comparison mode")
    identity=dynamic_fracture_provider_identity(q)
    sign=1 if q.propagation_direction=="increasing_path" else -1
    positions=[q.path_position_at_zero_advance_m+sign*x
               for x in (q.initial_advance_m,q.geometry.advance_m[-1])]
    selected_evidence=tuple(dict.fromkeys((*base.evidence_ids,*q.evidence_ids)))
    payload=base.model_dump(mode="python")
    payload.update(trajectory_origin=c.origin,coordinate_frame_id=q.coordinate_frame_id,
        crack_path_id=q.crack_path_id,material_condition_id=q.resistance.material_condition_id,
        material_orientation_id=q.resistance.material_orientation_id,
        new_crack_area_basis_id=q.geometry.new_crack_area_basis_id,
        gas_composition_id=q.fluid,eos_basis_id=q.property_basis_id,
        pipe_diameter_m=q.pipe_outer_diameter_m,wall_thickness_m=q.pipe_wall_thickness_m,
        path_position_limits_m=(min(positions),max(positions)),
        propagation_direction=q.propagation_direction,time_limits_s=(q.start_time_s,q.start_time_s+q.duration_s),
        supported_temperature_k=(q.resistance.material_temperature_K[0],q.resistance.material_temperature_K[-1]),
        evidence_ids=selected_evidence,**identity)
    derived=FractureArrestRequest.model_validate(payload)
    evidence={e.id:e for e in c.evidence}
    required=set(selected_evidence)|set(derived.review_evidence_ids.values())
    if len(evidence)!=len(c.evidence) or not required<=set(evidence):
        raise ValueError("dynamic/arrest review and source evidence must remain actual unambiguous context members")
    bound=bind_dynamic_fracture_context(q,c)
    metadata={**bound.metadata,"fracture_arrest_boundary":boundary_metadata(derived)}
    bound=RunContext.model_validate({**bound.model_dump(mode="python"),"metadata":metadata})
    receipt=dict(schema="pipeline_integrity.dynamic_fracture_arrest_binding.v1",
        forward_request_digest=digest(q),template_digest=digest(base),derived_arrest_request_digest=digest(derived),
        original_context_digest=digest(c),derived_context_digest=digest(bound),provider_identity=identity,
        pressure_envelope_meaning=PRESSURE_ENVELOPE_DEFINITION,
        original_arrest_boundary_digest=digest(c.metadata.get("fracture_arrest_boundary")),
        original_dynamic_boundary_digest=digest(c.metadata.get("dynamic_fracture_boundary")),
        applicability_reviews_created=False)
    receipt["record_digest"]=digest(receipt)
    return derived,bound,receipt


class DynamicFractureArrestRequest(Contract):
    id: Name
    dynamics: DynamicFractureRequest
    comparison: FractureArrestRequest
    maximum_result_bytes: int = Field(default=128000000,ge=4096,le=512000000)


def evaluate_dynamic_fracture_arrest(request: DynamicFractureArrestRequest,context: RunContext):
    """Execute the actual pressure/work/crack solver and original comparison together."""
    from .fracture_arrest import assess_fracture_arrest
    q=DynamicFractureArrestRequest.model_validate(request.model_dump(mode="python"))
    c=RunContext.model_validate(context.model_dump(mode="python"))
    request_pin,context_pin=digest(q),digest(c)
    sources=dynamic_fracture_metadata()
    comparison,bound,binding=bind_dynamic_fracture_arrest(q.dynamics,q.comparison,c)
    provider=DynamicFractureProvider(q.dynamics)
    issues=[]; original=None
    try:
        original=assess_fracture_arrest(comparison,bound,provider=provider)
        status="computed_diagnostic" if original["status"]=="computed" else original["status"]
    except (ValueError,RuntimeError,ArithmeticError) as error:
        if provider.last_record is None or provider.last_record.get("status")!="withheld":
            raise
        status="withheld"
        issues=[str(error)]
    if (sources!=dynamic_fracture_metadata() or digest(q)!=request_pin or digest(c)!=context_pin
            or digest(request)!=request_pin or digest(context)!=context_pin):
        raise ValueError("dynamic fracture/arrest source or caller input changed during the combined calculation")
    body=dict(schema="pipeline_integrity.dynamic_fracture_arrest.v1",request=q.model_dump(mode="json"),
        context=c.model_dump(mode="json"),derived_context=bound.model_dump(mode="json"),status=status,
        binding=binding,forward=provider.last_record,comparison=original,issues=issues,
        source_metadata=sources,full_scale_pipeline_arrest_proven=False,continuous_hold_proven=False,
        numerical_validation_completed=False)
    body["record_digest"]=digest(body)
    if len(json.dumps(body,allow_nan=False).encode("utf-8"))>q.maximum_result_bytes:
        raise ValueError("combined dynamic fracture/arrest receipt exceeds retained-byte budget")
    return body


def verify_dynamic_fracture_arrest_record(record,*,request=None,context=None):
    if not isinstance(record,dict) or record.get("record_digest")!=digest({k:v for k,v in record.items() if k!="record_digest"}):
        raise ValueError("combined dynamic fracture/arrest receipt changed")
    q=DynamicFractureArrestRequest.model_validate_json(json.dumps(record["request"]))
    c=RunContext.model_validate_json(json.dumps(record["context"]))
    if (request is not None and digest(request)!=digest(q)) or (context is not None and digest(context)!=digest(c)):
        raise ValueError("combined dynamic fracture/arrest caller boundary differs")
    if record.get("status")=="withheld":
        raise ValueError("withheld dynamic fracture/arrest requires diagnosis before replay qualification")
    if evaluate_dynamic_fracture_arrest(q,c)!=record:
        raise ValueError("combined dynamic fracture and original arrest comparison replay differs")
    return True
