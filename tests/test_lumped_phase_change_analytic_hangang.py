"""Analytic event and energy references for lumped phase storage.

Material and boundary conditions are authored synthetic inputs, not test data.
"""
import math

import pytest

from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.lumped_phase_change import (
    PhaseStorageRequest,
    PhaseStorageWindow,
    evaluate_phase_storage,
    verify_phase_storage_record,
)


def test_sensible_latent_sensible_heating_matches_closed_form_events_and_energy():
    mass, cp_s, cp_l = 2.0, 1000.0, 2400.0
    transition, latent_per_mass = 300.0, 100000.0
    conductance, ambient, duration = 100.0, 330.0, 90.0
    initial_enthalpy = -10000.0
    evidence = tuple(
        EvidenceRef(id=identity, citation="Authored synthetic phase-storage reference", origin="synthetic")
        for identity in ("model", "material", "boundary")
    )
    context = RunContext(asset_id="lumped-body", scenario_id="melt-cycle", clock_id="elapsed",
                         origin="synthetic", evidence=evidence)
    request = PhaseStorageRequest(
        id="three-region-melt", asset_id="lumped-body", clock_id="elapsed", mass_kg=mass,
        transition_temperature_K=transition, solid_heat_capacity_J_kg_K=cp_s,
        liquid_heat_capacity_J_kg_K=cp_l, latent_heat_J_kg=latent_per_mass,
        initial_enthalpy_J=initial_enthalpy,
        windows=(PhaseStorageWindow(id="warm", duration_s=duration, ambient_temperature_K=ambient,
            conductance_W_K=conductance, evidence_id="boundary"),),
        lumped_temperature_and_constant_properties_reviewed=True, evidence_ids=("model", "material"),
    )

    record = evaluate_phase_storage(request, context)
    solid_capacity, liquid_capacity = mass * cp_s, mass * cp_l
    latent = mass * latent_per_mass
    drive = conductance * (ambient - transition)
    solid_rate, liquid_rate = conductance / solid_capacity, conductance / liquid_capacity
    solid_equilibrium = solid_capacity * (ambient - transition)
    exact_solid_event = math.log((solid_equilibrium - initial_enthalpy) / solid_equilibrium) / solid_rate
    exact_melt_event = exact_solid_event + latent / drive
    liquid_elapsed = duration - exact_melt_event
    exact_final_enthalpy = latent + liquid_capacity * (ambient - transition) * (
        1.0 - math.exp(-liquid_rate * liquid_elapsed))
    exact_final_temperature = transition + (exact_final_enthalpy - latent) / liquid_capacity
    events, final = record["phase_events"], record["states"][-1]

    assert record["status"] == "computed"
    assert [event["boundary_enthalpy_J"] for event in events] == pytest.approx((0.0, latent))
    assert [event["time_s"] for event in events] == pytest.approx((exact_solid_event, exact_melt_event), abs=2e-12)
    assert [event["from_region"] for event in events] == ["solid", "transition"]
    assert [event["direction"] for event in events] == ["heating", "heating"]
    assert final["phase"] == "liquid" and final["liquid_mass_fraction"] == 1.0
    assert final["enthalpy_J"] == pytest.approx(exact_final_enthalpy, rel=2e-13)
    assert final["temperature_K"] == pytest.approx(exact_final_temperature, rel=2e-13)
    assert record["ambient_heat_J"] == pytest.approx(exact_final_enthalpy - initial_enthalpy, abs=1e-8)
    assert record["energy_residual_J"] == pytest.approx(0.0, abs=1e-8)
    assert verify_phase_storage_record(record, request=request, context=context)
