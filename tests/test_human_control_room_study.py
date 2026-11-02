"""Synthetic checks for caller-reviewed HRA outcome mapping; run on Nurion."""

from copy import deepcopy
import os
from pathlib import Path

import pytest

from pipeline_integrity.contracts import EvidenceRef, RunContext, digest
from pipeline_integrity.human_control_room_data import read_human_control_room_archive
from pipeline_integrity.human_control_room_study import (
    HumanControlRoomOutcomeMap,
    summarize_human_control_room_study,
)


def _archive_path():
    root = Path(os.environ.get("PIPELINE_HPC_RAW_ROOT", "data/raw"))
    return root / "human_control_room" / "dataset-v1.1.2.zip"


def _mapping():
    evidence = EvidenceRef(
        id="review",
        citation="reviewed simulator task definition",
        origin="user_declared",
        locator="controlled methodology record",
        sha256=digest("authored outcome mapping approval"),
        license_id="CC0-1.0",
    )
    context = RunContext(
        asset_id="control-room-simulator",
        scenario_id="all-scenarios",
        clock_id="unmapped-source-clock",
        origin="simulation",
        evidence=(evidence,),
    )
    mapping = HumanControlRoomOutcomeMap(
        evidence_id=evidence.id,
        evidence_digest=evidence.sha256,
        field_name="Consequence",
        successful_values=("Safe",),
        failed_values=("Reactor Overheated", "Plant Shutdown"),
        task_definition=evidence.citation,
        opportunity_definition="one participant-scenario simulator run",
        population_definition="source study participants only",
    )
    return mapping, context


def test_explicit_outcome_mapping_yields_private_aggregate_only():
    path = _archive_path()
    if not path.is_file():
        pytest.fail("reviewed raw source must be staged using the human-control-room data map")
    archive = read_human_control_room_archive(path)
    mapping, context = _mapping()
    result = summarize_human_control_room_study(archive, mapping, context)
    assert sum(item.mapped_trials + item.unmapped_trials for item in result.scenarios) == 139
    assert sum(item.unmapped_trials for item in result.scenarios) == 20
    assert result.participant_scenario_groups == sum(item.participant_count for item in result.scenarios)
    assert result.additional_rows_within_participant_scenario_groups == (
        sum(item.mapped_trials + item.unmapped_trials for item in result.scenarios)
        - result.participant_scenario_groups
    )
    assert sum(result.participant_scenario_group_status_counts.values()) == result.participant_scenario_groups
    assert all(
        sum(item.participant_scenario_group_status_counts.values()) == item.participant_scenario_groups
        for item in result.scenarios
    )
    assert result.participant_identifiers_included is False
    assert result.human_failure_probability_estimated is False
    serialized = str(result.model_dump(mode="json"))
    assert "P06" not in serialized
    assert "Reactor Overheated" not in serialized


def test_mapping_requires_pinned_review_evidence():
    path = _archive_path()
    if not path.is_file():
        pytest.fail("reviewed raw source must be staged using the human-control-room data map")
    archive = read_human_control_room_archive(path)
    mapping, context = _mapping()
    altered = context.model_copy(update={"evidence": ()})
    with pytest.raises(ValueError, match="mapping evidence"):
        summarize_human_control_room_study(archive, mapping, altered)


def test_conflicting_success_and_failure_labels_are_rejected():
    mapping, _ = _mapping()
    body = mapping.model_dump(mode="python")
    body["failed_values"] = body["failed_values"] + ("Safe",)
    with pytest.raises(ValueError, match="disjoint"):
        HumanControlRoomOutcomeMap.model_validate(body)


def test_summary_receipt_tampering_is_detected():
    path = _archive_path()
    if not path.is_file():
        pytest.fail("reviewed raw source must be staged using the human-control-room data map")
    archive = read_human_control_room_archive(path)
    mapping, context = _mapping()
    result = summarize_human_control_room_study(archive, mapping, context)
    body = deepcopy(result.model_dump(mode="python"))
    body["units_mapped"] = True
    changed = type(result).model_construct(**body)
    with pytest.raises(ValueError, match="integrity"):
        changed.check_integrity()

    inconsistent = deepcopy(result.model_dump(mode="python"))
    inconsistent["participant_scenario_group_status_counts"]["success_only"] += 1
    inconsistent["record_digest"] = digest(
        {key: value for key, value in inconsistent.items() if key != "record_digest"}
    )
    resealed = type(result).model_construct(**inconsistent)
    with pytest.raises(ValueError, match="do not reconcile"):
        resealed.check_integrity()
