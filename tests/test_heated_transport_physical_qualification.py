"""Analytic constant-property limits for the bounded heated-liquid model."""

import importlib.util
import math
from pathlib import Path

import pytest

from pipeline_integrity import heated_transport as ht


REPO = Path(__file__).resolve().parents[1]
EXAMPLE_SPEC = importlib.util.spec_from_file_location("heated_transport_example", REPO / "examples" / "heated_transport.py")
EXAMPLE = importlib.util.module_from_spec(EXAMPLE_SPEC)
EXAMPLE_SPEC.loader.exec_module(EXAMPLE)


class ConstantCpLiquid:
    """Synthetic liquid provider with a declared h=cp*T reference for the oracle."""

    def runtime_identity(self):
        return {
            "provider": "authored_constant_cp_liquid",
            "version": "1",
            "reference_basis": "h=4000*T J/kg; constant rho and cp for deterministic bounded tests",
        }

    def resolve(self, request):
        temperature = request.inputs.get("T")
        if temperature is None:
            temperature = request.inputs["Hmass"] / 4000.0
        return ht.ThermodynamicState(
            fluid=request.fluid,
            backend=request.backend,
            pressure_pa_abs=request.inputs["P"],
            temperature_k=temperature,
            density_kg_m3=1000.0,
            enthalpy_j_kg=4000.0 * temperature,
            entropy_j_kg_k=0.0,
            phase="liquid",
            viscosity_pa_s=0.001,
            cp_j_kg_k=4000.0,
            cv_j_kg_k=4000.0,
            conductivity_w_m_k=None,
            speed_of_sound_m_s=None,
            provider_version="1",
            provider_revision="authored_test",
        )


PROVIDER = ConstantCpLiquid()


def run_case(*, flow=1.0, conductance=0.0, ambient=290.0, heater=0.0, elevation=0.0, cells=None):
    if cells is None:
        cells = (
            ht.HeatedTransportCell(
                id="qualification_cell",
                length_m=10.0,
                inner_diameter_m=0.05,
                roughness_m=1e-5,
                minor_loss_coefficient=0.0,
                outlet_minus_inlet_elevation_m=elevation,
                conductance_W_K=conductance,
                ambient_temperature_K=ambient,
                heater_into_fluid_W=heater,
                evidence_id="authored_liquid_boundary",
            ),
        )
    q, c = EXAMPLE.make_case(
        property_provider_kind="custom",
        mass_flow_kg_s=flow,
        cells=cells,
        reviews=dict.fromkeys(ht.REVIEWS, True),
        review_evidence_ids=dict.fromkeys(ht.REVIEWS, "authored_liquid_boundary"),
    )
    q = q.model_copy(update={"owner_pins": ht.native_backend_metadata(q, property_provider=PROVIDER)})
    c = c.model_copy(update={"metadata": {"heated_transport_boundary": ht.boundary_metadata(q)}})
    result = ht.evaluate_heated_transport(q, c, property_provider=PROVIDER)
    assert result["status"] == "computed_diagnostic", result["notes"]
    assert ht.verify_heated_transport_record(result, request=q, context=c, property_provider=PROVIDER)["valid"]
    return q, result


def test_single_cell_heater_matches_constant_cp_enthalpy_rise_and_power_balance():
    flow, heater, cp, inlet = 1.0, 500.0, 4000.0, 300.0
    _, result = run_case(flow=flow, heater=heater)
    outlet = result["values"]["outlet"]["temperature_k"]
    assert outlet - inlet == pytest.approx(heater / (flow * cp), abs=1e-10)
    assert result["values"]["total_heat_into_fluid_W"] == pytest.approx(heater)
    assert result["values"]["global_enthalpy_flux_change_W"] == pytest.approx(heater, abs=1e-7)
    assert result["values"]["global_reduced_energy_residual_W"] == pytest.approx(0.0, abs=1e-7)


def test_ambient_cooling_matches_exact_exponential_lumped_solution():
    flow, cp, conductance, ambient, inlet = 1.0, 4000.0, 10.0, 280.0, 300.0
    _, result = run_case(flow=flow, conductance=conductance, ambient=ambient)
    expected = ambient + (inlet - ambient) * math.exp(-conductance / (flow * cp))
    assert result["values"]["outlet"]["temperature_k"] == pytest.approx(expected, abs=1e-10)
    assert result["values"]["total_heat_into_fluid_W"] == pytest.approx(flow * cp * (expected - inlet), abs=1e-7)


def test_doubling_mass_flow_halves_fixed_heater_temperature_rise():
    _, slow = run_case(flow=1.0, heater=400.0)
    _, fast = run_case(flow=2.0, heater=400.0)
    slow_rise = slow["values"]["outlet"]["temperature_k"] - 300.0
    fast_rise = fast["values"]["outlet"]["temperature_k"] - 300.0
    assert slow_rise == pytest.approx(0.1, abs=1e-10)
    assert fast_rise == pytest.approx(slow_rise / 2.0, abs=1e-10)


def test_elevation_work_matches_constant_cp_gravitational_temperature_change():
    rise_m, gravity, cp = 12.0, 9.80665, 4000.0
    _, result = run_case(elevation=rise_m)
    expected_delta = -gravity * rise_m / cp
    assert result["values"]["outlet"]["temperature_k"] - 300.0 == pytest.approx(expected_delta, abs=1e-10)
    assert result["cells"][0]["potential_energy_flux_change_W"] == pytest.approx(gravity * rise_m, abs=1e-8)
    assert result["values"]["global_reduced_energy_residual_W"] == pytest.approx(0.0, abs=1e-7)


def test_pressure_loss_and_viscous_dissipation_are_not_counted_as_added_heater_heat():
    _, result = run_case()
    assert result["values"]["summed_native_dissipated_power_W"] > 0
    assert result["values"]["total_heat_into_fluid_W"] == 0.0
    assert result["values"]["outlet"]["temperature_k"] == pytest.approx(300.0, abs=1e-10)
    assert result["values"]["global_enthalpy_flux_change_W"] == pytest.approx(0.0, abs=1e-7)
