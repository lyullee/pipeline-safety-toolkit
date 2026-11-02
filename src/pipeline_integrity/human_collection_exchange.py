"""Evidence-bound bridge from HRA 0.5 collection estimates to risk inputs.

The upstream HRA collection ledger owns its opportunity reconciliation and
posterior calculation. This module only checks the boundary, binds the result
to an explicitly reviewed process condition/opportunity, and seals provenance.
"""

from __future__ import annotations

import hashlib
import inspect
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .contracts import Contract, Name, RunContext, digest, json_value
from .risk_exchange import HRARiskClient, RiskProbability


HRA_COLLECTION_API_VERSION = "0.5.0"
HRA_COLLECTION_API_COMMIT = "d2754bbea16e12d00240e88d13cb4badbe672ac3"
HRA_SOURCE_FILES = (
    "collection.py",
    "opportunity_validation.py",
    "observations.py",
    "contracts.py",
    "probability.py",
)
HRA_SOURCE_SHA256 = {
    "collection.py": "6a5119c6b30942d3caf6e19e8adf9fbc2931a43bbb148fa0400537580b84e1cb",
    "opportunity_validation.py": "044e71664c59b5047906667d25055f4756794a3391d959b5f06e4f7e270d84d0",
    "observations.py": "7d30a6c30ec7ec4c16c944133f1a8fbc332884f56c1333f97d7c1d5f103c4aa3",
    "contracts.py": "b1d80221d420b93ad98a0e8405d0dfad6c0ee8f612c6a265d18b75b184856704",
    "probability.py": "e5d2eb96341a727d20bee30779432a31552caf86bc9d70e42a26a00abc806d8f",
}


class HumanCollectionRiskBinding(Contract):
    """A replayable HRA collection estimate bound to one declared risk demand."""

    schema_version: Literal["pipeline_integrity.human_collection_risk_binding.v1"] = (
        "pipeline_integrity.human_collection_risk_binding.v1"
    )
    owner_id: Literal["human-reliability-toolkit"] = "human-reliability-toolkit"
    owner_version: Literal[HRA_COLLECTION_API_VERSION] = HRA_COLLECTION_API_VERSION
    owner_source_commit: Literal[HRA_COLLECTION_API_COMMIT] = HRA_COLLECTION_API_COMMIT
    owner_source_sha256: dict[str, str]
    protocol_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    collection_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    estimand: Literal["initial", "final"]
    target_condition_id: Name
    target_opportunity_id: Name
    mapping_evidence_id: Name
    independence_review_evidence_id: Name
    missingness_review_evidence_id: Name | None = None
    owner_estimate: dict
    context_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    risk_probability: RiskProbability
    industrial_hep_validated: Literal[False] = False
    field_population_transfer_qualified: Literal[False] = False
    record_digest: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def source_and_receipt(self):
        if self.owner_source_sha256 != HRA_SOURCE_SHA256:
            raise ValueError("HRA 0.5 source identity differs from the reviewed owner commit")
        RiskProbability.model_validate_json(self.risk_probability.model_dump_json())
        payload = self.risk_probability.source_payload
        estimate = payload.get("estimate", {})
        expected_estimate = dict(self.owner_estimate)
        binding_evidence_ids = [self.mapping_evidence_id, self.independence_review_evidence_id]
        if self.missingness_review_evidence_id is not None:
            binding_evidence_ids.append(self.missingness_review_evidence_id)
        expected_estimate["evidence_ids"] = list(
            dict.fromkeys((*expected_estimate.get("evidence_ids", ()), *binding_evidence_ids))
        )
        expected_estimate["status"] = "research"
        context_evidence = {item.id: item for item in self.risk_probability.context.evidence}
        required_evidence = {
            *self.owner_estimate.get("evidence_ids", ()),
            self.mapping_evidence_id,
            self.independence_review_evidence_id,
        }
        if self.missingness_review_evidence_id is not None:
            required_evidence.add(self.missingness_review_evidence_id)
        if not required_evidence <= set(context_evidence):
            raise ValueError("collection risk binding cites evidence absent from its context")
        if any(
            context_evidence[identity].sha256 is None
            or context_evidence[identity].origin == "synthetic"
            for identity in required_evidence
        ):
            raise ValueError("collection risk binding evidence must be source-hash pinned and non-synthetic")
        trace = estimate.get("trace", {})
        audit = trace.get("collection_audit", {})
        if (
            payload.get("provider", {}).get("id") != self.owner_id
            or payload.get("provider", {}).get("version") != self.owner_version
            or audit.get("protocol_digest") != self.protocol_digest
            or audit.get("collection_digest") != self.collection_digest
            or audit.get("industrial_hep_validated") is not False
            or estimate.get("event_id") != self.risk_probability.id
            or estimate.get("status") != self.risk_probability.qualification
            or estimate != expected_estimate
            or self.mapping_evidence_id not in self.risk_probability.evidence_ids
            or self.independence_review_evidence_id not in self.risk_probability.evidence_ids
            or (
                self.missingness_review_evidence_id is not None
                and self.missingness_review_evidence_id not in self.risk_probability.evidence_ids
            )
            or self.risk_probability.condition_id != self.target_condition_id
            or self.risk_probability.opportunity_id != self.target_opportunity_id
            or digest(self.risk_probability.context) != self.context_digest
        ):
            raise ValueError("risk input is not bound to the pinned HRA collection audit")
        expected = digest(self.model_dump(mode="json", exclude={"record_digest"}))
        if self.record_digest != expected:
            raise ValueError("human collection risk binding receipt changed")
        return self

    def check_integrity(self):
        type(self).model_validate(self.model_dump(mode="python"))
        return True


def _source_identity(bundle, client: HRARiskClient) -> dict[str, str]:
    provider = client.provider
    if provider.get("id") != "human-reliability-toolkit" or provider.get("version") != HRA_COLLECTION_API_VERSION:
        raise ValueError("the HRA 0.5.0 owner API is required for collection exchange")
    module_name = type(bundle).__module__
    if module_name != "human_reliability.collection":
        raise ValueError("collection bundle must come from human_reliability.collection")
    module_file = inspect.getsourcefile(type(bundle))
    if not module_file:
        raise ValueError("HRA collection source location is unavailable")
    root = Path(module_file).resolve().parent
    result = {}
    for name in HRA_SOURCE_FILES:
        path = root / name
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"required HRA collection source file is unavailable: {name}")
        hasher = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                hasher.update(block)
        result[name] = hasher.hexdigest()
    if result != HRA_SOURCE_SHA256:
        raise ValueError(
            f"HRA collection source differs from the reviewed commit {HRA_COLLECTION_API_COMMIT}"
        )
    return result


def bind_human_collection_estimate(
    bundle,
    event,
    *,
    client: HRARiskClient,
    context: RunContext,
    estimand: Literal["initial", "final"],
    prior,
    independence_review: str,
    independence_review_evidence_id: str,
    mapping_evidence_id: str,
    target_condition_id: str,
    target_opportunity_id: str,
    missingness_review: str | None = None,
    missingness_review_evidence_id: str | None = None,
    selection: dict | None = None,
    protection_ids: tuple[str, ...] = (),
) -> HumanCollectionRiskBinding:
    """Call the owner's HRA collection estimator and bind it without recoding outcomes.

    ``mapping_evidence_id`` documents why the upstream event/conditioning and
    opportunity definitions apply to the target process identifiers.
    Independence and optional missingness reviews must each cite a source-hash-
    pinned, non-synthetic evidence item in ``context``. Collection estimates
    from simulator or synthetic settings remain marked research by the owner.
    """
    if estimand not in {"initial", "final"}:
        raise ValueError("initial or final HRA estimand required")
    if not isinstance(independence_review, str) or not independence_review.strip():
        raise ValueError("explicit opportunity-independence review required")
    if not isinstance(independence_review_evidence_id, str) or not independence_review_evidence_id.strip():
        raise ValueError("source-hash-pinned opportunity-independence review evidence required")
    if missingness_review is not None and (
        not isinstance(missingness_review_evidence_id, str) or not missingness_review_evidence_id.strip()
    ):
        raise ValueError("a missingness review requires source-hash-pinned evidence")
    if missingness_review is None and missingness_review_evidence_id is not None:
        raise ValueError("missingness review evidence cannot be supplied without a review")
    if not isinstance(mapping_evidence_id, str) or not mapping_evidence_id.strip():
        raise ValueError("condition/opportunity mapping evidence ID required")
    if selection is None:
        selection = {}
    if not isinstance(selection, dict) or set(selection) - {"window_ids", "conditions"}:
        raise ValueError("collection selection supports only explicit window_ids and conditions")

    owner_hashes = _source_identity(bundle, client)
    protocol = bundle.protocol
    for attribute in (
        "id",
        "event_id",
        "task_id",
        "conditioning",
        "failure_definition",
        "opportunity_definition",
        "source_id",
        "setting",
        "license_evidence",
        "coverage_review",
        "registry_review",
        "adjudication_rule",
        "collection_authorization",
    ):
        if not getattr(protocol, attribute, None):
            raise ValueError(f"HRA collection protocol is missing {attribute}")
    checked_context = RunContext.model_validate(context.model_dump(mode="python"))
    evidence_by_id = {item.id: item for item in checked_context.evidence}
    if len(evidence_by_id) != len(checked_context.evidence):
        raise ValueError("risk context evidence IDs must be unique")
    evidence_ids = {
        protocol.license_evidence,
        protocol.coverage_review,
        protocol.registry_review,
        protocol.adjudication_rule,
        protocol.collection_authorization,
        mapping_evidence_id,
        independence_review_evidence_id,
        *((missingness_review_evidence_id,) if missingness_review_evidence_id else ()),
        *(window.reconciliation_evidence for window in bundle.windows),
        *(demand.disposition_evidence for demand in bundle.demands),
        *(demand.context_evidence for demand in bundle.demands if demand.context),
        *(outcome.evidence for outcome in bundle.outcomes),
    }
    if not evidence_ids <= set(evidence_by_id):
        raise ValueError("risk context must carry every collection, reconciliation, mapping and review evidence ID")
    mapping_evidence = evidence_by_id[mapping_evidence_id]
    if mapping_evidence.sha256 is None or mapping_evidence.origin == "synthetic":
        raise ValueError("condition/opportunity mapping evidence must be source-hash pinned and reviewed")
    review_evidence_ids = {independence_review_evidence_id}
    if missingness_review_evidence_id is not None:
        review_evidence_ids.add(missingness_review_evidence_id)
    if any(
        evidence_by_id[identity].sha256 is None or evidence_by_id[identity].origin == "synthetic"
        for identity in review_evidence_ids
    ):
        raise ValueError("independence/missingness review evidence must be source-hash pinned and reviewed")
    if (
        event.id != protocol.event_id
        or event.task_id != protocol.task_id
        or event.conditioning != protocol.conditioning
        or event.failure_definition != protocol.failure_definition
        or event.opportunity_definition != protocol.opportunity_definition
    ):
        raise ValueError("human failure event does not match the frozen collection protocol")

    before = bundle.require_reconciled()
    if before.get("denominator_reconciled") is not True:
        raise ValueError("HRA opportunity denominator is not reconciled")
    if before.get("industrial_hep_validated") is not False:
        raise ValueError("unexpected HRA industrial-validation claim")
    observation_id = f"{protocol.id}:{before.get('collection_digest', '')}"
    observation_evidence = evidence_by_id.get(observation_id)
    if (
        not before.get("collection_digest")
        or observation_evidence is None
        or observation_evidence.sha256 != before["collection_digest"]
    ):
        raise ValueError("risk context must pin the HRA observation ID to its collection digest")
    estimate = bundle.hep(
        estimand=estimand,
        prior=prior,
        independence_review=independence_review,
        missingness_review=missingness_review,
        **selection,
    )
    after = bundle.require_reconciled()
    if before != after:
        raise ValueError("HRA collection changed during estimation")
    native_estimate = client.api.Estimate.model_validate_json(estimate.model_dump_json())
    owner_estimate = json_value(native_estimate)
    trace = json_value(native_estimate.trace)
    if (
        native_estimate.event_id != protocol.event_id
        or native_estimate.conditioning != protocol.conditioning
        or trace.get("collection_audit") != before
        or trace.get("source_id") != protocol.source_id
        or trace.get("setting") != protocol.setting
        or before.get("collection_digest") is None
    ):
        raise ValueError("HRA estimate omitted or changed its collection provenance")
    if not set(native_estimate.evidence_ids) <= set(evidence_by_id):
        raise ValueError("risk context is missing evidence cited by the HRA estimate")
    evidence_ids_for_estimate = tuple(
        dict.fromkeys(
            (
                *native_estimate.evidence_ids,
                mapping_evidence_id,
                independence_review_evidence_id,
                *((missingness_review_evidence_id,) if missingness_review_evidence_id else ()),
            )
        )
    )
    # Owner empirical status describes the collection setting. It does not
    # qualify transfer to an industrial process, so the risk handoff is research.
    native_estimate = native_estimate.model_copy(
        update={"evidence_ids": evidence_ids_for_estimate, "status": "research"}
    )

    risk_probability = client.bind_human_estimate(
        native_estimate,
        event,
        condition_id=target_condition_id,
        opportunity_id=target_opportunity_id,
        context=checked_context,
        protection_ids=protection_ids,
    )
    body = {
        "owner_source_sha256": owner_hashes,
        "owner_source_commit": HRA_COLLECTION_API_COMMIT,
        "protocol_digest": before["protocol_digest"],
        "collection_digest": before["collection_digest"],
        "estimand": estimand,
        "target_condition_id": target_condition_id,
        "target_opportunity_id": target_opportunity_id,
        "mapping_evidence_id": mapping_evidence_id,
        "independence_review_evidence_id": independence_review_evidence_id,
        "missingness_review_evidence_id": missingness_review_evidence_id,
        "owner_estimate": owner_estimate,
        "context_digest": digest(checked_context),
        "risk_probability": risk_probability,
    }
    record = HumanCollectionRiskBinding.model_construct(**body, record_digest="0" * 64)
    record = record.model_copy(
        update={"record_digest": digest(record.model_dump(mode="json", exclude={"record_digest"}))}
    )
    record.check_integrity()
    return record


def verify_human_collection_risk_binding(result: HumanCollectionRiskBinding, bundle, event, **inputs):
    """Replay owner collection estimation and risk binding against the saved receipt."""
    checked = HumanCollectionRiskBinding.model_validate(result.model_dump(mode="python"))
    replay = bind_human_collection_estimate(bundle, event, **inputs)
    if replay.model_dump(mode="json") != checked.model_dump(mode="json"):
        raise ValueError("replayed HRA collection risk binding differs from its receipt")
    return {"valid": True, "record_digest": checked.record_digest}
