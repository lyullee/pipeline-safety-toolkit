"""Physical state and identity checks for the native PRISM equipment port."""

from __future__ import annotations

import pytest
import CoolProp.CoolProp as CP

from lh2_tno.equipment_boundary import ProviderPortState
from pipeline_integrity.adapters import prism_port_state
from pipeline_integrity.contracts import FlowBoundary, RunContext, digest


FLUID = "ParaHydrogen"
BASIS = "CoolProp_HEOS_ParaHydrogen_reference"


def make_boundary(phase):
    if phase == "liquid":
        p, t = 1.0e6, 20.0
        h = CP.PropsSI("Hmass", "P", p, "T", t, FLUID)
        q = None
    elif phase == "gas":
        p, t = 2.0e5, 30.0
        h = CP.PropsSI("Hmass", "P", p, "T", t, FLUID)
        q = None
    else:
        p, q = 1.0e5, 0.35
        t = CP.PropsSI("T", "P", p, "Q", q, FLUID)
        h = CP.PropsSI("Hmass", "P", p, "Q", q, FLUID)
        phase = "two_phase"
    return FlowBoundary(
        component_id="synthetic_component",
        port_id="synthetic_port",
        clock_id="elapsed_seconds",
        time_s=12.5,
        direction="out_of_component",
        fluid=FLUID,
        property_basis=BASIS,
        pressure_pa_abs=p,
        temperature_k=t,
        mass_flow_kg_s=0.075,
        specific_enthalpy_j_kg=h,
        phase=phase,
        vapour_mass_fraction=q,
        evidence_ids=("authored_synthetic_eos_case",),
    )


@pytest.mark.parametrize("phase", ["liquid", "gas", "two_phase"])
def test_real_prism_port_preserves_eos_state_identity_and_flow(phase):
    source = make_boundary(phase)
    context = RunContext(
        asset_id=source.component_id,
        scenario_id="synthetic_port_exchange",
        clock_id=source.clock_id,
        origin="synthetic",
    )
    rho = CP.PropsSI("Dmass", "P", source.pressure_pa_abs, "Hmass", source.specific_enthalpy_j_kg, FLUID)
    entropy = CP.PropsSI("Smass", "P", source.pressure_pa_abs, "Hmass", source.specific_enthalpy_j_kg, FLUID)
    handoff = prism_port_state(
        source,
        context,
        density_kg_m3=rho,
        position_enu_m=(1.0, -2.0, 0.5),
        direction_enu=(0.0, 0.0, 1.0),
        entropy_j_kg_k=entropy,
    )
    native = handoff.native
    assert isinstance(native, ProviderPortState)
    assert native.component_id == source.component_id and native.port_id == source.port_id
    assert native.time_s == source.time_s and native.fluid == source.fluid
    assert native.pressure_pa_abs == pytest.approx(source.pressure_pa_abs)
    assert native.temperature_k == pytest.approx(source.temperature_k, abs=2e-7)
    assert native.density_kg_m3 == pytest.approx(rho, rel=2e-10)
    assert native.specific_enthalpy_j_kg == pytest.approx(source.specific_enthalpy_j_kg, abs=1e-5)
    assert native.specific_entropy_j_kgk == pytest.approx(entropy, rel=2e-10)
    assert native.mass_flow_kg_s == source.mass_flow_kg_s
    assert native.source_digest == digest(source)
    assert native.provenance.find(BASIS) >= 0
    assert handoff.context == context and handoff.boundary == source
    assert native.phase == {"liquid": "liquid", "gas": "vapour", "two_phase": "two_phase"}[phase]
    if phase == "two_phase":
        assert native.vapour_quality == pytest.approx(source.vapour_mass_fraction, abs=1e-12)


def test_actual_target_contract_rejects_nonunit_physical_direction():
    source = make_boundary("gas")
    context = RunContext(asset_id=source.component_id, scenario_id="bad_vector", clock_id=source.clock_id, origin="synthetic")
    with pytest.raises(ValueError, match="unit flow direction"):
        prism_port_state(
            source,
            context,
            density_kg_m3=CP.PropsSI("Dmass", "P", source.pressure_pa_abs, "Hmass", source.specific_enthalpy_j_kg, FLUID),
            position_enu_m=(0.0, 0.0, 0.0),
            direction_enu=(2.0, 0.0, 0.0),
        )


def test_clock_and_component_mismatch_are_rejected_before_transfer():
    source = make_boundary("liquid")
    mismatched = RunContext(asset_id="different_component", scenario_id="bad_identity", clock_id="other_clock", origin="synthetic")
    with pytest.raises(ValueError, match="identities or clocks"):
        prism_port_state(
            source,
            mismatched,
            density_kg_m3=CP.PropsSI("Dmass", "P", source.pressure_pa_abs, "Hmass", source.specific_enthalpy_j_kg, FLUID),
            position_enu_m=(0.0, 0.0, 0.0),
            direction_enu=(1.0, 0.0, 0.0),
        )


def test_supercritical_state_is_withheld_from_narrow_prism_port_schema():
    p, t = 5.0e6, 40.0
    h = CP.PropsSI("Hmass", "P", p, "T", t, FLUID)
    source = FlowBoundary(
        component_id="synthetic_component", port_id="synthetic_port", clock_id="elapsed_seconds",
        time_s=0.0, direction="out_of_component", fluid=FLUID, property_basis=BASIS,
        pressure_pa_abs=p, temperature_k=t, mass_flow_kg_s=0.01,
        specific_enthalpy_j_kg=h, phase="supercritical", evidence_ids=("authored_synthetic_eos_case",),
    )
    context = RunContext(asset_id=source.component_id, scenario_id="unsupported_sc", clock_id=source.clock_id, origin="synthetic")
    with pytest.raises(ValueError, match="does not support"):
        prism_port_state(
            source,
            context,
            density_kg_m3=CP.PropsSI("Dmass", "P", p, "T", t, FLUID),
            position_enu_m=(0.0, 0.0, 0.0),
            direction_enu=(1.0, 0.0, 0.0),
        )
