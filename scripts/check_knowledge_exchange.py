"""Run actual offline SAGA APIs against an original, privately stored MIT corpus."""

import argparse
import json
import re
import runpy
from copy import deepcopy
from pathlib import Path

from pipeline_integrity.contracts import digest
from pipeline_integrity.datasets import file_digest
from pipeline_integrity.knowledge_exchange import (
    SagaOfflineClient,
    evaluate_knowledge,
    knowledge_source_fingerprint,
    verify_knowledge_record,
)


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def tree(root):
    return {p.relative_to(root).as_posix(): file_digest(p) for p in sorted(root.rglob("*")) if p.is_file()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--saga", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path, default=Path("artifacts/knowledge_exchange"))
    args = parser.parse_args()
    args.saga = args.saga.resolve()
    before, pin = tree(args.saga), knowledge_source_fingerprint(args.saga)
    requests, context = runpy.run_path("examples/knowledge_exchange.py")["make_cases"](pin["source_sha256"])
    client = SagaOfflineClient(source_root=args.saga, python_executable=args.python, work_root=args.artifacts)
    records, checks = {}, {}

    def evaluate(name, request):
        result = evaluate_knowledge(request, context, client=client)
        verify_knowledge_record(result)
        records[name] = result
        return result

    def check(name, condition):
        assert condition, name
        checks[name] = True

    for method, request in requests.items():
        evaluate(method, request)
    retrieval = records["retrieve"]["native_receipt"]
    check("hybrid_actual_hit", retrieval["citations"][0]["doc_code"] == "DEMO-PRESSURE")
    check("native_citation_schema", len(retrieval["schema_digests"]["Citation"]) == 64)
    check("explicit_mit_rights", all(c["license_id"] == "MIT" for c in retrieval["citations"]))
    check(
        "retrieval_is_not_reasoning", retrieval["reasoning_executed"] is False and retrieval["answer"] is None
    )
    candidate = records["candidate_review"]["native_receipt"]
    check("pending_rule_never_monitoring_ready", candidate["candidate_audit"]["monitoring_ready"] is False)
    check("actual_readiness_one_candidate", candidate["candidate_audit"]["total_rules"] == 1)
    check("native_rule_pending", candidate["native_request"][0]["threshold_confidence"] == "pending")
    check(
        "native_candidate_fields_and_context_preserved",
        {
            k: candidate["native_request"][0][k]
            for k in ("threshold_value", "unit", "compare_dir", "scenario_id")
        }
        == {
            "threshold_value": 200000.0,
            "unit": "Pa",
            "compare_dir": ">=",
            "scenario_id": context.scenario_id,
        },
    )
    check("threshold_service_missing", records["threshold_proposal"]["status"] == "needs_data")
    proposal = records["threshold_proposal"]["native_receipt"]["threshold_proposal"]
    check("actual_proposal_references", bool(proposal["references"]))
    check("no_generated_thresholds", proposal["candidates"] == [] and proposal["ready"] is False)
    check("service_approval_required", proposal["requires_human_approval"] is True)
    check(
        "native_proposal_request_context_preserved",
        records["threshold_proposal"]["native_receipt"]["native_request"]["equipment_name"]
        == context.asset_id
        and records["threshold_proposal"]["native_receipt"]["native_request"]["context"]
        == requests["threshold_proposal"].question,
    )
    check("answer_service_missing", records["grounded_answer"]["status"] == "needs_data")
    filtered = evaluate(
        "filtered", requests["retrieve"].model_copy(update={"document_codes": ("DEMO-TEMPERATURE",)})
    )
    check(
        "hard_document_filter",
        bool(filtered["native_receipt"]["citations"])
        and all(c["doc_code"] == "DEMO-TEMPERATURE" for c in filtered["native_receipt"]["citations"]),
    )
    original = requests["candidate_review"].candidates[0]
    for name, update in (
        ("zero", {"threshold_value": 0.0}),
        ("unknown", {"threshold_value": None}),
        ("nonnumeric_direction", {"compare_dir": "High"}),
    ):
        changed = requests["candidate_review"].model_copy(
            update={"candidates": (original.model_copy(update=update),)}
        )
        audit = evaluate(name, changed)["native_receipt"]
        check(
            name + "_preserved", audit["rule_candidates"][0] == changed.candidates[0].model_dump(mode="json")
        )
        check(name + "_not_approved", audit["candidate_audit"]["monitoring_ready"] is False)
    check(
        "zero_not_missing", records["zero"]["native_receipt"]["native_request"][0]["threshold_value"] == 0.0
    )
    check(
        "unknown_remains_null",
        records["unknown"]["native_receipt"]["native_request"][0]["threshold_value"] is None,
    )
    for name, update in (
        ("default_review", {"applicability_reviews": {}}),
        ("absent_sources", {"sources": (), "allowed_source_ids": (), "candidates": ()}),
    ):
        result = evaluate(name, requests["retrieve"].model_copy(update=update))
        check(
            name + "_held_without_native",
            result["status"] == "needs_data" and result["native_receipt"] is None,
        )
    after = tree(args.saga)
    check("original_checkout_all_files_unchanged", before == after)
    rejected = []
    for name in (
        "citation_page",
        "source_version",
        "authority",
        "native_schema",
        "input_binding",
        "source_pin",
        "loaded_module",
    ):
        damaged = deepcopy(records["retrieve"])
        native = damaged["native_receipt"]
        if name == "citation_page":
            native["citations"][0]["page"] += 100
        elif name == "source_version":
            native["citations"][0]["source_version"] = "unreviewed-v2"
        elif name == "authority":
            native["authority"]["approved_rule"] = True
        elif name == "native_schema":
            native["schema_version"] = "unknown"
        elif name == "input_binding":
            native["request_digest"] = "f" * 64
        elif name == "source_pin":
            native["source_pin"]["unchanged"] = False
        else:
            native["loaded_modules"]["saga.rag"] = "other/rag.py"
        damaged["receipt_digest"] = digest({k: v for k, v in damaged.items() if k != "receipt_digest"})
        try:
            verify_knowledge_record(damaged)
        except ValueError:
            rejected.append(name)
        else:
            raise AssertionError("corrupt knowledge receipt accepted: " + name)
    corruptions = [
        ("candidate_review", "native_request", field, value)
        for field, value in (
            ("no", True),
            ("scenario_id", "other-scenario"),
            ("tag_id", "other-tag"),
            ("item_name", "temperature"),
            ("unit", "psi"),
            ("threshold_type", "DELTA_START"),
            ("threshold_value", None),
            ("compare_dir", "<="),
            ("threshold_basis", "changed rationale"),
            ("threshold_source", "other:p1"),
            ("source", "operating-rules"),
        )
    ]
    corruptions += [
        ("candidate_review", "candidate_audit", field, value)
        for field, value in (("scenario_id", "other"), ("ready_rules", 1), ("not_ready_rules", 0))
    ]
    corruptions += [
        ("threshold_proposal", target, field, value)
        for target, field, value in (
            ("threshold_proposal", "ready", True),
            ("threshold_proposal", "candidates", [{"threshold_value": 1.0}]),
            ("threshold_proposal", "requires_human_approval", False),
            ("threshold_proposal", "scenario_id", "other"),
            ("threshold_proposal", "tag_id", "other"),
            ("native_request", "equipment_name", "other"),
            ("native_request", "context", "different question"),
            ("native_request", "unit", "psi"),
            ("native_request", "model", "unreviewed-model"),
        )
    ]
    for method, target, field, value in corruptions:
        damaged = deepcopy(records[method])
        projected = damaged["native_receipt"][target]
        if target == "native_request" and method == "candidate_review":
            projected = projected[0]
        projected[field] = value
        damaged["receipt_digest"] = digest({k: v for k, v in damaged.items() if k != "receipt_digest"})
        try:
            verify_knowledge_record(damaged)
        except ValueError:
            rejected.append(f"{method}_{target}_{field}")
        else:
            raise AssertionError("rehashed knowledge projection accepted: " + field)
    for name, base, mutate in (
        ("missing_null_value", "unknown", "missing"),
        ("boolean_zero_value", "zero", "boolean"),
        ("unexecuted_proposal_computed", "threshold_proposal", "computed"),
    ):
        damaged = deepcopy(records[base])
        if mutate == "missing":
            del damaged["native_receipt"]["native_request"][0]["threshold_value"]
        elif mutate == "boolean":
            damaged["native_receipt"]["native_request"][0]["threshold_value"] = False
        else:
            damaged["status"] = damaged["native_receipt"]["status"] = "computed"
        damaged["receipt_digest"] = digest({k: v for k, v in damaged.items() if k != "receipt_digest"})
        try:
            verify_knowledge_record(damaged)
        except ValueError:
            rejected.append(name)
        else:
            raise AssertionError("rehashed knowledge meaning accepted: " + name)
    check("native_receipt_semantic_corruption_rejected", len(rejected) == 33)
    check(
        "no_network_calls",
        all(
            r["native_receipt"] is None or r["native_receipt"]["network_calls"] == 0 for r in records.values()
        ),
    )
    save(args.artifacts / "corpus.json", [s.model_dump(mode="json") for s in requests["retrieve"].sources])
    save(args.artifacts / "records.json", records)
    save(args.artifacts / "reference_checks.json", checks)
    (args.artifacts / "LICENSE").write_bytes(Path("LICENSE").read_bytes())
    doc = Path("docs/KNOWLEDGE_EXCHANGE_KO.md").read_text(encoding="utf-8")
    python_blocks = re.findall(r"```python\n(.*?)```", doc, flags=re.S)
    for block in python_blocks:
        exec(compile(block, "KNOWLEDGE_EXCHANGE_KO.md", "exec"), {})
    own = [
        "src/pipeline_integrity/knowledge_exchange.py",
        "src/pipeline_integrity/knowledge_worker.py",
        "tests/test_knowledge_exchange.py",
        "examples/knowledge_exchange.py",
        "scripts/check_knowledge_exchange.py",
        "docs/KNOWLEDGE_EXCHANGE_KO.md",
    ]
    summary = dict(
        schema_version="pipeline_integrity.knowledge_summary.v1",
        capability="knowledge.assistance",
        qualification="offline_library_interface_only",
        recommended_scope_state="partial",
        synthetic_corpus=dict(
            sources=2, passages=4, license_id="MIT", corpus_sha256=digest(requests["retrieve"].sources)
        ),
        executed_requests=sum(r["native_receipt"] is not None for r in records.values()),
        withheld_before_native=sum(r["native_receipt"] is None for r in records.values()),
        status_counts={
            s: sum(r["status"] == s for r in records.values()) for s in ("computed", "needs_data")
        },
        independent_contract_checks=len(checks),
        corrupted_receipts_rejected=len(rejected),
        documentation_python_blocks_executed=len(python_blocks),
        checks_passed=all(checks.values()),
        original_checkout_unchanged=before == after,
        source_fingerprint=pin,
        owner_version=retrieval["owner_version"],
        runtime=retrieval["runtime"],
        schema_digests=retrieval["schema_digests"],
        input_units={
            "as_of_utc": "timezone-aware instant",
            "threshold_value": "caller-declared SI unit",
            "retrieval_score": "owner ranking score; not probability",
        },
        original_license_sha256=file_digest(args.saga / "LICENSE"),
        limitations=[
            "Original synthetic corpus only; no regulatory correctness or field validation.",
            "No external LLM, provider API, human messaging or operational control was executed.",
            "Grounded answering and threshold reasoning remain needs_data.",
            "SAGA package version 2.0.0 and project version 2.0.1 are retained separately.",
        ],
        code_sha256={p: file_digest(Path(p)) for p in own if Path(p).exists()},
    )
    save(Path("research/knowledge_exchange_results.json"), summary)
    print(
        json.dumps(
            {
                k: summary[k]
                for k in (
                    "executed_requests",
                    "withheld_before_native",
                    "status_counts",
                    "independent_contract_checks",
                    "checks_passed",
                )
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
