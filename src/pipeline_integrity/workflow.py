"""Acyclic model composition with unit checks and explicit withheld results."""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import Field, model_validator

from .contracts import Contract, Name, RunContext, digest
from .registry import Registry


class ResultBinding(Contract):
    step_id: Name
    output_name: Name


class WorkflowStep(Contract):
    id: Name
    model_id: Name
    parameters: dict[str, Any]
    bindings: dict[str, ResultBinding] = Field(default_factory=dict)
    context: RunContext | None = None

    @model_validator(mode="after")
    def one_source_per_input(self):
        if set(self.parameters) & set(self.bindings):
            raise ValueError("a model input cannot be supplied both literally and by a result binding")
        return self


class Workflow(Contract):
    schema_version: Literal["pipeline_integrity.workflow.v1"] = "pipeline_integrity.workflow.v1"
    id: Name
    context: RunContext
    steps: tuple[WorkflowStep, ...]

    @model_validator(mode="after")
    def graph(self):
        keys = {step.id for step in self.steps}
        if not keys or len(keys) != len(self.steps):
            raise ValueError("nonempty unique workflow step IDs are required")
        pending = {step.id: {binding.step_id for binding in step.bindings.values()} for step in self.steps}
        if any(parents - keys for parents in pending.values()):
            raise ValueError("a result binding references a step outside this workflow")
        done = set()
        while pending:
            ready = {key for key, parents in pending.items() if parents <= done}
            if not ready:
                raise ValueError("model workflow contains a dependency cycle")
            done.update(ready)
            pending = {key: parents for key, parents in pending.items() if key not in ready}
        return self


def run_workflow(engine: Registry, workflow: Workflow):
    pending, results, rows = list(workflow.steps), {}, []
    while pending:
        step = next(step for step in pending if all(b.step_id in results for b in step.bindings.values()))
        info = engine.describe(step.model_id)["model"]
        parameters = dict(step.parameters)
        trace, withheld = [], []
        for field, binding in step.bindings.items():
            parent = results[binding.step_id]
            if parent["status"] != "computed":
                withheld.append(binding.step_id)
                continue
            source = parent["result"]
            if binding.output_name not in source["values"]:
                raise ValueError("bound output is absent from its supplying model")
            unit = source["output_units"][binding.output_name]
            if info["input_units"].get(field) != unit:
                raise ValueError("bound model quantities have different units or pressure references")
            parameters[field] = source["values"][binding.output_name]
            trace.append(
                {
                    "input": field,
                    "from_step": binding.step_id,
                    "from_output": binding.output_name,
                    "unit": unit,
                    "parent_result_digest": digest(source),
                }
            )
        if withheld:
            row = {
                "step_id": step.id,
                "model_id": step.model_id,
                "status": "withheld",
                "because_steps": sorted(set(withheld)),
                "bindings": trace,
            }
        else:
            result = engine.evaluate(
                step.model_id, json.dumps(parameters), step.context or workflow.context, json_input=True
            )
            row = {
                "step_id": step.id,
                "model_id": step.model_id,
                "status": result.status,
                "result": result.model_dump(mode="json"),
                "bindings": trace,
            }
        rows.append(row)
        results[step.id] = row
        pending.remove(step)
    return {
        "schema": "pipeline_integrity.workflow_result.v1",
        "workflow_id": workflow.id,
        "input_digest": digest(workflow),
        "steps": rows,
        "all_computed": all(row["status"] == "computed" for row in rows),
        "engineering_acceptance_claim": False,
    }
