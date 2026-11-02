"""Resource-constrained paths on a reviewed planar survey/corridor graph.

Survey vertices and feasible straight corridor links are inputs. Terrain is
checked from their elevations; no unsurveyed terrain or permit status is inferred.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Literal
from pydantic import Field
from .contracts import Contract, Name, Positive, Nonnegative, RunContext, digest, require_context_extension
from .datasets import file_digest
from .lifecycle import MoneyBasis
from .route_planning import RoutePlanningRequest, RouteCandidate, RouteQuote, route_boundary_metadata

METRICS=("length_m","earthwork_m3","disturbed_area_m2","emissions_kg_co2e","cost","duration_s")


class RouteSurveyVertex(Contract):
    id: Name
    x_m: float
    y_m: float
    elevation_m: float
    evidence_id: Name


class RouteCorridorEdge(Contract):
    id: Name
    start_vertex_id: Name
    end_vertex_id: Name
    earthwork_m3: Nonnegative
    disturbed_area_m2: Nonnegative
    emissions_kg_co2e: Nonnegative
    cost: Nonnegative
    duration_s: Nonnegative
    crossing_counts: dict[str,int]
    feasible: bool | None
    evidence_id: Name


class RouteGenerationRequest(Contract):
    id: Name
    asset_id: Name
    clock_id: Name
    frame_id: Name
    coordinate_definition_id: Name
    money_basis: MoneyBasis
    vertices: tuple[RouteSurveyVertex,...] = Field(min_length=2,max_length=10000)
    edges: tuple[RouteCorridorEdge,...] = Field(min_length=1,max_length=30000)
    start_vertex_id: Name
    finish_vertex_id: Name
    objective: Literal["length_m","earthwork_m3","disturbed_area_m2","emissions_kg_co2e","cost","duration_s"]
    maximum_metrics: dict[str,Nonnegative] = Field(default_factory=dict)
    maximum_crossings: dict[str,int] = Field(default_factory=dict)
    maximum_absolute_grade: Positive
    maximum_turn_angle_rad: float = Field(gt=0,le=math.pi)
    number_of_candidates: int = Field(default=1,ge=1,le=50)
    solver_time_limit_s: Positive = 60.0
    maximum_turn_constraints: int = Field(default=100000,ge=1,le=1000000)
    maximum_turn_pairs_checked: int = Field(default=1000000,ge=1,le=10000000)
    maximum_result_bytes: int = Field(default=64000000,ge=1,le=1000000000)
    graph_geometry_terrain_and_complete_resource_inventory_reviewed: bool = False


class GeneratedRouteConditions(Contract):
    candidate_id: Name
    conditions: dict[Name,bool | None]
    evidence_id: Name
    reviewed: bool = False


def route_generation_metadata():
    return {name:file_digest(Path(__file__).with_name(name+".py"))
            for name in ("route_generation","route_planning","lifecycle")}


def evaluate_route_generation(request: RouteGenerationRequest, context: RunContext):
    import numpy as np
    import scipy
    from scipy.optimize import milp, Bounds, LinearConstraint
    from scipy.sparse import coo_matrix
    q=RouteGenerationRequest.model_validate(request.model_dump(mode="python"))
    c=RunContext.model_validate(context.model_dump(mode="python"))
    pins,q_pin,c_pin=route_generation_metadata(),digest(q),digest(c)
    if (q.asset_id,q.clock_id)!=(c.asset_id,c.clock_id):
        raise ValueError("route graph asset/clock differs from context")
    if not q.graph_geometry_terrain_and_complete_resource_inventory_reviewed:
        raise ValueError("survey geometry, corridor completeness and resource inventory require review")
    vs={v.id:v for v in q.vertices}
    if len(vs)!=len(q.vertices) or len({e.id for e in q.edges})!=len(q.edges):
        raise ValueError("route graph vertex/edge IDs must be unique")
    if q.start_vertex_id==q.finish_vertex_id or not {q.start_vertex_id,q.finish_vertex_id}<=set(vs):
        raise ValueError("route start/finish must be distinct existing graph vertices")
    if not set(q.maximum_metrics)<=set(METRICS) or any(v<0 for v in q.maximum_crossings.values()):
        raise ValueError("route resource constraints are invalid")
    evidence=[e.id for e in c.evidence]
    if len(evidence)!=len(set(evidence)) or not {v.evidence_id for v in (*q.vertices,*q.edges)}<=set(evidence):
        raise ValueError("route graph requires unambiguous actual geometry/resource evidence")
    edges=[]; resources=[]; vectors=[]; exclusions=[]
    for e in q.edges:
        if e.start_vertex_id==e.end_vertex_id or not {e.start_vertex_id,e.end_vertex_id}<=set(vs):
            raise ValueError("route edge requires distinct existing endpoints")
        if any(not key.strip() or count<0 for key,count in e.crossing_counts.items()):
            raise ValueError("route crossings require named nonnegative counts")
        if not set(q.maximum_crossings)<=set(e.crossing_counts):
            raise ValueError("every edge must explicitly declare all constrained crossing counts")
        a,b=vs[e.start_vertex_id],vs[e.end_vertex_id]
        delta=(b.x_m-a.x_m,b.y_m-a.y_m,b.elevation_m-a.elevation_m)
        horizontal=math.hypot(*delta[:2]); length=math.hypot(*delta)
        if not math.isfinite(length) or horizontal<=0:
            raise ValueError("route graph requires nonvertical finite-length corridor links")
        if e.feasible is None:
            raise ValueError("unresolved corridor feasibility prevents a shortest-path claim")
        if not e.feasible or abs(delta[2])/horizontal>q.maximum_absolute_grade:
            exclusions.append(dict(edge_id=e.id,reason="infeasible_or_grade_limit"))
            continue
        edges.append(e); vectors.append(delta)
        resources.append(dict(length_m=length,**{m:getattr(e,m) for m in METRICS[1:]}))
    if not edges:
        raise ValueError("no feasible corridor links remain")
    n=len(edges); index={v.id:i for i,v in enumerate(q.vertices)}
    outgoing={key:[] for key in index}
    incoming={key:[] for key in index}
    incidence={key:{} for key in index}
    for i,e in enumerate(edges):
        outgoing[e.start_vertex_id].append(i)
        incoming[e.end_vertex_id].append(i)
        incidence[e.start_vertex_id][i]=1
        incidence[e.end_vertex_id][i]=-1
    turn_pairs=sum(len(incoming[key])*len(outgoing[key]) for key in index)
    if turn_pairs>q.maximum_turn_pairs_checked:
        raise ValueError("route turn-pair inspection budget exceeded")
    rr=[]; cc=[]; data=[]; lower=[]; upper=[]
    def constraint(coefficients,lo,hi):
        row=len(lower)
        for column,value in coefficients.items():
            if value:
                rr.append(row); cc.append(column); data.append(float(value))
        lower.append(lo); upper.append(hi)
    for vertex in q.vertices:
        coefficients=incidence[vertex.id]
        net=1 if vertex.id==q.start_vertex_id else -1 if vertex.id==q.finish_vertex_id else 0
        constraint(coefficients,net,net)
    for metric,limit in q.maximum_metrics.items():
        constraint({i:r[metric] for i,r in enumerate(resources)},-np.inf,limit)
    for crossing,limit in q.maximum_crossings.items():
        constraint({i:e.crossing_counts[crossing] for i,e in enumerate(edges)},-np.inf,limit)
    turns=0
    for i,e in enumerate(edges):
        for j in outgoing[e.end_vertex_id]:
            cosine=max(-1.0,min(1.0,math.fsum(
                (a/resources[i]["length_m"])*(b/resources[j]["length_m"])
                for a,b in zip(vectors[i],vectors[j],strict=True))))
            if math.acos(cosine)>q.maximum_turn_angle_rad:
                turns+=1
                if turns>q.maximum_turn_constraints:
                    raise ValueError("route turn constraint budget exceeded")
                constraint({i:1,j:1},-np.inf,1)
    objective=np.array([r[q.objective] for r in resources])
    # A strictly positive objective avoids cost-neutral disjoint cycles. If a
    # supplied metric is zero, the caller must choose another meaningful metric.
    if np.any(objective<=0):
        raise ValueError("selected route objective must be strictly positive on every feasible edge")
    candidates=[]; outcomes=[]
    for candidate_index in range(q.number_of_candidates):
        matrix=coo_matrix((data,(rr,cc)),shape=(len(lower),n)).tocsc()
        try:
            result=milp(objective,integrality=np.ones(n),bounds=Bounds(np.zeros(n),np.ones(n)),
                        constraints=LinearConstraint(matrix,np.array(lower),np.array(upper)),
                        options=dict(time_limit=q.solver_time_limit_s,mip_rel_gap=0.0))
        except (ValueError,RuntimeError,ArithmeticError) as error:
            outcomes.append(dict(status=4,message=type(error).__name__+": "+str(error),optimal=False,objective=None))
            break
        outcomes.append(dict(status=int(result.status),message=str(result.message),
                             optimal=result.status==0,objective=None if result.fun is None else float(result.fun)))
        if result.status==2:
            break
        if result.status!=0 or result.x is None:
            break
        if (not np.all(np.isfinite(result.x)) or np.min(result.x)<-1e-6 or np.max(result.x)>1+1e-6
                or np.max(np.abs(result.x-np.rint(result.x)))>1e-6):
            raise ValueError("route solver returned a nonintegral path")
        chosen={i for i,x in enumerate(result.x) if x>0.5}
        chosen_outgoing={}
        for i in chosen:
            chosen_outgoing.setdefault(edges[i].start_vertex_id,[]).append(i)
        current=q.start_vertex_id; ordered=[]; visited={current}
        while current!=q.finish_vertex_id:
            choices=chosen_outgoing.get(current,())
            if len(choices)!=1:
                raise ValueError("optimized links do not form one directed route")
            i=choices[0]; ordered.append(i); current=edges[i].end_vertex_id
            if current in visited:
                raise ValueError("optimized route contains a cycle")
            visited.add(current)
        if set(ordered)!=chosen:
            raise ValueError("optimized solution contains disconnected corridor links")
        totals={metric:math.fsum(resources[i][metric] for i in ordered) for metric in METRICS}
        if result.fun is None or not math.isfinite(result.fun) or abs(totals[q.objective]-result.fun)>1e-7*max(1.0,abs(totals[q.objective])):
            raise ValueError("route solver objective differs from independently recovered path resources")
        crossing_kinds=sorted(set().union(*(set(edges[i].crossing_counts) for i in ordered)))
        counts={key:sum(edges[i].crossing_counts[key] for i in ordered)
                if all(key in edges[i].crossing_counts for i in ordered) else None for key in crossing_kinds}
        if any(totals[m]>lim+1e-7*max(1.0,lim) for m,lim in q.maximum_metrics.items()) or any(counts[k] is None or counts[k]>lim for k,lim in q.maximum_crossings.items()):
            raise ValueError("optimized route violates an independently recovered resource constraint")
        candidates.append(dict(id=q.id+"/path/"+str(candidate_index),edge_ids=[edges[i].id for i in ordered],
            vertex_ids=[q.start_vertex_id]+[edges[i].end_vertex_id for i in ordered],metrics=totals,
            crossing_counts=counts,objective_value=totals[q.objective]))
        constraint({i:1 for i in chosen},-np.inf,len(chosen)-1)
    status="computed_diagnostic" if candidates and all(o["status"] in (0,2) for o in outcomes) else "withheld"
    if route_generation_metadata()!=pins or digest(q)!=q_pin or digest(c)!=c_pin:
        raise ValueError("route generation source/request/context changed during optimization")
    body=dict(schema="pipeline_integrity.route_generation.v1",request=q.model_dump(mode="json"),
              context=c.model_dump(mode="json"),status=status,candidates=candidates,solver_outcomes=outcomes,
              excluded_edges=exclusions,turn_constraints=turns,turn_pairs_checked=turn_pairs,scipy_version=scipy.__version__,
              implementation_sha256=file_digest(Path(__file__)),
              implementation=pins,
              optimality_scope="supplied_directed_graph_and_declared_linear_resources",
              unsurveyed_terrain_or_permit_feasibility_inferred=False,bend_radius_qualification=False,
              numerical_validation_completed=False)
    body["record_digest"]=digest(body)
    if len(json.dumps(body,allow_nan=False).encode("utf-8"))>q.maximum_result_bytes:
        raise ValueError("route generation receipt exceeds byte budget")
    return body


def prepare_generated_route_planning(record, template: RoutePlanningRequest, context: RunContext, *,
                                     mapping_evidence_id: str,
                                     conditions: tuple[GeneratedRouteConditions,...] = ()):
    verify_route_generation_record(record)
    if record["status"]!="computed_diagnostic":
        raise ValueError("only a completed graph search can provide route candidates")
    source=RouteGenerationRequest.model_validate_json(json.dumps(record["request"]))
    base=RoutePlanningRequest.model_validate(template.model_dump(mode="python"))
    c=RunContext.model_validate(context.model_dump(mode="python"))
    source_context=RunContext.model_validate_json(json.dumps(record["context"]))
    require_context_extension(source_context,c)
    if (base.asset_id,base.clock_id)!=(c.asset_id,c.clock_id):
        raise ValueError("generated-route planning asset/clock differs from the receiving context")
    evidence={e.id:e for e in c.evidence}
    if base.evidence_id not in evidence:
        raise ValueError("receiving route-comparison case evidence is absent from context")
    if mapping_evidence_id not in evidence or evidence[mapping_evidence_id].sha256 is None:
        raise ValueError("graph-to-route service/ports/resources mapping requires source-hash-pinned review evidence")
    if (source.asset_id,source.clock_id,source.frame_id,source.coordinate_definition_id,digest(source.money_basis))!=(base.asset_id,base.clock_id,base.frame_id,base.coordinate_definition_id,digest(base.money_basis)):
        raise ValueError("generated routes and comparison require the same coordinate and monetary basis")
    condition_rows=tuple(GeneratedRouteConditions.model_validate(row.model_dump(mode="python")) for row in conditions)
    condition_map={row.candidate_id:row for row in condition_rows}
    if len(condition_map)!=len(condition_rows) or not set(condition_map)<={row["id"] for row in record["candidates"]}:
        raise ValueError("condition reviews require unique generated candidate IDs")
    if any(set(row.conditions)!=set(base.required_conditions) for row in condition_rows):
        raise ValueError("condition mappings must identify exactly the receiving route's required conditions")
    if any(row.evidence_id not in evidence or evidence[row.evidence_id].sha256 is None for row in condition_rows):
        raise ValueError("supplied route conditions require source-hash-pinned evidence")
    candidates=[]
    for row in record["candidates"]:
        metrics=row["metrics"]
        candidates.append(RouteCandidate(id=row["id"],geometry_definition_id=record["record_digest"]+"/"+row["id"],
            **{m:metrics[m] for m in METRICS if m!="cost"},crossing_counts=row["crossing_counts"],
            conditions=({key:None for key in base.required_conditions} if row["id"] not in condition_map
                        else condition_map[row["id"]].conditions),
            cost_items=(RouteQuote(id=row["id"]+"/cost",scope_component_id=row["id"],quantity=1.0,
                quantity_unit="lump",unit_rate=metrics["cost"],evidence_id=mapping_evidence_id),),
            evidence_id=mapping_evidence_id,geometry_and_quantities_reviewed=True,cost_inventory_reviewed=True,
            environmental_inventory_reviewed=True,
            conditions_reviewed=(not base.required_conditions if row["id"] not in condition_map
                                 else condition_map[row["id"]].reviewed)))
    payload=base.model_dump(mode="python"); payload["candidates"]=tuple(candidates)
    derived=RoutePlanningRequest.model_validate(payload)
    binding=dict(schema="pipeline_integrity.generated_route_binding.v1",source_record_digest=record["record_digest"],
                 template_digest=digest(base),derived_request_digest=digest(derived),mapping_evidence_id=mapping_evidence_id,
                 condition_mappings=[row.model_dump(mode="json") for row in condition_rows],
                 graph_to_ports=dict(start_vertex_id=source.start_vertex_id,start_port_id=base.start_port_id,
                                    finish_vertex_id=source.finish_vertex_id,finish_port_id=base.finish_port_id),
                 source_context_digest=digest(source_context),receiving_context_digest=digest(c),
                 preserved_source_evidence_ids=[e.id for e in source_context.evidence],
                 refreshed_context_metadata_keys=["route_boundary","route_generation_source"],
                 required_conditions_inferred=False,implementation=route_generation_metadata())
    binding["record_digest"]=digest(binding)
    cdata=c.model_dump(mode="python")
    cdata["metadata"]={**c.metadata,"route_boundary":route_boundary_metadata(derived),
                       "route_generation_source":dict(record_digest=record["record_digest"],
                                                       binding_digest=binding["record_digest"])}
    return derived,RunContext.model_validate(cdata),binding


def verify_route_generation_record(record, *, request=None, context=None):
    if digest({k:v for k,v in record.items() if k!="record_digest"})!=record.get("record_digest"):
        raise ValueError("route generation receipt changed")
    q=RouteGenerationRequest.model_validate_json(json.dumps(record["request"]))
    c=RunContext.model_validate_json(json.dumps(record["context"]))
    if request is not None and digest(request)!=digest(q):
        raise ValueError("route graph caller request differs")
    if context is not None and digest(context)!=digest(c):
        raise ValueError("route graph caller context differs")
    if any(row["status"] not in (0,2) for row in record["solver_outcomes"]):
        raise ValueError("unfinished route optimization does not establish replay")
    replay=evaluate_route_generation(q,c)
    if replay!=record:
        raise ValueError("route graph optimization replay differs")
    return True
