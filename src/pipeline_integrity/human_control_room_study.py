"""Caller-reviewed process-control outcomes from a public human-factors archive.

The source workbook does not define pipeline task success, unit conventions or
an operational population. This module therefore summarizes only explicit,
reviewed mappings and never constructs HEP or FTA probabilities.
"""

from __future__ import annotations

from collections import Counter
from typing import Literal

from pydantic import Field, model_validator

from .contracts import Contract, Name, RunContext, digest
from .human_control_room_data import HumanControlRoomArchive


class HumanControlRoomOutcomeMap(Contract):
    """A reviewed interpretation of one simulator-log outcome field."""

    evidence_id: Name
    evidence_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    field_name: Literal["Consequence", "Overall Performance"]
    successful_values: tuple[Name, ...] = Field(min_length=1)
    failed_values: tuple[Name, ...] = Field(min_length=1)
    task_definition: Name
    opportunity_definition: Name
    population_definition: Name
    grouping_unit: Literal["participant_scenario"] = "participant_scenario"

    @model_validator(mode="after")
    def disjoint_labels(self):
        if set(self.successful_values) & set(self.failed_values):
            raise ValueError("success and failure outcome labels must be disjoint")
        return self


class HumanControlRoomScenarioSummary(Contract):
    scenario_id: Name
    participant_count: int = Field(ge=0)
    participant_scenario_groups: int = Field(ge=0)
    additional_rows_within_participant_scenario_groups: int = Field(ge=0)
    mapped_trials: int = Field(ge=0)
    unmapped_trials: int = Field(ge=0)
    successful_trials: int = Field(ge=0)
    failed_trials: int = Field(ge=0)
    participant_scenario_group_status_counts: dict[
        Literal["success_only", "failure_only", "mixed_success_failure", "contains_unmapped"], int
    ]

    @model_validator(mode="after")
    def grouped_counts_reconcile(self):
        expected = {"success_only", "failure_only", "mixed_success_failure", "contains_unmapped"}
        row_count = self.mapped_trials + self.unmapped_trials
        if (
            set(self.participant_scenario_group_status_counts) != expected
            or any(value < 0 for value in self.participant_scenario_group_status_counts.values())
            or self.participant_scenario_groups != self.participant_count
            or sum(self.participant_scenario_group_status_counts.values()) != self.participant_scenario_groups
            or self.additional_rows_within_participant_scenario_groups
            != row_count - self.participant_scenario_groups
            or self.mapped_trials != self.successful_trials + self.failed_trials
        ):
            raise ValueError("participant-scenario and row outcome counts do not reconcile")
        return self


class HumanControlRoomStudySummary(Contract):
    schema_version: Literal["pipeline_integrity.human_control_room_study.v2"] = (
        "pipeline_integrity.human_control_room_study.v2"
    )
    dataset_id: Literal["human_control_room"] = "human_control_room"
    source_archive_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_workbook_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    mapping_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    mapping_evidence_id: Name
    mapping_evidence_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    mapped_field: Literal["Consequence", "Overall Performance"]
    task_definition: Name
    opportunity_definition: Name
    population_definition: Name
    context_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    scenarios: tuple[HumanControlRoomScenarioSummary, ...] = Field(min_length=1)
    participant_scenario_groups: int = Field(ge=0)
    additional_rows_within_participant_scenario_groups: int = Field(ge=0)
    outcome_status_counts: dict[Literal["success", "failure", "unmapped"], int]
    participant_scenario_group_status_counts: dict[
        Literal["success_only", "failure_only", "mixed_success_failure", "contains_unmapped"], int
    ]
    participant_identifiers_included: Literal[False] = False
    participant_rows_included: Literal[False] = False
    raw_outcomes_included: Literal[False] = False
    units_mapped: Literal[False] = False
    time_axis_mapped: Literal[False] = False
    human_failure_probability_estimated: Literal[False] = False
    field_population_transfer_qualified: Literal[False] = False
    record_digest: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def study_counts_reconcile(self):
        keys = {"success_only", "failure_only", "mixed_success_failure", "contains_unmapped"}
        row_count = sum(
            scenario.successful_trials + scenario.failed_trials + scenario.unmapped_trials
            for scenario in self.scenarios
        )
        groups = sum(scenario.participant_scenario_groups for scenario in self.scenarios)
        group_counts = {
            key: sum(scenario.participant_scenario_group_status_counts[key] for scenario in self.scenarios)
            for key in keys
        }
        if (
            set(self.participant_scenario_group_status_counts) != keys
            or any(value < 0 for value in self.participant_scenario_group_status_counts.values())
            or self.participant_scenario_group_status_counts != group_counts
            or self.participant_scenario_groups != groups
            or groups != sum(self.participant_scenario_group_status_counts.values())
            or self.additional_rows_within_participant_scenario_groups != row_count - groups
            or self.outcome_status_counts
            != {
                "success": sum(s.successful_trials for s in self.scenarios),
                "failure": sum(s.failed_trials for s in self.scenarios),
                "unmapped": sum(s.unmapped_trials for s in self.scenarios),
            }
        ):
            raise ValueError("human-control-room row and participant-scenario counts do not reconcile")
        return self

    def check_integrity(self):
        try:
            checked = type(self).model_validate_json(self.model_dump_json())
        except Exception as error:
            raise ValueError(f"human-control-room summary contract/integrity check failed: {error}") from error
        body = checked.model_dump(mode="json", exclude={"record_digest"})
        if digest(body) != self.record_digest:
            raise ValueError("human-control-room summary integrity check failed")
        return True


def summarize_human_control_room_study(
    archive: HumanControlRoomArchive,
    mapping: HumanControlRoomOutcomeMap,
    context: RunContext,
) -> HumanControlRoomStudySummary:
    """Create an aggregate scenario summary using caller-reviewed outcome labels.

    Rows without a mapped label remain unmapped. The summary deliberately does
    not estimate a probability, confidence interval, rate, or field transfer.
    """
    source = HumanControlRoomArchive.model_validate(archive.model_dump(mode="python"))
    source.check_integrity()
    review = HumanControlRoomOutcomeMap.model_validate(mapping.model_dump(mode="python"))
    run = RunContext.model_validate(context.model_dump(mode="python"))
    evidence = [item for item in run.evidence if item.id == review.evidence_id]
    if len(evidence) != 1 or evidence[0].sha256 != review.evidence_digest:
        raise ValueError("outcome mapping evidence is absent or does not match its pinned digest")
    if evidence[0].origin not in {"literature", "user_declared", "measured"}:
        raise ValueError("synthetic or experimental rows cannot serve as mapping approval evidence")
    if evidence[0].citation != review.task_definition:
        raise ValueError("mapping task definition must match the reviewed evidence citation")

    table = next((item for item in source.tables if item.sheet_name == "simulator_logs"), None)
    if table is None:
        raise ValueError("pinned workbook has no simulator_logs table")
    indices = {header: index for index, header in enumerate(table.headers) if header is not None}
    required = {"Participant", "Scenario", review.field_name}
    if not required <= indices.keys():
        raise ValueError("reviewed outcome field is absent from simulator_logs")
    success, failure = set(review.successful_values), set(review.failed_values)
    totals: dict[str, Counter] = {}
    status_counts: Counter = Counter()
    participants_by_scenario: dict[str, set[str]] = {}
    statuses_by_participant_scenario: dict[tuple[str, str], set[str]] = {}
    for row in table.rows:
        scenario = row[indices["Scenario"]]
        participant = row[indices["Participant"]]
        label = row[indices[review.field_name]]
        if not isinstance(scenario, str) or not isinstance(participant, str):
            raise ValueError("participant-scenario identity is incomplete in simulator logs")
        bucket = totals.setdefault(scenario, Counter())
        participants_by_scenario.setdefault(scenario, set()).add(participant)
        bucket["rows"] += 1
        if label in success:
            bucket["success"] += 1
            status_counts["success"] += 1
            row_status = "success"
        elif label in failure:
            bucket["failure"] += 1
            status_counts["failure"] += 1
            row_status = "failure"
        else:
            bucket["unmapped"] += 1
            status_counts["unmapped"] += 1
            row_status = "unmapped"
        statuses_by_participant_scenario.setdefault((scenario, participant), set()).add(row_status)

    grouped_by_scenario: dict[str, Counter] = {
        scenario: Counter(
            {
                "success_only": 0,
                "failure_only": 0,
                "mixed_success_failure": 0,
                "contains_unmapped": 0,
            }
        )
        for scenario in totals
    }
    for (scenario, _participant), outcomes in statuses_by_participant_scenario.items():
        if "unmapped" in outcomes:
            grouped_by_scenario[scenario]["contains_unmapped"] += 1
        elif outcomes == {"success"}:
            grouped_by_scenario[scenario]["success_only"] += 1
        elif outcomes == {"failure"}:
            grouped_by_scenario[scenario]["failure_only"] += 1
        else:
            grouped_by_scenario[scenario]["mixed_success_failure"] += 1

    scenarios = tuple(
        HumanControlRoomScenarioSummary(
            scenario_id=scenario,
            participant_count=len(participants_by_scenario[scenario]),
            participant_scenario_groups=sum(grouped_by_scenario[scenario].values()),
            additional_rows_within_participant_scenario_groups=(
                counts["rows"] - sum(grouped_by_scenario[scenario].values())
            ),
            mapped_trials=counts["success"] + counts["failure"],
            unmapped_trials=counts["unmapped"],
            successful_trials=counts["success"],
            failed_trials=counts["failure"],
            participant_scenario_group_status_counts={
                key: grouped_by_scenario[scenario][key]
                for key in (
                    "success_only",
                    "failure_only",
                    "mixed_success_failure",
                    "contains_unmapped",
                )
            },
        )
        for scenario, counts in sorted(totals.items())
    )
    body = {
        "dataset_id": source.dataset_id,
        "source_archive_sha256": source.archive_sha256,
        "source_workbook_sha256": source.workbook_sha256,
        "mapping_digest": digest(review),
        "mapping_evidence_id": review.evidence_id,
        "mapping_evidence_digest": review.evidence_digest,
        "mapped_field": review.field_name,
        "task_definition": review.task_definition,
        "opportunity_definition": review.opportunity_definition,
        "population_definition": review.population_definition,
        "context_digest": digest(run),
        "scenarios": scenarios,
        "participant_scenario_groups": sum(
            sum(counts.values()) for counts in grouped_by_scenario.values()
        ),
        "additional_rows_within_participant_scenario_groups": sum(
            totals[scenario]["rows"] - sum(counts.values())
            for scenario, counts in grouped_by_scenario.items()
        ),
        "participant_scenario_group_status_counts": {
            key: sum(grouped_by_scenario[scenario][key] for scenario in grouped_by_scenario)
            for key in (
                "success_only",
                "failure_only",
                "mixed_success_failure",
                "contains_unmapped",
            )
        },
        "outcome_status_counts": {
            key: status_counts[key] for key in ("success", "failure", "unmapped")
        },
    }
    record = HumanControlRoomStudySummary(**body, record_digest="0" * 64)
    record = record.model_copy(update={"record_digest": digest(record.model_dump(mode="json", exclude={"record_digest"}))})
    record.check_integrity()
    return record
