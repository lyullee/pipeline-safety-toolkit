"""Independent laminar serial-gas closure references for the potential-flow solver."""

from __future__ import annotations

import math

import pytest

from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.gas_potential_network import (
    GasPotentialPathRequest,
    GasPotentialSegment,
    evaluate_gas_potential_path,
    gas_potential_path_metadata,
)
from pipeline_integrity.thermo import StateRequest, ThermodynamicState

GAS_CONSTANT_J_KG_K = 287.05
CP_J_KG_K = 1005.0
GAMMA = CP_J_KG_K / (CP_J_KG_K - GAS_CONSTANT_J_KG_K)
VISCOSITY_PA_S = 1.8e-5


class AnalyticIdealGasProvider:
    """Deterministic ideal-gas property fixture with a pinned test source file."""

    def resolve(self, request: StateRequest) -> ThermodynamicState:
        pressure = request.inputs["P"]
        temperature = request.inputs["T"]
        density = pressure / (GAS_CONSTANT_J_KG_K * temperature)
        return ThermodynamicState(
            fluid=request.fluid,
            backend=request.backend,
            pressure_pa_abs=pressure,
            temperature_k=temperature,
            density_kg_m3=density,
            enthalpy_j_kg=CP_J_KG_K * temperature,
            entropy_j_kg_k=CP_J_KG_K * math.log(temperature)
            - GAS_CONSTANT_J_KG_K * math.log(pressure),
            phase="gas",
            viscosity_pa_s=VISCOSITY_PA_S,
            cp_j_kg_k=CP_J_KG_K,
            cv_j_kg_k=CP_J_KG_K - GAS_CONSTANT_J_KG_K,
            conductivity_w_m_k=0.025,
            speed_of_sound_m_s=math.sqrt(GAMMA * GAS_CONSTANT_J_KG_K * temperature),
            provider_version="analytic-test-fixture-v1",
            provider_revision="ideal-gas-equation-of-state",
        )


def _laminar_pressure_step(
    inlet_pressure_pa: float, length_m: float, mass_flow_kg_s: float
) -> tuple[float, float]:
    """Solve one finite-volume ideal-gas momentum cell independently by bisection."""
    diameter_m = 0.1
    area_m2 = math.pi * diameter_m**2 / 4.0
    inlet_temperature_k = 300.0
    inlet_density = inlet_pressure_pa / (GAS_CONSTANT_J_KG_K * inlet_temperature_k)
    reynolds = mass_flow_kg_s * diameter_m / (area_m2 * VISCOSITY_PA_S)
    assert reynolds < 2_000.0
    darcy_factor = 64.0 / reynolds

    def residual(outlet_pressure_pa: float) -> float:
        outlet_density = outlet_pressure_pa / (GAS_CONSTANT_J_KG_K * inlet_temperature_k)
        mean_density = (inlet_density + outlet_density) / 2.0
        friction_drop = (
            darcy_factor
            * length_m
            / diameter_m
            * mass_flow_kg_s**2
            / (2.0 * mean_density * area_m2**2)
        )
        acceleration_drop = (mass_flow_kg_s / area_m2) ** 2 * (
            1.0 / outlet_density - 1.0 / inlet_density
        )
        return inlet_pressure_pa - outlet_pressure_pa - friction_drop - acceleration_drop

    lower, upper = inlet_pressure_pa - 1_000.0, inlet_pressure_pa
    assert residual(lower) > 0.0 > residual(upper)
    for _ in range(100):
        middle = (lower + upper) / 2.0
        if residual(middle) > 0.0:
            lower = middle
        else:
            upper = middle
    outlet_pressure_pa = (lower + upper) / 2.0
    outlet_density = outlet_pressure_pa / (GAS_CONSTANT_J_KG_K * inlet_temperature_k)
    inlet_velocity = mass_flow_kg_s / (area_m2 * inlet_density)
    outlet_velocity = mass_flow_kg_s / (area_m2 * outlet_density)
    heat_into_fluid_w = mass_flow_kg_s * (outlet_velocity**2 - inlet_velocity**2) / 2.0
    return outlet_pressure_pa, heat_into_fluid_w


def _continuum_pressure_reference(length_m: float, mass_flow_kg_s: float) -> float:
    """Integrate the laminar momentum equation in pressure coordinates."""
    diameter_m = 0.1
    area_m2 = math.pi * diameter_m**2 / 4.0
    inlet_pressure_pa = 200_000.0
    gas_temperature_k = 300.0
    reynolds = mass_flow_kg_s * diameter_m / (area_m2 * VISCOSITY_PA_S)
    darcy_factor = 64.0 / reynolds
    mass_flux = mass_flow_kg_s / area_m2
    pressure_acceleration_scale = mass_flux**2 * GAS_CONSTANT_J_KG_K * gas_temperature_k
    friction_scale_per_m = darcy_factor / diameter_m * pressure_acceleration_scale / 2.0

    def integrated_residual(outlet_pressure_pa: float) -> float:
        return (
            (outlet_pressure_pa**2 - inlet_pressure_pa**2) / 2.0
            - pressure_acceleration_scale * math.log(outlet_pressure_pa / inlet_pressure_pa)
            + friction_scale_per_m * length_m
        )

    lower, upper = inlet_pressure_pa - 1_000.0, inlet_pressure_pa
    assert integrated_residual(lower) < 0.0 < integrated_residual(upper)
    for _ in range(100):
        middle = (lower + upper) / 2.0
        if integrated_residual(middle) < 0.0:
            lower = middle
        else:
            upper = middle
    return (lower + upper) / 2.0


def _case(segment_count: int, total_length_m: float, mass_flow_kg_s: float):
    segment_length_m = total_length_m / segment_count
    node_pressures = [200_000.0]
    segment_heats = []
    for _ in range(segment_count):
        outlet_pressure, heat = _laminar_pressure_step(
            node_pressures[-1], segment_length_m, mass_flow_kg_s
        )
        node_pressures.append(outlet_pressure)
        segment_heats.append(heat)
    evidence = [
        EvidenceRef(
            id=f"segment-input-{index + 1}",
            citation="Authored segment boundary for an ideal-gas closure fixture",
            origin="synthetic",
        )
        for index in range(segment_count)
    ]
    evidence.append(
        EvidenceRef(
            id="method-review",
            citation="Authored review flags for a bounded serial laminar case",
            origin="synthetic",
        )
    )
    context = RunContext(
        asset_id="analytic-line",
        scenario_id=f"laminar-{segment_count}-cell-{total_length_m:g}m",
        clock_id="elapsed",
        origin="synthetic",
        evidence=tuple(evidence),
    )
    request = GasPotentialPathRequest(
        id="laminar-reference",
        asset_id="analytic-line",
        clock_id="elapsed",
        fluid="AnalyticProbeGas",
        backend="HEOS",
        property_basis_id="analytic-ideal-gas-fixture",
        diameter_m=0.1,
        inlet_pressure_Pa_abs=200_000.0,
        inlet_temperature_K=300.0,
        outlet_pressure_Pa_abs=node_pressures[-1],
        segments=tuple(
            GasPotentialSegment(
                id=f"segment-{index + 1}",
                length_m=segment_length_m,
                end_minus_start_elevation_m=0.0,
                roughness_m=0.0,
                heat_into_fluid_W=segment_heats[index],
                evidence_id=f"segment-input-{index + 1}",
            )
            for index in range(segment_count)
        ),
        pressure_support_Pa_abs=(190_000.0, 210_000.0),
        temperature_support_K=(290.0, 310.0),
        positive_mass_flow_support_kg_s=(0.001, 0.005),
        initial_mass_flow_kg_s=mass_flow_kg_s,
        initial_node_pressures_Pa_abs=tuple(node_pressures[1:]),
        initial_node_temperatures_K=(300.0,) * segment_count,
        expected_owner_pipe_sha256=gas_potential_path_metadata()["owner_pipe_sha256"],
        absolute_momentum_tolerance_Pa=1e-5,
        absolute_total_energy_tolerance_W=1e-7,
        relative_closure_tolerance=1e-9,
        fixed_area_serial_positive_flow_and_no_branch_mixing_reviewed=True,
        small_segment_property_variation_and_wall_heat_reviewed=True,
        owner_friction_and_common_thermodynamic_reference_reviewed=True,
        evidence_ids=("method-review",),
    )
    return request, context, tuple(node_pressures[1:]), tuple(segment_heats)


def test_gas_potential_path_matches_laminar_reference_and_converges_with_cell_refinement():
    expected_mass_flow = 0.0025
    total_length_m = 80_000.0
    endpoint_errors = []

    for segment_count in (1, 4, 16):
        request, context, expected_pressures, expected_heats = _case(
            segment_count, total_length_m, expected_mass_flow
        )
        record = evaluate_gas_potential_path(
            request, context, property_provider=AnalyticIdealGasProvider()
        )

        assert record["status"] == "computed_diagnostic", record["failure"]
        assert record["failure"] is None
        assert record["mass_flow_kg_s"] == pytest.approx(expected_mass_flow, rel=2e-5)
        assert len(record["states"]) == segment_count + 1
        for state, expected_pressure in zip(record["states"][1:], expected_pressures, strict=True):
            assert state["pressure_pa_abs"] == pytest.approx(expected_pressure, abs=2e-5)
            assert state["temperature_k"] == pytest.approx(300.0, abs=2e-5)
        for segment, check, expected_heat in zip(
            request.segments, record["segment_checks"], expected_heats, strict=True
        ):
            assert segment.heat_into_fluid_W == pytest.approx(expected_heat, abs=1e-15)
            assert abs(check["momentum_residual_Pa"]) <= request.absolute_momentum_tolerance_Pa
            assert abs(check["total_energy_residual_W"]) <= request.absolute_total_energy_tolerance_W
        assert abs(record["global_total_energy_residual_W"]) <= request.absolute_total_energy_tolerance_W
        assert record["branching_junction_mixing_solved"] is False
        assert record["flow_reversal_or_choking_solved"] is False
        endpoint_errors.append(
            abs(record["states"][-1]["pressure_pa_abs"]
                - _continuum_pressure_reference(total_length_m, expected_mass_flow))
        )

    assert endpoint_errors[1] < endpoint_errors[0]
    assert endpoint_errors[2] <= endpoint_errors[1] + 1e-8
    assert endpoint_errors[2] < endpoint_errors[0]
