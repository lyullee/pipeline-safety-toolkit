"""Dependency-light calls to the original SAGA hybrid database retriever.

Only reviewed caller-supplied passages enter a private new database. The
original database/text/vector APIs own indexing and retrieval. Native Citation
schemas carry a direct source projection without importing the application,
PDF indexer, external ingestors, or any network reasoning client.
"""
from __future__ import annotations

import importlib
import importlib.util
from importlib.metadata import version
import inspect
import json
import platform
import sqlite3
import sys
import tempfile
import threading
import tomllib
from pathlib import Path

from .contracts import RunContext, digest
from .datasets import file_digest
from .knowledge_exchange import (AUTHORITY, KnowledgeRequest, _issues, _json_budget,
                                  _validate_reply, knowledge_source_fingerprint)

_OWNER_LOCK = threading.RLock()
_LOADED_OWNER_FILES = {}
_MODULE_PATHS = {
    "saga": "python/saga/__init__.py",
    "saga.database": "python/saga/database.py",
    "saga.schemas": "python/saga/schemas.py",
    "saga.text": "python/saga/text.py",
    "saga.vector_index": "python/saga/vector_index.py",
}


def native_saga_library_metadata(source_root):
    """Inspect source/license bytes without starting SAGA or opening a corpus."""
    root = Path(source_root).resolve()
    pin = knowledge_source_fingerprint(root)
    license_text = (root / "LICENSE").read_text(encoding="utf-8")
    if "MIT License" not in license_text[:200]:
        raise ValueError("native SAGA source must retain the reviewed MIT owner license")
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    return dict(owner_source=pin, owner_project_name=project["name"], owner_project_version=project["version"],
                owner_license="MIT", owner_license_sha256=file_digest(root / "LICENSE"),
                selected_source_paths=dict(_MODULE_PATHS), sqlite_runtime=sqlite3.sqlite_version,
                runtime={"python": platform.python_version(), "pydantic": version("pydantic")},
                adapter_sha256=file_digest(Path(__file__)),
                exchange_sha256=file_digest(Path(__file__).with_name("knowledge_exchange.py")),
                retrieval_engine="original_saga.Database.hybrid_search",
                native_application_or_PDF_ingestor_import_required=False,
                reasoning_or_network_execution_supported=False)


def _load_owner(root):
    """Load only the owner package and its reviewed lightweight library modules."""
    for name, module in tuple(sys.modules.items()):
        if name == "saga" or name.startswith("saga."):
            if name not in _LOADED_OWNER_FILES:
                raise ValueError("preloaded SAGA modules have no source-at-load pin; use a fresh native library process")
            path = Path(inspect.getfile(module)).resolve()
            expected_path, expected_digest = _LOADED_OWNER_FILES[name]
            if path != expected_path or file_digest(path) != expected_digest:
                raise ValueError("SAGA source changed after library import; a fresh process is required")
    if "saga" not in sys.modules:
        path = root / _MODULE_PATHS["saga"]
        specification = importlib.util.spec_from_file_location("saga", path,
            submodule_search_locations=[str(path.parent)])
        if specification is None or specification.loader is None:
            raise ImportError("selected SAGA package cannot be loaded")
        module = importlib.util.module_from_spec(specification)
        sys.modules["saga"] = module
        try:
            specification.loader.exec_module(module)
        except BaseException:
            if sys.modules.get("saga") is module:
                del sys.modules["saga"]
            raise
    modules = {}
    for name, relative in _MODULE_PATHS.items():
        module = importlib.import_module(name)
        path = Path(inspect.getfile(module)).resolve()
        if path != (root / relative).resolve():
            raise ValueError("loaded native SAGA module differs from the selected owner source")
        modules[name] = module
    for name, module in tuple(sys.modules.items()):
        if name == "saga" or name.startswith("saga."):
            path = getattr(module, "__file__", None)
            if path is None or not Path(path).resolve().is_relative_to(root / "python" / "saga"):
                raise ValueError("SAGA process modules mix distinct source trees")
            resolved = Path(path).resolve()
            _LOADED_OWNER_FILES.setdefault(name, (resolved, file_digest(resolved)))
    return modules


class NativeSagaLibraryClient:
    """Actual offline native retrieval through the existing KnowledgeClient API.

    In-process execution relies on the caller's reviewed source and runtime;
    it is not a sandbox. Each call creates a private corpus outside both source
    projects. No existing SAGA database, corpus, application or credentials are
    read. The original owner does not yet expose a similarly light reasoning
    entry point, so this client accepts only the retrieve operation.
    """

    def __init__(self, *, source_root, work_root, offline_review_evidence_id,
                 maximum_database_bytes=128000000, maximum_reply_bytes=2000000):
        self.source_root = Path(source_root).resolve()
        self.work_root = Path(work_root).resolve()
        release_root = Path(__file__).resolve().parents[2]
        if self.work_root.is_relative_to(self.source_root) or self.work_root.is_relative_to(release_root):
            raise ValueError("native knowledge private corpus must stay outside both source distributions")
        if not isinstance(offline_review_evidence_id, str) or not offline_review_evidence_id.strip():
            raise ValueError("native offline source applicability review evidence is required")
        if (type(maximum_database_bytes) is not int or not 4096 <= maximum_database_bytes <= 1000000000
                or type(maximum_reply_bytes) is not int or not 4096 <= maximum_reply_bytes <= 2000000):
            raise ValueError("native knowledge database/reply byte budgets are invalid")
        self.offline_review_evidence_id = offline_review_evidence_id
        self.maximum_database_bytes = maximum_database_bytes
        self.maximum_reply_bytes = maximum_reply_bytes
        self.last_private_work_dir = None

    def _configuration_digest(self):
        return digest(dict(source_root=str(self.source_root), work_root=str(self.work_root),
                           offline_review_evidence_id=self.offline_review_evidence_id,
                           maximum_database_bytes=self.maximum_database_bytes,
                           maximum_reply_bytes=self.maximum_reply_bytes))

    def run(self, payload):
        _json_budget(payload)
        q = KnowledgeRequest.model_validate_json(json.dumps(payload["request"], allow_nan=False))
        c = RunContext.model_validate_json(json.dumps(payload["context"], allow_nan=False))
        if payload.get("request_digest") != digest(q) or payload.get("context_digest") != digest(c):
            raise ValueError("native knowledge request/context envelope changed")
        if _issues(q, c):
            raise ValueError("native knowledge sources, rights, applicability or context lack completed review")
        if q.method != "retrieve":
            raise ValueError("native dependency-light API supports actual retrieval only; reasoning needs an owner extension")
        evidence = [item for item in c.evidence if item.id == self.offline_review_evidence_id]
        if (len(evidence) != 1 or evidence[0].sha256 is None or evidence[0].origin == "synthetic"
                or self.offline_review_evidence_id not in q.evidence_ids):
            raise ValueError("native library offline review must select source-pinned actual context evidence")
        input_digest = digest(payload)
        configuration_digest = self._configuration_digest()
        before = native_saga_library_metadata(self.source_root)
        if before["owner_source"]["source_sha256"] != q.owner_source_sha256:
            raise ValueError("native SAGA source differs from the exact reviewed owner pin")
        with _OWNER_LOCK:
            previous_bytecode_policy = sys.dont_write_bytecode
            sys.dont_write_bytecode = True
            try:
                modules = _load_owner(self.source_root)
                result = self._retrieve(q, c, modules, before)
            finally:
                sys.dont_write_bytecode = previous_bytecode_policy
        after = native_saga_library_metadata(self.source_root)
        if (before != after or digest(payload) != input_digest
                or self._configuration_digest() != configuration_digest):
            raise ValueError("native knowledge owner/adapter source, runtime or caller input changed")
        result["source_pin"] = dict(before=before["owner_source"], after=after["owner_source"], unchanged=True)
        _json_budget(result)
        _validate_reply(result, q, c)
        if len(json.dumps(result, ensure_ascii=False, allow_nan=False).encode("utf-8")) > self.maximum_reply_bytes:
            raise ValueError("native knowledge reply exceeds byte budget")
        return result

    def _retrieve(self, q, c, modules, metadata):
        self.work_root.mkdir(parents=True, exist_ok=True)
        private_dir = Path(tempfile.mkdtemp(prefix="native-saga-", dir=self.work_root)).resolve()
        if not private_dir.is_relative_to(self.work_root):
            raise ValueError("native knowledge private corpus resolved outside the selected scratch directory")
        self.last_private_work_dir = private_dir
        database = modules["saga.database"].Database(private_dir / "saga.sqlite3")
        database.initialize()
        chunk_sources = {}
        inserted_documents = []
        for source in q.sources:
            if source.source_id not in q.allowed_source_ids:
                continue
            if q.document_codes and source.document_code not in q.document_codes:
                continue
            document = dict(doc_type="RULE", doc_code=source.document_code, title=source.title,
                filename=source.source_id + ".txt", file_path=source.source_uri,
                file_hash=source.content_sha256, page_count=max(p.page for p in source.passages),
                source_url=source.source_uri)
            chunks = [dict(hierarchy=passage.hierarchy, page=passage.page, content=passage.text,
                           search_text=modules["saga.text"].build_search_text(passage.text))
                      for passage in source.passages]
            document_id = database.replace_document(document, chunks)
            with database.connect() as connection:
                rows = connection.execute("SELECT id FROM chunks WHERE document_id=? ORDER BY id", (document_id,)).fetchall()
            if len(rows) != len(source.passages):
                raise ValueError("native SAGA changed the reviewed passage index cardinality")
            for row, passage in zip(rows, source.passages, strict=True):
                chunk_sources[int(row["id"])] = (source, passage)
            inserted_documents.append(dict(source_id=source.source_id, document_id=int(document_id),
                                           passage_count=len(rows), content_sha256=source.content_sha256))
            stored_bytes = sum(path.stat().st_size for path in private_dir.iterdir() if path.is_file())
            if stored_bytes > self.maximum_database_bytes:
                raise ValueError("native knowledge private database exceeds storage byte budget")
        native_request = dict(query=q.question, limit=q.retrieval_limit,
                              doc_codes=list(q.document_codes) or None, domain="CROSS", source_types=["RULE"])
        rows = database.hybrid_search(**native_request)
        if not isinstance(rows, list) or len(rows) > q.retrieval_limit:
            raise ValueError("native hybrid retriever exceeded its bounded result contract")
        citations = []
        for number, row in enumerate(rows, 1):
            source, passage = chunk_sources[int(row["chunk_id"])]
            if (row["content"] != passage.text or row["page"] != passage.page
                    or row["hierarchy"] != passage.hierarchy or row["doc_code"] != source.document_code):
                raise ValueError("native retriever changed passage text or source coordinates")
            # This is schema projection of an actual retrieved row. The owner
            # database selected/ranked it; this adapter performs no reranking.
            native = modules["saga.schemas"].Citation(number=number, document_id=row["document_id"],
                chunk_id=row["chunk_id"], doc_type=row["doc_type"], doc_code=row["doc_code"],
                title=row["title"], hierarchy=row["hierarchy"], page=row["page"], filename=row["filename"],
                excerpt=row["content"], score=row["score"], source_url=row.get("source_url") or None)
            citations.append(native.model_dump(mode="json") | dict(source_id=source.source_id,
                source_version=source.source_version, source_sha256=source.content_sha256,
                license_id=source.license_id, passage_id=passage.passage_id, locator=passage.locator))
        schema_names = ("Citation", "ChatRequest", "AnswerReview", "GroundedClaims", "HazopRuleInput")
        return dict(schema_version="saga.offline_exchange.v1", request_digest=digest(q), context_digest=digest(c),
            execution_profile="native_dependency_light_retrieval", status="computed" if citations else "needs_data",
            authority=dict(AUTHORITY), network_calls=0, reasoning_executed=False, answer=None, claims=[],
            reasoning_service=None, answer_review=None, citations=citations,
            rule_candidates=[item.model_dump(mode="json") for item in q.candidates], candidate_audit=None,
            threshold_proposal=None, native_request=native_request,
            owner_version=dict(package=modules["saga"].__version__, project=metadata["owner_project_version"],
                               vector_model=modules["saga.vector_index"].VECTOR_MODEL),
            schema_digests={name: digest(getattr(modules["saga.schemas"], name).model_json_schema()) for name in schema_names},
            loaded_modules=dict(_MODULE_PATHS), native_calls=["Database.initialize", "Database.replace_document",
                "text.build_search_text", "Database.hybrid_search", "schemas.Citation"],
            library_binding=dict(source_metadata=metadata, offline_review_evidence_id=self.offline_review_evidence_id,
                execution_isolation="caller_reviewed_in_process", original_application_or_PDF_ingestor_started=False,
                citation_projection="entire_exact_reviewed_passage_in_original_owner_Citation_schema"),
            indexed_sources=inserted_documents,
            notes=["Original SAGA database retrieval executed on the reviewed private corpus; no reasoning provider executed."])
