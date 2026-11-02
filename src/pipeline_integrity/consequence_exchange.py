"""Conditional physical consequences and annual accounting through the existing PRISM engines."""

import json
import math
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal

from pydantic import Field, model_validator

from .contracts import Contract, Fraction, Name, RunContext, digest, json_value
from .datasets import file_digest
from .release_exchange import ReleaseHandoff
from .risk_exchange import RiskFrequency


class CompressiblePlane(Contract):
    algorithm: Literal["birch_momentum_energy"] = "birch_momentum_energy"
    evidence_id: Name
    maximum_energy_residual_j_kg: float = Field(default=1e-6, ge=0)


class ConsequenceCase(Contract):
    id: Name
    initiating_condition_id: Name
    facility: dict[str, Any]
    defaults: dict[str, Any]
    evidence_ids: tuple[Name, ...] = Field(min_length=1)
    compressible_plane: CompressiblePlane | None = None
    maximum_cells: int = Field(default=100000, ge=1, le=250000)
    maximum_field_samples: int = Field(default=5000000, ge=1, le=100000000)
    maximum_receptors: int = Field(default=1000, ge=1, le=10000)

    @model_validator(mode="after")
    def context_only(self):
        forbidden = {
            "source_input",
            "resolved_release",
            "resolved_release_set",
            "resolved_liquid_release",
            "component_id",
            "available_mass_kg",
            "source_position_enu_m",
            "source_positions_enu_m",
            "annual_frequency_per_year",
            "annual_frequency_provenance",
            "frequency_event",
            "initiation_intensity_per_s",
        }
        if forbidden.intersection(self.defaults):
            raise ValueError(
                "consequence defaults cannot replace the source or attach an initiating frequency"
            )
        occupancy = self.defaults.get("occupancy_probability", 1.0)
        if isinstance(occupancy, bool) or occupancy != 1.0:
            raise ValueError(
                "conditional harm must use occupancy one; annual occupancy is applied separately"
            )
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("case evidence IDs must not repeat")
        json_value(self.facility)
        json_value(self.defaults)
        return self


class ConsequenceResult(Contract):
    schema_version: Literal["pipeline_integrity.consequence_exchange.v1"] = (
        "pipeline_integrity.consequence_exchange.v1"
    )
    status: Literal["computed_diagnostic", "withheld"]
    case: ConsequenceCase
    context: RunContext
    source_record: dict
    native_request: dict
    native_result: dict | None
    plane_evidence: dict | None
    provider: dict
    issues: tuple[Name, ...]
    implementation_sha256: str
    result_digest: str
    physical_validation_claim: Literal[False] = False

    def check_integrity(self):
        if digest(self.model_dump(mode="json", exclude={"result_digest"})) != self.result_digest:
            raise ValueError("the conditional consequence result or retained evidence changed")


class AnnualCondition(Contract):
    id: Name
    probability: Fraction
    occupancy_probability: Fraction
    occupancy_evidence_id: Name
    consequence: ConsequenceResult


class AnnualEvent(Contract):
    event_id: Name
    frequency: RiskFrequency | None
    conditions: tuple[AnnualCondition, ...] = Field(min_length=1)
    partition_evidence_id: Name
    frequency_condition_independence_evidence_id: Name


class AnnualRiskRequest(Contract):
    assessment_id: Name
    events: tuple[AnnualEvent, ...] = Field(min_length=1)
    receptor_ids: tuple[Name, ...] = Field(min_length=1)
    initiating_event_partition_evidence_id: Name
    time_scope_evidence_id: Name


def _checked(value, cls):
    return cls.model_validate_json(value.model_dump_json())


def _numeric(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _pair(values):
    return (
        isinstance(values, (list, tuple))
        and len(values) == 2
        and all(_numeric(x) and 0 <= x <= 1 for x in values)
        and values[0] <= values[1]
    )


class PRISMConsequenceClient:
    """Use an explicitly pinned native backend; property/expansion work remains in the owner projects."""

    def __init__(self, backend, *, properties=None, api=None, provider=None):
        if api is None:
            owner = import_module("lh2_tno")
            contracts = import_module("lh2_tno.contracts")
            consequences = import_module("lh2_tno.consequences")
            sources = import_module("lh2_tno.provider_release")
            expansion = import_module("lh2_tno.compressible_source_adapter")
            attach = import_module("lh2_tno.compressible_handoff")
            pipeline = import_module("lh2_tno.pipeline")
            risk = import_module("lh2_tno.risk")
            api = SimpleNamespace(
                Facility=contracts.Facility,
                ConsequenceRequest=consequences.ConsequenceRequest,
                build_request=sources.build_consequence_request_from_provider_release,
                build_plane=expansion.build_compressible_plane_handoff,
                attach_plane=attach.attach_compressible_plane_handoff,
                GasRelease=import_module("lh2_tno.equipment_boundary").ResolvedGasRelease,
                Pipeline=pipeline.ResearchPipeline,
                BackendError=import_module("lh2_tno.backend").BackendError,
                AnnualScenario=risk.AnnualScenario,
                annual_risk=risk.annual_risk,
            )
            root = Path(owner.__file__).parent
            provider = {
                "id": "PRISM/lh2-tno",
                "version": owner.__version__,
                "source_sha256": digest(
                    {p.relative_to(root).as_posix(): file_digest(p) for p in sorted(root.rglob("*.py"))}
                ),
                "backend_packages": [
                    {"name": p.name, "source_sha256": p.sha256} for p in backend.config.packages
                ],
                "required_versions": backend.config.required_versions,
            }
        if provider is None:
            raise ValueError(
                "an injected consequence API requires explicit provider identity and source metadata"
            )
        self.backend, self.properties, self.api, self.provider = (
            backend,
            properties,
            api,
            json_value(provider),
        )

    def schemas(self):
        return {
            "facility": self.api.Facility.model_json_schema(),
            "native_request": self.api.ConsequenceRequest.model_json_schema(),
            "case": ConsequenceCase.model_json_schema(),
            "annual_risk": AnnualRiskRequest.model_json_schema(),
        }

    def evaluate(self, handoff: ReleaseHandoff, case: ConsequenceCase):
        if not isinstance(handoff, ReleaseHandoff):
            raise TypeError("a checked ReleaseHandoff is required")
        source_record = handoff.record()
        case = _checked(case, ConsequenceCase)
        packet = handoff.native
        plane_evidence = None
        if case.compressible_plane is not None:
            if handoff.history.provider_response["data"]["phase_basis"] != "gas":
                raise ValueError("the inspected compressible adapter accepts a gas provider history")
            properties = self.properties
            if properties is None:
                from lh2dt.properties import HydrogenProperties

                properties = HydrogenProperties(fluid=packet.property_basis.fluid)
            if getattr(properties, "fluid", None) != packet.property_basis.fluid:
                raise ValueError("the plane property resolver must identify the same source fluid")
            plane = self.api.build_plane(
                packet,
                handoff.history.provider_response,
                properties=properties,
                algorithm=case.compressible_plane.algorithm,
                evidence_status="synthetic_diagnostic",
            )
            residual = plane.audit.get("max_energy_residual_j_per_kg")
            if not _numeric(residual) or abs(residual) > case.compressible_plane.maximum_energy_residual_j_kg:
                raise ValueError("the declared compressible-plane energy residual limit was not met")
            attached, audit = self.api.attach_plane(packet, plane.export)
            packet = self.api.GasRelease.model_validate_json(json.dumps(attached, allow_nan=False))
            plane_evidence = {
                "export": plane.export,
                "generation_audit": plane.audit,
                "attachment_audit": audit,
            }
            original = handoff.native.model_dump(mode="json")
            revised = packet.model_dump(mode="json")
            if {k: v for k, v in revised.items() if k != "compressible_plane_handoff"} != {
                k: v for k, v in original.items() if k != "compressible_plane_handoff"
            }:
                raise ValueError("the expansion adapter changed the original source packet")
        facility = self.api.Facility.model_validate_json(json.dumps(case.facility, allow_nan=False))
        if (
            handoff.history.build.context.origin == "synthetic"
            and facility.configuration_origin != "synthetic"
        ):
            raise ValueError("a synthetic source cannot become a reviewed or field-confirmed facility")
        defaults = dict(case.defaults) | {"occupancy_probability": 1.0}
        request = self.api.build_request(packet, defaults)
        angle = math.radians((request.weather.wind_from_bearing_deg + 180.0) % 360.0)
        for receptor in facility.receptors:
            east, north, height = receptor.position_enu_m
            coordinates = (
                east * math.sin(angle) + north * math.cos(angle),
                east * math.cos(angle) - north * math.sin(angle),
                height,
            )
            bounds = (
                request.domain.downwind_bounds_m,
                request.domain.crosswind_bounds_m,
                request.domain.height_bounds_m,
            )
            if any(x < a - 1e-12 or x > b + 1e-12 for x, (a, b) in zip(coordinates, bounds, strict=True)):
                raise ValueError("a receptor lies outside the absolute meteorological field domain")
        cells = math.prod(
            math.ceil((b - a) / request.domain.cell_size_m)
            for a, b in (
                request.domain.downwind_bounds_m,
                request.domain.crosswind_bounds_m,
                request.domain.height_bounds_m,
            )
        )
        if (
            cells > case.maximum_cells
            or cells * len(request.evaluation_times_s) > case.maximum_field_samples
            or len(facility.receptors) > case.maximum_receptors
        ):
            raise ValueError("the declared consequence field/receptor budget was exceeded")
        if (
            request.annual_frequency_per_year is not None
            or request.initiation_intensity_per_s is not None
            or request.occupancy_probability != 1.0
        ):
            raise ValueError("conditional consequence construction introduced frequency or occupancy")
        pipeline = self.api.Pipeline(facility, self.backend)
        issues = []
        try:
            native = json_value(pipeline.consequences(request))
        except self.api.BackendError as error:
            native = {"error_type": type(error).__name__, "message": str(error)}
            issues.append("owner_consequence_execution_failed")
        if not issues:
            data = native.get("data", {})
            if (
                data.get("full_chain_executed") is not True
                or data.get("physical_validation_claim") is not False
            ):
                issues.append("unexpected_native_execution_or_qualification")
            source = data.get("source", {})
            if (
                source.get("discharge_recalculated") is not False
                or source.get("equipment_dynamics_recalculated") is not False
                or digest(source.get("provider_boundary", {})) != digest(packet.model_dump(mode="json"))
            ):
                issues.append("source_ownership_or_result_identity_changed")
            ledger = data.get("mass_ledger", {})
            released, residual = ledger.get("released_kg"), ledger.get("end_to_end_residual_kg")
            if (
                not _numeric(released)
                or not math.isclose(released, packet.cumulative_mass_out_kg, rel_tol=1e-8, abs_tol=0)
                or not _numeric(residual)
                or abs(residual) > 1e-8 * packet.cumulative_mass_out_kg
            ):
                issues.append("source_to_cloud_mass_accounting_failed")
            if data.get("explosion", {}).get("domain_truncated") is not False:
                issues.append("flammable_cloud_outside_domain_unresolved")
            receptors = data.get("receptors", [])
            expected = {r.id: list(r.position_enu_m) for r in facility.receptors}
            if len(receptors) != len(expected) or {r.get("id") for r in receptors} != set(expected):
                issues.append("native_receptor_identity_changed")
            for row in receptors:
                if row.get("position_enu_m") != expected.get(row.get("id")) or not _pair(
                    row.get("harm_given_observed_release_low_high")
                ):
                    issues.append("native_receptor_geometry_or_harm_range_invalid")
                history = row.get("concentration_history", [])
                if [point.get("time_s") for point in history] != list(request.evaluation_times_s) or not all(
                    _numeric(point.get("h2_mole_fraction")) and 0 <= point["h2_mole_fraction"] <= 1
                    for point in history
                ):
                    issues.append("native_concentration_clock_or_range_changed")
                if (
                    row.get("annual_current_weather_contribution_low_high") is not None
                    or row.get("first_initiation_harm_probability_low_high") is not None
                ):
                    issues.append("native_conditional_harm_was_frequency_weighted")
            branch_weights = data.get("ignition_branch_probabilities", {})
            expected_branches = request.ignition.probabilities()
            if (
                not branch_weights
                or set(branch_weights) != set(expected_branches)
                or not all(_numeric(p) and 0 <= p <= 1 for p in branch_weights.values())
                or not math.isclose(math.fsum(branch_weights.values()), 1.0, rel_tol=0, abs_tol=1e-12)
                or any(
                    not math.isclose(branch_weights[key], value, rel_tol=1e-12, abs_tol=0)
                    for key, value in expected_branches.items()
                )
            ):
                issues.append("native_ignition_partition_invalid")
        handoff.record()
        body = {
            "schema_version": "pipeline_integrity.consequence_exchange.v1",
            "status": "withheld" if issues else "computed_diagnostic",
            "case": case.model_dump(mode="json"),
            "context": handoff.history.build.context.model_dump(mode="json"),
            "source_record": source_record,
            "native_request": request.model_dump(mode="json"),
            "native_result": native,
            "plane_evidence": plane_evidence,
            "provider": self.provider,
            "issues": list(dict.fromkeys(issues)),
            "implementation_sha256": file_digest(Path(__file__)),
            "physical_validation_claim": False,
        }
        return ConsequenceResult.model_validate_json(
            json.dumps({**body, "result_digest": digest(body)}, allow_nan=False)
        )

    def annual_risk(self, specification: AnnualRiskRequest):
        specification = _checked(specification, AnnualRiskRequest)
        if len({e.event_id for e in specification.events}) != len(specification.events) or len(
            set(specification.receptor_ids)
        ) != len(specification.receptor_ids):
            raise ValueError("annual events and selected receptors must have unique identities")
        facilities = set()
        pending = []
        rows_by_receptor = {key: [[], [], [], []] for key in specification.receptor_ids}
        for event in specification.events:
            if len({c.id for c in event.conditions}) != len(event.conditions) or not math.isclose(
                math.fsum(c.probability for c in event.conditions), 1.0, rel_tol=0, abs_tol=1e-12
            ):
                raise ValueError("annual condition partitions must be unique and sum to one")
            if event.frequency is not None and (
                event.frequency.endpoint != "loss_of_containment" or event.frequency.id != event.event_id
            ):
                raise ValueError(
                    "annual release risk requires an explicitly bound loss-of-containment frequency"
                )
            source_digests = set()
            for condition in event.conditions:
                consequence = condition.consequence
                consequence.check_integrity()
                uncomputed = consequence.status != "computed_diagnostic"
                if uncomputed:
                    pending.append(f"{event.event_id}/{condition.id}")
                facilities.add(digest(consequence.case.facility))
                packet = consequence.source_record["native_packet"]
                source_digests.add(consequence.source_record["native_packet_digest"])
                if packet["event_id"] != event.event_id:
                    raise ValueError("the initiating event and consequence source identity differ")
                if event.frequency is not None and event.frequency.context != consequence.context:
                    raise ValueError("annual frequency and consequence context differ")
                if (
                    event.frequency is not None
                    and event.frequency.condition_id != consequence.case.initiating_condition_id
                ):
                    raise ValueError("annual frequency and consequence initiating condition differ")
                # No additional barrier factors are accepted here: the native
                # conditional consequence already contains its ignition branches.
                receptors = {
                    r["id"]: r
                    for r in (
                        consequence.case.facility["receptors"]
                        if uncomputed
                        else consequence.native_result["data"]["receptors"]
                    )
                }
                for key in specification.receptor_ids:
                    if key not in receptors:
                        raise ValueError("a selected annual receptor is missing from a conditional result")
                    for index in range(2):
                        for unknown in range(2):
                            # These explicit 0/1 substitutions form an envelope for
                            # uncomputed harm. They never become a completed value.
                            harm = (
                                float(unknown)
                                if uncomputed
                                else receptors[key]["harm_given_observed_release_low_high"][index]
                            )
                            rows_by_receptor[key][2 * index + unknown].append(
                                self.api.AnnualScenario(
                                    id=f"{event.event_id}/{condition.id}",
                                    initiating_event_id=event.event_id,
                                    weather_class=condition.id,
                                    frequency_per_year=None
                                    if event.frequency is None
                                    else event.frequency.annual_frequency_per_year,
                                    harm_probability_given_event=harm,
                                    annual_weather_probability=condition.probability,
                                    occupancy_probability=condition.occupancy_probability,
                                )
                            )
            if len(source_digests) != 1:
                raise ValueError("annual conditions for one event must share the exact source release")
        if len(facilities) != 1:
            raise ValueError("annual risk needs one explicitly shared facility and receptor geometry")
        results = {
            key: [json_value(self.api.annual_risk(tuple(rows))) for rows in pair]
            for key, pair in rows_by_receptor.items()
        }
        missing = sorted({e.event_id for e in specification.events if e.frequency is None})
        for pair in results.values():
            for native in pair:
                value = native.get("value")
                if (
                    native.get("units") != "year^-1"
                    or native.get("physical_validation_claim") is not False
                    or sorted(native.get("uncomputed_scenarios", [])) != missing
                    or (missing and value is not None)
                    or (not missing and (not _numeric(value) or value < 0))
                    or not _numeric(native.get("known_contribution"))
                    or native["known_contribution"] < 0
                ):
                    raise ValueError(
                        "native annual risk changed missing-frequency, unit or qualification semantics"
                    )
            if (
                pair[0]["known_contribution"] > pair[2]["known_contribution"]
                or pair[1]["known_contribution"] > pair[3]["known_contribution"]
            ):
                raise ValueError("native annual risk reversed the declared sensitivity bounds")
        summaries = {
            key: {
                "value_low_high": None if pending or missing else [pair[0]["value"], pair[2]["value"]],
                "known_contribution_low_high": [pair[0]["known_contribution"], pair[2]["known_contribution"]],
                "uncomputed_harm_envelope_low_high": None
                if missing
                else [pair[0]["value"], pair[3]["value"]],
                "native_results": pair,
            }
            for key, pair in results.items()
        }
        status = (
            "needs_frequency_and_consequence"
            if missing and pending
            else "needs_frequency"
            if missing
            else "needs_consequence"
            if pending
            else "computed_diagnostic"
        )
        body = {
            "schema": "pipeline_integrity.annual_risk_exchange.v1",
            "metric": "annualized_declared_window_harm_rate",
            "complete_accident_or_facility_risk": False,
            "status": status,
            "inputs": specification.model_dump(mode="json"),
            "provider": self.provider,
            "receptor_results": summaries,
            "uncomputed_events": missing,
            "uncomputed_conditions": pending,
            "bound_meaning": "paired model sensitivity and explicitly uncomputed harm in [0,1]; not a confidence interval",
            "physical_validation_claim": False,
            "implementation_sha256": file_digest(Path(__file__)),
        }
        return {**body, "result_digest": digest(body)}
