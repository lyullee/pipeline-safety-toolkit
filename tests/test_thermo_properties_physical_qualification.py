"""Bounded water property anchors and saturation mixture lever-rule checks."""

import pytest

from pipeline_integrity.thermo import CoolPropProvider, StateRequest


P_ATM = 101325.0


def test_water_ambient_liquid_matches_tabulated_property_anchors():
    state = CoolPropProvider(use_cache=False).resolve(
        StateRequest(fluid="Water", inputs={"P": P_ATM, "T": 293.15})
    )
    assert state.phase == "liquid"
    assert state.density_kg_m3 == pytest.approx(998.207, rel=2e-6)
    assert state.cp_j_kg_k == pytest.approx(4184.05, rel=2e-5)
    assert state.viscosity_pa_s == pytest.approx(1.0016e-3, rel=2e-5)
    assert state.conductivity_w_m_k == pytest.approx(0.5980, rel=3e-5)


def test_atmospheric_water_saturation_boundary_and_phase_refusal():
    provider = CoolPropProvider(use_cache=False)
    sat = provider.resolve(
        StateRequest(fluid="Water", inputs={"P": P_ATM, "Q": 0.0}, single_phase_required=False)
    )
    assert sat.temperature_k == pytest.approx(373.1243, abs=1.0e-3)
    assert sat.density_kg_m3 == pytest.approx(958.37, rel=2e-5)
    with pytest.raises(ValueError, match="single-phase"):
        provider.resolve(StateRequest(fluid="Water", inputs={"P": P_ATM, "Q": 0.5}))


def test_saturated_water_mixture_uses_quality_mass_lever_rules():
    provider = CoolPropProvider(use_cache=False)
    liquid = provider.resolve(StateRequest(
        fluid="Water", inputs={"P": P_ATM, "Q": 0.0}, single_phase_required=False
    ))
    vapor = provider.resolve(StateRequest(
        fluid="Water", inputs={"P": P_ATM, "Q": 1.0}, single_phase_required=False
    ))
    for quality in (0.1, 0.5, 0.9):
        mixture = provider.resolve(StateRequest(
            fluid="Water", inputs={"P": P_ATM, "Q": quality}, single_phase_required=False
        ))
        specific_volume = (1.0-quality)/liquid.density_kg_m3 + quality/vapor.density_kg_m3
        assert mixture.phase == "twophase"
        assert mixture.temperature_k == pytest.approx(liquid.temperature_k, abs=1e-9)
        assert mixture.pressure_pa_abs == pytest.approx(P_ATM, abs=1e-6)
        assert mixture.density_kg_m3 == pytest.approx(1.0/specific_volume, rel=2e-10)
        assert mixture.enthalpy_j_kg == pytest.approx(
            (1.0-quality)*liquid.enthalpy_j_kg + quality*vapor.enthalpy_j_kg, rel=2e-10
        )
        assert mixture.entropy_j_kg_k == pytest.approx(
            (1.0-quality)*liquid.entropy_j_kg_k + quality*vapor.entropy_j_kg_k, rel=2e-10
        )
