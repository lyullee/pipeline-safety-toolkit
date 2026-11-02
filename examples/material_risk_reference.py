"""Compare existing material primitives with the existing scoped risk APIs.

The scoped probabilities are independently declared authored inputs. They are not
created by relabelling the material primitive's unscoped probability output.
"""

from pipeline_integrity.contracts import EvidenceRef, RunContext, digest
from pipeline_integrity.risk_exchange import (
    EventBranch,
    EventTreeRequest,
    FaultNode,
    FaultTreeRequest,
    RiskFrequency,
    RiskProbability,
)


def declared_probability(key, value, context):
    source = {
        "probability": value,
        "basis": "original hypothetical scoped input, not a material-output conversion",
    }
    return RiskProbability(
        id=key,
        probability=value,
        measure="demand_failure_probability",
        condition_id="authored_condition",
        conditioning="One original hypothetical conditional case",
        opportunity_id="authored_demand",
        opportunity_definition="One original hypothetical demand",
        failure_definition=f"Declared event {key}",
        role="equipment",
        evidence_ids=("authored",),
        qualification="assumed",
        context=context,
        source_kind="declared",
        source_payload=source,
        source_digest=digest(source),
    )


def make_cases():
    seconds_per_reference_year = 31536000.0
    context = RunContext(
        asset_id="authored_probability_asset",
        scenario_id="equivalent_arithmetic_reference",
        clock_id="declared_elapsed_seconds",
        origin="synthetic",
        evidence=(
            EvidenceRef(
                id="authored",
                citation="Original hypothetical events and complete exclusive partition",
                origin="synthetic",
                license_id="MIT",
            ),
        ),
        metadata={"seconds_per_reference_year": seconds_per_reference_year},
    )
    events = tuple(declared_probability(k, p, context) for k, p in (("A", 0.1), ("B", 0.2)))
    gates = {
        kind: FaultTreeRequest(
            root=FaultNode(kind=kind, children=tuple(FaultNode(kind="event", event_id=e.id) for e in events)),
            events=events,
            condition_id="authored_condition",
            opportunity_id="authored_demand",
            dependence="declared_independent",
            dependence_evidence_id="authored",
        )
        for kind in ("AND", "OR")
    }
    parameters = {
        "AND": {"probabilities": [0.1, 0.2], "gate": "AND", "independence_declared": True},
        "OR": {"probabilities": [0.1, 0.2], "gate": "OR", "independence_declared": True},
        "paths": {"initiating_frequency_per_s": 4e-8, "conditional_probabilities": [0.6, 0.3, 0.1]},
    }
    annual = parameters["paths"]["initiating_frequency_per_s"] * seconds_per_reference_year
    frequency_source = {
        "annual_frequency_per_year": annual,
        "original_frequency_per_s": 4e-8,
        "seconds_per_reference_year": seconds_per_reference_year,
        "basis": "original hypothetical initiating rate",
    }
    rate = RiskFrequency(
        id="authored_initiating",
        annual_frequency_per_year=annual,
        condition_id="authored_condition",
        endpoint="event_occurrence",
        evidence_ids=("authored",),
        context=context,
        source_payload=frequency_source,
        source_digest=digest(frequency_source),
    )
    paths = tuple(declared_probability(f"path-{i}", p, context) for i, p in enumerate((0.6, 0.3, 0.1)))
    tree = EventTreeRequest(
        branches=tuple(
            EventBranch(
                id=f"branch-{i}",
                probability_id=e.id,
                next_condition_id=f"outcome-{i}",
                consequence_id=f"consequence-{i}",
            )
            for i, e in enumerate(paths)
        ),
        events=paths,
        partition_evidence_id="authored",
    )
    return parameters, gates, rate, tree, context
