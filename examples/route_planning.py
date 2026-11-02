"""Fictional finite alternatives; no actual terrain, quote, or permit data."""

from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.lifecycle import MoneyBasis
from pipeline_integrity.route_planning import (
    RouteCandidate,
    RoutePlanningRequest,
    RouteQuote,
    route_boundary_metadata,
)


def make_case(*, reviewed=False, selection_mode="pareto_only"):
    candidates = []
    for name, length, excavation, footprint, price, crossings in (
        ("short", 100, 250, 400, 200, 1),
        ("detour", 150, 120, 250, 260, 0),
        ("dominated", 180, 300, 500, 300, 2),
    ):
        candidates.append(
            RouteCandidate(
                id=name,
                geometry_definition_id="authored_" + name,
                length_m=length,
                earthwork_m3=excavation,
                disturbed_area_m2=footprint,
                emissions_kg_co2e=price,
                duration_s=10000,
                crossing_counts={"road": crossings},
                conditions={"access_available": True},
                cost_items=(
                    RouteQuote(
                        id="all_scope",
                        scope_component_id="all_construction",
                        quantity=1,
                        quantity_unit="lump",
                        unit_rate=price,
                        evidence_id="authored",
                    ),
                ),
                evidence_id="authored",
                geometry_and_quantities_reviewed=reviewed,
                cost_inventory_reviewed=reviewed,
                environmental_inventory_reviewed=reviewed,
                conditions_reviewed=reviewed,
            )
        )
    q = RoutePlanningRequest(
        id="authored_routes",
        asset_id="authored_pipeline",
        clock_id="planning_snapshot",
        time_s=0,
        frame_id="local_xyz",
        coordinate_definition_id="authored_xyz_m",
        start_port_id="source",
        finish_port_id="destination",
        common_service_definition_id="same_supplied_service",
        common_cost_boundary_id="authored_construction_only",
        common_environmental_boundary_id="authored_direct_construction",
        emission_metric_id="authored_same_co2e_basis",
        supplied_case_id="deterministic_authored_case",
        money_basis=MoneyBasis(currency="USD", base_year=2026, prices="constant"),
        candidates=tuple(candidates),
        objectives=("cost", "earthwork_m3"),
        maximum_crossings={"road": 2},
        required_conditions=("access_available",),
        selection_mode=selection_mode,
        selection_order=("cost", "earthwork_m3") if selection_mode == "lexicographic" else (),
        case_and_comparability_reviewed=reviewed,
        evidence_id="authored",
    )
    c = RunContext(
        asset_id=q.asset_id,
        scenario_id="authored_route_comparison",
        clock_id=q.clock_id,
        origin="synthetic",
        evidence=(
            EvidenceRef(
                id="authored",
                citation="Original fictional routes and quotes, not field evidence",
                origin="synthetic",
            ),
        ),
        metadata={"route_boundary": route_boundary_metadata(q)},
    )
    return q, c
