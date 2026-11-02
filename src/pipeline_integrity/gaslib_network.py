"""Evidence-gated adapter from GasLib topology to the existing gas-network owner API."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .contracts import Contract, Name, Nonnegative, Positive, RunContext, digest
from .gas_network import GasBoundary, GasJunction, GasNetwork, GasNetworkResult, GasPipe, solve_gas_network
from .gaslib import read_gaslib


class GasLibNodeState(Contract):
    """Caller-resolved steady state; GasLib nomination bounds are not converted here."""

    role: Literal["boundary", "junction"]
    pressure_pa_abs: Positive
    temperature_k: Positive
    withdrawal_kg_s: Nonnegative = 0.0

    @model_validator(mode="after")
    def boundary_has_no_withdrawal(self):
        if self.role == "boundary" and self.withdrawal_kg_s != 0:
            raise ValueError("a fixed-pressure boundary cannot also be a specified withdrawal")
        return self


class GasLibPipeControl(Contract):
    """Caller-supplied controls not completely determined by GasLib geometry."""

    local_loss_coefficient: Nonnegative
    heat_into_fluid_w: float
    segments: int = Field(default=4, ge=1, le=128)
    evidence_id: Name

    @model_validator(mode="after")
    def finite_heat(self):
        if not math.isfinite(self.heat_into_fluid_w):
            raise ValueError("prescribed pipe heat must be finite")
        return self


class GasLibNetworkMapping(Contract):
    """Explicit caller mapping from one selected GasLib pair into owner inputs."""

    asset_id: Name
    scenario_id: Name
    fluid: Name
    backend: Literal["HEOS", "PR", "SRK"] = "HEOS"
    property_basis: Name
    parameter_source_id: Name
    source_archive_evidence_id: Name
    network_member_evidence_id: Name
    scenario_member_evidence_id: Name
    composition_evidence_id: Name
    pressure_reference_evidence_id: Name
    nomination_interpretation_evidence_id: Name
    mapping_review_evidence_id: Name
    node_states: dict[str, GasLibNodeState]
    pipe_controls: dict[str, GasLibPipeControl]
    reviewed: bool = False
    nomination_mapping_mode: Literal["caller_supplied_state_not_derived_from_bounds"] = (
        "caller_supplied_state_not_derived_from_bounds"
    )

    @model_validator(mode="after")
    def evidence_ids_are_distinct(self):
        ids = (
            self.parameter_source_id,
            self.source_archive_evidence_id,
            self.network_member_evidence_id,
            self.scenario_member_evidence_id,
            self.composition_evidence_id,
            self.pressure_reference_evidence_id,
            self.nomination_interpretation_evidence_id,
            self.mapping_review_evidence_id,
            *(control.evidence_id for control in self.pipe_controls.values()),
        )
        if len(ids) != len(set(ids)):
            raise ValueError("GasLib source, property, mapping and pipe-control evidence IDs must be distinct")
        return self


class GasLibNetworkRun(Contract):
    schema: Literal["pipeline_integrity.gaslib_network_run.v1"] = "pipeline_integrity.gaslib_network_run.v1"
    status: Literal["computed", "withheld", "out_of_scope"]
    source_record_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_archive_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    network_member_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    scenario_member_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    selected_scenario_id: Name
    mapping: GasLibNetworkMapping
    specification: GasNetwork
    context: RunContext
    owner_result: dict
    nomination_values_applied: Literal[False] = False
    gaslib_reference_solution_reproduced: Literal[False] = False
    field_qualification: Literal[False] = False
    record_digest: str = Field(pattern=r"^[a-f0-9]{64}$")

    def check_integrity(self):
        checked = GasLibNetworkRun.model_validate(self.model_dump(mode="python"))
        if digest(checked.model_dump(mode="json", exclude={"record_digest"})) != self.record_digest:
            raise ValueError("GasLib network run receipt changed")
        owner = GasNetworkResult.model_validate_json(json.dumps(checked.owner_result))
        owner.check_integrity()
        if owner.status != checked.status:
            raise ValueError("GasLib adapter status differs from the existing owner result")
        specification = GasNetwork.model_validate_json(checked.specification.model_dump_json())
        context = RunContext.model_validate_json(checked.context.model_dump_json())
        mapping = GasLibNetworkMapping.model_validate_json(checked.mapping.model_dump_json())
        if mapping.asset_id != context.asset_id or mapping.scenario_id != context.scenario_id:
            raise ValueError("GasLib adapter mapping and replay context differ")
        if not mapping.reviewed:
            raise ValueError("GasLib run receipt no longer carries its required caller-reviewed mapping")
        if mapping.scenario_id != checked.selected_scenario_id:
            raise ValueError("GasLib selected scenario differs from the replay mapping")
        if (
            specification.fluid != mapping.fluid
            or specification.backend != mapping.backend
            or specification.property_basis != mapping.property_basis
            or specification.parameter_source_id != mapping.parameter_source_id
        ):
            raise ValueError("GasLib thermodynamic mapping differs from the sealed owner specification")
        if owner.specification_digest != digest(specification) or owner.context != context:
            raise ValueError("GasLib owner result differs from the sealed specification or context")
        node_ids = {node.id for node in specification.boundaries + specification.junctions}
        if set(mapping.node_states) != node_ids:
            raise ValueError("GasLib mapped nodes differ from the sealed owner specification")
        boundaries = {node.id: node for node in specification.boundaries}
        junctions = {node.id: node for node in specification.junctions}
        for node_id, state in mapping.node_states.items():
            if state.role == "boundary":
                node = boundaries.get(node_id)
                if node is None or state.withdrawal_kg_s != 0 or (
                    node.pressure_pa_abs != state.pressure_pa_abs
                    or node.temperature_k != state.temperature_k
                ):
                    raise ValueError("GasLib boundary mapping differs from the sealed owner specification")
            else:
                node = junctions.get(node_id)
                if node is None or (
                    node.initial_pressure_pa_abs != state.pressure_pa_abs
                    or node.initial_temperature_k != state.temperature_k
                    or node.withdrawal_kg_s != state.withdrawal_kg_s
                ):
                    raise ValueError("GasLib junction mapping differs from the sealed owner specification")
        pipes = {pipe.id: pipe for pipe in specification.pipes}
        if set(mapping.pipe_controls) != set(pipes):
            raise ValueError("GasLib pipe controls differ from the sealed owner specification")
        for pipe_id, control in mapping.pipe_controls.items():
            pipe = pipes[pipe_id]
            if (
                pipe.local_loss_coefficient != control.local_loss_coefficient
                or pipe.heat_into_fluid_w != control.heat_into_fluid_w
                or pipe.segments != control.segments
            ):
                raise ValueError("GasLib pipe control differs from the sealed owner specification")
        evidence = {item.id: item for item in context.evidence}
        required_evidence = {
            mapping.parameter_source_id,
            mapping.source_archive_evidence_id,
            mapping.network_member_evidence_id,
            mapping.scenario_member_evidence_id,
            mapping.composition_evidence_id,
            mapping.pressure_reference_evidence_id,
            mapping.nomination_interpretation_evidence_id,
            mapping.mapping_review_evidence_id,
            *(control.evidence_id for control in mapping.pipe_controls.values()),
        }
        if len(evidence) != len(context.evidence) or not required_evidence <= evidence.keys():
            raise ValueError("GasLib mapping evidence is missing or non-unique in the sealed context")
        source_pins = (
            (mapping.source_archive_evidence_id, checked.source_archive_sha256),
            (mapping.network_member_evidence_id, checked.network_member_sha256),
            (mapping.scenario_member_evidence_id, checked.scenario_member_sha256),
        )
        if any(
            evidence_id not in evidence
            or evidence[evidence_id].sha256 != source_sha
            or evidence[evidence_id].license_id != "CC-BY-3.0"
            for evidence_id, source_sha in source_pins
        ):
            raise ValueError("GasLib source digest or license differs from the sealed context evidence")
        return True


def prepare_gaslib_network(
    source_record: dict,
    archive_path,
    mapping: GasLibNetworkMapping,
    context: RunContext,
) -> GasNetwork:
    """Revalidate the selected archive and create explicit SI owner inputs, without solving."""
    path = Path(archive_path)
    if not path.is_file() or path.is_symlink():
        raise ValueError("GasLib source must be a regular local archive")
    if not isinstance(source_record, dict):
        raise TypeError("the parsed GasLib source record must be a dictionary")
    if source_record.get("schema") != "pipeline_integrity.gaslib.v1":
        raise ValueError("source is not a parsed GasLib record")
    checked_mapping = GasLibNetworkMapping.model_validate_json(mapping.model_dump_json())
    checked_context = RunContext.model_validate_json(context.model_dump_json())
    current = read_gaslib(
        path,
        network_member=source_record.get("network_member"),
        scenario_member=source_record.get("scenario_member"),
    )
    if digest(current) != digest(source_record):
        raise ValueError("GasLib archive or parsed members differ from the supplied source record")
    if not current.get("inline_cc_by_3_0_markers_present"):
        raise ValueError("the selected GasLib member pair lacks its reviewed CC BY 3.0 markers")
    if checked_mapping.asset_id != checked_context.asset_id:
        raise ValueError("GasLib mapping and run context asset IDs differ")
    if checked_mapping.scenario_id != checked_context.scenario_id:
        raise ValueError("GasLib mapping and run context scenario IDs differ")
    if checked_mapping.scenario_id not in current["nominations"]:
        raise ValueError("selected GasLib scenario is absent from the parsed member")
    if not checked_mapping.reviewed:
        raise ValueError("caller review of the GasLib scenario and state mapping is required")
    if set(checked_mapping.node_states) != set(current["nodes"]):
        raise ValueError("caller node-state mapping must cover every source node exactly")
    if set(checked_mapping.pipe_controls) != set(current["pipes"]):
        raise ValueError("caller controls must cover every source pipe exactly")
    if any(link["kind"] != "pipe" for link in current["connections"].values()):
        raise ValueError("GasLib active valves/compressors or other non-pipe links need an owner mapping")
    if not checked_mapping.node_states or not any(
        state.role == "boundary" for state in checked_mapping.node_states.values()
    ):
        raise ValueError("at least one explicitly mapped absolute-pressure boundary is required")

    evidence = {item.id: item for item in checked_context.evidence}
    required = {
        checked_mapping.parameter_source_id,
        checked_mapping.source_archive_evidence_id,
        checked_mapping.network_member_evidence_id,
        checked_mapping.scenario_member_evidence_id,
        checked_mapping.composition_evidence_id,
        checked_mapping.pressure_reference_evidence_id,
        checked_mapping.nomination_interpretation_evidence_id,
        checked_mapping.mapping_review_evidence_id,
        *(control.evidence_id for control in checked_mapping.pipe_controls.values()),
    }
    if len(evidence) != len(checked_context.evidence) or not required <= evidence.keys():
        raise ValueError("all source, property, boundary and review evidence must occur uniquely in context")
    expected_source_evidence = {
        checked_mapping.source_archive_evidence_id: current["archive_sha256"],
        checked_mapping.network_member_evidence_id: current["network_member_sha256"],
        checked_mapping.scenario_member_evidence_id: current["scenario_member_sha256"],
    }
    for evidence_id, expected_sha in expected_source_evidence.items():
        ref = evidence[evidence_id]
        if ref.sha256 != expected_sha or ref.license_id != "CC-BY-3.0":
            raise ValueError("GasLib archive/member evidence must pin its source SHA-256 and CC BY 3.0 license")
    for pipe_id, source_pipe in current["pipes"].items():
        control = checked_mapping.pipe_controls[pipe_id]
        endpoints = source_pipe["start_node"], source_pipe["end_node"]
        if source_pipe["elevation_change_m"] != 0:
            raise ValueError("unequal endpoint elevation needs unsupported hydrostatic energy/momentum coupling")
        if control.evidence_id not in evidence:
            raise ValueError(f"prescribed control evidence is absent for pipe {pipe_id}")
        if any(endpoint not in checked_mapping.node_states for endpoint in endpoints):
            raise ValueError("GasLib pipe endpoint is absent from the caller state mapping")

    boundaries, junctions = [], []
    for node_id, parsed_node in current["nodes"].items():
        state = checked_mapping.node_states[node_id]
        elevation = parsed_node["elevation_m"]
        if state.role == "boundary":
            boundaries.append(
                GasBoundary(
                    id=node_id,
                    pressure_pa_abs=state.pressure_pa_abs,
                    temperature_k=state.temperature_k,
                    elevation_m=elevation,
                )
            )
        else:
            junctions.append(
                GasJunction(
                    id=node_id,
                    initial_pressure_pa_abs=state.pressure_pa_abs,
                    initial_temperature_k=state.temperature_k,
                    withdrawal_kg_s=state.withdrawal_kg_s,
                    elevation_m=elevation,
                )
            )
    pipes = []
    for pipe_id, source_pipe in current["pipes"].items():
        control = checked_mapping.pipe_controls[pipe_id]
        pipes.append(
            GasPipe(
                id=pipe_id,
                start_node=source_pipe["start_node"],
                end_node=source_pipe["end_node"],
                length_m=source_pipe["length_m"],
                diameter_m=source_pipe["diameter_m"],
                roughness_m=source_pipe["roughness_m"],
                local_loss_coefficient=control.local_loss_coefficient,
                heat_into_fluid_w=control.heat_into_fluid_w,
                segments=control.segments,
            )
        )
    return GasNetwork(
        fluid=checked_mapping.fluid,
        backend=checked_mapping.backend,
        property_basis=checked_mapping.property_basis,
        parameter_source_id=checked_mapping.parameter_source_id,
        boundaries=tuple(boundaries),
        junctions=tuple(junctions),
        pipes=tuple(pipes),
    )


def solve_gaslib_network(
    source_record: dict,
    archive_path,
    mapping: GasLibNetworkMapping,
    context: RunContext,
    *,
    backend=None,
) -> GasLibNetworkRun:
    """Map reviewed inputs, then delegate conservation and physics to gas_network."""
    # Freeze caller-owned mutable containers before source verification and the
    # potentially long-running owner solve. Receipt fields must describe the
    # same values that produced the owner specification.
    source_snapshot = json.loads(json.dumps(source_record, allow_nan=False))
    mapping_snapshot = GasLibNetworkMapping.model_validate_json(mapping.model_dump_json())
    context_snapshot = RunContext.model_validate_json(context.model_dump_json())
    specification = prepare_gaslib_network(
        source_snapshot, archive_path, mapping_snapshot, context_snapshot
    )
    owner = solve_gas_network(specification, context_snapshot, backend=backend)
    owner.check_integrity()
    body = {
        "schema": "pipeline_integrity.gaslib_network_run.v1",
        "status": owner.status,
        "source_record_digest": digest(source_snapshot),
        "source_archive_sha256": source_snapshot["archive_sha256"],
        "network_member_sha256": source_snapshot["network_member_sha256"],
        "scenario_member_sha256": source_snapshot["scenario_member_sha256"],
        "selected_scenario_id": mapping_snapshot.scenario_id,
        "mapping": mapping_snapshot.model_dump(mode="json"),
        "specification": specification.model_dump(mode="json"),
        "context": context_snapshot.model_dump(mode="json"),
        "owner_result": owner.model_dump(mode="json"),
        "nomination_values_applied": False,
        "gaslib_reference_solution_reproduced": False,
        "field_qualification": False,
    }
    return GasLibNetworkRun.model_validate_json(json.dumps(body | {"record_digest": digest(body)}))


def verify_gaslib_network_run(record: GasLibNetworkRun):
    if not isinstance(record, GasLibNetworkRun):
        raise TypeError("a GasLibNetworkRun record is required")
    record.check_integrity()
    return True
