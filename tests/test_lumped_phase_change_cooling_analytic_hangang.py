"""Independent cooling-direction phase-event and energy reference.

This fixture uses authored lumped, constant-property material data and makes
no claim about spatial fronts or field behaviour.
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


@pytest.mark.parametrize("ambient_K, duration_s", [(270.0, 180.0), (260.0, 220.0)])
def test_liquid_to_latent_to_solid_cooling_matches_closed_form_events_and_energy(
    ambient_K, duration_s
):
    mass_kg = 2.0
    transition_K = 300.0
    cp_solid = 1000.0
    cp_liquid = 2400.0
    latent_per_kg = 100_000.0
    conductance = 100.0
    initial_temperature_K = 320.0
    solid_capacity = mass_kg * cp_solid
    liquid_capacity = mass_kg * cp_liquid
    latent = mass_kg * latent_per_kg
    initial_enthalpy = latent + liquid_capacity * (initial_temperature_K - transition_K)
    drive = conductance * (ambient_K - transition_K)
    liquid_equilibrium = latent + liquid_capacity * (ambient_K - transition_K)
    liquid_event_time = -(liquid_capacity / conductance) * math.log(
        (latent - liquid_equilibrium) / (initial_enthalpy - liquid_equilibrium)
    )
    latent_event_time = liquid_event_time + latent / (-drive)
    solid_equilibrium = solid_capacity * (ambient_K - transition_K)
    solid_duration = duration_s - latent_event_time
    final_enthalpy = solid_equilibrium * (
        1.0 - math.exp(-conductance * solid_duration / solid_capacity)
    )
    final_temperature = transition_K + final_enthalpy / solid_capacity

    evidence = tuple(
        EvidenceRef(
            id=identifier,
            citation="Authored synthetic lumped phase-change cooling case",
            origin="synthetic",
        )
        for identifier in ("model", "material", "boundary")
    )
    context = RunContext(
        asset_id="lumped-body-cooling",
        scenario_id=f"cooling-{ambient_K:g}K",
        clock_id="elapsed",
        origin="synthetic",
        evidence=evidence,
    )
    request = PhaseStorageRequest(
        id="liquid-through-latent-to-solid",
        asset_id=context.asset_id,
        clock_id=context.clock_id,
        mass_kg=mass_kg,
        transition_temperature_K=transition_K,
        solid_heat_capacity_J_kg_K=cp_solid,
        liquid_heat_capacity_J_kg_K=cp_liquid,
        latent_heat_J_kg=latent_per_kg,
        initial_enthalpy_J=initial_enthalpy,
        windows=(
            PhaseStorageWindow(
                id="sub-transition-cooling",
                duration_s=duration_s,
                ambient_temperature_K=ambient_K,
                conductance_W_K=conductance,
                evidence_id="boundary",
            ),
        ),
        lumped_temperature_and_constant_properties_reviewed=True,
        evidence_ids=("model", "material"),
    )
    result = evaluate_phase_storage(request, context)
    events = result["phase_events"]
    final = result["states"][-1]

    assert result["status"] == "computed"
    assert [event["from_region"] for event in events] == ["liquid", "transition"]
    assert [event["direction"] for event in events] == ["cooling", "cooling"]
    assert [event["boundary_enthalpy_J"] for event in events] == pytest.approx([latent, 0.0])
    assert [event["time_s"] for event in events] == pytest.approx(
        [liquid_event_time, latent_event_time], abs=2e-12
    )
    assert final["phase"] == "solid"
    assert final["liquid_mass_fraction"] == 0.0
    assert final["enthalpy_J"] == pytest.approx(final_enthalpy, rel=2e-13)
    assert final["temperature_K"] == pytest.approx(final_temperature, rel=2e-13)
    assert result["ambient_heat_J"] == pytest.approx(final_enthalpy - initial_enthalpy, abs=1e-7)
    assert result["energy_residual_J"] == pytest.approx(0.0, abs=1e-7)
    assert verify_phase_storage_record(result, request=request, context=context)
