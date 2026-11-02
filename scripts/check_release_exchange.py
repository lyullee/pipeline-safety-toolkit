"""Exercise native accidental releases, retain mismatch reproducers, and call PRISM's existing builder."""

import argparse
import json
import math
import sys
from pathlib import Path

from check_integrations import revision

from pipeline_integrity.contracts import RunContext
from pipeline_integrity.equipment_exchange import EquipmentRequest, LH2EquipmentClient
from pipeline_integrity.release_exchange import PipeAccidentRequest, prism_release


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--lh2dt", type=Path, required=True)
    parser.add_argument("--prism-source", type=Path, required=True)
    args = parser.parse_args()
    sys.path[:0] = [str((args.lh2dt / "src").resolve()), str(args.prism_source.resolve())]
    cases = {}
    for fluid, quality, step in (
        ("Hydrogen", None, 0.005),
        ("Hydrogen", None, 0.0025),
        ("ParaHydrogen", None, 0.005),
        ("Hydrogen", 0.0, 0.005),
        ("Hydrogen", 0.1, 0.005),
        ("Hydrogen", 1.0, 0.005),
    ):
        client = LH2EquipmentClient(fluid=fluid, property_basis=f"CoolProp8_HEOS_{fluid}_default_reference")
        context = RunContext(
            asset_id="accident_pipe",
            scenario_id="declared_single_outlet",
            clock_id="elapsed",
            origin="synthetic",
        )
        handle = client.build(
            EquipmentRequest(
                model_id="dynamic_hem_pipe",
                parameter_source="synthetic_exchange_reference",
                parameters={
                    "length_m": 1.0,
                    "inner_diameter_m": 0.05,
                    "roughness_m": 1e-6,
                    "wall_heat_capacity_J_K": 1e4,
                    "local_loss_coefficient": 0.0,
                    "fluid_wall_UA_W_K": 0.0,
                    "ambient_temperature_K": 288.15,
                    "ambient_UA_W_K": 0.0,
                },
            ),
            context,
        )
        pressure = 500000.0 if quality is not None else 1000000.0
        temperature = (
            client.properties.saturated_liquid(pressure).temperature_K if quality is not None else 300.0
        )
        request = PipeAccidentRequest(
            event_id="accident_pipe/synthetic_leak",
            port_id="failed_opening",
            clock_id="elapsed",
            start_time_s=10.0,
            source_id="synthetic_native_export_reference",
            initial_pressure_pa_abs=pressure,
            initial_temperature_k=temperature,
            initial_vapour_mass_fraction=quality,
            wall_temperature_k=temperature,
            ambient_pressure_pa_abs=101325.0,
            ambient_temperature_k=288.15,
            failure_opening_diameter_m=0.001,
            discharge_coefficient=0.8,
            horizon_s=0.01,
            time_step_s=step,
            position_enu_m=(0.0, 0.0, 1.0),
            direction_enu=(1.0, 0.0, 0.0),
            droplet_flight_time_s=0.1 if quality is not None else None,
            impact_position_enu_m=(2.0, 0.0, 0.0) if quality is not None else None,
            droplet_diameter_m=0.001 if quality is not None else None,
        )
        history = client.export_accident_history(handle, request)
        key = f"{fluid}_quality_{quality}_dt_{step}"
        case = {"history": history.model_dump(mode="json")}
        if fluid == "Hydrogen" and quality is None:
            assert history.status == "exchange_checked", history.issues
            handoff = prism_release(history)
            assert handoff.native.property_basis.fluid == fluid
            assert handoff.native.provider_mass_balance.final_mass_kg > 0.0
            assert handoff.native.provider_mass_balance.dynamic_state_closure.inventory_feedback_closed
            assert handoff.native.termination_basis == "synthetic_diagnostic_horizon"
            case["prism_handoff"] = handoff.record()
        else:
            assert history.status == "withheld"
            expected = (
                {"provider_fluid_mismatch"}
                if fluid == "ParaHydrogen"
                else (
                    {"provider_phase_history_shape_mismatch"}
                    if quality == 1.0
                    else {"gas_branch_property_state_mismatch", "provider_changed_impact_position"}
                )
            )
            assert expected <= set(history.issues), history.issues
            try:
                prism_release(history)
            except ValueError as error:
                case["rejection"] = str(error)
            else:
                raise AssertionError("a mismatched source must not reach the receiving builder")
        cases[key] = case
    gas = [case["history"]["audit"] for key, case in cases.items() if key.startswith("Hydrogen_quality_None")]
    mass_difference = abs(gas[0]["integrated_mass_kg"] - gas[1]["integrated_mass_kg"])
    assert math.isclose(
        gas[0]["integrated_mass_kg"], gas[1]["integrated_mass_kg"], rel_tol=1e-7, abs_tol=1e-12
    )
    record = {
        "schema": "pipeline_integrity.release_exchange_checks.v1",
        "sources": {"lh2dt_models": revision(args.lh2dt), "PRISM": revision(args.prism_source.parent)},
        "cases": cases,
        "gas_timestep_mass_difference_kg": mass_difference,
        "gas_timestep_agreement_relative": 1e-7,
        "gas_timestep_agreement_absolute_kg": 1e-12,
        "frequency_or_consequence_calculated": False,
        "independent_field_validation": False,
        "external_sources_modified": False,
    }
    Path("research/release_exchange_results.json").write_text(
        json.dumps(record, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "case_statuses": {key: case["history"]["status"] for key, case in cases.items()},
                "mass_timestep_difference_kg": mass_difference,
            }
        )
    )


if __name__ == "__main__":
    main()
