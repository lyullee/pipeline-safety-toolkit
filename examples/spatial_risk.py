"""Two authored population cells; optional actual PRISM/HRA risk bookkeeping."""

import argparse
import json
import sys
from pathlib import Path

from pipeline_integrity.contracts import EvidenceRef, RunContext, digest
from pipeline_integrity.risk_exchange import RiskFrequency
from pipeline_integrity.spatial_risk import (
    REVIEWS,
    JointCasualtyDistribution,
    JointCasualtyState,
    SpatialCondition,
    SpatialEvent,
    SpatialGrid,
    SpatialHarmField,
    SpatialPopulation,
    SpatialRiskRequest,
    evaluate_spatial_risk,
    native_backend_metadata,
)


def make_case(*, reviewed=False, with_joint=True, origin="synthetic"):
    evidence = "authored:spatial_reference"
    context = RunContext(
        asset_id="authored_pipe",
        scenario_id="spatial_conditional_reference",
        clock_id="process_elapsed",
        origin=origin,
        evidence=(
            EvidenceRef(id=evidence, citation="Authored two-cell arithmetic reference", origin="synthetic"),
        ),
        metadata={"location": "abstract local demonstration, no field population", "event": "leak"},
    )
    grid = SpatialGrid(
        frame_id="authored_ENU",
        origin_description="Authored pipe ground origin",
        horizontal_datum="abstract local metres",
        vertical_datum="local ground z=0",
        x_edges_m=(-0.5, 0.5, 1.5),
        y_edges_m=(-0.5, 0.5),
        receptor_height_m=1.0,
        evidence_id=evidence,
    )
    source = {
        "annual_frequency_per_year": 0.02,
        "basis": "authored release occurrence rate, not observations",
    }
    rate = RiskFrequency(
        id="leak",
        annual_frequency_per_year=0.02,
        condition_id="release_initiated",
        endpoint="loss_of_containment",
        evidence_ids=(evidence,),
        context=context,
        source_payload=source,
        source_digest=digest(source),
    )
    conditions = []
    for name, probability, presence, states_data in (
        ("day", 0.25, (1.0, 0.5), (((0, 0), 0.25), ((2, 0), 0.25), ((1, 1), 0.5))),
        ("night", 0.75, (0.5, 0.0), (((0, 0), 0.5), ((1, 0), 0.5))),
    ):
        population = tuple(
            SpatialPopulation(
                cell_id=f"cell:0:{i}",
                receptor_id=f"R{i}",
                persons=n,
                presence_probability=presence[i],
                evidence_id=evidence,
            )
            for i, n in enumerate((2, 1))
        )
        field_source = {
            "schema": "pipeline_integrity.declared_spatial_harm.v1",
            "event_id": "leak",
            "condition_id": name,
            "initiating_condition_id": "release_initiated",
            "context": context.model_dump(mode="json"),
            "grid_digest": digest(grid),
            "clock_id": context.clock_id,
            "window_start_s": 10.0,
            "window_finish_s": 20.0,
            "harm_units": "1",
            "conditional_on": "event_and_condition_without_presence_or_frequency",
            "receptors": [
                {"id": f"R{i}", "position_enu_m": cell["position_enu_m"], "probability": p}
                for i, (cell, p) in enumerate(zip(grid.cells(), (0.5, 1.0), strict=True))
            ],
            "provenance": "Authored marginal probabilities; not an exposure-response model",
        }
        field = SpatialHarmField(
            source_kind="declared_conditional_field",
            source_record=field_source,
            source_digest=digest(field_source),
            frame_id=grid.frame_id,
            evidence_id=evidence,
        )
        joint = None
        if with_joint:
            states = tuple(
                JointCasualtyState(
                    fatalities_by_cell={f"cell:0:{i}": n for i, n in enumerate(counts)}, probability=p
                )
                for counts, p in states_data
            )
            joint_source = {
                "schema": "pipeline_integrity.joint_spatial_casualties.v1",
                "event_id": "leak",
                "condition_id": name,
                "context_digest": digest(context),
                "grid_digest": digest(grid),
                "field_source_digest": field.source_digest,
                "population_digest": digest(population),
                "window_start_s": 10.0,
                "window_finish_s": 20.0,
                "includes_presence_once": True,
                "states": [x.model_dump(mode="json") for x in states],
                "provenance": "Authored correlated integer casualty law, separately reviewed; no independent-person assumption",
            }
            joint = JointCasualtyDistribution(
                states=states,
                dependence_evidence_id=evidence,
                source_record=joint_source,
                source_digest=digest(joint_source),
            )
        conditions.append(
            SpatialCondition(
                id=name,
                probability_given_event=probability,
                population=population,
                field=field,
                joint_casualties=joint,
                evidence_id=evidence,
            )
        )
    event = SpatialEvent(
        id="leak",
        initiating_condition_id="release_initiated",
        frequency=rate,
        conditions=tuple(conditions),
        partition_evidence_id=evidence,
        frequency_condition_independence_evidence_id=evidence,
    )
    request = SpatialRiskRequest(
        id="two_cell_reference",
        grid=grid,
        events=(event,),
        clock_id=context.clock_id,
        window_start_s=10.0,
        window_finish_s=20.0,
        time_scope_evidence_id=evidence,
        scenario_relation="distinct_event_types",
        nonoverlapping_event_counting_evidence_id=evidence,
        fn_thresholds=(1, 2, 3),
        reviews=dict.fromkeys(REVIEWS, reviewed),
        owner_pins=native_backend_metadata() if reviewed else {},
    )
    return request, context


def make_native_case(record, *, receptor_id="near", reviewed=False):
    """Retain an actual conditional consequence and append exposure evidence only."""
    from pipeline_integrity.consequence_exchange import ConsequenceResult

    original = ConsequenceResult.model_validate_json(json.dumps(record, allow_nan=False))
    original.check_integrity()
    evidence = "declared:spatial_native_mapping"
    context = original.context.model_copy(
        update={
            "evidence": original.context.evidence
            + (
                EvidenceRef(
                    id=evidence,
                    citation="Explicit representative receptor/cell and exposure mapping for retained native diagnostic",
                    origin="synthetic",
                ),
            )
        }
    )
    receptor = next(r for r in original.case.facility["receptors"] if r["id"] == receptor_id)
    x, y, z = receptor["position_enu_m"]
    grid = SpatialGrid(
        frame_id=original.case.facility["id"],
        origin_description="Exact retained facility ENU origin; geographic placement unqualified",
        horizontal_datum="retained facility local ENU metres",
        vertical_datum="retained facility ground datum",
        x_edges_m=(x - 0.05, x + 0.05),
        y_edges_m=(y - 0.05, y + 0.05),
        receptor_height_m=z,
        evidence_id=evidence,
    )
    event_id = original.source_record["native_packet"]["event_id"]
    rate_source = {
        "annual_frequency_per_year": 0.02,
        "basis": "authored initiating rate for the retained native field, not inferred from the process",
    }
    rate = RiskFrequency(
        id=event_id,
        annual_frequency_per_year=0.02,
        condition_id=original.case.initiating_condition_id,
        endpoint="loss_of_containment",
        evidence_ids=(evidence,),
        context=context,
        source_payload=rate_source,
        source_digest=digest(rate_source),
    )
    field = SpatialHarmField(
        source_kind="consequence_exchange",
        source_record=record,
        source_digest=digest(record),
        sensitivity_member="low",
        frame_id=grid.frame_id,
        evidence_id=evidence,
    )
    population = (
        SpatialPopulation(
            cell_id="cell:0:0",
            receptor_id=receptor_id,
            persons=2,
            presence_probability=0.5,
            evidence_id=evidence,
        ),
    )
    condition = SpatialCondition(
        id=original.case.id,
        probability_given_event=1.0,
        population=population,
        field=field,
        evidence_id=evidence,
    )
    event = SpatialEvent(
        id=event_id,
        initiating_condition_id=original.case.initiating_condition_id,
        frequency=rate,
        conditions=(condition,),
        partition_evidence_id=evidence,
        frequency_condition_independence_evidence_id=evidence,
    )
    offset = original.source_record["provider_time_origin_in_context_s"]
    times = original.native_request["evaluation_times_s"]
    return SpatialRiskRequest(
        id="retained_native_conditional_map",
        grid=grid,
        events=(event,),
        clock_id=context.clock_id,
        window_start_s=offset + times[0],
        window_finish_s=offset + times[-1],
        time_scope_evidence_id=evidence,
        scenario_relation="distinct_event_types",
        nonoverlapping_event_counting_evidence_id=evidence,
        reviews=dict.fromkeys(REVIEWS, reviewed),
        owner_pins=native_backend_metadata() if reviewed else {},
    ), context


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prism", type=Path, help="optional reviewed lh2-tno directory")
    parser.add_argument("--hra", type=Path, help="optional reviewed human-reliability-toolkit directory")
    parser.add_argument("--reviewed", action="store_true")
    parser.add_argument(
        "--consequence", type=Path, help="retained actual ConsequenceResult JSON; no synthetic replacement"
    )
    args = parser.parse_args()
    for source in (args.prism, args.hra):
        if source:
            sys.path.insert(0, str((source / "src").resolve()))
    request, context = (
        make_native_case(json.loads(args.consequence.read_text(encoding="utf-8")), reviewed=args.reviewed)
        if args.consequence
        else make_case(reviewed=args.reviewed)
    )
    print(json.dumps(evaluate_spatial_risk(request, context), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
