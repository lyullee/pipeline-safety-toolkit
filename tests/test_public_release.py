"""Portable public API checks requiring only the package's mandatory dependencies."""

import json
from pathlib import Path

import pytest

from pipeline_integrity import create_registry, engineering_method_specs
from pipeline_integrity.cli import main
from pipeline_integrity.contracts import RunContext
from pipeline_integrity.engineering_methods import (
    engineering_method_implementation_progress,
    get_engineering_method_callable,
)
from pipeline_integrity.nominal_pipe_pressure import NominalHoopStressInput
from pipeline_integrity.workflow import Workflow, run_workflow


def test_all_223_catalog_source_bindings_match_packaged_bytes():
    specs = engineering_method_specs()
    assert len(specs) == len({s.id for s in specs}) == 223
    result = engineering_method_implementation_progress()
    assert result["implemented"] == result["total"] == 223
    assert result["remaining"] == 0
    assert result["numerical_qualification_inferred"] is False
    assert all(not row["issues"] for row in result["rows"])


def test_resolved_public_method_executes_with_typed_si_request():
    solve = get_engineering_method_callable(
        "pressure.nominal_membrane",
        "pipeline_integrity.nominal_pipe_pressure.nominal_hoop_stress_Pa",
    )
    request = NominalHoopStressInput(
        differential_pressure_Pa=1e6, outside_diameter_m=0.4, wall_thickness_m=0.01
    )
    restored = NominalHoopStressInput.model_validate_json(request.model_dump_json())
    assert solve(restored) == pytest.approx(20e6)


def test_core_registry_preserves_context_and_units():
    context = RunContext(asset_id="release-pipe", scenario_id="pump", clock_id="elapsed", origin="synthetic")
    result = create_registry().evaluate(
        "equipment.pump_power",
        {
            "volume_flow_m3_s": 0.02,
            "density_kg_m3": 1000.0,
            "head_m": 10.0,
            "pump_efficiency": 0.8,
            "motor_efficiency": 0.9,
            "gravity_m_s2": 10.0,
        },
        context,
    )
    assert result.status == "computed"
    assert result.values["hydraulic_power_w"] == pytest.approx(2000.0)
    assert result.values["electrical_power_w"] == pytest.approx(2000 / 0.8 / 0.9)
    assert result.context == context
    assert result.output_units["electrical_power_w"] == "W"


def test_complete_core_workflow_uses_real_downstream_calculation():
    root = Path(__file__).resolve().parents[1]
    workflow = Workflow.model_validate_json(
        (root / "examples/pump_energy_workflow.json").read_text(encoding="utf-8")
    )
    result = run_workflow(create_registry(), workflow)
    assert result["all_computed"]
    assert len(result["steps"]) >= 2
    assert any(step["bindings"] for step in result["steps"])
    assert result["engineering_acceptance_claim"] is False


def test_console_api_reports_the_fixed_scope(capsys):
    assert main(["code-progress"]) in (None, 0)
    result = json.loads(capsys.readouterr().out)
    assert result["implemented"] == result["total"] == 223
    assert result["numerical_qualification_inferred"] is False


def test_unknown_method_and_unbound_api_are_rejected():
    with pytest.raises(KeyError):
        get_engineering_method_callable("not-a-method")
    with pytest.raises(ValueError, match="not bound"):
        get_engineering_method_callable("pressure.nominal_membrane", "os.system")
