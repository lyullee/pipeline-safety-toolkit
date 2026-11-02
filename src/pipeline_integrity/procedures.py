"""Evidence completion against caller-owned field procedures, independent of physical acceptance."""

import json
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .contracts import Contract, EvidenceRef, Name, RunContext, digest
from .datasets import file_digest


class ProcedureItem(Contract):
    id: Name
    task: Name
    criterion_source: Name
    required_evidence_ids: tuple[Name, ...] = Field(min_length=1)
    prerequisites: tuple[Name, ...] = ()


class Procedure(Contract):
    id: Name
    version: Name
    applicable_asset_classes: tuple[Name, ...] = Field(min_length=1)
    items: tuple[ProcedureItem, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def dependencies(self):
        known, visited = {item.id for item in self.items}, set()
        if len(known) != len(self.items):
            raise ValueError("procedure item IDs must be unique")
        remaining = list(self.items)
        while remaining:
            ready = [item for item in remaining if set(item.prerequisites) <= visited]
            if not ready:
                raise ValueError("procedure has a cycle or an unknown prerequisite")
            for item in ready:
                visited.add(item.id)
                remaining.remove(item)
        return self


class ProcedureRecord(Contract):
    item_id: Name
    result: Literal["satisfied", "not_satisfied", "not_observed"]
    evidence: tuple[EvidenceRef, ...] = ()
    reviewer_id: Name
    reviewed_at: datetime
    note: str = ""

    @field_validator("reviewed_at", mode="before")
    @classmethod
    def reviewed_time_has_timezone(cls, value):
        # This public timestamp accepts ISO 8601 text as well as typed datetimes.
        if isinstance(value, str):
            value = datetime.fromisoformat(value)
        if not isinstance(value, datetime) or value.utcoffset() is None:
            raise ValueError("review time must include an explicit UTC offset")
        return value


def assess_procedure(
    procedure: Procedure, records: tuple[ProcedureRecord, ...], context: RunContext, *, asset_class: str
):
    procedure = Procedure.model_validate(procedure.model_dump(mode="python"))
    context = RunContext.model_validate(context.model_dump(mode="python"))
    records = tuple(ProcedureRecord.model_validate(r.model_dump(mode="python")) for r in records)
    evidence = {}
    for group in (context.evidence, *(r.evidence for r in records)):
        ids = [entry.id for entry in group]
        if len(ids) != len(set(ids)):
            raise ValueError("evidence IDs must be unique within each record/context")
        for entry in group:
            identity = digest(entry)
            if entry.id in evidence and evidence[entry.id] != identity:
                raise ValueError("the same evidence ID has conflicting identity across records/context")
            evidence[entry.id] = identity
    if asset_class not in procedure.applicable_asset_classes:
        raise ValueError("procedure does not declare applicability to this asset class")
    known = {item.id for item in procedure.items}
    if len({row.item_id for row in records}) != len(records) or {r.item_id for r in records} - known:
        raise ValueError("provide unique records for declared procedure items")
    lookup, outcomes = {row.item_id: row for row in records}, {}
    pending = list(procedure.items)
    while pending:
        item = next(item for item in pending if set(item.prerequisites) <= set(outcomes))
        record = lookup.get(item.id)
        missing = set(item.required_evidence_ids) - ({e.id for e in record.evidence} if record else set())
        blocked = [key for key in item.prerequisites if outcomes[key]["state"] != "recorded_satisfied"]
        readiness = (
            "blocked"
            if blocked
            else "needs_evidence"
            if record is None or missing
            else "ready_for_recorded_result"
        )
        # Negative/unobserved declarations are never hidden behind missing evidence or prerequisites.
        state = (
            record.result
            if record is not None and record.result != "satisfied"
            else ("recorded_satisfied" if readiness == "ready_for_recorded_result" else readiness)
        )
        outcomes[item.id] = {
            "state": state,
            "readiness": readiness,
            "recorded_result": record.result if record is not None else None,
            "missing_evidence_ids": sorted(missing),
            "blocked_by": blocked,
            "record_digest": digest(record) if record else None,
        }
        pending.remove(item)
    body = {
        "schema": "pipeline_integrity.procedure_result.v1",
        "procedure_id": procedure.id,
        "procedure": procedure.model_dump(mode="json"),
        "records": [r.model_dump(mode="json") for r in records],
        "asset_class": asset_class,
        "source_sha256": file_digest(Path(__file__)),
        "dependency_sha256": {
            name: file_digest(Path(__file__).with_name(name + ".py")) for name in ("contracts", "datasets")
        },
        "procedure_digest": digest(procedure),
        "context": context.model_dump(mode="json"),
        "items": outcomes,
        "all_records_satisfied": all(r["state"] == "recorded_satisfied" for r in outcomes.values()),
        "any_recorded_not_satisfied": any(r.result == "not_satisfied" for r in records),
        "independent_physical_or_regulatory_acceptance": False,
    }

    return dict(body, result_digest=digest(body))


def _records_json(records):
    """Validate records and compare their serialized provenance, including UTC offsets."""
    normalized = []
    for record in records:
        payload = record.model_dump(mode="python") if isinstance(record, ProcedureRecord) else record
        normalized.append(ProcedureRecord.model_validate(payload).model_dump(mode="json"))
    return normalized


def verify_procedure_result(result, *, procedure=None, records=None, context=None):
    """Compare evidence and current deterministic replay; unsigned hashes are not authentication."""
    if not isinstance(result, dict) or result.get("result_digest") != digest(
        {k: v for k, v in result.items() if k != "result_digest"}
    ):
        raise ValueError("procedure result seal changed")
    q = Procedure.model_validate_json(json.dumps(result["procedure"]))
    c = RunContext.model_validate_json(json.dumps(result["context"]))
    rows = tuple(ProcedureRecord.model_validate_json(json.dumps(r)) for r in result["records"])
    if procedure is not None and procedure != q or context is not None and context != c:
        raise ValueError("caller procedure/context differs")
    if records is not None and _records_json(records) != [row.model_dump(mode="json") for row in rows]:
        raise ValueError("caller records differ")
    if assess_procedure(q, rows, c, asset_class=result["asset_class"]) != result:
        raise ValueError("procedure evidence, decisions or source differ on replay")
    return True
