"""Versioned advisory exchange with an independently installed knowledge owner.

This module validates permissions and provenance. Retrieval, ranking, citation
construction and HAZOP readiness remain the responsibility of SAGA.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import re
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol

from pydantic import Field, model_validator

from .contracts import Contract, Name, RunContext, digest, json_value

Method = Literal["retrieve", "candidate_review", "threshold_proposal", "grounded_answer"]
METHODS = ("retrieve", "candidate_review", "threshold_proposal", "grounded_answer")
REVIEWS = (
    "source_rights_and_permitted_use",
    "current_version_and_period",
    "asset_clock_and_domain_applicability",
    "source_locator_and_fidelity",
)
SCHEMA = "pipeline_integrity.knowledge_exchange.v1"
AUTHORITY = {
    "purpose": "advisory",
    "requires_human_review": True,
    "approved_rule": False,
    "control_command": False,
    "legal_determination": False,
}


def _json_budget(value, *, maximum_nodes=30000, maximum_chars=2000000):
    stack, nodes, chars = [(value, 0)], 0, 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > maximum_nodes or depth > 24:
            raise ValueError("knowledge JSON node or nesting budget exceeded")
        if isinstance(item, str):
            chars += len(item)
            if chars > maximum_chars:
                raise ValueError("knowledge JSON character budget exceeded")
        elif isinstance(item, (dict, tuple, list)):
            if len(item) > maximum_nodes - nodes:
                raise ValueError("knowledge JSON container budget exceeded")
            values = [*item.keys(), *item.values()] if isinstance(item, dict) else item
            stack.extend((v, depth + 1) for v in values)


class KnowledgePassage(Contract):
    passage_id: Name
    page: int = Field(ge=1)
    hierarchy: Name
    locator: Name
    text: str = Field(min_length=1, max_length=12000)


class KnowledgeSource(Contract):
    source_id: Name
    source_version: Name | None
    document_code: Name
    title: Name
    source_uri: Name
    license_id: Name | None
    rights_statement: Name | None
    asset_id: Name
    domain_id: Name
    origin: Literal["synthetic", "literature", "user_declared"]
    permitted_operations: tuple[Method, ...] = ()
    valid_from_utc: datetime | None
    valid_until_utc: datetime | None
    passages: tuple[KnowledgePassage, ...] = Field(min_length=1, max_length=100)
    content_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def identity(self):
        for value in (self.valid_from_utc, self.valid_until_utc):
            if value is not None and value.utcoffset() is None:
                raise ValueError("source validity requires timezone-aware instants")
        if self.valid_from_utc and self.valid_until_utc and self.valid_until_utc <= self.valid_from_utc:
            raise ValueError("source validity interval must have positive duration")
        if len({p.passage_id for p in self.passages}) != len(self.passages):
            raise ValueError("passage identifiers must be unique within a source")
        if self.content_sha256 is not None and self.content_sha256 != digest(self.passages):
            raise ValueError("source passages differ from their declared digest")
        return self


class KnowledgeCandidate(Contract):
    candidate_id: Name
    source_id: Name
    passage_id: Name
    exact_quote: Name
    tag_id: Name
    variable: Name
    threshold_type: Literal["STATIC", "DELTA_START", "DELTA_PREV"] = "STATIC"
    threshold_value: float | None
    unit: str = Field(max_length=30)
    compare_dir: str = Field(max_length=30)
    rationale: str = Field(max_length=2000)


class KnowledgeRequest(Contract):
    schema_version: Literal["pipeline_integrity.knowledge_request.v1"] = (
        "pipeline_integrity.knowledge_request.v1"
    )
    method: Method
    asset_id: Name
    location_id: Name
    clock_id: Name
    domain_id: Name
    as_of_utc: datetime
    question: str = Field(min_length=1, max_length=2000)
    sources: tuple[KnowledgeSource, ...] = Field(default=(), max_length=20)
    allowed_source_ids: tuple[Name, ...] = ()
    document_codes: tuple[Name, ...] = Field(default=(), max_length=20)
    candidates: tuple[KnowledgeCandidate, ...] = Field(default=(), max_length=20)
    applicability_reviews: dict[str, bool] = Field(default_factory=dict)
    review_evidence_ids: dict[str, Name] = Field(default_factory=dict)
    evidence_ids: tuple[Name, ...] = Field(min_length=1, max_length=100)
    retrieval_limit: int = Field(default=5, ge=1, le=20)
    maximum_corpus_chars: int = Field(default=100000, ge=1000, le=250000)
    maximum_passages: int = Field(default=100, ge=1, le=500)
    owner_source_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def bounded(self):
        if not self.question.strip():
            raise ValueError("knowledge question cannot be blank")
        if self.as_of_utc.utcoffset() is None:
            raise ValueError("knowledge applicability requires a timezone-aware as-of instant")
        if set(self.applicability_reviews) - set(REVIEWS) or set(self.review_evidence_ids) - set(REVIEWS):
            raise ValueError("unknown knowledge applicability review")
        for values in (self.allowed_source_ids, self.evidence_ids, self.document_codes):
            if len(values) != len(set(values)):
                raise ValueError("knowledge identifiers must be unique")
        if len({s.source_id for s in self.sources}) != len(self.sources):
            raise ValueError("source identifiers must be unique")
        if len({s.document_code for s in self.sources}) != len(self.sources):
            raise ValueError("one document code must identify one reviewed source version")
        passages = [p for s in self.sources for p in s.passages]
        if len(passages) > self.maximum_passages:
            raise ValueError("knowledge passage budget exceeded; no truncation")
        if sum(len(p.text) for p in passages) > self.maximum_corpus_chars:
            raise ValueError("knowledge corpus budget exceeded; no truncation")
        if len({c.candidate_id for c in self.candidates}) != len(self.candidates):
            raise ValueError("candidate identifiers must be unique")
        if set(self.allowed_source_ids) - {s.source_id for s in self.sources}:
            raise ValueError("allowlist identifies an absent source")
        if set(self.document_codes) - {s.document_code for s in self.sources}:
            raise ValueError("document filter identifies an absent source")
        by_source = {s.source_id: s for s in self.sources}
        for candidate in self.candidates:
            source = by_source.get(candidate.source_id)
            passage = (
                next((p for p in source.passages if p.passage_id == candidate.passage_id), None)
                if source
                else None
            )
            if passage is None or candidate.exact_quote not in passage.text:
                raise ValueError("candidate quote must retain its exact reviewed source locator")
        return self


def knowledge_source_fingerprint(root):
    """Hash original Python and resource bytes without touching its database."""
    root = Path(root).resolve()
    paths = sorted((root / "python" / "saga").rglob("*.py"))
    if not paths:
        raise ValueError("SAGA checkout lacks required source Python files")
    paths += [
        root / name
        for name in ("pyproject.toml", "LICENSE", "CITATION.cff", "README.md", "term_aliases.json")
    ]
    if not paths or any(not p.is_file() for p in paths):
        raise ValueError("SAGA checkout lacks required source, license or alias resource")
    h = hashlib.sha256()
    resources = {}
    for path in sorted(paths):
        relative = path.relative_to(root).as_posix()
        contents = path.read_bytes()
        h.update(relative.encode("utf-8"))
        h.update(contents)
        resources[relative] = hashlib.sha256(contents).hexdigest()
    return {"source_sha256": h.hexdigest(), "files": resources}


class KnowledgeClient(Protocol):
    """Provider replacements return the same bounded, provenance-bearing JSON.

    A client controls its own service authorization. This exchange never grants
    permission for network calls, messages, rule activation or physical actions.
    """

    def run(self, payload: dict) -> dict: ...


class SagaOfflineClient:
    def __init__(self, *, source_root, python_executable, work_root, timeout_s=30):
        self.source_root = Path(source_root).resolve()
        self.python_executable = Path(python_executable).resolve()
        self.work_root = Path(work_root).resolve()
        if self.work_root.is_relative_to(self.source_root):
            raise ValueError("knowledge scratch storage must be outside the source project")
        if type(timeout_s) not in (int, float) or not 1 <= timeout_s <= 60:
            raise ValueError("knowledge worker timeout must be finite and in [1,60] seconds")
        self.timeout_s = timeout_s

    def run(self, payload):
        expected = payload["request"]["owner_source_sha256"]
        before = knowledge_source_fingerprint(self.source_root)
        if expected is None or before["source_sha256"] != expected:
            raise ValueError("knowledge owner source differs from the reviewed pin")
        self.work_root.mkdir(parents=True, exist_ok=True)
        private_dir = Path(tempfile.mkdtemp(prefix="saga-", dir=self.work_root))
        environment = {
            k: v
            for k, v in os.environ.items()
            if not any(
                token in k.upper() for token in ("KEY", "TOKEN", "SECRET", "PASSWORD", "SAGA_", "GROQ_")
            )
        }
        environment.update(PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
        envelope = dict(payload, source_root=str(self.source_root), work_dir=str(private_dir))
        try:
            process = subprocess.run(
                [
                    str(self.python_executable),
                    "-I",
                    "-X",
                    "utf8",
                    str(Path(__file__).with_name("knowledge_worker.py")),
                ],
                input=json.dumps(envelope, ensure_ascii=False, allow_nan=False).encode("utf-8"),
                capture_output=True,
                timeout=self.timeout_s,
                env=environment,
                cwd=private_dir,
                check=False,
            )
        finally:
            after = knowledge_source_fingerprint(self.source_root)
            if before != after:
                raise ValueError("knowledge source changed during execution")
        (private_dir / "stderr.log").write_bytes(process.stderr)
        if process.returncode:
            raise RuntimeError(
                f"SAGA offline worker failed with code {process.returncode}; inspect private logs"
            )
        if len(process.stdout) > 2000000:
            raise ValueError("knowledge response size budget exceeded")
        result = json.loads(process.stdout.decode("utf-8"))
        result["source_pin"] = {"before": before, "after": after, "unchanged": True}
        return result


class SagaLibraryClient:
    """Injected owner-library API with pinned source, schemas and offline review.

    The callable must implement the existing bounded offline reply contract.
    This path does not import the owner's UI or database bootstrap and does not
    implement retrieval itself. In-process execution is not an isolation
    boundary; the caller must review the supplied offline entry point.
    """

    def __init__(self, *, api_function, source_root, owner_version: dict,
                 schema_digests: dict, offline_review_evidence_id: str):
        if not inspect.isfunction(api_function) or inspect.iscoroutinefunction(api_function) or inspect.isgeneratorfunction(api_function):
            raise TypeError("an explicit owner-library function is required")
        self.source_root=Path(source_root).resolve()
        source=inspect.getsourcefile(api_function)
        if source is None or not Path(source).resolve().is_relative_to(self.source_root):
            raise ValueError("knowledge library function must belong to the selected owner source tree")
        self.api_function=api_function
        self.api_source=Path(source).resolve()
        self.api_name=api_function.__module__+"."+api_function.__qualname__
        self.owner_version=json_value(owner_version)
        self.schema_digests=json_value(schema_digests)
        if any(not isinstance(self.owner_version.get(k),str) or not self.owner_version[k].strip()
               for k in ("package","project","vector_model")):
            raise ValueError("reviewed knowledge owner versions are required")
        if not schema_digests or any(not isinstance(v,str) or re.fullmatch(r"[a-f0-9]{64}",v) is None
                                     for v in self.schema_digests.values()):
            raise ValueError("reviewed knowledge owner schema hashes are required")
        if not isinstance(offline_review_evidence_id,str) or not offline_review_evidence_id.strip():
            raise ValueError("offline library applicability review evidence is required")
        self.offline_review_evidence_id=offline_review_evidence_id

    def run(self, payload):
        _json_budget(payload)
        request=KnowledgeRequest.model_validate_json(json.dumps(payload["request"],allow_nan=False))
        context=RunContext.model_validate_json(json.dumps(payload["context"],allow_nan=False))
        if payload.get("request_digest")!=digest(request) or payload.get("context_digest")!=digest(context):
            raise ValueError("knowledge library envelope binding differs")
        reviews=[e for e in context.evidence if e.id==self.offline_review_evidence_id]
        if len(reviews)!=1 or reviews[0].sha256 is None or reviews[0].origin=="synthetic":
            raise ValueError("knowledge offline library review must be context-bound and source-hash pinned")
        if self.offline_review_evidence_id not in request.evidence_ids:
            raise ValueError("knowledge offline library review must be selected by the request")
        before=knowledge_source_fingerprint(self.source_root)
        if before["source_sha256"]!=request.owner_source_sha256:
            raise ValueError("knowledge library source differs from the reviewed owner pin")
        api_before=hashlib.sha256(self.api_source.read_bytes()).hexdigest()
        isolated=json.loads(json.dumps(json_value(payload),allow_nan=False))
        input_digest=digest(isolated)
        try:
            response=self.api_function(isolated)
        finally:
            after=knowledge_source_fingerprint(self.source_root)
            if before!=after or hashlib.sha256(self.api_source.read_bytes()).hexdigest()!=api_before:
                raise ValueError("knowledge library source changed during execution")
        if digest(isolated)!=input_digest:
            raise ValueError("knowledge library mutated its input envelope")
        _json_budget(response)
        result=json_value(response)
        if not isinstance(result,dict):
            raise ValueError("knowledge library must return a structured owner receipt")
        if result.get("owner_version")!=self.owner_version or result.get("schema_digests")!=self.schema_digests:
            raise ValueError("knowledge library owner version or schema differs from the reviewed binding")
        result["source_pin"]={"before":before,"after":after,"unchanged":True}
        _validate_reply(result,request,context)
        result["library_binding"]={"api":self.api_name,"api_source_sha256":api_before,
                                   "offline_review_evidence_id":self.offline_review_evidence_id,
                                   "execution_isolation":"caller_reviewed_in_process"}
        return result


def _issues(request, context):
    if (request.asset_id, request.clock_id) != (context.asset_id, context.clock_id):
        raise ValueError("knowledge asset or clock differs from context")
    evidence = {e.id: e for e in context.evidence}
    if len(evidence) != len(context.evidence) or not set(request.evidence_ids) <= set(evidence):
        raise ValueError("knowledge evidence must be unique and bound to context")
    if not set(request.review_evidence_ids.values()) <= set(request.evidence_ids):
        raise ValueError("knowledge review evidence must belong to the selected evidence")
    issues = [
        f"review and evidence required: {r}"
        for r in REVIEWS
        if not request.applicability_reviews.get(r, False) or r not in request.review_evidence_ids
    ]
    if not request.sources or not request.allowed_source_ids:
        issues.append("reviewed source corpus and explicit allowlist required")
    if request.owner_source_sha256 is None:
        issues.append("reviewed owner source version pin required")
    for source in request.sources:
        if source.source_id not in request.allowed_source_ids:
            issues.append(f"source is not allowed: {source.source_id}")
        if (source.asset_id, source.domain_id) != (request.asset_id, request.domain_id):
            issues.append(f"source asset or domain unresolved: {source.source_id}")
        if any(
            v is None
            for v in (
                source.source_version,
                source.license_id,
                source.rights_statement,
                source.content_sha256,
                source.valid_from_utc,
                source.valid_until_utc,
            )
        ):
            issues.append(f"source version, rights, digest or validity unresolved: {source.source_id}")
        elif not source.valid_from_utc <= request.as_of_utc < source.valid_until_utc:
            issues.append(f"source is outside the declared validity period: {source.source_id}")
        if request.method not in source.permitted_operations:
            issues.append(f"source does not permit requested operation: {source.source_id}")
    if request.method == "candidate_review" and not request.candidates:
        issues.append("candidate review requires explicitly sourced candidates")
    return issues


def _candidate_rule_projection(request, context):
    """Fields deliberately supplied to the owner, before its schema adds defaults."""
    return [
        {
            "no": index,
            "scenario_id": context.scenario_id,
            "tag_id": candidate.tag_id,
            "item_name": candidate.variable,
            "unit": candidate.unit,
            "threshold_type": candidate.threshold_type,
            "threshold_value": candidate.threshold_value,
            "compare_dir": candidate.compare_dir,
            "threshold_basis": candidate.rationale,
            "threshold_source": f"{candidate.source_id}:{candidate.passage_id}",
            "threshold_confidence": "pending",
            "source": "pipeline-private-candidate-audit",
        }
        for index, candidate in enumerate(request.candidates, 1)
    ]


def _threshold_request_projection(request, context):
    return {
        "scenario_id": context.scenario_id,
        "tag_id": "knowledge-review",
        "equipment_name": request.asset_id,
        "context": request.question,
        "variable": "",
        "unit": "",
        "model": None,
    }


def _validate_native_projection(actual, expected, label):
    # Additional owner-schema defaults are retained. Supplied fields must remain
    # present and byte-equivalent in canonical JSON, including null and bool types.
    if (
        not isinstance(actual, dict)
        or not expected.keys() <= actual.keys()
        or digest({key: actual[key] for key in expected}) != digest(expected)
    ):
        raise ValueError(f"knowledge {label} changed caller fields or context")


def _validate_reply(reply, request, context):
    _json_budget(reply)
    json_value(reply)
    if not isinstance(reply, dict):
        raise ValueError("knowledge owner reply must be a JSON object")
    if reply.get("schema_version") != "saga.offline_exchange.v1":
        raise ValueError("unsupported knowledge owner response schema")
    if reply.get("request_digest") != digest(request) or reply.get("context_digest") != digest(context):
        raise ValueError("knowledge owner response is bound to different inputs or context")
    if (
        reply.get("authority") != AUTHORITY
        or type(reply.get("network_calls")) is not int
        or reply["network_calls"] != 0
    ):
        raise ValueError("knowledge response exceeds advisory offline authority")
    if reply.get("status") not in ("computed", "needs_data"):
        raise ValueError("invalid knowledge owner status")
    if type(reply.get("reasoning_executed")) is not bool:
        raise ValueError("knowledge response must distinguish retrieval from reasoning")
    if not isinstance(reply.get("owner_version"), dict) or not isinstance(reply.get("schema_digests"), dict):
        raise ValueError("knowledge owner version and schema fingerprints required")
    if any(
        not isinstance(reply["owner_version"].get(k), str) or not reply["owner_version"][k].strip()
        for k in ("package", "project", "vector_model")
    ):
        raise ValueError("knowledge owner version identifiers are unresolved")
    if not {"Citation", "ChatRequest", "AnswerReview", "GroundedClaims", "HazopRuleInput"} <= set(
        reply["schema_digests"]
    ) or any(
        not isinstance(v, str) or re.fullmatch(r"[a-f0-9]{64}", v) is None
        for v in reply["schema_digests"].values()
    ):
        raise ValueError("knowledge native schemas are unresolved or malformed")
    if reply["reasoning_executed"] is False and reply.get("answer") is not None:
        raise ValueError("knowledge answer cannot claim unexecuted reasoning")
    source_by_code = {s.document_code: s for s in request.sources}
    citations = reply.get("citations")
    if not isinstance(citations, list) or len(citations) > request.retrieval_limit:
        raise ValueError("invalid or oversized knowledge citation list")
    for index, citation in enumerate(citations, 1):
        if not isinstance(citation, dict) or not isinstance(citation.get("excerpt"), str):
            raise ValueError("knowledge citation must retain a string excerpt")
        source = source_by_code.get(citation.get("doc_code"))
        if (
            source is None
            or citation.get("source_id") != source.source_id
            or citation.get("source_version") != source.source_version
            or citation.get("source_sha256") != source.content_sha256
            or citation.get("license_id") != source.license_id
        ):
            raise ValueError("knowledge citation changed source, version, digest or rights")
        passage = next((p for p in source.passages if p.passage_id == citation.get("passage_id")), None)
        if (
            passage is None
            or type(citation.get("page")) is not int
            or citation["page"] != passage.page
            or citation.get("locator") != passage.locator
            or citation.get("hierarchy") != passage.hierarchy
            or citation.get("excerpt") not in passage.text
        ):
            raise ValueError("knowledge citation locator or source excerpt changed")
        if not citation.get("excerpt") or type(citation.get("score")) not in (int, float):
            raise ValueError("knowledge citation requires an excerpt and numeric retrieval score")
        if (
            type(citation.get("number")) is not int
            or citation["number"] != index
            or citation.get("title") != source.title
            or citation.get("source_url") != source.source_uri
            or citation.get("filename") != source.source_id + ".txt"
            or citation.get("doc_type") != "RULE"
            or any(type(citation.get(k)) is not int or citation[k] <= 0 for k in ("document_id", "chunk_id"))
        ):
            raise ValueError("knowledge native citation identity changed")
    if request.document_codes and any(c["doc_code"] not in request.document_codes for c in citations):
        raise ValueError("knowledge reply violates the requested document scope")
    if request.method == "retrieve" and reply["status"] == "computed" and not citations:
        raise ValueError("computed retrieval must retain actual cited hits")
    if reply["reasoning_executed"] and not citations:
        raise ValueError("reasoned knowledge response lacks source citations")
    if request.method == "grounded_answer" and reply["status"] == "computed":
        if not reply["reasoning_executed"] or not reply.get("answer"):
            raise ValueError("retrieval cannot masquerade as a completed grounded answer")
        service = reply.get("reasoning_service")
        if not isinstance(service, dict) or not service.get("id") or not service.get("version"):
            raise ValueError("reasoned knowledge answer needs a named versioned reasoning service")
        review = reply.get("answer_review")
        if not isinstance(review, dict) or type(review.get("approved")) is not bool:
            raise ValueError(
                "reasoned knowledge answer needs its machine review, distinct from human approval"
            )
        claims = reply.get("claims")
        if not isinstance(claims, list) or not 1 <= len(claims) <= 20:
            raise ValueError("reasoned knowledge answer needs bounded source claims")
        for claim in claims:
            citation = next((c for c in citations if c["number"] == claim.get("citation_number")), None)
            if (
                citation is None
                or not claim.get("claim")
                or not claim.get("exact_quote")
                or claim["exact_quote"] not in citation["excerpt"]
            ):
                raise ValueError("reasoning claim has no exact retrieved citation binding")
    if reply.get("rule_candidates") != [c.model_dump(mode="json") for c in request.candidates]:
        raise ValueError("knowledge response changed caller-supplied pending candidates")
    if request.method == "candidate_review" and not isinstance(reply.get("candidate_audit"), dict):
        raise ValueError("knowledge candidate review lacks original owner audit")
    if request.method == "candidate_review" and (
        type(reply["candidate_audit"].get("total_rules")) is not int
        or reply["candidate_audit"]["total_rules"] != len(request.candidates)
        or reply["candidate_audit"].get("monitoring_ready") is not False
        or not isinstance(reply.get("native_request"), list)
        or len(reply["native_request"]) != len(request.candidates)
        or any(
            not isinstance(r, dict) or r.get("threshold_confidence") != "pending"
            for r in reply.get("native_request", [])
        )
    ):
        raise ValueError("knowledge pending candidates were promoted or omitted")
    if request.method == "candidate_review":
        for actual, expected in zip(
            reply["native_request"], _candidate_rule_projection(request, context), strict=True
        ):
            _validate_native_projection(actual, expected, "native candidate projection")
        audit = reply["candidate_audit"]
        if (
            audit.get("scenario_id") != context.scenario_id
            or type(audit.get("ready_rules")) is not int
            or audit["ready_rules"] != 0
            or type(audit.get("not_ready_rules")) is not int
            or audit["not_ready_rules"] != len(request.candidates)
        ):
            raise ValueError("knowledge pending candidate audit changed scenario or readiness counts")
    if request.method == "threshold_proposal":
        expected = _threshold_request_projection(request, context)
        _validate_native_projection(reply.get("native_request"), expected, "native threshold request")
        proposal = reply.get("threshold_proposal")
        if (
            not isinstance(proposal, dict)
            or proposal.get("scenario_id") != expected["scenario_id"]
            or proposal.get("tag_id") != expected["tag_id"]
            or proposal.get("requires_human_approval") is not True
            or type(proposal.get("ready")) is not bool
            or not isinstance(proposal.get("candidates"), list)
        ):
            raise ValueError("knowledge threshold proposal changed identity or approval authority")
        if reply["reasoning_executed"] is False and (
            reply["status"] != "needs_data" or proposal["ready"] is not False or proposal["candidates"]
        ):
            raise ValueError("knowledge unexecuted threshold reasoning cannot produce ready candidates")
    elif reply.get("threshold_proposal") is not None:
        raise ValueError("knowledge reply returned a threshold proposal for a different operation")
    if request.method == "grounded_answer":
        _validate_native_projection(
            reply.get("native_request"), {"message": request.question, "mode": "rag"}, "native answer request"
        )
    if request.method != "candidate_review" and reply.get("candidate_audit") is not None:
        raise ValueError("knowledge reply returned a candidate audit for a different operation")
    pin = reply.get("source_pin", {})
    if (
        not isinstance(pin, dict)
        or not isinstance(pin.get("before"), dict)
        or pin.get("unchanged") is not True
        or pin.get("before") != pin.get("after")
        or pin.get("before", {}).get("source_sha256") != request.owner_source_sha256
    ):
        raise ValueError("knowledge response lacks the unchanged reviewed owner pin")
    loaded = reply.get("loaded_modules", {})
    profile = reply.get("execution_profile", "full_owner_library")
    required_modules = {
        "saga": "python/saga/__init__.py",
        "saga.database": "python/saga/database.py",
        "saga.hazop": "python/saga/hazop.py",
        "saga.rag": "python/saga/rag.py",
        "saga.schemas": "python/saga/schemas.py",
    }
    if profile == "native_dependency_light_retrieval":
        if request.method != "retrieve" or reply["reasoning_executed"]:
            raise ValueError("dependency-light native retrieval cannot claim reasoning or HAZOP execution")
        if reply.get("native_request") != {
            "query": request.question, "limit": request.retrieval_limit,
            "doc_codes": list(request.document_codes) or None, "domain": "CROSS", "source_types": ["RULE"]
        }:
            raise ValueError("dependency-light native retrieval request changed")
        required_modules = {
            "saga": "python/saga/__init__.py",
            "saga.database": "python/saga/database.py",
            "saga.schemas": "python/saga/schemas.py",
            "saga.text": "python/saga/text.py",
            "saga.vector_index": "python/saga/vector_index.py",
        }
    elif profile != "full_owner_library":
        raise ValueError("unknown knowledge library execution profile")
    if not isinstance(loaded, dict) or any(
        loaded.get(name) != path
        for name, path in required_modules.items()
    ):
        raise ValueError("knowledge owner response lacks actual loaded library provenance")


def evaluate_knowledge(request, context, *, client: KnowledgeClient | None = None):
    _json_budget(request.model_dump() if isinstance(request, KnowledgeRequest) else request)
    _json_budget(context.model_dump() if isinstance(context, RunContext) else context)
    request = KnowledgeRequest.model_validate(
        request.model_dump() if isinstance(request, KnowledgeRequest) else request
    )
    context = RunContext.model_validate(context.model_dump() if isinstance(context, RunContext) else context)
    issues = _issues(request, context)
    reply = None
    if not issues and client is not None:
        payload = {
            "request": request.model_dump(mode="json"),
            "context": context.model_dump(mode="json"),
            "request_digest": digest(request),
            "context_digest": digest(context),
        }
        payload_before = digest(payload)
        reply = client.run(payload)
        if digest(payload) != payload_before:
            raise ValueError("knowledge client mutated the request payload")
        _validate_reply(reply, request, context)
    elif not issues:
        issues.append("knowledge client is not configured")
    result = {
        "schema_version": SCHEMA,
        "request": request.model_dump(mode="json"),
        "context": context.model_dump(mode="json"),
        "request_digest": digest(request),
        "context_digest": digest(context),
        "status": "needs_data" if reply is None else reply["status"],
        "authority": dict(AUTHORITY),
        "notes": issues,
        "native_receipt": reply,
    }
    result["receipt_digest"] = digest(result)
    return result


def verify_knowledge_record(record):
    if record.get("schema_version") != SCHEMA:
        raise ValueError("unsupported knowledge record schema")
    if record.get("receipt_digest") != digest({k: v for k, v in record.items() if k != "receipt_digest"}):
        raise ValueError("knowledge record digest changed")
    request = KnowledgeRequest.model_validate_json(json.dumps(record["request"], allow_nan=False))
    context = RunContext.model_validate_json(json.dumps(record["context"], allow_nan=False))
    if (record.get("request_digest"), record.get("context_digest")) != (
        digest(request),
        digest(context),
    ) or record.get("authority") != AUTHORITY:
        raise ValueError("knowledge record input or authority binding changed")
    issues = _issues(request, context)
    if record.get("native_receipt") is None:
        if record.get("status") != "needs_data" or not record.get("notes"):
            raise ValueError("unexecuted knowledge request cannot be computed")
    else:
        if issues:
            raise ValueError("unreviewed knowledge request claims an executed receipt")
        _validate_reply(record["native_receipt"], request, context)
        if record["status"] != record["native_receipt"]["status"]:
            raise ValueError("knowledge wrapper changed owner status")
    return True


def verify_knowledge_replay(record, *, client: KnowledgeClient | None = None):
    """Re-evaluate a sealed request and require exact receipt reproduction.

    This re-invokes the supplied client. Use only a caller-authorized deterministic,
    offline client; stochastic or externally changing providers may not reproduce.
    """
    verify_knowledge_record(record)
    if record["native_receipt"] is None and client is not None:
        raise ValueError("an unexecuted knowledge receipt must be replayed without a provider")
    if record["native_receipt"] is not None and client is None:
        raise ValueError("an executed knowledge receipt requires its caller-selected replay client")
    request = KnowledgeRequest.model_validate_json(json.dumps(record["request"], allow_nan=False))
    context = RunContext.model_validate_json(json.dumps(record["context"], allow_nan=False))
    replay = evaluate_knowledge(request, context, client=client)
    if replay["receipt_digest"] != record["receipt_digest"]:
        raise ValueError("knowledge replay did not reproduce the sealed receipt")
    return True


def verify_knowledge_adapter_record(record, *, request=None, context=None, client: KnowledgeClient | None = None):
    """Preserve the knowledge replay contract in the common adapter-job API."""
    verify_knowledge_record(record)
    retained=KnowledgeRequest.model_validate_json(json.dumps(record["request"],allow_nan=False))
    retained_context=RunContext.model_validate_json(json.dumps(record["context"],allow_nan=False))
    if request is not None and request.model_dump(mode="json")!=retained.model_dump(mode="json"):
        raise ValueError("knowledge adapter caller request differs")
    if context is not None and context.model_dump(mode="json")!=retained_context.model_dump(mode="json"):
        raise ValueError("knowledge adapter caller context differs")
    return verify_knowledge_replay(record,client=client)
