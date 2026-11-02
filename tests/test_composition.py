import json
from pathlib import Path

import pytest
from pydantic import Field

from pipeline_integrity import create_registry
from pipeline_integrity.adapters import hra_signal_series, monitoring_batch
from pipeline_integrity.capabilities import capabilities
from pipeline_integrity.cli import main
from pipeline_integrity.contracts import (
    Calculation,
    Contract,
    EvidenceRef,
    ModelInfo,
    RunContext,
    SignalFrame,
)
from pipeline_integrity.procedures import Procedure, ProcedureItem, ProcedureRecord, assess_procedure
from pipeline_integrity.registry import ModelSpec

CONTEXT = RunContext(asset_id="test", scenario_id="test", clock_id="elapsed", origin="synthetic")


def test_custom_registered_model_and_cli(tmp_path):
    class Length(Contract):
        value_m: float = Field(gt=0)

    def calculate(value):
        return Calculation(values={"double_m": 2 * value.value_m})

    engine = create_registry()
    engine.register(
        ModelSpec(
            ModelInfo(
                id="user.double",
                version="1",
                title="User-owned model",
                category="custom",
                input_units={"value_m": "m"},
                output_units={"double_m": "m"},
                sources=("caller",),
                assumptions=("demonstration",),
                implementation="user_model",
                qualification="user_model",
            ),
            Length,
            calculate,
        )
    )
    assert engine.evaluate("user.double", {"value_m": 3.0}, CONTEXT).values["double_m"] == 6
    with pytest.raises(ValueError):
        engine.evaluate("user.double", {"value_m": "3"}, CONTEXT)
    output = tmp_path / "result.json"
    assert main(["evaluate", "examples/liquid_loss.json", "--output", str(output)]) == 0
    assert json.loads(output.read_text())["status"] == "computed"
    assert main(["workflow", "examples/pump_energy_workflow.json", "--output", str(output)]) == 0
    assert json.loads(output.read_text())["all_computed"] is True


def test_entire_calculation_catalog_has_runnable_examples():
    data = json.loads(Path("examples/engineering_cases.json").read_text(encoding="utf-8"))
    engine = create_registry()
    assert set(data["cases"]) == {row.id for row in engine.models()}
    context = RunContext.model_validate_json(json.dumps(data["context"]))
    for model_id, parameters in data["cases"].items():
        result = engine.evaluate(model_id, json.dumps(parameters), context, json_input=True)
        assert result.status == "computed", model_id
        assert result.input_digest and result.model_source_digest
    assert len({row.id for row in capabilities()}) == len(capabilities())
    assert {row.id for row in engine.models()} <= {row.id for row in capabilities()}


def test_exchange_retains_missing_samples_and_rejects_clock_mismatch():
    frames = tuple(
        SignalFrame(
            stream_id="s",
            clock_id="clock",
            time_s=float(i),
            values={"p": None if i == 1 else 2.0},
            units={"p": "Pa_abs"},
            valid={"p": i != 1},
            origin="synthetic",
        )
        for i in range(3)
    )
    batch = monitoring_batch(frames, factory=lambda **fields: fields)
    assert batch["valid"][:, 0].tolist() == [True, False, True]
    series = hra_signal_series(frames, "p", factory=lambda **fields: fields)
    assert series["values"][1] is None and series["clock_id"] == "clock"
    changed = frames[1].model_copy(update={"clock_id": "different"})
    with pytest.raises(ValueError, match="clock"):
        monitoring_batch((frames[0], changed), factory=lambda **fields: fields)


def test_procedure_missing_evidence_blocks_descendants():
    procedure = Procedure(
        id="site",
        version="1",
        applicable_asset_classes=("pipe",),
        items=(
            ProcedureItem(
                id="isolate",
                task="Caller-owned isolation check",
                criterion_source="site_1",
                required_evidence_ids=("isolation_record",),
            ),
            ProcedureItem(
                id="open",
                task="Caller-owned opening check",
                criterion_source="site_2",
                required_evidence_ids=("opening_record",),
                prerequisites=("isolate",),
            ),
        ),
    )
    result = assess_procedure(procedure, (), CONTEXT, asset_class="pipe")
    assert result["items"]["isolate"]["state"] == "needs_evidence"
    assert result["items"]["open"]["state"] == "blocked"
    records = tuple(
        ProcedureRecord(
            item_id=item.id,
            result="satisfied",
            evidence=(
                EvidenceRef(
                    id=item.required_evidence_ids[0], citation="reviewed record", origin="user_declared"
                ),
            ),
            reviewer_id="reviewer",
            reviewed_at="2026-10-26T12:00:00+09:00",
        )
        for item in procedure.items
    )
    result = assess_procedure(procedure, records, CONTEXT, asset_class="pipe")
    assert (
        result["all_records_satisfied"] and result["independent_physical_or_regulatory_acceptance"] is False
    )
