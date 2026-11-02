"""Adiabatic ideal-gas law across pressure-coupled tank drainage.

The vertical tank and liquid/gas properties are authored synthetic values.
The test checks the integrated gas/liquid feedback path, not field performance.
"""

import math

import pytest

from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.gas_headspace import HeadspaceRequest, HeadspaceWindow
from pipeline_integrity.headspace_drainage import (
    HeadspaceDrainageRequest,
    evaluate_headspace_drainage,
)
from pipeline_integrity.tank_drainage import DrainageRequest
from pipeline_integrity.tanks import AxisymmetricTank


def _coupled_case():
    diameter_m = 1.0
    tank_length_m = 2.0
    initial_liquid_volume_m3 = 1.0
    tank_capacity_m3 = math.pi * diameter_m**2 * tank_length_m / 4.0
    initial_gas_volume_m3 = tank_capacity_m3 - initial_liquid_volume_m3
    gas_temperature_K = 300.0
    gas_pressure_Pa = 150_000.0
    gas_constant_J_kg_K = 287.0
    cv_J_kg_K = 718.0
    initial_gas_mass_kg = (
        gas_pressure_Pa * initial_gas_volume_m3 / (gas_constant_J_kg_K * gas_temperature_K)
    )
    context = RunContext(
        asset_id="synthetic-drainage-tank",
        scenario_id="adiabatic-gas-expansion",
        clock_id="seconds",
        origin="synthetic",
        evidence=(EvidenceRef(id="reference", citation="Authored synthetic drainage case", origin="synthetic"),),
    )
    geometry = AxisymmetricTank(
        inner_diameter_m=diameter_m,
        straight_length_m=tank_length_m,
        horizontal=False,
        evidence_id="reference",
    )
    drainage = DrainageRequest(
        id="drainage",
        asset_id=context.asset_id,
        clock_id=context.clock_id,
        geometry=geometry,
        initial_volume_m3=initial_liquid_volume_m3,
        stop_volume_m3=0.2,
        duration_s=20.0,
        maximum_step_s=1.0,
        tank_gas_pressure_pa_abs=gas_pressure_Pa,
        downstream_pressure_pa_abs=100_000.0,
        tank_bottom_minus_outlet_elevation_m=0.0,
        pipe_length_m=1.0,
        pipe_diameter_m=0.02,
        roughness_m=0.0,
        minor_loss_coefficient=0.0,
        density_kg_m3=1000.0,
        viscosity_pa_s=1.0,
        maximum_volume_flow_m3_s=0.001,
        flow_absolute_tolerance_m3_s=1e-12,
        volume_absolute_tolerance_m3=1e-10,
        transition_policy="withhold",
        quasi_steady_nonflashing_single_phase_reviewed=True,
        evidence_ids=("reference",),
    )
    headspace = HeadspaceRequest(
        id="headspace",
        asset_id=context.asset_id,
        clock_id=context.clock_id,
        volume_m3=initial_gas_volume_m3,
        gas_constant_J_kg_K=gas_constant_J_kg_K,
        cv_J_kg_K=cv_J_kg_K,
        initial_mass_kg=initial_gas_mass_kg,
        initial_temperature_K=gas_temperature_K,
        maximum_step_s=1.0,
        windows=(
            HeadspaceWindow(
                id="adiabatic-window",
                duration_s=20.0,
                inlet_temperature_K=gas_temperature_K,
                ambient_temperature_K=gas_temperature_K,
                ambient_conductance_W_K=0.0,
                heat_input_W=0.0,
                volume_change_m3_s=0.0,
            ),
        ),
        evidence_ids=("reference",),
    )
    request = HeadspaceDrainageRequest(
        id="adiabatic-coupled-drain",
        drainage=drainage,
        headspace=headspace,
        coupling_evidence_id="reference",
        rigid_shell_and_interface_volume_displacement_reviewed=True,
        ideal_gas_fixed_composition_and_constant_liquid_properties_reviewed=True,
        minimum_forward_driving_pressure_pa=1.0,
        sample_step_s=1.0,
        relative_tolerance=1e-10,
        absolute_volume_tolerance_m3=1e-10,
        absolute_gas_mass_tolerance_kg=1e-10,
        absolute_gas_energy_tolerance_J=1e-5,
        absolute_energy_balance_tolerance_J=1e-5,
        relative_energy_balance_tolerance=1e-9,
    )
    return request, context, initial_gas_volume_m3, gas_pressure_Pa, cv_J_kg_K, gas_constant_J_kg_K


def test_adiabatic_headspace_pressure_work_tracks_liquid_displacement_and_conserves_mass_energy():
    request, context, initial_volume, initial_pressure, cv, gas_constant = _coupled_case()
    record = evaluate_headspace_drainage(request, context)

    assert record["status"] == "computed_diagnostic"
    assert record["gas_pressure_feedback_to_liquid_flow"] is True
    assert record["liquid_displacement_feedback_to_gas_work"] is True
    assert record["completed_requested_duration"] is True
    gamma = (cv + gas_constant) / cv
    states = record["states"]
    invariant = initial_pressure * initial_volume**gamma
    for row in states:
        assert row["gas_mass_kg"] == pytest.approx(request.headspace.initial_mass_kg, abs=1e-10)
        assert row["pressure_pa_abs"] * row["gas_volume_m3"] ** gamma == pytest.approx(
            invariant, rel=2e-8
        )
        assert abs(row["liquid_volume_residual_m3"]) <= 1e-8
        assert abs(row["gas_mass_residual_kg"]) <= 1e-8
        assert abs(row["gas_energy_residual_J"]) <= 1e-4
    assert states[-1]["gas_volume_m3"] > states[0]["gas_volume_m3"]
    assert states[-1]["pressure_pa_abs"] < states[0]["pressure_pa_abs"]
    assert abs(record["states"][-1]["gas_energy_residual_J"]) < 1e-4
