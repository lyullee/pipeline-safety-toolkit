"""Nurion references for graph MILP and native finite-EA suspended spans.

Graph paths are enumerated without an optimization library. Catenary references
integrate material-coordinate force balance analytically for equal-height ends.
No MILP, MoorPy, binding function or receiving solver is replaced in these tests.
"""

import math
from copy import deepcopy

import pytest

from pipeline_integrity.contracts import EvidenceRef, RunContext, digest
from pipeline_integrity.elastic_pull import (
    ElasticPullKnot,
    ElasticPullRequest,
    bind_elastic_pull_context,
    elastic_pull_backend_metadata,
    evaluate_elastic_pull,
    prepare_elastic_span_guide_pull,
    verify_elastic_pull_record,
)
from pipeline_integrity.lifecycle import MoneyBasis
from pipeline_integrity.pulling import PullingLimits, PullingRequest, PullingSegment, evaluate_pulling
from pipeline_integrity.route_generation import (
    METRICS,
    GeneratedRouteConditions,
    RouteCorridorEdge,
    RouteGenerationRequest,
    RouteSurveyVertex,
    evaluate_route_generation,
    prepare_generated_route_planning,
    verify_route_generation_record,
)
from pipeline_integrity.route_planning import (
    RouteCandidate,
    RoutePlanningRequest,
    evaluate_routes,
    route_boundary_metadata,
)


def evidence_context(asset, clock):
    evidence = tuple(
        EvidenceRef(id=name, citation="Authored validation " + name, origin="synthetic", sha256=digest(name))
        for name in ("survey", "mapping")
    )
    return RunContext(
        asset_id=asset, clock_id=clock, scenario_id="declared-reference",
        origin="synthetic", evidence=evidence, metadata={"survey_definition": "authored-local-frame"},
    )


def graph_case(*, road_limit=1, turn_limit=math.pi, earthwork_limit=100.0):
    positions = {
        "s": (0.0, 0.0, 0.0), "a": (1.0, 0.0, 0.0), "b": (1.0, 1.0, 0.0),
        "c": (1.0, -1.0, 0.0), "d": (2.0, 0.0, 0.0), "f": (3.0, 0.0, 0.0),
        "steep": (1.5, 0.0, 3.0),
    }
    vertices = tuple(
        RouteSurveyVertex(id=name, x_m=x, y_m=y, elevation_m=z, evidence_id="survey")
        for name, (x, y, z) in positions.items()
    )
    edges = []
    for name, start, end, cost, earth, road, feasible in (
        ("sa", "s", "a", 1.0, 1.0, 1, True),
        ("af", "a", "f", 1.0, 1.0, 1, True),
        ("sb", "s", "b", 2.0, 2.0, 0, True),
        ("bf", "b", "f", 2.0, 2.0, 0, True),
        ("sc", "s", "c", 3.0, 1.0, 0, True),
        ("cf", "c", "f", 3.0, 1.0, 0, True),
        ("ab", "a", "b", 0.8, 0.5, 0, True),
        ("ba", "b", "a", 0.7, 0.5, 0, True),
        ("ad", "a", "d", 1.0, 1.0, 0, True),
        ("bd", "b", "d", 0.8, 0.5, 0, True),
        ("df", "d", "f", 0.8, 0.5, 0, True),
        ("steep-in", "s", "steep", 0.1, 0.1, 0, True),
        ("steep-out", "steep", "f", 0.1, 0.1, 0, True),
        ("known-closed", "s", "f", 0.05, 0.1, 0, False),
    ):
        edges.append(RouteCorridorEdge(
            id=name, start_vertex_id=start, end_vertex_id=end, cost=cost, earthwork_m3=earth,
            disturbed_area_m2=2.0 * earth, emissions_kg_co2e=3.0 * cost, duration_s=5.0 * cost,
            crossing_counts={"road": road}, feasible=feasible, evidence_id="survey",
        ))
    request = RouteGenerationRequest(
        id="small-directed-survey", asset_id="route-reference", clock_id="planning-snapshot",
        frame_id="local-xyz", coordinate_definition_id="authored-metre-frame",
        money_basis=MoneyBasis(currency="USD", base_year=2026, prices="constant"),
        vertices=vertices, edges=tuple(edges), start_vertex_id="s", finish_vertex_id="f",
        objective="cost", maximum_metrics={"earthwork_m3": earthwork_limit},
        maximum_crossings={"road": road_limit}, maximum_absolute_grade=0.5,
        maximum_turn_angle_rad=turn_limit, number_of_candidates=3,
        graph_geometry_terrain_and_complete_resource_inventory_reviewed=True,
    )
    return request, evidence_context(request.asset_id, request.clock_id)


def brute_force_paths(request):
    """Independent simple-path enumeration, vector angles and resource accounting."""
    points = {v.id: (v.x_m, v.y_m, v.elevation_m) for v in request.vertices}
    adjacency = {name: [] for name in points}
    for edge in request.edges:
        if edge.feasible:
            adjacency[edge.start_vertex_id].append(edge)
    accepted = []

    def visit(vertex, visited, path):
        if vertex == request.finish_vertex_id:
            vectors = [tuple(b - a for a, b in zip(points[e.start_vertex_id], points[e.end_vertex_id])) for e in path]
            lengths = [math.sqrt(sum(v * v for v in direction)) for direction in vectors]
            if any(abs(v[2]) > request.maximum_absolute_grade * math.sqrt(v[0] ** 2 + v[1] ** 2) for v in vectors):
                return
            for i in range(1, len(vectors)):
                cosine = sum(a * b for a, b in zip(vectors[i - 1], vectors[i])) / (lengths[i - 1] * lengths[i])
                if math.acos(max(-1.0, min(1.0, cosine))) > request.maximum_turn_angle_rad + 1e-13:
                    return
            resources = {name: sum(getattr(edge, name) for edge in path) for name in METRICS if name != "length_m"}
            resources["length_m"] = sum(lengths)
            crossings = {name: sum(edge.crossing_counts[name] for edge in path) for name in request.maximum_crossings}
            if any(resources[name] > limit for name, limit in request.maximum_metrics.items()):
                return
            if any(crossings[name] > limit for name, limit in request.maximum_crossings.items()):
                return
            accepted.append({"edge_ids": tuple(e.id for e in path), "metrics": resources, "crossings": crossings})
            return
        for edge in adjacency[vertex]:
            if edge.end_vertex_id not in visited:
                visit(edge.end_vertex_id, visited | {edge.end_vertex_id}, (*path, edge))

    visit(request.start_vertex_id, {request.start_vertex_id}, ())
    return sorted(accepted, key=lambda row: row["metrics"][request.objective])


@pytest.mark.parametrize("road_limit,turn_limit,earthwork_limit", [
    (1, math.pi, 100.0),
    (0, math.radians(80.0), 100.0),
    (0, math.pi, 2.5),
])
def test_actual_milp_matches_bruteforce_simple_paths_under_crossing_turn_and_resource_constraints(
    road_limit, turn_limit, earthwork_limit,
):
    request, context = graph_case(road_limit=road_limit, turn_limit=turn_limit, earthwork_limit=earthwork_limit)
    independent = brute_force_paths(request)
    record = evaluate_route_generation(request, context)
    expected = independent[:request.number_of_candidates]
    assert record["status"] == "computed_diagnostic" and len(record["candidates"]) == len(expected)
    assert [tuple(row["edge_ids"]) for row in record["candidates"]] == [row["edge_ids"] for row in expected]
    for actual, reference in zip(record["candidates"], expected, strict=True):
        assert actual["metrics"] == pytest.approx(reference["metrics"])
        assert actual["crossing_counts"] == reference["crossings"]
        assert actual["objective_value"] == pytest.approx(reference["metrics"]["cost"])
    assert {row["edge_id"] for row in record["excluded_edges"]} == {"steep-in", "steep-out", "known-closed"}
    assert record["unsurveyed_terrain_or_permit_feasibility_inferred"] is False
    assert verify_route_generation_record(record, request=request, context=context)


def planning_template(source):
    placeholder = RouteCandidate(
        id="placeholder", geometry_definition_id="unused-template-geometry", length_m=1.0,
        earthwork_m3=1.0, disturbed_area_m2=1.0, emissions_kg_co2e=1.0, duration_s=1.0,
        crossing_counts={"road": 0}, conditions={"access_available": None}, cost_items=(), evidence_id="mapping",
    )
    return RoutePlanningRequest(
        id="receiving-comparison", asset_id=source.asset_id, clock_id=source.clock_id, time_s=0.0,
        frame_id=source.frame_id, coordinate_definition_id=source.coordinate_definition_id,
        start_port_id="source-port", finish_port_id="delivery-port", common_service_definition_id="same-service",
        common_cost_boundary_id="survey-construction", common_environmental_boundary_id="construction-only",
        emission_metric_id="declared-co2e", supplied_case_id="authored-survey-case", money_basis=source.money_basis,
        candidates=(placeholder,), objectives=("cost",), required_conditions=("access_available",),
        case_and_comparability_reviewed=True, evidence_id="mapping",
    )


def test_generated_route_binder_retains_resource_prices_context_and_unreviewed_conditions():
    request, context = graph_case()
    record = evaluate_route_generation(request, context)
    template = planning_template(request)
    derived, bound, receipt = prepare_generated_route_planning(record, template, context, mapping_evidence_id="mapping")
    assert bound.evidence == context.evidence and bound.metadata["survey_definition"] == context.metadata["survey_definition"]
    assert bound.metadata["route_boundary"] == route_boundary_metadata(derived)
    assert bound.metadata["route_generation_source"]["binding_digest"] == receipt["record_digest"]
    assert receipt["source_record_digest"] == record["record_digest"]
    assert receipt["required_conditions_inferred"] is False
    assert receipt["graph_to_ports"] == {
        "start_vertex_id": "s", "start_port_id": "source-port", "finish_vertex_id": "f", "finish_port_id": "delivery-port",
    }
    for candidate, source in zip(derived.candidates, record["candidates"], strict=True):
        assert candidate.conditions == {"access_available": None} and not candidate.conditions_reviewed
        assert candidate.length_m == pytest.approx(source["metrics"]["length_m"])
        assert candidate.cost_items[0].unit_rate == pytest.approx(source["metrics"]["cost"])
    withheld = evaluate_routes(derived, bound)
    assert withheld["status"] == "needs_data" and withheld["selected_candidate_ids"] is None
    conditions = tuple(GeneratedRouteConditions(
        candidate_id=row["id"], conditions={"access_available": True}, evidence_id="mapping", reviewed=True,
    ) for row in record["candidates"])
    accepted, receiving, _ = prepare_generated_route_planning(
        record, template, context, mapping_evidence_id="mapping", conditions=conditions,
    )
    comparison = evaluate_routes(accepted, receiving)
    assert comparison["status"] == "computed_diagnostic"
    assert comparison["pareto_candidate_ids"] == [record["candidates"][0]["id"]]
    assert comparison["global_terrain_optimum"] is False
    wrong = context.model_copy(update={"evidence": context.evidence[:1]})
    with pytest.raises(ValueError, match="context|mapping|evidence"):
        prepare_generated_route_planning(record, template, wrong, mapping_evidence_id="mapping")


def symmetric_span_reference(horizontal_force, *, length=10.0, weight=10.0, rigidity=100000.0):
    """Integrals of T(s)^2/(2 EA), T(s)/EA and load potential on [-L/2,L/2]."""
    half = length / 2.0
    end_tension = math.sqrt(horizontal_force**2 + (weight * half)**2)
    integral_tension = half * end_tension + horizontal_force**2 / weight * math.asinh(weight * half / horizontal_force)
    span = 2.0 * horizontal_force / weight * math.asinh(weight * half / horizontal_force) + horizontal_force * length / rigidity
    elastic = (horizontal_force**2 * length + weight**2 * length**3 / 12.0) / (2.0 * rigidity)
    potential = integral_tension - length * end_tension - weight**2 * length**3 / (12.0 * rigidity)
    return dict(span=span, end_tension=end_tension, extension=integral_tension / rigidity,
                elastic=elastic, potential=potential, stored=elastic + potential)


def suspended_case(*, horizontal_force=500.0, height_translation=0.0, final_horizontal_force=None, subdivisions=8):
    reference = symmetric_span_reference(horizontal_force)
    knots = [ElasticPullKnot(
        id="initial", time_s=0.0, start_position_m=(0.0, 0.0, 0.0), end_position_m=(reference["span"], 0.0, 0.0),
    )]
    if height_translation or final_horizontal_force is not None:
        span = reference["span"] if final_horizontal_force is None else symmetric_span_reference(final_horizontal_force)["span"]
        knots.append(ElasticPullKnot(
            id="moved", time_s=1.0, start_position_m=(0.0, 0.0, height_translation),
            end_position_m=(span, 0.0, height_translation),
        ))
    request = ElasticPullRequest(
        id="symmetric-material-coordinate-catenary", asset_id="suspended-span", clock_id="quasistatic-s",
        frame_id="local-xyz", coordinate_definition_id="authored-metre-frame", start_port_id="anchor", end_port_id="span-end",
        unstretched_length_m=10.0, axial_rigidity_EA_N=100000.0,
        distributed_force_N_per_unstretched_m=(0.0, 0.0, -10.0), knots=tuple(knots),
        subdivisions_per_motion_interval=subdivisions, profile_nodes=129, maximum_axial_strain=0.02,
        position_tolerance_m=1e-8, force_tolerance_N=1e-5,
        absolute_work_energy_tolerance_J=1.0, relative_work_energy_tolerance=1e-6,
        geometry_evidence_id="survey", axial_rigidity_evidence_id="survey", load_evidence_id="survey",
        fully_suspended_linear_axial_quasistatic_model_reviewed=True,
        native_backend_pins=elastic_pull_backend_metadata(),
    )
    context = bind_elastic_pull_context(request, evidence_context(request.asset_id, request.clock_id))
    return request, context


@pytest.mark.parametrize("horizontal_force", [500.0, 1000.0])
def test_native_moorpy_symmetric_finite_ea_forces_extension_and_energy_match_independent_integrals(horizontal_force):
    request, context = suspended_case(horizontal_force=horizontal_force)
    record = evaluate_elastic_pull(request, context)
    assert record["status"] == "computed_diagnostic" and record["native_calls"] == 1
    state = record["states"][0]
    reference = symmetric_span_reference(horizontal_force)
    assert state["native_profile_type"] == 1
    assert state["force_on_start_support_N"] == pytest.approx((horizontal_force, 0.0, -50.0), abs=2e-4)
    assert state["force_on_end_support_N"] == pytest.approx((-horizontal_force, 0.0, -50.0), abs=2e-4)
    assert state["exact_uniform_load_peak_tension_N"] == pytest.approx(reference["end_tension"], rel=5e-7)
    assert state["force_balance_residual_N"] == pytest.approx((0.0, 0.0, 0.0), abs=1e-5)
    assert state["axial_extension_m"] == pytest.approx(reference["extension"], rel=5e-7)
    assert state["elastic_strain_energy_J"] == pytest.approx(reference["elastic"], rel=1e-6)
    assert state["distributed_load_potential_J"] == pytest.approx(reference["potential"], abs=2e-5)
    assert state["stored_plus_potential_energy_J"] == pytest.approx(reference["stored"], abs=4e-5)
    for coordinate, tension, position in zip(
        state["profile_material_coordinate_m"], state["profile_tension_N"], state["profile_position_m"], strict=True,
    ):
        centred = coordinate - request.unstretched_length_m / 2.0
        independent_tension = math.sqrt(horizontal_force**2 + (10.0 * centred)**2)
        independent_z = (independent_tension - reference["end_tension"]) / 10.0 + 10.0 * (centred**2 - 25.0) / 200000.0
        assert tension == pytest.approx(independent_tension, rel=5e-7)
        assert position[2] == pytest.approx(independent_z, abs=2e-6)
    assert record["ground_contact_solved"] is False and record["bending_EI_solved"] is False
    assert verify_elastic_pull_record(record, request=request, context=context)


def test_native_rigid_vertical_translation_has_exact_load_potential_and_endpoint_work():
    request, context = suspended_case(height_translation=0.25)
    record = evaluate_elastic_pull(request, context)
    assert record["status"] == "computed_diagnostic"
    initial = record["states"][0]
    for state in record["states"]:
        lift = state["start_position_m"][2]
        assert state["force_on_start_support_N"] == pytest.approx(initial["force_on_start_support_N"], abs=1e-5)
        assert state["elastic_strain_energy_J"] == pytest.approx(initial["elastic_strain_energy_J"], abs=1e-5)
        assert state["distributed_load_potential_J"] - initial["distributed_load_potential_J"] == pytest.approx(100.0 * lift, abs=1e-5)
        assert state["cumulative_endpoint_work_J"] == pytest.approx(100.0 * lift, abs=1e-5)
        assert state["work_energy_residual_J"] == pytest.approx(0.0, abs=2e-5)


def test_native_endpoint_opening_work_refines_toward_independent_finite_ea_energy_difference():
    reference_change = symmetric_span_reference(1000.0)["stored"] - symmetric_span_reference(500.0)["stored"]
    errors = []
    for subdivisions in (8, 16):
        request, context = suspended_case(final_horizontal_force=1000.0, subdivisions=subdivisions)
        record = evaluate_elastic_pull(request, context)
        assert record["status"] == "computed_diagnostic"
        first, last = record["states"][0], record["states"][-1]
        assert last["stored_plus_potential_energy_J"] - first["stored_plus_potential_energy_J"] == pytest.approx(reference_change, abs=8e-5)
        errors.append(abs(last["cumulative_endpoint_work_J"] - reference_change))
    assert errors[0] > 1e-4
    assert errors[1] < 0.4 * errors[0] + 1e-5


def test_elastic_span_guide_binder_transfers_only_aligned_snapshot_tension():
    request, context = suspended_case()
    record = evaluate_elastic_pull(request, context)
    state = record["states"][0]
    tension = math.hypot(*state["force_on_end_support_N"])
    tangent = tuple(-force / tension for force in state["force_on_end_support_N"])
    segment = PullingSegment(
        id="massless-straight-guide", start_m=tuple(state["end_position_m"]), initial_tangent=tangent,
        length_m=1.0, sliding_friction=0.0, immersed_fraction=0.0,
        external_fluid_density_kg_m3=0.0, axial_resistance_N_m=0.0, evidence_id="mapping",
    )
    template = PullingRequest(
        id="receiving-static-guide", asset_id=request.asset_id, clock_id=request.clock_id, time_s=0.0,
        frame_id=request.frame_id, coordinate_definition_id=request.coordinate_definition_id,
        trailing_port_id=request.end_port_id, puller_port_id="guide-out", outer_diameter_m=0.1,
        pipe_mass_kg_m=0.0, contents_mass_kg_m=0.0, displaced_area_m2=0.0,
        gravity_m_s2=(0.0, 0.0, -9.80665), trailing_tension_N=1.0, power_budget_required=False,
        segments=(segment,), operating_case_id="separate-massless-static-guide",
        geometry_evidence_id="mapping", loading_evidence_id="mapping", geometry_reviewed=True,
        flexible_line_model_reviewed=True, steady_sliding_and_guide_contact_reviewed=True,
        load_and_buoyancy_basis_reviewed=True, maximum_step_m=0.1,
        limits=PullingLimits(
            effective_axial_tension_limit_N=10000.0, minimum_bend_radius_m=0.1,
            maximum_geometric_outer_fiber_strain=0.1, applicability_id="authored-guide-only",
            evidence_id="mapping", reviewed=True,
        ),
    )
    derived, bound, receipt = prepare_elastic_span_guide_pull(
        record, template, context, state_index=0, mapping_evidence_id="mapping",
        static_effective_tension_transfer_reviewed=True,
    )
    assert derived.trailing_tension_N == pytest.approx(symmetric_span_reference(500.0)["end_tension"], rel=5e-7)
    assert receipt["snapshot_force_transfer_only"] is True
    assert receipt["throughput_power_from_endpoint_work_inferred"] is False
    assert bound.evidence == context.evidence and bound.metadata["elastic_pull_boundary"] == context.metadata["elastic_pull_boundary"]
    receiving = evaluate_pulling(derived, bound)
    assert receiving["status"] == "computed_diagnostic"
    assert receiving["solution"]["puller_tension_N"] == pytest.approx(derived.trailing_tension_N, abs=1e-6)
    assert receiving["solution"]["power_budget"] is None
    mismatched = template.model_copy(update={"segments": (segment.model_copy(update={"start_m": (0.0, 0.0, 0.0)}),)})
    with pytest.raises(ValueError, match="position and tangent"):
        prepare_elastic_span_guide_pull(record, mismatched, context, state_index=0, mapping_evidence_id="mapping", static_effective_tension_transfer_reviewed=True)
    changed = deepcopy(record)
    changed["states"][0]["force_on_end_support_N"][0] += 1.0
    with pytest.raises(ValueError, match="receipt changed"):
        verify_elastic_pull_record(changed)
