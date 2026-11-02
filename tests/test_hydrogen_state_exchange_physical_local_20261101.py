"""Bounded EOS state-handoff checks using authored synthetic inputs."""

from __future__ import annotations

import pytest
import CoolProp.CoolProp as CP

from lh2dt.properties import HydrogenProperties
from pipeline_integrity.adapters import lh2dt_state
from pipeline_integrity.contracts import FlowBoundary


FLUID = "ParaHydrogen"
BASIS = "CoolProp_HEOS_ParaHydrogen_reference"


def boundary(*, pressure_pa, temperature_k, enthalpy_j_kg, phase, quality=None):
    return FlowBoundary(
        component_id="synthetic_component",
        port_id="synthetic_port",
        clock_id="elapsed_seconds",
        time_s=0.0,
        direction="out_of_component",
        fluid=FLUID,
        property_basis=BASIS,
        pressure_pa_abs=pressure_pa,
        temperature_k=temperature_k,
        mass_flow_kg_s=0.1,
        specific_enthalpy_j_kg=enthalpy_j_kg,
        phase=phase,
        vapour_mass_fraction=quality,
        evidence_ids=("authored_synthetic_eos_case",),
    )


def state_case(phase):
    if phase == "liquid":
        p, t = 1.0e6, 20.0
        h = CP.PropsSI("Hmass", "P", p, "T", t, FLUID)
        return boundary(pressure_pa=p, temperature_k=t, enthalpy_j_kg=h, phase=phase)
    if phase == "gas":
        p, t = 2.0e5, 30.0
        h = CP.PropsSI("Hmass", "P", p, "T", t, FLUID)
        return boundary(pressure_pa=p, temperature_k=t, enthalpy_j_kg=h, phase=phase)
    if phase == "supercritical":
        p, t = 5.0e6, 40.0
        h = CP.PropsSI("Hmass", "P", p, "T", t, FLUID)
        return boundary(pressure_pa=p, temperature_k=t, enthalpy_j_kg=h, phase=phase)
    p, quality = 1.0e5, 0.35
    t = CP.PropsSI("T", "P", p, "Q", quality, FLUID)
    h = CP.PropsSI("Hmass", "P", p, "Q", quality, FLUID)
    return boundary(
        pressure_pa=p,
        temperature_k=t,
        enthalpy_j_kg=h,
        phase="two_phase",
        quality=quality,
    )


@pytest.mark.parametrize("phase", ["liquid", "gas", "supercritical", "two_phase"])
def test_eos_p_h_reconstruction_matches_direct_coolprop_oracle(phase):
    source = state_case(phase)
    receiving = lh2dt_state(
        source,
        properties=HydrogenProperties(fluid=FLUID),
        expected_property_basis=BASIS,
        temperature_tolerance_k=2e-7,
    )

    # Independent public CoolProp calls check the receiving P-h solution and
    # its intensive-property closure; the handoff layer only validates it.
    p = source.pressure_pa_abs
    h = source.specific_enthalpy_j_kg
    assert receiving.pressure_Pa == pytest.approx(p, rel=2e-12)
    assert receiving.temperature_K == pytest.approx(CP.PropsSI("T", "P", p, "Hmass", h, FLUID), abs=2e-7)
    assert receiving.density_kg_m3 == pytest.approx(CP.PropsSI("Dmass", "P", p, "Hmass", h, FLUID), rel=2e-10)
    if phase == "two_phase":
        q = source.vapour_mass_fraction
        h_l = CP.PropsSI("Hmass", "P", p, "Q", 0.0, FLUID)
        h_v = CP.PropsSI("Hmass", "P", p, "Q", 1.0, FLUID)
        rho_l = CP.PropsSI("Dmass", "P", p, "Q", 0.0, FLUID)
        rho_v = CP.PropsSI("Dmass", "P", p, "Q", 1.0, FLUID)
        assert receiving.specific_enthalpy_J_kg == pytest.approx((1.0 - q) * h_l + q * h_v, rel=2e-9, abs=1e-5)
        assert receiving.density_kg_m3 == pytest.approx(1.0 / ((1.0 - q) / rho_l + q / rho_v), rel=2e-9)
    else:
        assert receiving.specific_enthalpy_J_kg == pytest.approx(CP.PropsSI("Hmass", "P", p, "T", receiving.temperature_K, FLUID), rel=2e-9, abs=1e-5)
    assert receiving.phase in {
        "liquid": {"liquid"},
        "gas": {"gas"},
        "supercritical": {"supercritical", "supercritical_gas", "supercritical_liquid"},
        "two_phase": {"twophase"},
    }[phase]
    if phase == "two_phase":
        assert receiving.quality == pytest.approx(source.vapour_mass_fraction, abs=1e-7)
    else:
        assert receiving.quality is None


def test_temperature_energy_reference_mismatch_is_rejected():
    source = state_case("liquid").model_copy(update={"temperature_k": 21.0})
    with pytest.raises(ValueError, match="temperature or energy reference"):
        lh2dt_state(source, properties=HydrogenProperties(fluid=FLUID), expected_property_basis=BASIS)


def test_property_reference_mismatch_is_rejected_before_eos_conversion():
    source = state_case("gas")
    with pytest.raises(ValueError, match="bases differ"):
        lh2dt_state(
            source,
            properties=HydrogenProperties(fluid=FLUID),
            expected_property_basis="different_reference",
        )


def test_two_phase_quality_mismatch_is_rejected():
    source = state_case("two_phase").model_copy(update={"vapour_mass_fraction": 0.8})
    with pytest.raises(ValueError, match="quality"):
        lh2dt_state(source, properties=HydrogenProperties(fluid=FLUID), expected_property_basis=BASIS)
