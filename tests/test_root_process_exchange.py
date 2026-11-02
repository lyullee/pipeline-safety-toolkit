"""Public API serialization and process-source rejection before native replay."""

import copy

import pytest

import pipeline_integrity as api
from pipeline_integrity.contracts import EvidenceRef, RunContext, digest
from pipeline_integrity.signal_exchange import ProcessSignalRequest, evaluate_process_signals


def test_root_adapter_typed_context_preserves_json_identity_and_explicit_job_id(monkeypatch):
    seen = []

    def dispatch(job, **kwargs):
        seen.append((job, kwargs))
        return job

    monkeypatch.setattr(api, "run_adapter_job", dispatch)
    context = RunContext(asset_id="unit", scenario_id="case", clock_id="seconds", origin="synthetic")
    first = api.run_adapter("risk.stochastic_process", {"supplied_rates": (0.1, 0.2)}, context)
    second = api.run_adapter("risk.stochastic_process", {"supplied_rates": [0.1, 0.2]}, context.model_dump(mode="json"))
    assert first == second
    assert first["parameters"]["supplied_rates"] == [0.1, 0.2]
    assert first["context"] == context.model_dump(mode="json")
    named = api.run_adapter("risk.stochastic_process", {}, context, job_id="reviewed-job", maximum_job_bytes=12345)
    assert named["id"] == "reviewed-job"
    assert seen[-1][1]["maximum_job_bytes"] == 12345


def signal_case():
    source_context = RunContext(asset_id="unit", scenario_id="case", clock_id="seconds", origin="synthetic")
    source = {"context": source_context.model_dump(mode="json"), "states": [{"time_s": 0.0}]}
    source["record_digest"] = digest(source)
    context = source_context.model_copy(update={"evidence": (
        EvidenceRef(id="source", citation="Authored source receipt fixture", origin="synthetic", sha256=source["record_digest"]),)})
    request = ProcessSignalRequest(id="signals", source_kind="vapor_cavity_moc", source_record=source,
        expected_source_record_digest=source["record_digest"], source_evidence_id="source", stream_id="stream")
    return request, context


def test_process_signal_requires_explicit_spatial_selector_before_native_replay():
    request, context = signal_case()
    with pytest.raises(ValueError, match="section selector"):
        evaluate_process_signals(request, context)


def test_process_signal_rejects_changed_source_and_mismatched_context_evidence():
    request, context = signal_case()
    changed = copy.deepcopy(request.source_record)
    changed["states"][0]["time_s"] = 1.0
    with pytest.raises(ValueError, match="receipt changed"):
        evaluate_process_signals(request.model_copy(update={"source_record": changed}), context)
    wrong = context.model_copy(update={"evidence": (context.evidence[0].model_copy(update={"sha256": "f" * 64}),)})
    with pytest.raises(ValueError, match="matching unambiguous"):
        evaluate_process_signals(request, wrong)
