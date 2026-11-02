"""Scope inventory distinguishes callable calculations, external ownership and research backlog."""

import json
from importlib.resources import files
from typing import Literal

from .contracts import Contract, Name


class Capability(Contract):
    id: Name
    title_ko: Name
    category: Name
    state: Literal[
        "implemented", "available_external", "interface_verified", "partial", "planned", "procedure_required"
    ]
    owner: Name
    entrypoint: str | None = None
    evidence: tuple[str, ...] = ()
    limitations: tuple[str, ...]
    next_step: Name


def capabilities():
    document = (
        files("pipeline_integrity.catalog_data").joinpath("capabilities.json").read_text(encoding="utf-8")
    )
    rows = tuple(
        Capability.model_validate_json(json.dumps(row)) for row in json.loads(document)["capabilities"]
    )
    if len({row.id for row in rows}) != len(rows):
        raise ValueError("duplicate capability IDs")
    return rows
