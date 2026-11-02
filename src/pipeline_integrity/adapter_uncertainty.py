"""Reviewed scalar adapter bindings for the shared ensemble owner."""
from __future__ import annotations

import copy
import importlib
import math
import json
from pathlib import Path
from typing import Literal

from pydantic import ValidationError, model_validator

from .contracts import Contract, Name, RunContext, digest
from .datasets import file_digest
from . import adapter_jobs, adapter_campaign, uncertainty
from .uncertainty import EnsembleMetric, EnsembleNode, EnsembleValues, UncertainVariable


class AdapterUncertainInput(Contract):
    variable_id: Name
    parameter_pointer: str
    target_unit: Name
    evidence_id: Name
    unit_mapping_reviewed: bool = False

    @model_validator(mode="after")
    def pointer(self):
        adapter_campaign._tokens(self.parameter_pointer)
        return self


class AdapterUncertainMetric(Contract):
    metric: EnsembleMetric
    result_pointer: str
    source_unit: Name
    source_meaning: Literal["quantity", "conditional_probability", "expected_count_rate", "cost"]
    evidence_id: Name
    scalar_unit_meaning_reviewed: bool = False
    probability_calculated_pointer: str | None = None
    scalar_status_pointer: str | None = None

    @model_validator(mode="after")
    def pointers(self):
        adapter_campaign._tokens(self.result_pointer)
        if self.probability_calculated_pointer is not None:
            adapter_campaign._tokens(self.probability_calculated_pointer)
        if self.scalar_status_pointer is not None:
            adapter_campaign._tokens(self.scalar_status_pointer)
        if self.metric.meaning == "conditional_probability" and self.probability_calculated_pointer is None:
            raise ValueError("probability output requires an explicit native calculation flag pointer")
        if self.source_unit != self.metric.unit or self.source_meaning != self.metric.meaning:
            raise ValueError("source unit and meaning must exactly match the ensemble metric")
        return self


def adapter_ensemble_node(job: adapter_jobs.AdapterJob, *, id: str,
                          variables: tuple[UncertainVariable, ...],
                          inputs: tuple[AdapterUncertainInput, ...],
                          metrics: tuple[AdapterUncertainMetric, ...], providers: dict,
                          accept_computed_diagnostic: bool = False,
                          context_boundary_review_evidence_id: str | None = None,
                          maximum_job_bytes: int = 64_000_000) -> EnsembleNode:
    """Prepare without evaluating a model; draw generation/statistics remain external.

    Pointers address existing parameters and the native result record respectively.
    Scalar unit/meaning declarations are explicit reviewed caller contracts, rather
    than inferred from field spelling. Provider objects retain native source rules.
    """
    base = adapter_jobs.AdapterJob.model_validate_json(job.model_dump_json())
    declared = tuple(UncertainVariable.model_validate_json(v.model_dump_json()) for v in variables)
    bound = tuple(AdapterUncertainInput.model_validate_json(b.model_dump_json()) for b in inputs)
    outputs = tuple(AdapterUncertainMetric.model_validate_json(m.model_dump_json()) for m in metrics)
    if not isinstance(providers, dict):
        raise TypeError("providers must be an explicit alias-to-object dictionary")
    registry = dict(providers)
    if not id.strip() or not declared or len({v.id for v in declared}) != len(declared):
        raise ValueError("nonempty node ID and unique variables are required")
    if not bound or len({b.variable_id for b in bound}) != len(bound):
        raise ValueError("each input variable must have one binding")
    if not outputs or len({m.metric.id for m in outputs}) != len(outputs):
        raise ValueError("unique nonempty scalar metrics are required")
    variable_map = {v.id: v for v in declared}
    paths = [adapter_campaign._tokens(b.parameter_pointer) for b in bound]
    for i, path in enumerate(paths):
        for other in paths[i + 1:]:
            if path[:len(other)] == other or other[:len(path)] == path:
                raise ValueError("uncertain parameter writes must not overlap")
    for b, path in zip(bound, paths):
        if b.variable_id not in variable_map or variable_map[b.variable_id].unit != b.target_unit:
            raise ValueError("binding variable and target units must exactly match")
        current = adapter_campaign._read(base.parameters, path)
        if current is not None and (type(current) not in (int, float) or not math.isfinite(current)):
            raise ValueError("uncertain target must be an existing finite scalar or null")
        if not b.unit_mapping_reviewed:
            raise ValueError("input scalar unit mapping requires explicit review")
    if any(not m.scalar_unit_meaning_reviewed for m in outputs):
        raise ValueError("output scalar unit/meaning mapping requires explicit review")
    if context_boundary_review_evidence_id is not None and base.adapter_id not in adapter_campaign.CONTEXT_BOUNDARIES:
        raise ValueError("adapter has no allowlisted context boundary builder")
    if type(accept_computed_diagnostic) is not bool:
        raise TypeError("diagnostic acceptance must be an explicit boolean")
    if type(maximum_job_bytes) is not int or not 0 < maximum_job_bytes <= 1_000_000_000:
        raise ValueError("job byte budget must be positive and bounded")
    description = adapter_jobs.describe_adapter(base.adapter_id)
    spec = description["adapter"]
    module = importlib.import_module("pipeline_integrity." + spec["module"])
    request_type = getattr(module, spec["inputs"])
    source_paths = {"bridge": Path(__file__), "adapter_jobs": Path(adapter_jobs.__file__),
                    "adapter_campaign": Path(adapter_campaign.__file__),
                    "uncertainty": Path(uncertainty.__file__), "adapter": Path(module.__file__),
                    "contracts": Path(__file__).with_name("contracts.py"),
                    "datasets": Path(__file__).with_name("datasets.py")}
    source_pins = {k: file_digest(p) for k, p in source_paths.items()}
    configuration = dict(base_job=base.model_dump(mode="json"),
                         variables=[v.model_dump(mode="json") for v in declared],
                         inputs=[b.model_dump(mode="json") for b in bound],
                         metrics=[m.model_dump(mode="json") for m in outputs],
                         accept_computed_diagnostic=accept_computed_diagnostic,
                         context_boundary_review_evidence_id=context_boundary_review_evidence_id,
                         maximum_job_bytes=maximum_job_bytes, source_pins=source_pins,
                         provider_aliases=sorted(registry), adapter_description=description)
    frozen = copy.deepcopy(configuration)
    config_digest = digest(frozen)
    required_evidence = {v.evidence_id for v in declared} | {b.evidence_id for b in bound}
    required_evidence |= {m.evidence_id for m in outputs} | {m.metric.evidence_id for m in outputs}
    required_evidence |= {m.metric.threshold.evidence_id for m in outputs if m.metric.threshold is not None}
    if context_boundary_review_evidence_id is not None:
        required_evidence.add(context_boundary_review_evidence_id)

    def guard():
        if digest(configuration) != config_digest:
            raise ValueError("prepared adapter ensemble configuration changed")
        if {k: file_digest(p) for k, p in source_paths.items()} != source_pins:
            raise ValueError("adapter ensemble source changed after preparation")

    def evaluate(values, context: RunContext):
        guard()
        evidence = {e.id: e for e in context.evidence}
        if any(k not in evidence or evidence[k].sha256 is None for k in required_evidence):
            raise ValueError("draw context requires hash-pinned input and metric review evidence")
        if (context.asset_id, context.scenario_id, context.clock_id) != (
                base.context.asset_id, base.context.scenario_id, base.context.clock_id):
            raise ValueError("draw asset/scenario/clock differs from the base adapter job")
        if any(evidence.get(e.id) != e for e in base.context.evidence):
            raise ValueError("draw context must preserve every base source evidence reference")
        units = {m.metric.id: m.metric.unit for m in outputs}

        def withheld(status, payload):
            guard()
            return EnsembleValues(context=context, values=dict.fromkeys(units), units=units,
                                  statuses=dict.fromkeys(units, status), native_payload=payload)

        missing = [b.variable_id for b in bound if values.get(b.variable_id) is None]
        if missing:
            return withheld("needs_data", dict(issue="missing bound draw values", variable_ids=missing))
        raw = copy.deepcopy(frozen["base_job"])
        raw["context"] = context.model_dump(mode="json")
        for b, path in zip(bound, paths):
            value = values[b.variable_id]
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError("bound draw value must be a finite scalar")
            target = adapter_campaign._read(raw["parameters"], path[:-1]) if len(path) > 1 else raw["parameters"]
            target[int(path[-1]) if isinstance(target, list) else path[-1]] = value
        # Only the declared parameter contract's validation failures are classified.
        # Exceptions inside context builders, providers or numerical owners propagate.
        try:
            request_type.model_validate_json(json.dumps(raw["parameters"],allow_nan=False))
        except ValidationError as exc:
            if exc.title!=description["parameters_schema"]["title"]:
                raise
            return withheld("out_of_scope", dict(issue="draw violates adapter parameter contract",
                                                 validation_errors=exc.errors(include_url=False,include_context=False,include_input=False),
                                                 draw_values=dict(values)))
        draw_job = adapter_jobs.AdapterJob.model_validate_json(json.dumps(raw,allow_nan=False))
        binding_receipt = None
        if context_boundary_review_evidence_id is not None:
            draw_job, binding_receipt = adapter_campaign.bind_adapter_job_context(
                draw_job, review_evidence_id=context_boundary_review_evidence_id)
        native = adapter_jobs.run_adapter_job(draw_job, providers=dict(registry),
                                              maximum_job_bytes=maximum_job_bytes)
        guard()
        result = native["result"]
        native_status = result.get("status")
        accepted = native_status == "computed" or (
            accept_computed_diagnostic and native_status == "computed_diagnostic")
        out, statuses = {}, {}
        for m in outputs:
            key = m.metric.id
            out[key] = None
            statuses[key] = "needs_data" if native_status == "needs_data" else "out_of_scope"
            if not accepted:
                continue
            if m.scalar_status_pointer is not None:
                scalar_status = adapter_campaign._read(result, adapter_campaign._tokens(m.scalar_status_pointer))
                scalar_accepted = scalar_status == "computed" or (
                    accept_computed_diagnostic and scalar_status == "computed_diagnostic")
                if not scalar_accepted:
                    statuses[key] = "needs_data" if scalar_status == "needs_data" else "out_of_scope"
                    continue
            if m.metric.meaning == "conditional_probability":
                flag = adapter_campaign._read(result, adapter_campaign._tokens(m.probability_calculated_pointer))
                if flag is not True or result.get("probability_calculated") is False:
                    raise ValueError("native record does not confirm a calculated conditional probability")
            value = adapter_campaign._read(result, adapter_campaign._tokens(m.result_pointer))
            if value is None:
                statuses[key] = "needs_data"
            elif type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError("reviewed result pointer did not produce a finite scalar")
            else:
                out[key], statuses[key] = value, "computed"
        return EnsembleValues(context=context, values=out, units=units, statuses=statuses,
                              native_payload=dict(adapter_job_record=native,
                                                  context_binding_receipt=binding_receipt,
                                                  native_status=native_status,
                                                  diagnostic_accepted=accepted and native_status == "computed_diagnostic",
                                                  field_qualification_conferred=False,
                                                  draw_values=dict(values), configuration_digest=config_digest))

    guard()
    return EnsembleNode(id=id, version="1", source_digest=digest(source_pins),
                        configuration=configuration, metrics=tuple(m.metric for m in outputs), evaluate=evaluate)
