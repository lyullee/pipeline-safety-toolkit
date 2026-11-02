import pytest
from pydantic import ValidationError

from pipeline_integrity import create_registry
from pipeline_integrity.contracts import RunContext
from pipeline_integrity.requirements import DeclaredRequirement, evaluate_requirement
from pipeline_integrity.workflow import ResultBinding, Workflow, WorkflowStep, run_workflow


def context():
    return RunContext(asset_id="authored", scenario_id="budget", clock_id="elapsed_s", origin="synthetic")


def pump():
    return WorkflowStep(
        id="pump",
        model_id="equipment.pump_power",
        parameters={
            "volume_flow_m3_s": 0.01,
            "density_kg_m3": 1000.0,
            "head_m": 10.0,
            "pump_efficiency": 0.8,
            "motor_efficiency": 0.9,
        },
    )


def energy():
    return WorkflowStep(
        id="energy",
        model_id="economics.energy",
        parameters={
            "operating_duration_s": 3600.0,
            "price_per_kwh": 0.2,
            "emission_kg_co2e_per_kwh": 0.4,
            "currency": "USD",
            "factor_source": "authored_test",
        },
        bindings={"electrical_power_w": ResultBinding(step_id="pump", output_name="electrical_power_w")},
    )


def test_out_of_order_workflow_closes_energy_budget():
    workflow = Workflow(id="pump_energy", context=context(), steps=(energy(), pump()))
    restored = Workflow.model_validate_json(workflow.model_dump_json())
    result = run_workflow(create_registry(), restored)
    assert result["all_computed"] is True
    assert result["steps"][0]["step_id"] == "pump"
    value = result["steps"][1]["result"]["values"]
    assert value["energy_kwh"] == pytest.approx(0.980665 / (0.8 * 0.9))
    assert value["energy_cost"] == pytest.approx(value["energy_kwh"] * 0.2)
    assert result["steps"][1]["bindings"][0]["parent_result_digest"]


def test_workflow_rejects_unit_mismatch():
    step = energy().model_copy(
        update={
            "bindings": {"electrical_power_w": ResultBinding(step_id="pump", output_name="shaft_power_w")}
        }
    )
    assert run_workflow(create_registry(), Workflow(id="valid", context=context(), steps=(step, pump())))[
        "all_computed"
    ]
    wrong = WorkflowStep(
        id="wrong",
        model_id="thermal.free_expansion",
        parameters={
            "linear_expansion_per_k": 1e-5,
            "reference_temperature_k": 293.15,
            "operating_temperature_k": 350.0,
        },
        bindings={"reference_length_m": ResultBinding(step_id="pump", output_name="shaft_power_w")},
    )
    with pytest.raises(ValueError, match="units"):
        run_workflow(create_registry(), Workflow(id="wrong_units", context=context(), steps=(pump(), wrong)))


def test_workflow_cycle_and_double_input_are_rejected():
    with pytest.raises(ValidationError):
        WorkflowStep(
            id="double",
            model_id="anything",
            parameters={"x": 1.0},
            bindings={"x": ResultBinding(step_id="parent", output_name="x")},
        )
    step = WorkflowStep(
        id="cycle",
        model_id="anything",
        parameters={},
        bindings={"x": ResultBinding(step_id="cycle", output_name="x")},
    )
    with pytest.raises(ValidationError, match="cycle"):
        Workflow(id="cycle", context=context(), steps=(step,))


def test_requirement_does_not_promote_incomplete_result():
    engine = create_registry()
    output = engine.evaluate(
        "hydraulics.hydrostatic",
        dict(inlet_pressure_pa_abs=101325.0, density_kg_m3=1000.0, outlet_minus_inlet_elevation_m=100.0),
        context(),
    )
    rule = DeclaredRequirement(
        id="declared_minimum",
        model_id="hydraulics.hydrostatic",
        output_name="outlet_pressure_pa_abs",
        comparison="ge",
        limit=100000.0,
        unit="Pa_abs",
        criterion_source="authored_example",
        applicability="authored scenario only",
    )
    assert evaluate_requirement(rule, output)["outcome"] == "withheld"
    actual = engine.evaluate(
        "hydraulics.hydrostatic",
        dict(inlet_pressure_pa_abs=201325.0, density_kg_m3=1000.0, outlet_minus_inlet_elevation_m=1.0),
        context(),
    )
    assert evaluate_requirement(rule, actual)["outcome"] == "satisfied"
    assert not evaluate_requirement(rule, actual)["whole_standard_compliance_claim"]
