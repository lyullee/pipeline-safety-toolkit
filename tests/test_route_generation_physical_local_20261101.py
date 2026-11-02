"""Independent exhaustive path reference for the reviewed route graph optimizer."""
import math
import pytest
from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.lifecycle import MoneyBasis
from pipeline_integrity.route_generation import (
    RouteCorridorEdge, RouteGenerationRequest, RouteSurveyVertex,
    evaluate_route_generation, verify_route_generation_record,
)


def case(*, reviewed=True, max_grade=1.0):
    ev = tuple(EvidenceRef(id=f"survey:{x}", citation=f"Authored synthetic {x}", origin="synthetic")
               for x in ("A", "B", "C", "D", "ab", "bd", "ac", "cd", "ad"))
    vertices = (
        RouteSurveyVertex(id="A", x_m=0.0, y_m=0.0, elevation_m=0.0, evidence_id="survey:A"),
        RouteSurveyVertex(id="B", x_m=2.0, y_m=0.0, elevation_m=0.0, evidence_id="survey:B"),
        RouteSurveyVertex(id="C", x_m=2.0, y_m=4.0, elevation_m=0.0, evidence_id="survey:C"),
        RouteSurveyVertex(id="D", x_m=4.0, y_m=0.0, elevation_m=0.0, evidence_id="survey:D"),
    )
    def edge(name, a, b, crossings):
        return RouteCorridorEdge(id=name, start_vertex_id=a, end_vertex_id=b,
            earthwork_m3=0.0, disturbed_area_m2=0.0, emissions_kg_co2e=0.0,
            cost=0.0, duration_s=0.0, crossing_counts={"road":crossings},
            feasible=True, evidence_id=f"survey:{name}")
    request=RouteGenerationRequest(id="route-ref",asset_id="pipe",clock_id="clock",
      frame_id="ENU",coordinate_definition_id="synthetic-metre-grid",
      money_basis=MoneyBasis(currency="USD",base_year=2020,prices="constant"),
      vertices=vertices,edges=(edge("ab","A","B",1),edge("bd","B","D",1),
          edge("ac","A","C",0),edge("cd","C","D",0),edge("ad","A","D",2)),
      start_vertex_id="A",finish_vertex_id="D",objective="length_m",
      maximum_crossings={"road":1},maximum_absolute_grade=max_grade,
      maximum_turn_angle_rad=math.pi,number_of_candidates=1,
      graph_geometry_terrain_and_complete_resource_inventory_reviewed=reviewed)
    context=RunContext(asset_id="pipe",scenario_id="route-ref",clock_id="clock",origin="synthetic",evidence=ev)
    return request,context


def test_milp_path_matches_exhaustive_geometry_and_crossing_reference():
    q,c=case()
    record=evaluate_route_generation(q,c)
    expected={"A-C-D":math.hypot(2,4)+math.hypot(2,-4)}
    independently_enumerated={"A-B-D":2+2,"A-C-D":math.hypot(2,4)+math.hypot(2,-4),"A-D":4}
    feasible={path:length for path,length in independently_enumerated.items()
              if path!="A-B-D" and path!="A-D"}
    assert min(feasible.values())==pytest.approx(expected["A-C-D"],rel=1e-14)
    assert record["status"]=="computed_diagnostic"
    assert record["candidates"][0]["vertex_ids"]==["A","C","D"]
    assert record["candidates"][0]["edge_ids"]==["ac","cd"]
    assert record["candidates"][0]["metrics"]["length_m"]==pytest.approx(expected["A-C-D"],rel=1e-12)
    assert record["candidates"][0]["crossing_counts"]=={"road":0}
    assert record["unsurveyed_terrain_or_permit_feasibility_inferred"] is False
    assert verify_route_generation_record(record,request=q,context=c)


def test_unreviewed_survey_graph_is_not_optimized():
    q,c=case(reviewed=False)
    with pytest.raises(ValueError,match="require review"):
        evaluate_route_generation(q,c)


def test_grade_limit_excludes_steep_links_before_path_selection():
    q,c=case(max_grade=0.1)
    vertices=tuple(v.model_copy(update={"elevation_m":1.0}) if v.id=="B" else v for v in q.vertices)
    q=q.model_copy(update={"vertices":vertices})
    record=evaluate_route_generation(q,c)
    assert record["candidates"][0]["vertex_ids"]==["A","C","D"]
    assert record["candidates"][0]["metrics"]["length_m"]==pytest.approx(2*math.sqrt(20),rel=1e-12)
    assert {row["edge_id"] for row in record["excluded_edges"]}=={"ab","bd"}
