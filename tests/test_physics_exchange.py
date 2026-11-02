from types import SimpleNamespace

import pytest
from pydantic import BaseModel, ConfigDict

from pipeline_integrity.adapters import lh2dt_state, prism_port_state
from pipeline_integrity.contracts import FlowBoundary, RunContext


def boundary():
    return FlowBoundary(
        component_id="pipe",
        port_id="outlet",
        clock_id="physical_elapsed",
        time_s=3.0,
        direction="out_of_component",
        fluid="ParaHydrogen",
        property_basis="explicit_eos_reference",
        pressure_pa_abs=300000.0,
        temperature_k=20.0,
        mass_flow_kg_s=0.01,
        specific_enthalpy_j_kg=1000.0,
        phase="liquid",
        evidence_ids=("synthetic_source",),
    )


def test_receiving_thermo_basis_and_temperature_are_checked():
    source = boundary()
    props = SimpleNamespace(
        fluid="ParaHydrogen", from_ph=lambda p, h: SimpleNamespace(temperature_K=22.0, phase="liquid")
    )
    with pytest.raises(ValueError, match="bases"):
        lh2dt_state(source, properties=props, expected_property_basis="different_reference")
    with pytest.raises(ValueError, match="temperature"):
        lh2dt_state(source, properties=props, expected_property_basis=source.property_basis)


def test_port_clock_and_property_basis_cannot_disappear_in_target_exchange():
    class Target(BaseModel):
        model_config = ConfigDict(extra="allow")
        schema_version: str = "prism.provider_port_state.v1"

    source = boundary()
    context = RunContext(asset_id="pipe", scenario_id="test", clock_id=source.clock_id, origin="synthetic")
    parameters = {
        "density_kg_m3": 70.0,
        "position_enu_m": (0.0, 0.0, 0.0),
        "direction_enu": (1.0, 0.0, 0.0),
        "factory": Target,
    }
    packet = prism_port_state(source, context, **parameters).to_dict()
    assert packet["boundary"]["clock_id"] == source.clock_id
    assert packet["boundary"]["property_basis"] == source.property_basis
    assert packet["native_payload"]["phase"] == "liquid"
    with pytest.raises(ValueError, match="clocks"):
        prism_port_state(source, context.model_copy(update={"clock_id": "other_elapsed"}), **parameters)
