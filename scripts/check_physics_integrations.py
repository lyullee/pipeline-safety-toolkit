"""Exercise installed/supplied public hydrogen equipment and PRISM port classes."""

import argparse
import json
import sys
from pathlib import Path

from check_integrations import revision

from pipeline_integrity.adapters import lh2dt_state, prism_port_state
from pipeline_integrity.contracts import FlowBoundary, RunContext


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--lh2dt", type=Path, required=True)
    parser.add_argument("--prism-source", type=Path, required=True)
    args = parser.parse_args()
    sys.path[:0] = [str((args.lh2dt / "src").resolve()), str(args.prism_source.resolve())]
    from lh2dt.properties import HydrogenProperties
    from lh2dt.valve import StrokeLimitedCommandedValve, Valve

    properties = HydrogenProperties("ParaHydrogen")
    initial = properties.from_pT(300000.0, 20.0)
    boundary = FlowBoundary(
        component_id="demo_hydrogen_pipe",
        port_id="outlet",
        clock_id="elapsed",
        time_s=0.0,
        direction="out_of_component",
        fluid="ParaHydrogen",
        property_basis="CoolProp8_HEOS_ParaHydrogen_default_reference",
        pressure_pa_abs=initial.pressure_Pa,
        temperature_k=initial.temperature_K,
        mass_flow_kg_s=0.01,
        specific_enthalpy_j_kg=initial.specific_enthalpy_J_kg,
        phase="liquid",
        evidence_ids=("declared_synthetic_case",),
    )
    context = RunContext(
        asset_id=boundary.component_id,
        scenario_id="declared_port_exchange",
        clock_id="elapsed",
        origin="synthetic",
    )
    reconstructed = lh2dt_state(
        boundary, properties=properties, expected_property_basis=boundary.property_basis
    )
    handoff = prism_port_state(
        boundary,
        context,
        density_kg_m3=reconstructed.density_kg_m3,
        entropy_j_kg_k=reconstructed.specific_entropy_J_kgK,
        position_enu_m=(0.0, 0.0, 0.0),
        direction_enu=(1.0, 0.0, 0.0),
    )
    actuator = StrokeLimitedCommandedValve(
        Valve(1e-6, 0.8, properties), stroke_time_s=2.0, initial_opening=1.0
    )
    downstream = properties.from_pT(101325.0, 20.0)
    initial_flow = actuator.evaluate(reconstructed, downstream).mass_flow_kg_s
    actuator.set_target_opening(0.0)
    opening = actuator.advance(0.5)
    advanced_flow = actuator.evaluate(reconstructed, downstream).mass_flow_kg_s
    assert abs(opening - 0.75) < 1e-12
    assert 0 < advanced_flow < initial_flow
    assert handoff.native.port_id == boundary.port_id
    record = {
        "schema": "pipeline_integrity.physics_integration_checks.v1",
        "origin": "synthetic",
        "lh2dt_source": revision(args.lh2dt),
        "prism_source": revision(args.prism_source.parent),
        "lh2dt_roundtrip_temperature_difference_k": reconstructed.temperature_K - boundary.temperature_k,
        "native_actuator": {
            "stroke_time_s": 2.0,
            "elapsed_s": 0.5,
            "opening": opening,
            "initial_flow_kg_s": initial_flow,
            "advanced_flow_kg_s": advanced_flow,
        },
        "prism_handoff": handoff.to_dict(),
        "consequence_chain_executed": False,
        "external_sources_modified": False,
    }
    Path("research/physics_integration_results.json").write_text(
        json.dumps(record, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "roundtrip_delta_k": record["lh2dt_roundtrip_temperature_difference_k"],
                "opening": opening,
                "prism_schema": handoff.native.schema_version,
            }
        )
    )


if __name__ == "__main__":
    main()
