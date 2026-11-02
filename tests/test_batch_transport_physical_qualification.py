from __future__ import annotations

import math

import pytest

from pipeline_integrity.batch_transport import BatchComposition, BatchInletStep, BatchTransportRequest
from pipeline_integrity.batch_transport_solver import (
    BatchLiquidComponent,
    ConservativeBatchConfiguration,
    ConservativeBatchRequest,
    evaluate_conservative_batch_transport,
)
from pipeline_integrity.contracts import EvidenceRef, RunContext


def _case(*, thermal=False, route=1.0, area=1.0, flow=0.25, duration=0.25, cells=4, uniform=False):
    evidence_ids = ("geometry", "boundary", "properties", "component-a", "component-b")
    if thermal:
        evidence_ids += ("thermal",)
    context = RunContext(
        asset_id="line", scenario_id="qualification", clock_id="elapsed", origin="synthetic",
        evidence=tuple(EvidenceRef(id=key, citation="Local authored synthetic fixture", origin="synthetic",
                                   sha256=(f"{i + 1:064x}")) for i, key in enumerate(evidence_ids)),
    )
    initial = BatchComposition(fraction_by_component={"A": 0.5, "B": 0.5} if uniform else {"A": 1.0, "B": 0.0})
    inlet = BatchComposition(fraction_by_component={"A": 0.5, "B": 0.5} if uniform else {"A": 0.0, "B": 1.0})
    request = BatchTransportRequest(
        id="batch-case", asset_id="line", location_id="route", clock_id="elapsed",
        route_length_m=route, cross_section_area_m2=area, duration_s=duration,
        component_ids=("A", "B"), initial_composition=initial,
        inlet_steps=(BatchInletStep(time_s=0, volume_flow_m3_s=flow, composition=inlet,
                                   temperature_K=300, pressure_Pa_abs=200000),),
        property_basis_id="constant-pure-liquid", dispersion_model_id="zero-dispersion",
        geometry_evidence_id="geometry", boundary_evidence_id="boundary", property_evidence_id="properties",
        absolute_mass_balance_tolerance_kg=1e-9, relative_mass_balance_tolerance=1e-9,
        maximum_mass_numerical_adjustment_kg=1e-7,
        thermal_mode="energy_coupled" if thermal else "prescribed_temperature",
        thermal_model_id="sensible-energy" if thermal else None,
        thermal_evidence_id="thermal" if thermal else None,
        absolute_energy_balance_tolerance_J=1e-7 if thermal else None,
        relative_energy_balance_tolerance=1e-9 if thermal else None,
        maximum_energy_numerical_adjustment_J=1e-7 if thermal else None,
    )
    solver = ConservativeBatchConfiguration(
        id="qualification-config", property_basis_id="constant-pure-liquid",
        dispersion_model_id="zero-dispersion", thermal_model_id="sensible-energy" if thermal else None,
        components=(
            BatchLiquidComponent(id="A", density_kg_m3=800, heat_capacity_J_kg_K=2000,
                                 dynamic_viscosity_Pa_s=0.01, evidence_id="component-a"),
            BatchLiquidComponent(id="B", density_kg_m3=1000, heat_capacity_J_kg_K=4000,
                                 dynamic_viscosity_Pa_s=0.001, evidence_id="component-b"),
        ),
        axial_dispersion_m2_s=0, initial_temperature_K=300, initial_pressure_Pa_abs=200000,
        minimum_temperature_K=250, maximum_temperature_K=400, cells=cells,
        maximum_step_s=duration, ambient_temperature_K=300,
        evidence_ids=("properties",),
        additive_incompressible_volumes_and_mass_fraction_basis_reviewed=True,
        constant_pure_component_properties_and_log_viscosity_mixing_reviewed=True,
        prescribed_flow_pressure_and_dispersion_scope_reviewed=True,
        mechanical_energy_absent_or_in_imposed_heat_reviewed=True,
    )
    return ConservativeBatchRequest(transport=request, solver=solver), context


def test_zero_dispersion_upwind_step_matches_independent_recurrence_and_component_ledgers():
    request, context = _case()
    record = evaluate_conservative_batch_transport(request, context)
    result = record["result"]["native"]
    # For one implicit upwind step with CFL=0.25, phi_i = (CFL/(1+CFL))**i.
    c = 0.25 / 1.25
    expected = [c**i for i in range(1, 5)]
    got = result["final_cell_component_volume_fractions"]
    assert [row[1] for row in got] == pytest.approx(expected, rel=1e-12, abs=1e-15)
    b = result["component_mass_ledger_kg"]["B"]
    assert b["initial"] + b["inlet"] - b["outlet"] - b["final"] + b["numerical_adjustment"] == pytest.approx(0, abs=1e-10)
    assert result["mass_ledger_kg"]["initial"] + result["mass_ledger_kg"]["inlet"] - result["mass_ledger_kg"]["outlet"] - result["mass_ledger_kg"]["final"] + result["mass_ledger_kg"]["numerical_adjustment"] == pytest.approx(0, abs=1e-10)


def test_energy_coupled_uniform_state_closes_sensible_energy_ledger():
    request, context = _case(thermal=True, flow=0.1, duration=0.5, uniform=True)
    record = evaluate_conservative_batch_transport(request, context)
    result = record["result"]["native"]
    temperatures = result["temperature_K"][-(request.solver.cells + 2):]
    assert temperatures == pytest.approx([300.0] * (request.solver.cells + 2), abs=1e-10)
    energy = result["energy_ledger_J"]
    residual = energy["initial"] + energy["inlet"] - energy["outlet"] - energy["final"] + energy["heat_transfer"] + energy["shaft_work"] + energy["numerical_adjustment"]
    assert residual == pytest.approx(0, abs=1e-7)
    assert result["mass_ledger_kg"]["initial"] + result["mass_ledger_kg"]["inlet"] - result["mass_ledger_kg"]["outlet"] - result["mass_ledger_kg"]["final"] + result["mass_ledger_kg"]["numerical_adjustment"] == pytest.approx(0, abs=1e-9)


def test_missing_geometry_evidence_fails_closed_before_transport_result():
    request, context = _case()
    context = context.model_copy(update={"evidence": tuple(item for item in context.evidence if item.id != "geometry")})
    with pytest.raises(ValueError, match="geometry.*evidence"):
        evaluate_conservative_batch_transport(request, context)

