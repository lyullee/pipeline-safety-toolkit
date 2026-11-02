"""Pure-hydrogen tank pressure/vent histories through the original owner solver.

Implementation is supplied for later HPC verification. Native RK4 integration,
phase closures, geometry and valve physics remain with their existing owners.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict
from importlib.metadata import version
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from . import phase_change_exchange, tanks
from .contracts import (
    Contract,
    Fraction,
    Name,
    Nonnegative,
    Positive,
    RunContext,
    SignalFrame,
    digest,
    json_value,
)
from .datasets import file_digest
from .equipment_exchange import LH2EquipmentClient
from .tanks import AxisymmetricTank
from .thermo import StateRequest

REVIEWS = (
    "pure_fluid_property_and_initial_energy_reference",
    "geometry_wall_zones_and_phase_closures",
    "phase_selective_ports_and_constant_window_boundaries",
    "stateless_vent_opening_and_backpressure",
    "numerical_step_and_mass_energy_qualification",
)


class TankBreathingClosures(Contract):
    lower_wall_heat_capacity_J_K: Positive
    upper_wall_heat_capacity_J_K: Positive
    lower_ambient_UA_W_K: Nonnegative
    upper_ambient_UA_W_K: Nonnegative
    wall_liquid_U_W_m2K: Nonnegative
    wall_vapor_U_W_m2K: Nonnegative
    liquid_interface_UA_W_K: Nonnegative
    vapor_interface_UA_W_K: Nonnegative
    wall_axial_UA_W_K: Nonnegative
    wall_zone_split_height_m: Nonnegative
    minimum_pressure_Pa: Positive
    maximum_pressure_Pa: Positive
    evidence_id: Name

    @model_validator(mode="after")
    def pressure_domain(self):
        if self.minimum_pressure_Pa >= self.maximum_pressure_Pa:
            raise ValueError("native pressure-search bounds must be ordered")
        return self


class TankBreathingState(Contract):
    liquid_mass_kg: Positive
    liquid_internal_energy_J: float
    vapor_mass_kg: Positive
    vapor_internal_energy_J: float
    lower_wall_temperature_K: Positive
    upper_wall_temperature_K: Positive


class TankBreathingVent(Contract):
    mode: Literal["closed", "specified_opening", "pressure_relief_hysteresis", "atmospheric_mixture"]
    tank_port_id: Name
    downstream_boundary_id: Name
    downstream_state: StateRequest | None
    full_open_area_m2: Positive | None
    discharge_coefficient: Fraction | None
    closed_leakage_CdA_m2: Nonnegative | None
    evidence_id: Name


class TankBreathingWindow(Contract):
    id: Name
    duration_s: Positive | None
    timestep_s: Positive | None
    maximum_reviewed_step_s: Positive | None
    ambient_temperature_K: Positive | None
    direct_lower_wall_heat_W: float | None
    direct_upper_wall_heat_W: float | None
    direct_liquid_heat_W: float | None
    direct_vapor_heat_W: float | None
    liquid_inflow_kg_s: Nonnegative | None
    liquid_inlet_state: StateRequest | None
    liquid_process_outflow_kg_s: Nonnegative | None
    vapor_inflow_kg_s: Nonnegative | None
    vapor_inlet_state: StateRequest | None
    vapor_process_outflow_kg_s: Nonnegative | None
    vent_opening: Fraction | None
    evidence_id: Name


class TankBreathingPorts(Contract):
    liquid_inlet_id: Name
    liquid_outlet_id: Name
    vapor_inlet_id: Name
    vapor_outlet_id: Name

    @model_validator(mode="after")
    def distinct(self):
        if len(set(self.model_dump().values())) != 4:
            raise ValueError("phase-specific inlet/outlet ports must be distinct")
        return self


class TankBreathingRequest(Contract):
    id: Name
    asset_id: Name
    event_id: Name
    clock_id: Name
    start_time_s: Nonnegative
    fluid: Literal["Hydrogen", "ParaHydrogen"] | None
    shape: AxisymmetricTank | None
    shape_evidence_id: Name
    closures: TankBreathingClosures | None
    initial_state: TankBreathingState | None = None
    initial_pressure_pa_abs: Positive | None = None
    initial_liquid_volume_fraction: float | None = Field(default=None, gt=0, lt=1)
    initial_lower_wall_temperature_K: Positive | None = None
    initial_upper_wall_temperature_K: Positive | None = None
    ports: TankBreathingPorts
    vent: TankBreathingVent | None
    windows: tuple[TankBreathingWindow, ...] = Field(min_length=1, max_length=256)
    evidence_ids: tuple[Name, ...] = Field(min_length=1)
    reviews: dict[str, bool] = Field(default_factory=lambda: dict.fromkeys(REVIEWS, False))
    review_evidence_ids: dict[str, Name] = Field(default_factory=dict)
    owner_pins: dict = Field(default_factory=dict)
    maximum_steps: int = Field(default=10000, ge=1, le=1000000)
    maximum_duration_s: Positive = 86400.0
    absolute_mass_tolerance_kg: Nonnegative = 1e-9
    absolute_energy_tolerance_J: Nonnegative = 1e-4
    relative_balance_tolerance: float = Field(default=1e-8, ge=0, le=1e-3)
    declared_pressure_limits_pa_abs: tuple[Positive, Positive] | None = None

    @model_validator(mode="after")
    def request_shape(self):
        if set(self.reviews) != set(REVIEWS):
            raise ValueError("all named breathing review fields are required")
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("selected evidence IDs must be distinct")
        if len({w.id for w in self.windows}) != len(self.windows):
            raise ValueError("operating-window IDs must be distinct")
        saturated = (
            self.initial_pressure_pa_abs,
            self.initial_liquid_volume_fraction,
            self.initial_lower_wall_temperature_K,
            self.initial_upper_wall_temperature_K,
        )
        if self.initial_state is not None and any(v is not None for v in saturated):
            raise ValueError("choose an explicit conserved initial state or saturated initialization")
        if (
            self.declared_pressure_limits_pa_abs
            and self.declared_pressure_limits_pa_abs[0] >= self.declared_pressure_limits_pa_abs[1]
        ):
            raise ValueError("declared operating pressure limits must be ordered")
        if self.vent and self.vent.tank_port_id in self.ports.model_dump().values():
            raise ValueError("vent and process flow ports must be distinct")
        for window in self.windows:
            if window.duration_s is not None and window.timestep_s is not None:
                if window.timestep_s > window.duration_s:
                    raise ValueError("time step cannot exceed its physical operating window")
            if self.vent and self.vent.mode == "closed" and window.vent_opening not in (None, 0):
                raise ValueError("closed vent mode cannot carry a nonzero opening")
        return self


def native_backend_metadata():
    """Reuse the existing native owner/reference identity rather than another scanner."""
    return {
        "thermal_owner": phase_change_exchange.native_backend_metadata(),
        "shared_tank_geometry_sha256": file_digest(Path(tanks.__file__)),
        "shared_thermal_adapter_sha256": file_digest(Path(phase_change_exchange.__file__)),
        "geometry_provider_fluids_version": version("fluids"),
    }


def boundary_metadata(q):
    return {
        "request_id": q.id,
        "event_id": q.event_id,
        "asset_id": q.asset_id,
        "clock_id": q.clock_id,
        "start_time_s": q.start_time_s,
        "fluid": q.fluid,
        "shape_digest": digest(q.shape),
        "ports": q.ports.model_dump(mode="json"),
        "vent_digest": digest(q.vent),
        "windows_digest": digest(q.windows),
        "request_digest": digest(q),
        "reference": "runtime-pinned HEOS H/S anchors; signed mass into phase CV",
    }


def _policy(q, c):
    if (q.asset_id, q.event_id, q.clock_id) != (c.asset_id, c.scenario_id, c.clock_id) or c.metadata.get(
        "tank_breathing_boundary"
    ) != boundary_metadata(q):
        raise ValueError("tank breathing request and actual asset/clock/ports/property context differ")
    available = [e.id for e in c.evidence]
    selected = set(q.evidence_ids)
    used = {q.shape_evidence_id, *q.review_evidence_ids.values(), *(w.evidence_id for w in q.windows)}
    if q.closures:
        used.add(q.closures.evidence_id)
    if q.vent:
        used.add(q.vent.evidence_id)
    if len(set(available)) != len(available) or selected - set(available) or used - selected:
        raise ValueError(
            "shape/closure/vent/window/review evidence must select actual unique context members"
        )
    missing = ["review:" + key for key in REVIEWS if not q.reviews[key] or key not in q.review_evidence_ids]
    missing += [
        "missing:" + name for name in ("fluid", "shape", "closures", "vent") if getattr(q, name) is None
    ]
    if not q.owner_pins and (q.vent is None or q.vent.mode in ("closed", "specified_opening")):
        missing.append("reviewed_native_owner_and_property_pins")
    if q.initial_state is None:
        missing += [
            "missing:" + name
            for name in (
                "initial_pressure_pa_abs",
                "initial_liquid_volume_fraction",
                "initial_lower_wall_temperature_K",
                "initial_upper_wall_temperature_K",
            )
            if getattr(q, name) is None
        ]
    for w in q.windows:
        fields = (
            "duration_s",
            "timestep_s",
            "maximum_reviewed_step_s",
            "ambient_temperature_K",
            "direct_lower_wall_heat_W",
            "direct_upper_wall_heat_W",
            "direct_liquid_heat_W",
            "direct_vapor_heat_W",
            "liquid_inflow_kg_s",
            "liquid_process_outflow_kg_s",
            "vapor_inflow_kg_s",
            "vapor_process_outflow_kg_s",
            "vent_opening",
        )
        missing += [f"{w.id}:missing:{name}" for name in fields if getattr(w, name) is None]
        for phase in ("liquid", "vapor"):
            rate = getattr(w, phase + "_inflow_kg_s")
            state = getattr(w, phase + "_inlet_state")
            if rate is not None and rate > 0 and state is None:
                missing.append(f"{w.id}:missing:{phase}_inlet_state")
            if state and (state.fluid != q.fluid or state.backend != "HEOS"):
                raise ValueError("all source streams must use the common pure-fluid HEOS identity")
    if q.vent and q.vent.mode == "specified_opening":
        missing += [
            "vent:missing:" + name
            for name in (
                "downstream_state",
                "full_open_area_m2",
                "discharge_coefficient",
                "closed_leakage_CdA_m2",
            )
            if getattr(q.vent, name) is None
        ]
        state = q.vent.downstream_state
        if state and (state.fluid != q.fluid or state.backend != "HEOS"):
            raise ValueError("a pure-hydrogen outlet cannot silently become atmospheric mixed gas")
    return missing


def _total_energy(state, closures):
    return math.fsum(
        (
            state.liquid_internal_energy_J,
            state.vapor_internal_energy_J,
            closures.lower_wall_heat_capacity_J_K * state.lower_wall_temperature_K,
            closures.upper_wall_heat_capacity_J_K * state.upper_wall_temperature_K,
        )
    )


def _initialize_native_tank(q):
    """Construct the original owner and recover an initial state without time integration."""
    from lh2dt.stratified_tank import StratifiedTank, StratifiedTankParameters, StratifiedTankState
    shape, geometry_record = tanks.lh2dt_tank_geometry(q.shape)
    client = LH2EquipmentClient(fluid=q.fluid, property_basis="runtime-pinned pure hydrogen")
    properties = client.properties
    parameters = StratifiedTankParameters(**q.closures.model_dump(exclude={"evidence_id"}))
    tank = StratifiedTank(shape, parameters, properties)
    initial = (
        StratifiedTankState(**q.initial_state.model_dump())
        if q.initial_state
        else tank.initialize_saturated(
            q.initial_pressure_pa_abs,
            q.initial_liquid_volume_fraction,
            q.initial_lower_wall_temperature_K,
            q.initial_upper_wall_temperature_K,
        )
    )
    return tank, client, properties, initial, geometry_record, tank.thermo(initial)


def initialize_tank_breathing(request: TankBreathingRequest, context: RunContext):
    """Return pinned original-owner conserved initial state/thermo without advancing time."""
    q=TankBreathingRequest.model_validate_json(request.model_dump_json())
    c=RunContext.model_validate_json(context.model_dump_json())
    body=dict(schema="pipeline_integrity.tank_breathing_initial.v1",request=q.model_dump(mode="json"),
              context=c.model_dump(mode="json"),status="needs_data",notes=_policy(q,c),
              native_called=False,integration_performed=False,initial_state=None,initial_thermo=None,
              geometry=None,provider=None,implementation_sha256=file_digest(Path(__file__)))
    if not q.owner_pins:
        body["notes"].append("reviewed native owner/property pins are required for initialization")
    if not body["notes"]:
        try:
            before=native_backend_metadata()
        except ImportError as error:
            body.update(status="out_of_scope",notes=[f"{type(error).__name__}: {error}"])
        else:
            if before!=q.owner_pins:
                raise ValueError("tank initialization owner/property pins differ from reviewed values")
            try:
                body["native_called"]=True
                tank,client,properties,initial,geometry_record,thermo=_initialize_native_tank(q)
                body.update(status="computed_diagnostic",provider=before,initial_state=asdict(initial),
                            initial_thermo=asdict(thermo),geometry=geometry_record,
                            initial_total_mass_kg=initial.liquid_mass_kg+initial.vapor_mass_kg,
                            initial_total_energy_J=_total_energy(initial,q.closures))
            except (ImportError,ValueError,RuntimeError,ArithmeticError,TypeError,KeyError) as error:
                body.update(status="out_of_scope",notes=[f"{type(error).__name__}: {error}"])
            finally:
                if native_backend_metadata()!=before:
                    raise ValueError("tank owner/property reference changed during initialization")
    body=json_value(body)
    return {**body,"record_digest":digest(body)}


def _native(q, c, body):
    from lh2dt.streams import MassEnergyFlow
    from lh2dt.valve import Valve

    # Every phase state still comes from the original tank owner.
    body["native_called"] = True
    tank,client,properties,initial,geometry_record,initial_thermo=_initialize_native_tank(q)
    initial_mass = initial.liquid_mass_kg + initial.vapor_mass_kg
    initial_energy = _total_energy(initial, q.closures)
    initial_vapor = initial.vapor_mass_kg
    initial_liquid = initial.liquid_mass_kg
    state = initial
    valve = None
    backpressure = None
    if q.vent.mode == "specified_opening":
        if q.vent.discharge_coefficient <= 0:
            raise ValueError("the native discharge coefficient must be positive")
        valve = Valve(
            q.vent.full_open_area_m2,
            q.vent.discharge_coefficient,
            properties,
            allow_reverse=False,
            closed_leakage_CdA_m2=q.vent.closed_leakage_CdA_m2,
        )
        backpressure = client.state(q.vent.downstream_state)
        if backpressure.quality is not None or backpressure.phase not in {
            "gas",
            "supercritical_gas",
            "supercritical",
        }:
            raise ValueError("the vent downstream boundary must be reviewed pure single-phase vapor")
    rows, endpoint_rows, windows = [], [], []
    elapsed = cumulative_mass = cumulative_energy = prescribed_liquid_mass = prescribed_vapor_mass = 0.0
    pressures = [initial_thermo.pressure_Pa]
    requested_steps = 0
    for window in q.windows:
        window_initial = state
        if window.timestep_s > window.maximum_reviewed_step_s:
            raise ValueError("requested time step exceeds its reviewed envelope")
        requested_steps += math.ceil(window.duration_s / window.timestep_s)
        if requested_steps > q.maximum_steps:
            raise ValueError("the declared native step-work budget is exceeded")
        liquid_inlet = client.state(window.liquid_inlet_state) if window.liquid_inflow_kg_s > 0 else None
        vapor_inlet = client.state(window.vapor_inlet_state) if window.vapor_inflow_kg_s > 0 else None
        if liquid_inlet and (
            liquid_inlet.quality is not None or liquid_inlet.phase not in {"liquid", "supercritical_liquid"}
        ):
            raise ValueError("liquid inlet must be a reviewed single liquid-phase stream")
        if vapor_inlet and (
            vapor_inlet.quality is not None
            or vapor_inlet.phase not in {"gas", "supercritical_gas", "supercritical"}
        ):
            raise ValueError("vapor inlet must be a reviewed single vapor-phase stream")

        def flows(time, thermo, window=window, li=liquid_inlet, vi=vapor_inlet):
            if thermo.liquid.quality not in (None, 0.0) or thermo.vapor.quality not in (None, 1.0):
                raise ValueError(
                    "native phase regions entered a wet interior; phase-selective ports are unqualified"
                )
            if thermo.liquid.quality is None and thermo.liquid.phase not in {
                "liquid",
                "supercritical_liquid",
            }:
                raise ValueError("native liquid region left its qualified phase")
            if thermo.vapor.quality is None and thermo.vapor.phase not in {
                "gas",
                "supercritical_gas",
                "supercritical",
            }:
                raise ValueError("native vapor region left its qualified phase")
            liquid, vapor = [], []
            if li:
                liquid.append(
                    MassEnergyFlow(
                        window.liquid_inflow_kg_s, li.specific_enthalpy_J_kg, q.ports.liquid_inlet_id
                    )
                )
            if vi:
                vapor.append(
                    MassEnergyFlow(
                        window.vapor_inflow_kg_s, vi.specific_enthalpy_J_kg, q.ports.vapor_inlet_id
                    )
                )
            if window.liquid_process_outflow_kg_s:
                liquid.append(
                    MassEnergyFlow(
                        -window.liquid_process_outflow_kg_s,
                        thermo.liquid.specific_enthalpy_J_kg,
                        q.ports.liquid_outlet_id,
                    )
                )
            if window.vapor_process_outflow_kg_s:
                vapor.append(
                    MassEnergyFlow(
                        -window.vapor_process_outflow_kg_s,
                        thermo.vapor.specific_enthalpy_J_kg,
                        q.ports.vapor_outlet_id,
                    )
                )
            if valve is not None:
                vent = valve.evaluate(thermo.vapor, backpressure, opening=window.vent_opening)
                if vent.mass_flow_kg_s < 0 or not math.isfinite(vent.mass_flow_kg_s):
                    raise ValueError("native vent returned reverse or nonfinite mass flow")
                vapor.append(
                    MassEnergyFlow(
                        -vent.mass_flow_kg_s, thermo.vapor.specific_enthalpy_J_kg, q.vent.tank_port_id
                    )
                )
            return tuple(liquid), tuple(vapor)

        def wall_heat(time, thermo, w=window):
            return w.direct_lower_wall_heat_W, w.direct_upper_wall_heat_W

        def fluid_heat(time, thermo, w=window):
            return w.direct_liquid_heat_W, w.direct_vapor_heat_W

        native_rows = tank.simulate(
            state,
            window.duration_s,
            window.timestep_s,
            window.ambient_temperature_K,
            flows,
            wall_heat_callback=wall_heat,
            fluid_heat_callback=fluid_heat,
        )
        if not native_rows:
            raise ValueError("owner returned no integrated step; sub-resolution duration is not a result")
        liquid_net = window.liquid_inflow_kg_s - window.liquid_process_outflow_kg_s
        vapor_net = window.vapor_inflow_kg_s - window.vapor_process_outflow_kg_s
        for row in native_rows:
            time = elapsed + row.time_s
            absolute_time = q.start_time_s + time
            if not math.isfinite(absolute_time) or (rows and absolute_time <= rows[-1]["time_s"]):
                raise ValueError("context clock cannot represent distinct owner step times")
            mass = row.state.liquid_mass_kg + row.state.vapor_mass_kg
            energy = _total_energy(row.state, q.closures)
            boundary_mass = cumulative_mass + row.cumulative_boundary_mass_kg
            boundary_energy = cumulative_energy + row.cumulative_boundary_energy_J
            prescribed_liquid = prescribed_liquid_mass + liquid_net * row.time_s
            prescribed_vapor = prescribed_vapor_mass + vapor_net * row.time_s
            reconstructed_vent_mass = prescribed_liquid + prescribed_vapor - boundary_mass
            vent_mass = 0.0 if q.vent.mode == "closed" else reconstructed_vent_mass
            if vent_mass < 0:
                raise ArithmeticError(
                    "negative reconstructed cumulative vent loss is unresolved; no clipping"
                )
            transfer_vapor = row.state.vapor_mass_kg - initial_vapor - prescribed_vapor + vent_mass
            transfer_liquid = initial_liquid + prescribed_liquid - row.state.liquid_mass_kg
            mass_residual = mass - initial_mass - boundary_mass
            energy_residual = energy - initial_energy - boundary_energy
            mass_limit = q.absolute_mass_tolerance_kg + q.relative_balance_tolerance * max(
                abs(mass - initial_mass), abs(boundary_mass)
            )
            energy_limit = q.absolute_energy_tolerance_J + q.relative_balance_tolerance * max(
                abs(energy - initial_energy), abs(boundary_energy)
            )
            within = (
                abs(mass_residual) <= mass_limit
                and abs(energy_residual) <= energy_limit
                and (q.vent.mode != "closed" or abs(reconstructed_vent_mass) <= mass_limit)
            )
            rows.append(
                {
                    "window_id": window.id,
                    "time_s": absolute_time,
                    "state": asdict(row.state),
                    "thermo": asdict(row.thermo),
                    "cumulative_boundary_mass_kg": boundary_mass,
                    "cumulative_boundary_energy_J": boundary_energy,
                    "cumulative_vented_mass_kg": vent_mass,
                    "signed_vent_mass_reconstruction_kg": reconstructed_vent_mass,
                    "cumulative_net_liquid_to_vapor_transfer_kg": transfer_vapor,
                    "phase_balance_crosscheck_kg": transfer_vapor - transfer_liquid,
                    "mass_residual_kg": mass_residual,
                    "energy_residual_J": energy_residual,
                    "mass_tolerance_kg": mass_limit,
                    "energy_tolerance_J": energy_limit,
                    "within_budget": within,
                    "native_step": asdict(row),
                }
            )
            pressures.append(row.thermo.pressure_Pa)
        last = native_rows[-1]
        if not math.isclose(last.time_s, window.duration_s, rel_tol=1e-12, abs_tol=1e-12):
            raise ValueError("native simulation did not cover the complete operating window")
        state = last.state
        thermo = last.thermo
        liquid_flows, vapor_flows = flows(last.time_s, thermo)
        derivative = tank.derivative(
            state,
            liquid_flows,
            vapor_flows,
            window.ambient_temperature_K,
            window.direct_lower_wall_heat_W,
            window.direct_upper_wall_heat_W,
            pressure_hint_Pa=thermo.pressure_Pa,
            direct_liquid_heat_W=window.direct_liquid_heat_W,
            direct_vapor_heat_W=window.direct_vapor_heat_W,
        )
        vent_result = (
            None
            if valve is None
            else asdict(valve.evaluate(thermo.vapor, backpressure, opening=window.vent_opening))
        )
        endpoint_rows.append(
            {
                "window_id": window.id,
                "time_s": q.start_time_s + elapsed + last.time_s,
                "native_endpoint_derivative": asdict(derivative),
                "native_vent_result": vent_result,
                "endpoint_evaporation_rate_kg_s": derivative.evaporation_rate_kg_s,
                "endpoint_vent_mass_flow_kg_s": 0.0 if vent_result is None else vent_result["mass_flow_kg_s"],
            }
        )
        windows.append(
            {
                "window": window.model_dump(mode="json"),
                "native_initial_state": asdict(window_initial),
                "constant_liquid_inlet_state": None if liquid_inlet is None else asdict(liquid_inlet),
                "constant_vapor_inlet_state": None if vapor_inlet is None else asdict(vapor_inlet),
            }
        )
        elapsed += last.time_s
        cumulative_mass += last.cumulative_boundary_mass_kg
        cumulative_energy += last.cumulative_boundary_energy_J
        prescribed_liquid_mass += liquid_net * last.time_s
        prescribed_vapor_mass += vapor_net * last.time_s
    final = rows[-1]
    limits = q.declared_pressure_limits_pa_abs
    decision = None if limits is None else min(pressures) >= limits[0] and max(pressures) <= limits[1]
    body.update(
        status="computed_diagnostic" if all(r["within_budget"] for r in rows) else "diagnostic_rejected",
        notes=[]
        if all(r["within_budget"] for r in rows)
        else ["native integrated mass/energy residual exceeded declared limits"],
        geometry=geometry_record,
        initial_state=asdict(initial),
        initial_thermo=asdict(initial_thermo),
        windows=windows,
        history=rows,
        endpoint_records=endpoint_rows,
        values={
            "actual_simulated_duration_s": elapsed,
            "requested_duration_s": math.fsum(w.duration_s for w in q.windows),
            "unintegrated_tail_s": math.fsum(w.duration_s for w in q.windows) - elapsed,
            "final_pressure_pa_abs": pressures[-1],
            "minimum_pressure_pa_abs": min(pressures),
            "maximum_pressure_pa_abs": max(pressures),
            "cumulative_vented_mass_kg": final["cumulative_vented_mass_kg"],
            "cumulative_net_liquid_to_vapor_transfer_kg": final["cumulative_net_liquid_to_vapor_transfer_kg"],
            "gross_evaporated_mass_kg": None,
            "gross_condensed_mass_kg": None,
            "cumulative_vent_enthalpy_J": None,
            "pressure_within_supplied_limits": decision,
            "pressure_limit_support": "initial state and accepted-step endpoints only; no dense or RK-stage extrema claim",
            "vent_mass_derivation": "constant prescribed phase net mass minus native integrated total boundary mass",
            "phase_transfer_derivation": "phase inventory change minus prescribed phase boundary mass plus vent mass; signed net, not gross loss",
        },
    )


def evaluate_tank_breathing(request: TankBreathingRequest, context: RunContext):
    q = TankBreathingRequest.model_validate(request.model_dump(mode="python"))
    c = RunContext.model_validate(context.model_dump(mode="python"))
    body = {
        "schema": "pipeline_integrity.tank_breathing.v1",
        "request": q.model_dump(mode="json"),
        "context": c.model_dump(mode="json"),
        "implementation_sha256": file_digest(Path(__file__)),
        "status": "needs_data",
        "notes": _policy(q, c),
        "native_called": False,
        "provider": None,
        "history": None,
        "endpoint_records": None,
        "values": None,
        "physical_validation_claim": False,
        "broad_tank_breathing_complete": False,
        "qualification": "conditional native pure-hydrogen inventory/vent integration; not an independent validation record",
    }
    if not body["notes"]:
        if q.vent.mode in ("pressure_relief_hysteresis", "atmospheric_mixture"):
            body.update(
                status="out_of_scope",
                notes=[
                    "stateful relief/vacuum hysteresis in RK4 trial evaluations and mixed-gas atmospheric breathing require a qualified owner event/mixture protocol"
                ],
            )
        else:
            work = 0
            for w in q.windows:
                ratio = w.duration_s / w.timestep_s
                if not math.isfinite(ratio) or w.timestep_s > w.maximum_reviewed_step_s:
                    raise ValueError("window exceeds reviewed finite step domain")
                work += math.ceil(ratio)
                if work > q.maximum_steps:
                    raise ValueError("requested work exceeds native step budget")
            try:
                before = native_backend_metadata()
            except ImportError as exc:
                body.update(status="out_of_scope", notes=[f"{type(exc).__name__}: {exc}"])
            else:
                if before != q.owner_pins:
                    raise ValueError("reviewed tank owner/source/runtime/property pins changed")
                total = math.fsum(w.duration_s for w in q.windows)
                if (
                    not math.isfinite(total)
                    or total > q.maximum_duration_s
                    or not math.isfinite(q.start_time_s + total)
                ):
                    raise ValueError("requested duration exceeds reviewed work/clock domain")
                body["provider"] = before
                try:
                    _native(q, c, body)
                    json_value(body)
                except (ImportError, ValueError, RuntimeError, ArithmeticError, TypeError, KeyError) as exc:
                    body.update(status="out_of_scope", notes=[f"{type(exc).__name__}: {exc}"])
                    # If the optional backend vanished before owner entry, return
                    # the diagnostic receipt without trying to rediscover it.
                    if isinstance(exc, ImportError) and not body["native_called"]:
                        body["provider"] = None
                        body = json_value(body)
                        return {**body, "record_digest": digest(body)}
                if native_backend_metadata() != before:
                    raise ValueError("tank owner/reference changed during evaluation")
    body = json_value(body)
    return {**body, "record_digest": digest(body)}


def verify_tank_breathing_record(record, *, request=None, context=None):
    try:
        if record["record_digest"] != digest({k: v for k, v in record.items() if k != "record_digest"}):
            raise ValueError("tank breathing receipt changed")
        q = TankBreathingRequest.model_validate_json(json.dumps(record["request"], allow_nan=False))
        c = RunContext.model_validate_json(json.dumps(record["context"], allow_nan=False))
        if (request is not None and q != request) or (context is not None and c != context):
            raise ValueError("caller request/context differs")
        if evaluate_tank_breathing(q, c) != record:
            raise ValueError("native tank/vent/ledger/property/qualification differs on replay")
        return {"valid": True}
    except (ValueError, TypeError, KeyError, OSError, ImportError, AttributeError) as exc:
        return {"valid": False, "reason": str(exc)}


def to_signal_frames(record, context):
    if (
        record.get("status") != "computed_diagnostic"
        or not verify_tank_breathing_record(record, context=context)["valid"]
    ):
        raise ValueError("a complete verified tank history and actual matching context are required")
    return tuple(
        SignalFrame(
            stream_id=f"{context.asset_id}/{record['request']['event_id']}/tank_breathing",
            clock_id=context.clock_id,
            time_s=r["time_s"],
            values={
                "pressure_pa_abs": r["thermo"]["pressure_Pa"],
                "liquid_mass_kg": r["state"]["liquid_mass_kg"],
                "vapor_mass_kg": r["state"]["vapor_mass_kg"],
                "cumulative_vented_mass_kg": r["cumulative_vented_mass_kg"],
                "net_phase_transfer_kg": r["cumulative_net_liquid_to_vapor_transfer_kg"],
            },
            units={
                "pressure_pa_abs": "Pa",
                "liquid_mass_kg": "kg",
                "vapor_mass_kg": "kg",
                "cumulative_vented_mass_kg": "kg",
                "net_phase_transfer_kg": "kg",
            },
            valid=dict.fromkeys(
                (
                    "pressure_pa_abs",
                    "liquid_mass_kg",
                    "vapor_mass_kg",
                    "cumulative_vented_mass_kg",
                    "net_phase_transfer_kg",
                ),
                True,
            ),
            origin="synthetic" if context.origin == "synthetic" else "simulation",
            operating_mode="conditional_pure_hydrogen_tank_inventory",
        )
        for r in record["history"]
    )
