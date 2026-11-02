"""Cylindrical deposit inventories driven by supplied deposition/removal rates.

Rates are external physical inputs, not equilibrium predictions or fitted wax /
hydrate kinetics. Removed material exits the selected control volume explicitly.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from importlib.metadata import version
from .datasets import file_digest
from pydantic import Field, model_validator
from .contracts import Contract, Name, Positive, Nonnegative, Fraction, RunContext, digest, require_context_extension
from .models.hydraulics import LiquidPressureLoss, liquid_pressure_loss
from typing import Literal


class DepositWindow(Contract):
    id: Name
    duration_s: Positive
    deposition_kg_s: Nonnegative
    removal_kg_s: Nonnegative
    cleaning_fraction_at_end: Fraction = 0.0
    evidence_id: Name


class DepositInventoryRequest(Contract):
    id: Name
    asset_id: Name
    clock_id: Name
    start_time_s: Nonnegative = 0.0
    clean_inner_diameter_m: Positive
    length_m: Positive
    deposit_density_kg_m3: Positive
    initial_thickness_m: Nonnegative = 0.0
    minimum_open_diameter_m: Positive
    windows: tuple[DepositWindow, ...] = Field(min_length=1, max_length=10000)
    evidence_ids: tuple[Name, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def geometry(self):
        if self.minimum_open_diameter_m >= self.clean_inner_diameter_m:
            raise ValueError("minimum opening must be below clean diameter")
        if self.initial_thickness_m*2 >= self.clean_inner_diameter_m:
            raise ValueError("initial deposit closes the pipe")
        if len({w.id for w in self.windows}) != len(self.windows):
            raise ValueError("deposit windows must be uniquely identified")
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("deposit evidence IDs must be unique")
        return self


def prescribed_mass_step(initial_mass_kg: float, deposition_kg_s: float,
                         removal_kg_s: float, duration_s: float):
    """Advance a supplied nonnegative inventory without clipping absent material."""
    values = (initial_mass_kg, deposition_kg_s, removal_kg_s, duration_s)
    if not all(math.isfinite(v) for v in values) or min(values) < 0 or duration_s <= 0:
        raise ValueError("prescribed mass step requires finite nonnegative inventory/rates and positive time")
    deposited, removed = deposition_kg_s * duration_s, removal_kg_s * duration_s
    endpoint = math.fsum((initial_mass_kg, deposited, -removed))
    if not all(math.isfinite(v) for v in (deposited, removed, endpoint)):
        raise ValueError("prescribed mass step exceeded finite arithmetic")
    if endpoint < 0:
        raise ValueError("prescribed removal exceeds available deposit; no silent clipping")
    return endpoint, deposited, removed


def evaluate_deposit_inventory(request: DepositInventoryRequest, context: RunContext) -> dict:
    q = DepositInventoryRequest.model_validate(request.model_dump(mode="python"))
    c = RunContext.model_validate(context.model_dump(mode="python"))
    if (q.asset_id, q.clock_id) != (c.asset_id, c.clock_id):
        raise ValueError("deposit asset/clock differs from context")
    required = set(q.evidence_ids) | {w.evidence_id for w in q.windows}
    if not required <= {e.id for e in c.evidence}:
        raise ValueError("deposit rates or geometry lack context evidence")
    radius = q.clean_inner_diameter_m/2
    factor = math.pi*q.length_m*q.deposit_density_kg_m3
    initial = factor*(radius**2-(radius-q.initial_thickness_m)**2)
    capacity = factor*(radius**2-(q.minimum_open_diameter_m/2)**2)
    mass = initial
    time = q.start_time_s
    deposited = removed = cleaned = 0.0
    states, events = [], []

    def row(window_id, phase):
        if mass < 0 or mass >= factor*radius**2:
            raise ValueError("deposit inventory leaves open-pipe geometry")
        diameter = 2*math.sqrt(radius**2-mass/factor)
        return dict(window_id=window_id, phase=phase, time_s=time, deposit_mass_kg=mass,
                    thickness_m=(q.clean_inner_diameter_m-diameter)/2,
                    open_diameter_m=diameter, flow_area_m2=math.pi*diameter**2/4,
                    below_minimum_opening=diameter < q.minimum_open_diameter_m)

    states.append(row(q.windows[0].id,"initial"))
    if mass >= capacity:
        events.append(dict(kind="minimum_opening_already_reached", time_s=time))
    for window in q.windows:
        rate = window.deposition_kg_s-window.removal_kg_s
        endpoint, deposited_step, removed_step = prescribed_mass_step(
            mass, window.deposition_kg_s, window.removal_kg_s, window.duration_s)
        if endpoint >= factor*radius**2:
            raise ValueError("prescribed deposition closes the pipe; split the window at closure")
        if rate > 0 and mass < capacity <= endpoint:
            events.append(dict(kind="minimum_opening_reached", window_id=window.id,
                               time_s=time+(capacity-mass)/rate))
        if rate < 0 and endpoint < capacity <= mass:
            events.append(dict(kind="minimum_opening_restored", window_id=window.id,
                               time_s=time+(capacity-mass)/rate))
        mass = endpoint
        time += window.duration_s
        deposited += deposited_step
        removed += removed_step
        states.append(row(window.id,"before_cleaning"))
        cleaning_mass = mass*window.cleaning_fraction_at_end
        before_cleaning = mass
        mass -= cleaning_mass
        cleaned += cleaning_mass
        if mass < capacity <= before_cleaning:
            events.append(dict(kind="minimum_opening_restored_by_cleaning", window_id=window.id,time_s=time))
        if cleaning_mass:
            states.append(row(window.id,"after_cleaning"))
    body = dict(schema="pipeline_integrity.deposition_inventory.v1",request=q.model_dump(mode="json"),
                status="computed",
                implementation_sha256=file_digest(Path(__file__)),
                context=c.model_dump(mode="json"), states=states, events=events,
                deposited_mass_kg=deposited, removed_mass_kg=removed, cleaned_mass_kg=cleaned,
                mass_residual_kg=mass-initial-deposited+removed+cleaned,
                kinetics_source="caller_supplied_rates", numerical_validation_completed=False)
    body["record_digest"] = digest(body)
    return body


def verify_deposit_inventory_record(record, *, request=None, context=None):
    if digest({k:v for k,v in record.items() if k!="record_digest"}) != record.get("record_digest"):
        raise ValueError("deposit inventory receipt changed")
    q = DepositInventoryRequest.model_validate_json(json.dumps(record["request"]))
    c = RunContext.model_validate_json(json.dumps(record["context"]))
    if request is not None and request.model_dump(mode="json") != q.model_dump(mode="json"):
        raise ValueError("deposit caller request differs")
    if context is not None and context.model_dump(mode="json") != c.model_dump(mode="json"):
        raise ValueError("deposit caller context differs")
    if evaluate_deposit_inventory(q,c) != record:
        raise ValueError("deposit inventory replay differs")
    return True


class DepositOperatingPoint(Contract):
    window_id: Name
    volume_flow_m3_s: float
    density_kg_m3: Positive
    viscosity_pa_s: Positive
    deposited_surface_roughness_m: Nonnegative
    minor_loss_coefficient: Nonnegative = 0.0
    outlet_minus_inlet_elevation_m: float = 0.0
    deposit_thermal_conductivity_W_m_K: Positive | None = None
    transition_policy: Literal["withhold", "use_declared_correlation"] = "withhold"
    friction_method: Literal["Clamond", "Colebrook", "Haaland", "Swamee_Jain_1976"] = "Clamond"
    evidence_id: Name


class DepositImpactRequest(Contract):
    inventory: DepositInventoryRequest
    operating_points: tuple[DepositOperatingPoint, ...] = Field(min_length=1,max_length=10000)

    @model_validator(mode="after")
    def aligned(self):
        ids = [p.window_id for p in self.operating_points]
        if len(ids) != len(set(ids)) or set(ids) != {w.id for w in self.inventory.windows}:
            raise ValueError("each inventory window needs exactly one operating-point mapping")
        return self


def evaluate_deposit_impacts(request: DepositImpactRequest, context: RunContext) -> dict:
    """Compose changing bore with the shared Darcy solver and radial conduction.

    Operating points are prescribed snapshots, not a pressure-network solution.
    Roughness is supplied independently of thickness. Thermal resistance covers
    the deposit layer only; it does not include wall, films or soil.
    """
    q = DepositImpactRequest.model_validate(request.model_dump(mode="python"))
    c = RunContext.model_validate(context.model_dump(mode="python"))
    if not {p.evidence_id for p in q.operating_points} <= {e.id for e in c.evidence}:
        raise ValueError("deposit hydraulic/thermal properties lack context evidence")
    inventory = evaluate_deposit_inventory(q.inventory,c)
    points = {p.window_id:p for p in q.operating_points}
    outputs = []
    for state in inventory["states"]:
        point = points[state["window_id"]]
        diameter = state["open_diameter_m"]
        parameters = dict(diameter_m=diameter,length_m=q.inventory.length_m,
                          roughness_m=point.deposited_surface_roughness_m,
                          volume_flow_m3_s=point.volume_flow_m3_s,
                          density_kg_m3=point.density_kg_m3,viscosity_pa_s=point.viscosity_pa_s,
                          minor_loss_coefficient=point.minor_loss_coefficient,
                          outlet_minus_inlet_elevation_m=point.outlet_minus_inlet_elevation_m,
                          transition_policy=point.transition_policy,friction_method=point.friction_method)
        if point.deposited_surface_roughness_m/diameter > 0.1:
            hydraulic = dict(status="out_of_scope",values={},notes=["deposit roughness exceeds Darcy owner domain"])
        else:
            hydraulic = liquid_pressure_loss(LiquidPressureLoss(**parameters)).model_dump(mode="json")
        conductivity = point.deposit_thermal_conductivity_W_m_K
        resistance = None if conductivity is None else math.log(q.inventory.clean_inner_diameter_m/diameter)/(
            2*math.pi*conductivity*q.inventory.length_m)
        outputs.append(dict(window_id=state["window_id"],phase=state["phase"],time_s=state["time_s"],
                            open_diameter_m=diameter,hydraulic=hydraulic,
                            deposit_radial_thermal_resistance_K_W=resistance))
    body = dict(schema="pipeline_integrity.deposit_impacts.v1",request=q.model_dump(mode="json"),
                implementation_sha256=file_digest(Path(__file__)),
                hydraulic_owner_source_sha256=file_digest(Path(__file__).parent/"models"/"hydraulics.py"),
                fluids_version=version("fluids"),
                context=c.model_dump(mode="json"),inventory=inventory,impacts=outputs,
                status="computed" if all(p["hydraulic"]["status"]=="computed" for p in outputs) else "partial",
                flow_boundary="prescribed_per_window",numerical_validation_completed=False)
    body["record_digest"] = digest(body)
    return body


def verify_deposit_impacts_record(record, *, request=None, context=None):
    if digest({k:v for k,v in record.items() if k!="record_digest"}) != record.get("record_digest"):
        raise ValueError("deposit impact receipt changed")
    q = DepositImpactRequest.model_validate_json(json.dumps(record["request"]))
    c = RunContext.model_validate_json(json.dumps(record["context"]))
    if request is not None and request.model_dump(mode="json") != q.model_dump(mode="json"):
        raise ValueError("deposit impact caller request differs")
    if context is not None and context.model_dump(mode="json") != c.model_dump(mode="json"):
        raise ValueError("deposit impact caller context differs")
    if evaluate_deposit_impacts(q,c) != record:
        raise ValueError("deposit hydraulic/thermal impact replay differs")
    return True


def deposit_heated_transport_cell(record: dict, *, state_index: int, clean_cell,
                                  output_cell_id: str, mapping_evidence_id: str,
                                  context: RunContext | None = None):
    """Map a replayed deposit state to an existing heated-transport cell.

    The supplied clean-cell conductance must exclude the deposit layer. The
    layer resistance is added in series, retaining the original heater and
    ambient boundaries. This operation does not infer temperature-dependent
    viscosity; that remains the heated-transport property provider's task.
    """
    from .heated_transport import HeatedTransportCell
    verify_deposit_impacts_record(record)
    if type(state_index) is not int or not 0 <= state_index < len(record["impacts"]):
        raise ValueError("a valid deposit impact state index is required")
    base = HeatedTransportCell.model_validate(clean_cell.model_dump(mode="python"))
    q = DepositImpactRequest.model_validate_json(json.dumps(record["request"]))
    c = RunContext.model_validate_json(json.dumps(record["context"]))
    if context is not None:
        target_context=RunContext.model_validate(context.model_dump(mode="python"))
        require_context_extension(c,target_context)
        c=target_context
    if mapping_evidence_id not in {e.id for e in c.evidence}:
        raise ValueError("deposit-to-heated-cell mapping evidence is absent")
    if not math.isclose(base.length_m,q.inventory.length_m,rel_tol=1e-12,abs_tol=0):
        raise ValueError("deposit inventory and heated cell lengths differ")
    if not math.isclose(base.inner_diameter_m,q.inventory.clean_inner_diameter_m,rel_tol=1e-12,abs_tol=0):
        raise ValueError("heated cell must describe the clean pipe geometry")
    impact = record["impacts"][state_index]
    point = next(p for p in q.operating_points if p.window_id==impact["window_id"])
    resistance = impact["deposit_radial_thermal_resistance_K_W"]
    if base.conductance_W_K is None or resistance is None:
        raise ValueError("reviewed clean-cell conductance and deposit conductivity are required")
    conductance = 0.0 if base.conductance_W_K==0 else 1/(1/base.conductance_W_K+resistance)
    payload = base.model_dump(mode="python")
    payload.update(id=output_cell_id,inner_diameter_m=impact["open_diameter_m"],
                   roughness_m=point.deposited_surface_roughness_m,
                   conductance_W_K=conductance,evidence_id=mapping_evidence_id)
    cell = HeatedTransportCell.model_validate(payload)
    body = dict(schema="pipeline_integrity.deposit_heated_cell.v1",
                deposit_impact_digest=record["record_digest"],state_index=state_index,
                clean_cell=base.model_dump(mode="json"),cell=cell.model_dump(mode="json"),
                mapping_evidence_id=mapping_evidence_id,context_digest=digest(c),
                clean_conductance_excludes_deposit_required=True)
    body["record_digest"] = digest(body)
    return cell,body
