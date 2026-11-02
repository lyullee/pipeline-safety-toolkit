"""Analytic transient sensible-energy validation for conservative batch transport.

A constant-property, two-component batch follows a uniform initial/inlet
composition. A one-dimensional implicit upwind energy equation with ambient
conductance is checked against its independently assembled backward-Euler
recurrence, exact first-cell continuous solution, and global energy ledger.
All inputs are synthetic and define the constitutive scope explicitly.
"""
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


def _case(step_s: float):
    evidence = "review"
    context = RunContext(
        asset_id="uniform-batch-line",
        scenario_id="ambient-energy-step",
        clock_id="elapsed",
        origin="synthetic",
        evidence=(EvidenceRef(
            id=evidence,
            citation="Authored constant-property thermal transient fixture",
            origin="synthetic",
            sha256="a" * 64,
        ),),
    )
    composition = BatchComposition(fraction_by_component={"A": 0.5, "B": 0.5})
    transport = BatchTransportRequest(
        id="batch-energy",
        asset_id=context.asset_id,
        location_id="straight-route",
        clock_id=context.clock_id,
        route_length_m=1.0,
        cross_section_area_m2=0.01,
        duration_s=0.125,
        component_ids=("A", "B"),
        initial_composition=composition,
        inlet_steps=(BatchInletStep(
            time_s=0.0,
            volume_flow_m3_s=0.25,
            composition=composition,
            temperature_K=300.0,
            pressure_Pa_abs=200000.0,
        ),),
        property_basis_id="constant-authored-properties",
        dispersion_model_id="zero-dispersion",
        geometry_evidence_id=evidence,
        boundary_evidence_id=evidence,
        property_evidence_id=evidence,
        absolute_mass_balance_tolerance_kg=1e-8,
        relative_mass_balance_tolerance=1e-10,
        maximum_mass_numerical_adjustment_kg=1e-9,
        thermal_mode="energy_coupled",
        thermal_model_id="sensible-energy-upwind",
        thermal_evidence_id=evidence,
        absolute_energy_balance_tolerance_J=1e-3,
        relative_energy_balance_tolerance=1e-10,
        maximum_energy_numerical_adjustment_J=1e-3,
    )
    solver = ConservativeBatchConfiguration(
        id="constant-property-fv",
        property_basis_id=transport.property_basis_id,
        dispersion_model_id=transport.dispersion_model_id,
        thermal_model_id=transport.thermal_model_id,
        components=(
            BatchLiquidComponent(id="A", density_kg_m3=1000.0,
                                 heat_capacity_J_kg_K=2000.0,
                                 dynamic_viscosity_Pa_s=0.001, evidence_id=evidence),
            BatchLiquidComponent(id="B", density_kg_m3=1000.0,
                                 heat_capacity_J_kg_K=2000.0,
                                 dynamic_viscosity_Pa_s=0.001, evidence_id=evidence),
        ),
        axial_dispersion_m2_s=0.0,
        initial_temperature_K=300.0,
        initial_pressure_Pa_abs=200000.0,
        sensible_energy_reference_temperature_K=300.0,
        minimum_temperature_K=290.0,
        maximum_temperature_K=400.0,
        cells=4,
        maximum_step_s=step_s,
        ambient_temperature_K=350.0,
        ambient_conductance_per_length_W_m_K=20.0,
        imposed_heat_per_length_W_m=0.0,
        evidence_ids=(evidence,),
        additive_incompressible_volumes_and_mass_fraction_basis_reviewed=True,
        constant_pure_component_properties_and_log_viscosity_mixing_reviewed=True,
        prescribed_flow_pressure_and_dispersion_scope_reviewed=True,
        mechanical_energy_absent_or_in_imposed_heat_reviewed=True,
    )
    return ConservativeBatchRequest(transport=transport, solver=solver), context


def _backward_euler_first_cell(step_s: float, steps: int) -> float:
    # The first cell solves C*dT/dt = (Q/dx)*C*(Tin-T) + (UA/A)*(Tamb-T).
    # This recurrence is independently derived in temperature coordinates.
    dx = 1.0 / 4
    area = 0.01
    flow = 0.25
    volumetric_heat_capacity = 1000.0 * 2000.0
    ua_per_length = 20.0
    inlet, ambient, initial = 300.0, 350.0, 300.0
    advection_rate = flow / (area * dx)
    ambient_rate = ua_per_length / (area * volumetric_heat_capacity)
    temperature = initial
    for _ in range(steps):
        temperature = (
            temperature + step_s * (advection_rate * inlet + ambient_rate * ambient)
        ) / (1.0 + step_s * (advection_rate + ambient_rate))
    return temperature


def test_energy_coupled_batch_matches_independent_implicit_oracle_and_refines_to_analytic_limit():
    errors = []
    for step_s in (0.03125, 0.015625, 0.0078125):
        request, context = _case(step_s)
        record = evaluate_conservative_batch_transport(request, context)
        assert record["status"] == "computed", record
        native = record["result"]["native"]
        steps = round(request.transport.duration_s / step_s)
        first_cell = native["temperature_K"][-(request.solver.cells + 1)]
        assert first_cell == pytest.approx(
            _backward_euler_first_cell(step_s, steps), rel=2e-12, abs=2e-12
        )
        ledger = native["energy_ledger_J"]
        residual = (ledger["initial"] + ledger["inlet"] - ledger["outlet"]
                    - ledger["final"] + ledger["heat_transfer"]
                    + ledger["shaft_work"] + ledger["numerical_adjustment"])
        assert residual == pytest.approx(0.0, abs=1e-3)
        assert ledger["heat_transfer"] > 0.0

        dx, area, flow = 0.25, 0.01, 0.25
        cvol = 2_000_000.0
        a = flow / (area * dx) + 20.0 / (area * cvol)
        b = (flow / (area * dx) * 300.0 + 20.0 / (area * cvol) * 350.0) / a
        exact = b + (300.0 - b) * math.exp(-a * request.transport.duration_s)
        errors.append(abs(first_cell - exact))
    assert errors[0] > errors[1] > errors[2] > 0.0
