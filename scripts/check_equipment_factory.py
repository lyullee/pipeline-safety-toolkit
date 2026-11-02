"""Check the public equipment factory and actual steady/dynamic owner invocations."""

import argparse
import json
import sys
from pathlib import Path

from check_integrations import revision

from pipeline_integrity import create_registry
from pipeline_integrity.contracts import RunContext
from pipeline_integrity.equipment_exchange import (
    DynamicStepTolerance,
    EquipmentRequest,
    LH2EquipmentClient,
    PumpMapConversion,
    lh2dt_pump_map,
)
from pipeline_integrity.models.pump_curves import ReferencePumpCurve
from pipeline_integrity.thermo import CoolPropProvider, StateRequest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--lh2dt", type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str((args.lh2dt / "src").resolve()))
    client = LH2EquipmentClient(
        fluid="ParaHydrogen", property_basis="CoolProp8_HEOS_ParaHydrogen_default_reference"
    )
    context = RunContext(
        asset_id="pipe_factory_demo", scenario_id="declared_reference", clock_id="elapsed", origin="synthetic"
    )
    parameters = {
        "length_m": 1.0,
        "inner_diameter_m": 0.01,
        "roughness_m": 1e-6,
        "local_loss_coefficient": 0.0,
        "heat_into_fluid_W": 0.0,
        "elevation_change_m": 0.0,
    }
    steady = client.build(
        EquipmentRequest(model_id="pipe", parameters=parameters, parameter_source="synthetic_example"),
        context,
    )
    assert steady.report.status == "ready"
    unknown_parameters = {**parameters, "unrecognized_demo_parameter": 123.0}
    unknown = client.build(
        EquipmentRequest(
            model_id="pipe", parameters=unknown_parameters, parameter_source="synthetic_schema_probe"
        ),
        context,
    )
    assert unknown.report.status == "rejected" and unknown.native is None
    owner_native = client.build(
        EquipmentRequest(
            model_id="pipe",
            parameters=unknown_parameters,
            parameter_source="synthetic_schema_probe",
            parameter_policy="owner_native",
        ),
        context,
    )
    assert owner_native.report.status == "ready" and owner_native.report.unreviewed_parameter_names
    left = StateRequest(fluid="ParaHydrogen", inputs={"P": 300000.0, "T": 20.0})
    right = StateRequest(fluid="ParaHydrogen", inputs={"P": 290000.0, "T": 20.0})
    result = client.evaluate_two_port(steady, left, right)
    assert result["owner_result"]["mass_flow_kg_s"] > 0
    inlet = client.state(left)
    reference_curve = ReferencePumpCurve(
        volume_flow_m3_s=(0.0, 0.01, 0.02), head_m=(60.0, 40.0, 20.0), source_id="synthetic_curve"
    )
    converted = lh2dt_pump_map(
        PumpMapConversion(reference_curve=reference_curve, reference_density_kg_m3=inlet.density_kg_m3)
    )
    pump_context = context.model_copy(update={"asset_id": "mapped_pump_demo"})
    pump_parameters = {
        "head_curve_points": converted["head_curve_points"],
        "reference_speed_rpm": 3000.0,
        "speed_rpm": 3000.0,
        "isentropic_efficiency": 0.8,
        "NPSHr_m": 1.0,
    }
    pump = client.build(
        EquipmentRequest(
            model_id="mapped_pump", parameters=pump_parameters, parameter_source="synthetic_curve_conversion"
        ),
        pump_context,
    )
    outlet = StateRequest(fluid="ParaHydrogen", inputs={"P": 320000.0, "T": 20.0})
    mapped_result = client.evaluate_two_port(pump, left, outlet)
    operating = create_registry().evaluate(
        "equipment.pump_system",
        {
            "reference_curve": reference_curve,
            "static_head_m": 20000.0 / (inlet.density_kg_m3 * 9.80665),
            "resistance_head_s2_m5": 0.0,
            "parallel_pumps": 1,
            "series_pumps": 1,
            "density_kg_m3": inlet.density_kg_m3,
            "pump_efficiency": 0.8,
            "motor_efficiency": 1.0,
        },
        pump_context,
    )
    expected_mass_flow = operating.values["volume_flow_m3_s"] * inlet.density_kg_m3
    assert abs(mapped_result["owner_result"]["mass_flow_kg_s"] - expected_mass_flow) < 1e-10
    assert mapped_result["status"] == "computed"
    invalid_pump = client.build(
        EquipmentRequest(
            model_id="mapped_pump",
            parameters={**pump_parameters, "NPSHr_m": 1e6},
            parameter_source="synthetic_invalid_suction_margin",
        ),
        pump_context,
    )
    invalid_result = client.evaluate_two_port(invalid_pump, left, outlet)
    assert invalid_result["status"] == "withheld"
    missing = client.build(
        EquipmentRequest(
            model_id="pipe", parameters={"length_m": 1.0}, parameter_source="declared_incomplete"
        ),
        context,
    )
    assert missing.report.status == "needs_data" and missing.native is None
    dynamic_parameters = {
        "length_m": 1.0,
        "inner_diameter_m": 0.01,
        "roughness_m": 1e-6,
        "wall_heat_capacity_J_K": 1000.0,
        "local_loss_coefficient": 0.0,
        "fluid_wall_UA_W_K": 0.0,
        "ambient_temperature_K": 300.0,
        "ambient_UA_W_K": 0.0,
    }
    dynamic = client.build(
        EquipmentRequest(
            model_id="dynamic_hem_pipe",
            parameters=dynamic_parameters,
            parameter_source="synthetic_equilibrium",
        ),
        context,
    )
    liquid_state = client.initialize_dynamic_pipe(dynamic, left, time_s=0.0)
    equilibrium = client.step_dynamic_pipe(dynamic, liquid_state, left, left, timestep_s=0.001)
    assert abs(equilibrium.state.native.mass_kg - liquid_state.native.mass_kg) < 1e-12
    mixture = CoolPropProvider().resolve(
        StateRequest(fluid="ParaHydrogen", inputs={"P": 300000.0, "Q": 0.5}, single_phase_required=False)
    )
    mixture_request = StateRequest(
        fluid="ParaHydrogen",
        inputs={"P": 300000.0, "Hmass": mixture.enthalpy_j_kg},
        single_phase_required=False,
    )
    mixture_state = client.initialize_dynamic_pipe(dynamic, mixture_request, time_s=0.0)
    mixture_step = client.step_dynamic_pipe(
        dynamic, mixture_state, mixture_request, mixture_request, timestep_s=0.001
    )
    assert abs(mixture_step.native_result.fluid.quality - 0.5) < 1e-7
    lower_pressure = StateRequest(
        fluid="ParaHydrogen",
        inputs={"P": 299000.0, "Hmass": mixture.enthalpy_j_kg},
        single_phase_required=False,
    )
    pulse = client.advance_dynamic_pipe(
        dynamic,
        mixture_state,
        mixture_request,
        lower_pressure,
        interval_s=1e-5,
        tolerance=DynamicStepTolerance(
            mass_absolute_kg=1e-12,
            energy_absolute_j=1e-8,
            wall_temperature_absolute_k=1e-6,
            relative=1e-7,
            maximum_refinements=6,
        ),
    )
    assert pulse.state.native.mass_kg < mixture_state.native.mass_kg
    refinement_mass_difference = pulse.record["refinement_history"][-1]["absolute_differences"]["mass_kg"]
    record = {
        "schema": "pipeline_integrity.equipment_factory_checks.v1",
        "source": revision(args.lh2dt),
        "factory_model_ids": list(client.models()),
        "unknown_parameter_rejection": unknown.report.model_dump(mode="json"),
        "owner_native_unknown_name_reproducer": owner_native.report.model_dump(mode="json"),
        "missing_parameters_result": missing.report.model_dump(mode="json"),
        "steady_pipe": result,
        "mapped_pump_conversion": converted,
        "mapped_pump": mapped_result,
        "mapped_pump_reference_mass_flow_kg_s": expected_mass_flow,
        "mapped_pump_invalid_margin": invalid_result,
        "liquid_equilibrium_step": equilibrium.record,
        "two_phase_equilibrium_step": mixture_step.record,
        "two_phase_pressure_response_step": pulse.record,
        "two_phase_refinement_mass_difference_kg": refinement_mass_difference,
        "dynamic_release_or_column_separation_qualified": False,
        "independent_field_validation": False,
    }
    Path("research/equipment_factory_results.json").write_text(
        json.dumps(record, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "factory_models": len(client.models()),
                "steady_pipe_flow_kg_s": result["owner_result"]["mass_flow_kg_s"],
                "dynamic_liquid_state": "passed",
                "dynamic_equilibrium_quality": mixture_step.native_result.fluid.quality,
                "pressure_response_substeps": pulse.record["accepted_substeps"],
            }
        )
    )


if __name__ == "__main__":
    main()
