import math

import pytest
from pydantic import ValidationError

from pipeline_integrity import create_registry
from pipeline_integrity.contracts import Pressure, RunContext
from pipeline_integrity.thermo import CoolPropProvider, StateRequest


@pytest.fixture
def engine():
    return create_registry()


@pytest.fixture
def context():
    return RunContext(
        asset_id="authored_pipe",
        scenario_id="software_verification",
        clock_id="elapsed_s",
        origin="synthetic",
    )


def loss_inputs(q=1e-5):
    return dict(
        diameter_m=0.02,
        length_m=10.0,
        roughness_m=0.0,
        volume_flow_m3_s=q,
        density_kg_m3=1000.0,
        viscosity_pa_s=0.001,
    )


def test_laminar_loss_against_poiseuille(engine, context):
    p = loss_inputs()
    result = engine.evaluate("hydraulics.liquid_loss", p, context)
    expected = (
        128 * p["viscosity_pa_s"] * p["length_m"] * p["volume_flow_m3_s"] / (math.pi * p["diameter_m"] ** 4)
    )
    assert result.values["required_inlet_minus_outlet_pa"] == pytest.approx(expected, rel=1e-12)
    assert result.values["dissipated_power_w"] == pytest.approx(expected * p["volume_flow_m3_s"])


def test_reverse_flow_keeps_dissipation_and_reverses_friction(engine, context):
    forward = engine.evaluate(
        "hydraulics.liquid_loss", loss_inputs() | {"outlet_minus_inlet_elevation_m": 2.0}, context
    )
    reverse = engine.evaluate(
        "hydraulics.liquid_loss", loss_inputs(-1e-5) | {"outlet_minus_inlet_elevation_m": 2.0}, context
    )
    assert forward.values["dissipated_power_w"] == reverse.values["dissipated_power_w"]
    assert forward.values["dissipative_pressure_loss_pa"] == -reverse.values["dissipative_pressure_loss_pa"]
    assert forward.values["static_pressure_change_pa"] == reverse.values["static_pressure_change_pa"]


def test_zero_flow_retains_static_head_without_nan(engine, context):
    result = engine.evaluate(
        "hydraulics.liquid_loss", loss_inputs(0.0) | {"outlet_minus_inlet_elevation_m": 2.0}, context
    )
    assert result.values["required_inlet_minus_outlet_pa"] == pytest.approx(19613.3)
    assert result.values["dissipated_power_w"] == 0.0


def test_turbulent_factor_satisfies_colebrook_residual(engine, context):
    p = loss_inputs(0.002) | {"roughness_m": 0.00002}
    result = engine.evaluate("hydraulics.liquid_loss", p, context)
    re, fd = result.values["reynolds"], result.values["darcy_friction_factor"]
    residual = 1 / math.sqrt(fd) + 2 * math.log10(
        p["roughness_m"] / p["diameter_m"] / 3.7 + 2.51 / re / math.sqrt(fd)
    )
    assert abs(residual) < 1e-10


def test_transition_requires_explicit_choice(engine, context):
    q = 3000.0 * 0.001 / (1000 * 0.02) * math.pi * 0.02**2 / 4
    result = engine.evaluate("hydraulics.liquid_loss", loss_inputs(q), context)
    assert result.status == "out_of_scope" and not result.values
    accepted = engine.evaluate(
        "hydraulics.liquid_loss", loss_inputs(q) | {"transition_policy": "use_declared_correlation"}, context
    )
    assert accepted.status == "computed"


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -0.1, True, "0.1"])
def test_physical_input_rejection(engine, context, bad):
    with pytest.raises(ValidationError):
        engine.evaluate("hydraulics.liquid_loss", loss_inputs() | {"diameter_m": bad}, context)


def test_pressure_references_are_not_interchanged():
    pressure = Pressure(value=1.0, unit="bar", reference="gauge", ambient_pa_abs=101325.0)
    assert pressure.absolute_pa() == 201325.0
    with pytest.raises(ValidationError):
        Pressure(value=1.0, unit="bar", reference="gauge")
    with pytest.raises(ValueError):
        Pressure(value=1.0, unit="bar", reference="differential").absolute_pa()


def test_wave_speed_rigid_limit_and_elastic_reduction(engine, context):
    p = dict(
        density_kg_m3=1000.0,
        fluid_bulk_modulus_pa=2e9,
        pipe_elastic_modulus_pa=200e9,
        inner_diameter_m=0.1,
        wall_thickness_m=0.005,
        restraint_coefficient=0.0,
    )
    rigid = engine.evaluate("transient.wave_speed", p, context).values["wave_speed_m_s"]
    flexible = engine.evaluate("transient.wave_speed", p | {"restraint_coefficient": 1.0}, context).values[
        "wave_speed_m_s"
    ]
    assert rigid == pytest.approx(math.sqrt(2e6))
    assert flexible < rigid


def test_hydrostatic_translation_and_invalid_absolute_state(engine, context):
    p = dict(inlet_pressure_pa_abs=101325.0, density_kg_m3=1000.0, outlet_minus_inlet_elevation_m=0.0)
    assert engine.evaluate("hydraulics.hydrostatic", p, context).values["outlet_pressure_pa_abs"] == 101325.0
    assert (
        engine.evaluate(
            "hydraulics.hydrostatic", p | {"outlet_minus_inlet_elevation_m": 100.0}, context
        ).status
        == "out_of_scope"
    )


def test_coolprop_known_state_and_phase_guard():
    provider = CoolPropProvider()
    state = provider.resolve(StateRequest(fluid="Water", inputs={"P": 101325.0, "T": 300.0}))
    assert state.density_kg_m3 == pytest.approx(996.56, abs=0.05)
    assert state.cp_j_kg_k == pytest.approx(4180.6, abs=1)
    with pytest.raises(ValueError):
        provider.resolve(StateRequest(fluid="Water", inputs={"P": 101325.0, "Q": 0.5}))
    saturated = provider.resolve(
        StateRequest(fluid="Water", inputs={"P": 101325.0, "Q": 0.5}, single_phase_required=False)
    )
    assert saturated.temperature_k == pytest.approx(373.1243, abs=0.001)


def test_inventory_representations_conserve_mass(engine, context):
    result = engine.evaluate(
        "inventory.uniform",
        dict(
            fluid="Hydrogen",
            pressure_pa_abs=2e6,
            temperature_k=293.15,
            internal_volume_m3=0.1,
            reference_pressure_pa_abs=101325.0,
            reference_temperature_k=273.15,
        ),
        context,
    )
    reference = CoolPropProvider().resolve(
        StateRequest(fluid="Hydrogen", inputs={"P": 101325.0, "T": 273.15})
    )
    assert result.values["mass_kg"] == pytest.approx(
        result.values["reference_volume_m3"] * reference.density_kg_m3
    )


def test_compressor_enthalpy_and_efficiency_identity(engine, context):
    p = dict(
        fluid="Air",
        inlet_pressure_pa_abs=101325.0,
        inlet_temperature_k=300.0,
        outlet_pressure_pa_abs=500000.0,
        mass_flow_kg_s=0.2,
        isentropic_efficiency=1.0,
    )
    ideal = engine.evaluate("equipment.compression", p, context)
    actual = engine.evaluate("equipment.compression", p | {"isentropic_efficiency": 0.8}, context)
    assert actual.values["specific_work_j_kg"] == pytest.approx(ideal.values["specific_work_j_kg"] / 0.8)
    assert actual.values["shaft_power_w"] == pytest.approx(actual.values["specific_work_j_kg"] * 0.2)
    assert actual.values["outlet_temperature_k"] > ideal.values["outlet_temperature_k"] > 300
    assert engine.evaluate("equipment.compression", p | {"fluid": "Water"}, context).status == "out_of_scope"


def test_convection_laminar_reference_and_regime_guard(engine, context):
    p = dict(
        reynolds=1000.0, prandtl=7.0, diameter_m=0.02, conductivity_w_m_k=0.6, method="Laminar - constant T"
    )
    result = engine.evaluate("thermal.internal_convection", p, context)
    assert result.values["nusselt"] == pytest.approx(3.66)
    assert result.values["coefficient_w_m2_k"] == pytest.approx(109.8)
    assert (
        engine.evaluate("thermal.internal_convection", p | {"method": "Gnielinski"}, context).status
        == "out_of_scope"
    )


def test_energy_financial_and_thermal_limits(engine, context):
    thermal = dict(
        inlet_temperature_k=400.0,
        ambient_temperature_k=300.0,
        mass_flow_kg_s=1.0,
        specific_heat_j_kg_k=1000.0,
        conductance_w_k=0.0,
    )
    assert engine.evaluate("thermal.flow_cooling", thermal, context).values["outlet_temperature_k"] == 400.0
    cooled = engine.evaluate("thermal.flow_cooling", thermal | {"conductance_w_k": 1e6}, context)
    assert cooled.values["outlet_temperature_k"] == 300.0
    financial = dict(
        cashflows=(-100.0, 60.0, 60.0),
        discount_rate_per_period=0.0,
        period_duration_s=31557600.0,
        currency="USD",
        price_year=2026,
    )
    assert engine.evaluate("economics.cashflow", financial, context).values["net_present_value"] == 20.0
    assert (
        engine.evaluate("economics.cashflow", financial | {"discount_rate_per_period": 0.1}, context).values[
            "net_present_value"
        ]
        < 20.0
    )
