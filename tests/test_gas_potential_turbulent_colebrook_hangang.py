"""Independent Colebrook turbulent-flow reference for the gas potential owner.

All input values are authored synthetic states; this verifies the discrete
single-cell momentum/energy contract and does not establish field accuracy.
"""
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

R = 287.05
CP = 1005.0
MU = 1.8e-5
T = 300.0
PIN = 200_000.0
D = 0.1
AREA = math.pi * D**2 / 4


class IdealGasFixture:
    def resolve(self, request: StateRequest) -> ThermodynamicState:
        p, t = request.inputs["P"], request.inputs["T"]
        return ThermodynamicState(
            fluid=request.fluid, backend=request.backend, pressure_pa_abs=p,
            temperature_k=t, density_kg_m3=p / (R * t),
            enthalpy_j_kg=CP * t,
            entropy_j_kg_k=CP * math.log(t) - R * math.log(p),
            phase="gas", viscosity_pa_s=MU, cp_j_kg_k=CP,
            cv_j_kg_k=CP - R, conductivity_w_m_k=0.025,
            speed_of_sound_m_s=math.sqrt((CP / (CP - R)) * R * t),
            provider_version="analytic-fixture-v1",
            provider_revision="ideal-gas-reference",
        )


def _colebrook(reynolds: float, relative_roughness: float) -> float:
    assert reynolds > 4000.0
    def residual(f: float) -> float:
        return 1 / math.sqrt(f) + 2 * math.log10(
            relative_roughness / 3.7 + 2.51 / (reynolds * math.sqrt(f)))
    lo, hi = 0.005, 0.12
    assert residual(lo) > 0 > residual(hi)
    for _ in range(100):
        mid = (lo + hi) / 2
        if residual(mid) > 0:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def _haaland(reynolds: float, relative_roughness: float) -> float:
    """Independent evaluation of the external pipe owner's stated closure."""
    assert reynolds > 4000.0
    return 1 / (-1.8 * math.log10(
        (relative_roughness / 3.7) ** 1.11 + 6.9 / reynolds
    )) ** 2


def _reference_flow(length: float, roughness: float, delta_p: float) -> float:
    pin, pout = PIN, PIN - delta_p
    rho_in, rho_out = pin / (R * T), pout / (R * T)
    rho_mean = (rho_in + rho_out) / 2
    def pressure_balance(mass_flow: float) -> float:
        v_in = mass_flow / (AREA * rho_in)
        v_out = mass_flow / (AREA * rho_out)
        reynolds = rho_mean * (v_in + v_out) / 2 * D / MU
        f = _haaland(reynolds, roughness / D)
        friction = f * length / D * mass_flow**2 / (2 * rho_mean * AREA**2)
        acceleration = (mass_flow / AREA)**2 * (1 / rho_out - 1 / rho_in)
        return friction + acceleration
    lo, hi = 0.01, 0.5
    assert pressure_balance(lo) < delta_p < pressure_balance(hi)
    for _ in range(100):
        mid = (lo + hi) / 2
        if pressure_balance(mid) < delta_p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


@pytest.mark.parametrize(
    "length_m, roughness_m, delta_p_pa",
    [(20.0, 0.00002, 80.0), (50.0, 0.00005, 120.0), (12.0, 0.00010, 60.0)],
)
def test_turbulent_gas_pressure_flow_matches_independent_colebrook_and_energy(
    length_m, roughness_m, delta_p_pa
):
    pout = PIN - delta_p_pa
    mass_flow = _reference_flow(length_m, roughness_m, delta_p_pa)
    rho_in, rho_out = PIN / (R * T), pout / (R * T)
    vin, vout = mass_flow / (AREA * rho_in), mass_flow / (AREA * rho_out)
    heat = mass_flow * (vout**2 - vin**2) / 2
    evidence = tuple(
        EvidenceRef(id=identity, citation="Authored synthetic turbulent-flow fixture",
                    origin="synthetic")
        for identity in ("segment-map", "method-review")
    )
    context = RunContext(asset_id="synthetic-gas-line", scenario_id="colebrook-single-segment",
                         clock_id="elapsed", origin="synthetic", evidence=evidence)
    request = GasPotentialPathRequest(
        id="turbulent-colebrook-reference", asset_id=context.asset_id,
        clock_id=context.clock_id, fluid="AnalyticProbeGas", backend="HEOS",
        property_basis_id="ideal-gas-test-basis", diameter_m=D,
        inlet_pressure_Pa_abs=PIN, inlet_temperature_K=T,
        outlet_pressure_Pa_abs=pout,
        segments=(GasPotentialSegment(id="pipe", length_m=length_m,
            end_minus_start_elevation_m=0.0, roughness_m=roughness_m,
            heat_into_fluid_W=heat, evidence_id="segment-map"),),
        pressure_support_Pa_abs=(PIN - 2 * delta_p_pa, PIN + 500.0),
        temperature_support_K=(295.0, 305.0),
        positive_mass_flow_support_kg_s=(0.001, 0.5),
        initial_mass_flow_kg_s=mass_flow,
        initial_node_pressures_Pa_abs=(pout,), initial_node_temperatures_K=(T,),
        expected_owner_pipe_sha256=gas_potential_path_metadata()["owner_pipe_sha256"],
        absolute_momentum_tolerance_Pa=1e-4,
        absolute_total_energy_tolerance_W=1e-8,
        relative_closure_tolerance=1e-8,
        maximum_segment_density_change=0.01,
        maximum_segment_pressure_change=0.01,
        fixed_area_serial_positive_flow_and_no_branch_mixing_reviewed=True,
        small_segment_property_variation_and_wall_heat_reviewed=True,
        owner_friction_and_common_thermodynamic_reference_reviewed=True,
        evidence_ids=("method-review",),
    )
    result = evaluate_gas_potential_path(request, context, property_provider=IdealGasFixture())
    assert result["status"] == "computed_diagnostic", result["failure"]
    assert result["mass_flow_kg_s"] == pytest.approx(mass_flow, rel=2e-5)
    assert result["states"][-1]["pressure_pa_abs"] == pytest.approx(pout, abs=1e-4)
    assert result["states"][-1]["temperature_k"] == pytest.approx(T, abs=2e-5)
    check = result["segment_checks"][0]
    assert check["reynolds_number"] > 4000
    assert check["darcy_factor"] == pytest.approx(
        _haaland(check["reynolds_number"], roughness_m / D), rel=2e-5)
    # The owner explicitly uses Haaland, an approximation to Colebrook.
    # Record that approximation gap separately instead of making the discrete
    # solver match a different friction law.
    colebrook_factor = _colebrook(check["reynolds_number"], roughness_m / D)
    assert abs(check["darcy_factor"] - colebrook_factor) / colebrook_factor < 0.03
    assert abs(check["momentum_residual_Pa"]) <= request.absolute_momentum_tolerance_Pa
    assert abs(check["total_energy_residual_W"]) <= request.absolute_total_energy_tolerance_W
    assert abs(result["global_total_energy_residual_W"]) <= request.absolute_total_energy_tolerance_W
