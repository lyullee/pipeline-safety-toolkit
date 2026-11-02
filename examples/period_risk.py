"""Original common-period examples; source survival and dependence are declared hypotheses."""

from pipeline_integrity.contracts import EvidenceRef, RunContext, digest
from pipeline_integrity.period_risk import PeriodEventBinding, PeriodLogicRequest, RiskPeriod
from pipeline_integrity.risk_exchange import FaultNode, JointState, RiskProbability


def node(key):
    return FaultNode(kind="event", event_id=key)


def make_cases():
    evidence = tuple(
        EvidenceRef(
            id=key,
            citation="Original synthetic common-period reference",
            origin="synthetic",
            license_id="MIT",
        )
        for key in ("A_basis", "B_basis", "period_basis", "mapping", "logic", "dependence")
    )
    context = RunContext(
        asset_id="system",
        scenario_id="common_survival",
        clock_id="elapsed_s",
        origin="synthetic",
        evidence=evidence,
    )
    period = RiskPeriod(
        id="mission_10_20",
        clock_id=context.clock_id,
        start_s=10.0,
        finish_s=20.0,
        common_condition_id="both_operable_at_start",
        common_conditioning="Both components survived to 10 s, with unchanged declared conditions",
        evidence_ids=("period_basis",),
    )
    bindings = []
    for key, value in (("A", 0.2), ("B", 0.1)):
        source = dict(
            probability=value,
            basis="Authored period probability, conditioned on the declared common initial state",
        )
        event = RiskProbability(
            id=key,
            probability=value,
            measure="mission_probability",
            condition_id=period.common_condition_id,
            conditioning=period.common_conditioning,
            opportunity_id=f"component_{key}_period",
            opportunity_definition="One first-failure event in [10, 20] s",
            failure_definition=f"Component {key} first fails during the period",
            role="equipment",
            evidence_ids=(key + "_basis",),
            qualification="assumed",
            context=context.model_copy(update={"asset_id": f"component_{key}"}),
            source_kind="declared",
            source_payload=source,
            source_digest=digest(source),
        )
        bindings.append(
            PeriodEventBinding(
                probability=event,
                source_start_s=10.0,
                source_finish_s=20.0,
                physical_scope_id=f"span_{key}",
                endpoint_id="first_failure",
                common_condition_id=period.common_condition_id,
                target_context_digest=digest(context),
                target_period_digest=digest(period),
                mapping_evidence_ids=("mapping",),
                period_asset_and_common_conditioning_reviewed=True,
            )
        )
    base = dict(
        events=tuple(bindings),
        period=period,
        top_id="system_period_event",
        top_endpoint_id="declared_boolean_event",
        top_definition="At least one component first fails within the declared period; no coincidence or leak is inferred",
        logic_evidence_id="logic",
        event_logic_reviewed=True,
        dependence="declared_independent",
        dependence_evidence_id="dependence",
    )
    cases = {
        "independent_or": PeriodLogicRequest(
            root=FaultNode(kind="OR", children=(node("A"), node("B"))), **base
        ),
        "shared_identity": PeriodLogicRequest(
            root=FaultNode(
                kind="OR", children=(node("A"), FaultNode(kind="AND", children=(node("A"), node("B"))))
            ),
            **base,
        ),
        "period_and": PeriodLogicRequest(root=FaultNode(kind="AND", children=(node("A"), node("B"))), **base),
        "vote_one": PeriodLogicRequest(
            root=FaultNode(kind="VOTE", children=(node("A"), node("B")), threshold=1), **base
        ),
        "neither": PeriodLogicRequest(
            root=FaultNode(kind="NOT", children=(FaultNode(kind="OR", children=(node("A"), node("B"))),)),
            **base,
        ),
    }
    joint = tuple(
        JointState(states=dict(A=a, B=b), probability=p)
        for a, b, p in ((False, False, 0.75), (True, False, 0.15), (False, True, 0.05), (True, True, 0.05))
    )
    cases["joint_or"] = cases["independent_or"].model_copy(
        update={"dependence": "joint_states", "joint_states": joint}
    )
    return cases, context
