"""Prescribed-flux frost growth with changing resistance and sensible storage.

A fixed-area flat frost layer and a lumped substrate have separate temperatures.
The frost node represents the layer midpoint; each side contributes half its
thickness resistance. Supplied mass fluxes carry a declared common-reference
enthalpy. Vapor transfer kinetics and a local surface phase-equilibrium closure
are external inputs, not predictions made by this reduced thermal model.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from pydantic import Field, model_validator

from .contracts import Contract, Name, Nonnegative, Positive, RunContext, SignalFrame, digest
from .datasets import file_digest
from .deposition_inventory import prescribed_mass_step


class FrostMaterial(Contract):
    density_kg_m3: Positive
    solid_heat_capacity_J_kg_K: Positive
    liquid_heat_capacity_J_kg_K: Positive
    vapor_heat_capacity_J_kg_K: Positive
    conductivity_W_m_K: Positive
    phase_reference_temperature_K: Positive
    latent_sublimation_at_reference_J_kg: Positive
    latent_fusion_at_reference_J_kg: Positive
    evidence_id: Name

    @model_validator(mode="after")
    def latent(self):
        if self.latent_sublimation_at_reference_J_kg <= self.latent_fusion_at_reference_J_kg:
            raise ValueError("vapor/solid enthalpy offset must exceed liquid/solid offset at the common reference")
        return self


class FrostFluxWindow(Contract):
    id: Name
    duration_s: Positive
    maximum_step_s: Positive
    vapor_deposition_kg_m2_s: Nonnegative
    melting_removal_kg_m2_s: Nonnegative = 0.0
    sublimation_removal_kg_m2_s: Nonnegative = 0.0
    incoming_vapor_temperature_K: Positive
    outgoing_vapor_temperature_K: Positive
    outgoing_liquid_temperature_K: Positive
    ambient_temperature_K: Positive
    ambient_film_W_m2_K: Nonnegative
    substrate_reservoir_temperature_K: Positive
    substrate_reservoir_conductance_W_K: Nonnegative
    frost_direct_heat_W: float = 0.0
    substrate_direct_heat_W: float = 0.0
    evidence_id: Name


class FrostGrowthRequest(Contract):
    id: Name
    asset_id: Name
    clock_id: Name
    start_time_s: Nonnegative = 0.0
    area_m2: Positive
    material: FrostMaterial
    initial_thickness_m: Positive
    minimum_supported_thickness_m: Positive
    maximum_supported_thickness_m: Positive
    initial_frost_temperature_K: Positive
    initial_substrate_temperature_K: Positive
    substrate_heat_capacity_J_K: Positive
    substrate_contact_resistance_K_W: Nonnegative = 0.0
    minimum_supported_temperature_K: Positive
    maximum_substrate_temperature_K: Positive
    windows: tuple[FrostFluxWindow, ...] = Field(min_length=1, max_length=10000)
    absolute_energy_tolerance_J: Positive
    relative_energy_tolerance: Positive = Field(default=1e-9, le=1e-3)
    maximum_steps: int = Field(default=20000, ge=1, le=100000)
    maximum_result_bytes: int = Field(default=32000000, ge=1000, le=256000000)
    geometry_and_midpoint_thermal_storage_reviewed: bool = False
    constant_properties_and_solid_only_storage_reviewed: bool = False
    fluxes_and_phase_change_energy_boundary_reviewed: bool = False
    evidence_ids: tuple[Name, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def domain(self):
        if not self.minimum_supported_thickness_m <= self.initial_thickness_m <= self.maximum_supported_thickness_m:
            raise ValueError("initial frost thickness is outside its reviewed support")
        reference = self.material.phase_reference_temperature_K
        if not self.minimum_supported_temperature_K <= self.initial_frost_temperature_K <= reference:
            raise ValueError("initial frost temperature must be in the reviewed solid-temperature range")
        if not self.minimum_supported_temperature_K <= self.initial_substrate_temperature_K <= self.maximum_substrate_temperature_K:
            raise ValueError("initial substrate temperature is outside support")
        if len({w.id for w in self.windows}) != len(self.windows):
            raise ValueError("frost window IDs must be unique")
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("frost evidence IDs must be unique")
        for w in self.windows:
            if w.melting_removal_kg_m2_s and w.outgoing_liquid_temperature_K < reference:
                raise ValueError("supplied melted outlet is below the declared melting reference")
        if sum(math.ceil(w.duration_s / w.maximum_step_s) for w in self.windows) > self.maximum_steps:
            raise ValueError("frost trajectory exceeds step budget")
        return self


def frost_growth_metadata():
    return {"frost_growth_sha256": file_digest(Path(__file__)),
            "deposition_inventory_sha256": file_digest(Path(__file__).with_name("deposition_inventory.py"))}


def evaluate_frost_growth(request: FrostGrowthRequest, context: RunContext):
    q = FrostGrowthRequest.model_validate(request.model_dump(mode="python"))
    c = RunContext.model_validate(context.model_dump(mode="python"))
    if (q.asset_id, q.clock_id) != (c.asset_id, c.clock_id):
        raise ValueError("frost asset/clock differs from context")
    if not all((q.geometry_and_midpoint_thermal_storage_reviewed,
                q.constant_properties_and_solid_only_storage_reviewed,
                q.fluxes_and_phase_change_energy_boundary_reviewed)):
        raise ValueError("frost geometry, thermal-domain and supplied phase-flux reviews are required")
    evidence_ids = [e.id for e in c.evidence]
    if len(evidence_ids) != len(set(evidence_ids)):
        raise ValueError("frost source evidence must be unambiguous")
    required = set(q.evidence_ids) | {q.material.evidence_id} | {w.evidence_id for w in q.windows}
    if not required <= set(evidence_ids):
        raise ValueError("frost material/geometry/flux evidence is absent")
    pins, q_pin, c_pin = frost_growth_metadata(), digest(q), digest(c)
    material = q.material
    reference = material.phase_reference_temperature_K
    cp = material.solid_heat_capacity_J_kg_K
    mass = initial_mass = material.density_kg_m3 * q.area_m2 * q.initial_thickness_m
    frost_t, wall_t = q.initial_frost_temperature_K, q.initial_substrate_temperature_K
    wall_c = q.substrate_heat_capacity_J_K
    initial_energy = mass * cp * (frost_t - reference) + wall_c * (wall_t - reference)
    if not all(math.isfinite(v) and v > 0 for v in (
            mass, material.density_kg_m3*q.area_m2, material.conductivity_W_m_K*q.area_m2)) or not math.isfinite(initial_energy):
        raise ValueError("initial frost geometry/storage leaves finite positive arithmetic")
    time = q.start_time_s
    deposited = melted = sublimated = 0.0
    heat_totals = dict(vapor_enthalpy_in_J=0.0, liquid_enthalpy_out_J=0.0,
                       vapor_enthalpy_out_J=0.0, ambient_heat_J=0.0,
                       reservoir_heat_J=0.0, direct_heat_J=0.0)

    def state():
        thickness = mass / (material.density_kg_m3 * q.area_m2)
        return dict(time_s=time, frost_mass_kg=mass, thickness_m=thickness,
                    frost_temperature_K=frost_t, substrate_temperature_K=wall_t,
                    frost_heat_capacity_J_K=mass * cp,
                    frost_layer_resistance_K_W=thickness / (material.conductivity_W_m_K * q.area_m2),
                    total_stored_energy_J=mass * cp * (frost_t-reference) + wall_c * (wall_t-reference))

    states, steps = [state()], []
    retained_bytes = len(json.dumps(states).encode("utf-8"))
    failure = None
    try:
        for window in q.windows:
            count = math.ceil(window.duration_s / window.maximum_step_s)
            dt = window.duration_s / count
            incoming_rate = window.vapor_deposition_kg_m2_s * q.area_m2
            melt_rate = window.melting_removal_kg_m2_s * q.area_m2
            sub_rate = window.sublimation_removal_kg_m2_s * q.area_m2
            h_in = material.latent_sublimation_at_reference_J_kg + material.vapor_heat_capacity_J_kg_K * (window.incoming_vapor_temperature_K-reference)
            h_sub = material.latent_sublimation_at_reference_J_kg + material.vapor_heat_capacity_J_kg_K * (window.outgoing_vapor_temperature_K-reference)
            h_melt = material.latent_fusion_at_reference_J_kg + material.liquid_heat_capacity_J_kg_K * (window.outgoing_liquid_temperature_K-reference)
            mass_power = incoming_rate*h_in - melt_rate*h_melt - sub_rate*h_sub
            for local_step in range(count):
                if not math.isfinite(dt) or dt <= 0 or time + dt <= time:
                    raise ValueError("frost integration step is not representable on the clock")
                next_mass, dm_in, _ = prescribed_mass_step(mass, incoming_rate, melt_rate+sub_rate, dt)
                thickness = next_mass / (material.density_kg_m3*q.area_m2)
                if not q.minimum_supported_thickness_m <= thickness <= q.maximum_supported_thickness_m:
                    raise ValueError("frost growth/removal leaves the reviewed positive-thickness support")
                half_r = thickness/(2*material.conductivity_W_m_K*q.area_m2)
                if not math.isfinite(half_r) or half_r <= 0:
                    raise ValueError("frost half-layer resistance is outside finite positive arithmetic")
                base_g = 1/(half_r+q.substrate_contact_resistance_K_W)
                film_g = window.ambient_film_W_m2_K*q.area_m2
                if not math.isfinite(film_g):
                    raise ValueError("ambient frost-film conductance is outside finite arithmetic")
                outer_g = 0.0 if film_g == 0 else 1/(half_r+1/film_g)
                back_g = window.substrate_reservoir_conductance_W_K
                # Open-volume storage is m_new cp theta_new - m_old cp
                # theta_old; dropping the old/new mass difference would lose
                # the sensible energy carried by growth or material removal.
                a = next_mass*cp/dt+base_g+outer_g
                b = wall_c/dt+base_g+back_g
                rhs_f = mass*cp*(frost_t-reference)/dt+mass_power+window.frost_direct_heat_W+outer_g*(window.ambient_temperature_K-reference)
                rhs_w = wall_c*(wall_t-reference)/dt+window.substrate_direct_heat_W+back_g*(window.substrate_reservoir_temperature_K-reference)
                # Evaluate the positive determinant as a sum to avoid the
                # cancellation in a*b-base_g**2 for very strong coupling.
                fa, wb = next_mass*cp/dt+outer_g, wall_c/dt+back_g
                determinant = fa*wb+base_g*(fa+wb)
                if not math.isfinite(determinant) or determinant <= 0:
                    raise ValueError("frost thermal system leaves finite positive arithmetic")
                next_frost = reference+(rhs_f*b+base_g*rhs_w)/determinant
                next_wall = reference+(a*rhs_w+base_g*rhs_f)/determinant
                if not (q.minimum_supported_temperature_K <= next_frost <= reference
                        and q.minimum_supported_temperature_K <= next_wall <= q.maximum_substrate_temperature_K):
                    raise ValueError("frost thermal step leaves the reviewed solid/substrate temperature support")
                ambient = dt*outer_g*(window.ambient_temperature_K-next_frost)
                reservoir = dt*back_g*(window.substrate_reservoir_temperature_K-next_wall)
                internal = dt*base_g*(next_frost-next_wall)
                frost_delta = next_mass*cp*(next_frost-reference)-mass*cp*(frost_t-reference)
                wall_delta = wall_c*(next_wall-wall_t)
                frost_residual = frost_delta-(dt*(mass_power+window.frost_direct_heat_W)+ambient-internal)
                wall_residual = wall_delta-(dt*window.substrate_direct_heat_W+reservoir+internal)
                scale = q.absolute_energy_tolerance_J+q.relative_energy_tolerance*max(
                    abs(frost_delta),abs(wall_delta),abs(dt*mass_power),abs(ambient),abs(reservoir),abs(internal))
                if not all(math.isfinite(v) for v in (frost_residual,wall_residual,scale)) or max(abs(frost_residual),abs(wall_residual)) > scale:
                    raise ValueError("frost step failed individual thermal-node energy conservation")
                step = dict(window_id=window.id, local_step=local_step, duration_s=dt,
                            frost_to_substrate_heat_J=internal, ambient_heat_J=ambient,
                            reservoir_heat_J=reservoir, vapor_enthalpy_in_J=dm_in*h_in,
                            liquid_enthalpy_out_J=melt_rate*dt*h_melt,
                            vapor_enthalpy_out_J=sub_rate*dt*h_sub,
                            direct_heat_J=dt*(window.frost_direct_heat_W+window.substrate_direct_heat_W),
                            frost_energy_residual_J=frost_residual, substrate_energy_residual_J=wall_residual,
                            full_frost_resistance_K_W=2*half_r, ambient_to_frost_conductance_W_K=outer_g,
                            frost_to_substrate_conductance_W_K=base_g)
                # Validate retention before promoting a state or its flux ledger.
                estimate = len(json.dumps(step).encode("utf-8"))+1000
                if retained_bytes+estimate > q.maximum_result_bytes:
                    raise ValueError("frost retained trajectory exceeds result-byte budget")
                retained_bytes += estimate
                mass, frost_t, wall_t = next_mass, next_frost, next_wall
                time += dt
                deposited += dm_in
                melted += melt_rate*dt
                sublimated += sub_rate*dt
                for key in heat_totals:
                    heat_totals[key] += step[key]
                states.append(state())
                steps.append(step)
    except (ValueError, ArithmeticError) as error:
        failure = str(error)
    if frost_growth_metadata() != pins or digest(q) != q_pin or digest(c) != c_pin:
        raise ValueError("frost implementation/request/context changed during evaluation")
    net_energy = (heat_totals["vapor_enthalpy_in_J"]-heat_totals["liquid_enthalpy_out_J"]
                  -heat_totals["vapor_enthalpy_out_J"]+heat_totals["ambient_heat_J"]
                  +heat_totals["reservoir_heat_J"]+heat_totals["direct_heat_J"])
    body = dict(schema="pipeline_integrity.frost_growth.v1", request=q.model_dump(mode="json"),
                context=c.model_dump(mode="json"), status="withheld" if failure else "computed_diagnostic",
                failure=failure, states=states, steps=steps, energy_ledger=heat_totals,
                deposited_mass_kg=deposited, melted_mass_kg=melted, sublimated_mass_kg=sublimated,
                mass_residual_kg=mass-initial_mass-deposited+melted+sublimated,
                energy_residual_J=states[-1]["total_stored_energy_J"]-initial_energy-net_energy,
                enthalpy_reference="solid_at_supplied_phase_reference_temperature",
                implementation=pins, transfer_kinetics_predicted=False,
                surface_phase_equilibrium_solved=False, numerical_validation_completed=False)
    if len(json.dumps(body).encode("utf-8")) > q.maximum_result_bytes:
        raise ValueError("frost complete receipt exceeds result-byte budget")
    body["record_digest"] = digest(body)
    return body


def verify_frost_growth_record(record, *, request=None, context=None):
    if digest({k:v for k,v in record.items() if k != "record_digest"}) != record.get("record_digest"):
        raise ValueError("frost receipt changed")
    q = FrostGrowthRequest.model_validate_json(json.dumps(record["request"]))
    c = RunContext.model_validate_json(json.dumps(record["context"]))
    if request is not None and request.model_dump(mode="json") != q.model_dump(mode="json"):
        raise ValueError("frost caller request differs")
    if context is not None and context.model_dump(mode="json") != c.model_dump(mode="json"):
        raise ValueError("frost caller context differs")
    if record.get("status") == "withheld":
        raise ValueError("withheld frost trajectory is not a completed replay")
    if evaluate_frost_growth(q,c) != record:
        raise ValueError("frost replay differs")
    return True


def frost_growth_signal_frames(record, *, stream_id: str):
    verify_frost_growth_record(record)
    q = FrostGrowthRequest.model_validate_json(json.dumps(record["request"]))
    units = {"frost_temperature_K":"K", "substrate_temperature_K":"K",
             "thickness_m":"m", "frost_mass_kg":"kg", "frost_layer_resistance_K_W":"K/W"}
    frames = tuple(SignalFrame(stream_id=stream_id,clock_id=q.clock_id,time_s=s["time_s"],
                              values={key:s[key] for key in units},units=units,
                              valid={key:True for key in units},origin="simulation",
                              operating_mode="prescribed_flux_frost") for s in record["states"])
    receipt = dict(schema="pipeline_integrity.frost_frames.v1",source_record_digest=record["record_digest"],
                   stream_id=stream_id,asset_id=q.asset_id,clock_id=q.clock_id,
                   frames_digest=digest(frames),origin="simulation")
    receipt["record_digest"] = digest(receipt)
    return frames,receipt
