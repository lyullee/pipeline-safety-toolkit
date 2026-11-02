"""Isolated JSON worker: SAGA library APIs, a private corpus, and no network."""

from __future__ import annotations

import asyncio
import importlib.metadata
import inspect
import json
import socket
import sys
import tomllib
from pathlib import Path
from types import SimpleNamespace


def main():
    sys.dont_write_bytecode = True
    envelope = json.load(sys.stdin)
    source_root = Path(envelope["source_root"]).resolve()
    work_dir = Path(envelope["work_dir"]).resolve()
    if work_dir.is_relative_to(source_root) or not work_dir.is_dir():
        raise ValueError("worker requires an existing scratch folder outside SAGA")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from pipeline_integrity.contracts import RunContext, digest
    from pipeline_integrity.knowledge_exchange import (
        AUTHORITY,
        KnowledgeRequest,
        _candidate_rule_projection,
        _threshold_request_projection,
        knowledge_source_fingerprint,
    )

    request = KnowledgeRequest.model_validate_json(json.dumps(envelope["request"]))
    context = RunContext.model_validate_json(json.dumps(envelope["context"]))
    if (digest(request), digest(context)) != (envelope["request_digest"], envelope["context_digest"]):
        raise ValueError("worker JSON request binding differs")
    before = knowledge_source_fingerprint(source_root)
    if before["source_sha256"] != request.owner_source_sha256:
        raise ValueError("worker source pin differs")
    # Windows' event loop creates its own loopback wake-up pipe. Build it
    # before refusing subsequent socket connections, including provider calls.
    event_loop = asyncio.new_event_loop() if request.method == "threshold_proposal" else None

    def denied(*_args, **_kwargs):
        raise RuntimeError("network operations are disabled in the SAGA offline worker")

    socket.create_connection = denied
    socket.socket.connect = denied
    socket.socket.connect_ex = denied
    sys.path.insert(0, str(source_root / "python"))
    import saga
    from saga.database import Database
    from saga.hazop import HazopEngine
    from saga.rag import RagPipeline
    from saga.schemas import (
        AnswerReview,
        ChatRequest,
        Citation,
        DigitalTwinHazopRequest,
        GroundedClaims,
        HazopRuleInput,
        HazopThresholdProposalRequest,
        HazopThresholdProposalResponse,
    )
    from saga.text import build_search_text
    from saga.vector_index import VECTOR_MODEL

    for module_name, module in list(sys.modules.items()):
        if module_name == "saga" or module_name.startswith("saga."):
            if not Path(inspect.getfile(module)).resolve().is_relative_to(source_root / "python"):
                raise ValueError("loaded SAGA module lies outside the selected source")
    settings = SimpleNamespace(service_hub_api_key="", context_limit=request.retrieval_limit)
    database = Database(work_dir / "saga.sqlite3")
    database.initialize()
    chunk_sources = {}
    for source in request.sources:
        if request.document_codes and source.document_code not in request.document_codes:
            continue
        metadata = dict(
            doc_type="RULE",
            doc_code=source.document_code,
            title=source.title,
            filename=source.source_id + ".txt",
            file_path=source.source_uri,
            file_hash=source.content_sha256,
            page_count=max(p.page for p in source.passages),
            source_url=source.source_uri,
        )
        database.replace_document(
            metadata,
            [
                dict(
                    hierarchy=p.hierarchy, page=p.page, content=p.text, search_text=build_search_text(p.text)
                )
                for p in source.passages
            ],
        )
        with database.connect() as connection:
            rows = connection.execute(
                "SELECT c.id FROM chunks c JOIN documents d ON d.id=c.document_id WHERE d.filename=? ORDER BY c.id",
                (metadata["filename"],),
            ).fetchall()
        for row, passage in zip(rows, source.passages, strict=True):
            chunk_sources[int(row["id"])] = (source, passage)
    rows = database.hybrid_search(
        request.question,
        request.retrieval_limit,
        list(request.document_codes) or None,
        "CROSS",
        ["RULE"],
    )
    pipeline = RagPipeline(settings, database, None)
    citations = []
    for native in pipeline.citations(rows):
        source, passage = chunk_sources[native.chunk_id]
        citations.append(
            native.model_dump(mode="json")
            | dict(
                source_id=source.source_id,
                source_version=source.source_version,
                source_sha256=source.content_sha256,
                license_id=source.license_id,
                passage_id=passage.passage_id,
                locator=passage.locator,
            )
        )
    hazop = HazopEngine(database, settings, None, pipeline)
    audit, proposal, native_request = None, None, None
    status = "computed" if citations else "needs_data"
    notes = ["Offline retrieval and schema/readiness diagnostics; no reasoning provider was executed."]
    if request.method == "candidate_review":
        rules = []
        for fields in _candidate_rule_projection(request, context):
            rule = HazopRuleInput.model_validate(fields)
            rules.append(rule.model_dump(mode="json"))
        database.replace_hazop_rules(rules, replace=True)
        audit = hazop.monitoring_readiness(context.scenario_id)
        native_request = rules
    elif request.method == "threshold_proposal":
        native_request = HazopThresholdProposalRequest(**_threshold_request_projection(request, context))
        proposal = event_loop.run_until_complete(hazop.propose_threshold(native_request)).model_dump(
            mode="json"
        )
        event_loop.close()
        native_request = native_request.model_dump(mode="json")
        status = "needs_data"
    elif request.method == "grounded_answer":
        native_request = ChatRequest(message=request.question, mode="rag").model_dump(mode="json")
        status = "needs_data"
        notes.append(
            "Grounded answer request validated; reasoning service authorization and independent acceptance remain unresolved."
        )
    schemas = (
        Citation,
        ChatRequest,
        AnswerReview,
        DigitalTwinHazopRequest,
        HazopRuleInput,
        HazopThresholdProposalRequest,
        HazopThresholdProposalResponse,
        GroundedClaims,
    )
    loaded = sorted(name for name in sys.modules if name == "saga" or name.startswith("saga."))
    result = dict(
        schema_version="saga.offline_exchange.v1",
        request_digest=digest(request),
        context_digest=digest(context),
        status=status,
        authority=AUTHORITY,
        network_calls=0,
        reasoning_executed=False,
        answer=None,
        claims=[],
        reasoning_service=None,
        answer_review=None,
        citations=citations,
        rule_candidates=[c.model_dump(mode="json") for c in request.candidates],
        candidate_audit=audit,
        threshold_proposal=proposal,
        native_request=native_request,
        owner_version=dict(
            package=saga.__version__,
            project=tomllib.loads((source_root / "pyproject.toml").read_text(encoding="utf-8"))["project"][
                "version"
            ],
            vector_model=VECTOR_MODEL,
        ),
        schema_digests={s.__name__: digest(s.model_json_schema()) for s in schemas},
        loaded_modules={
            name: Path(inspect.getfile(sys.modules[name])).relative_to(source_root).as_posix()
            for name in loaded
        },
        runtime={
            name: importlib.metadata.version(name)
            for name in ("pydantic", "pydantic-settings", "openai", "pymupdf")
        },
        notes=notes,
    )
    after = knowledge_source_fingerprint(source_root)
    if before != after:
        raise ValueError("worker changed the source")
    (work_dir / "request.json").write_text(
        json.dumps(envelope, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (work_dir / "reply.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
