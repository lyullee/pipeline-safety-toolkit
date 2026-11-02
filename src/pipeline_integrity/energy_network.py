"""Steady junction energy mixing for prescribed conservative network mass flows.

The transported scalar is specific total flow energy h+v^2/2+g*z, on one common
fluid/composition/reference basis. Hydraulic flow directions and heat inputs
are supplied. This solver does not close pressure/temperature/property feedback.
"""
from __future__ import annotations

import json
import math
import warnings
from pathlib import Path
from pydantic import Field, model_validator
from .contracts import Contract, Name, Positive, Nonnegative, RunContext, digest
from .datasets import file_digest


class EnergyMixingNode(Contract):
    id: Name
    supplied_mass_flow_kg_s: Nonnegative = 0.0
    supplied_specific_total_energy_J_kg: float | None = None
    withdrawn_mass_flow_kg_s: Nonnegative = 0.0
    heat_into_node_W: float = 0.0
    evidence_id: Name

    @model_validator(mode="after")
    def source(self):
        if self.supplied_mass_flow_kg_s>0 and self.supplied_specific_total_energy_J_kg is None:
            raise ValueError("a mass injection requires its explicit specific total flow energy")
        return self


class EnergyMixingEdge(Contract):
    id: Name
    upstream_node_id: Name
    downstream_node_id: Name
    mass_flow_kg_s: Nonnegative
    heat_into_flow_W: float = 0.0
    evidence_id: Name

    @model_validator(mode="after")
    def directed(self):
        if self.upstream_node_id==self.downstream_node_id:
            raise ValueError("mixing-network edges require distinct nodes")
        if self.mass_flow_kg_s==0 and self.heat_into_flow_W!=0:
            raise ValueError("zero-flow edges cannot carry a steady advective heat increment")
        return self


class EnergyNetworkRequest(Contract):
    id: Name
    asset_id: Name
    clock_id: Name
    time_s: Nonnegative
    fluid_composition_id: Name
    total_energy_reference_id: Name
    hydraulic_source_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    nodes: tuple[EnergyMixingNode, ...] = Field(min_length=2,max_length=2000)
    edges: tuple[EnergyMixingEdge, ...] = Field(min_length=1,max_length=10000)
    absolute_mass_balance_tolerance_kg_s: Positive = 1e-10
    relative_mass_balance_tolerance: Nonnegative = Field(default=1e-9,le=1e-3)
    absolute_energy_balance_tolerance_W: Positive = 1e-6
    relative_energy_balance_tolerance: Nonnegative = Field(default=1e-9,le=1e-3)
    fixed_flow_complete_mixing_and_common_energy_basis_reviewed: bool = False
    evidence_ids: tuple[Name, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def graph(self):
        known={n.id for n in self.nodes}
        if len(known)!=len(self.nodes) or len({e.id for e in self.edges})!=len(self.edges):
            raise ValueError("mixing-network node and edge IDs must be unique")
        if any(e.upstream_node_id not in known or e.downstream_node_id not in known for e in self.edges):
            raise ValueError("energy edges must reference existing nodes")
        return self


def evaluate_energy_network(request: EnergyNetworkRequest, context: RunContext):
    import numpy as np
    from scipy.sparse import coo_matrix
    from scipy.sparse.linalg import spsolve, MatrixRankWarning
    from importlib.metadata import version
    q=EnergyNetworkRequest.model_validate(request.model_dump(mode="python"))
    c=RunContext.model_validate(context.model_dump(mode="python"))
    if not q.fixed_flow_complete_mixing_and_common_energy_basis_reviewed:
        raise ValueError("fixed-flow mixing and common total-energy basis require review")
    if (q.asset_id,q.clock_id)!=(c.asset_id,c.clock_id):
        raise ValueError("energy-network asset/clock differs")
    required=set(q.evidence_ids)|{v.evidence_id for v in (*q.nodes,*q.edges)}
    if not required <= {e.id for e in c.evidence}:
        raise ValueError("energy-network hydraulic/thermal/basis evidence is absent")
    n=len(q.nodes)
    indices={node.id:i for i,node in enumerate(q.nodes)}
    incoming=[[] for _ in range(n)]
    outgoing=[[] for _ in range(n)]
    for edge in q.edges:
        incoming[indices[edge.downstream_node_id]].append(edge)
        outgoing[indices[edge.upstream_node_id]].append(edge)
    rows,cols,data=[],[],[]
    rhs=np.zeros(n)
    mass_audits=[]
    for i,node in enumerate(q.nodes):
        entering=math.fsum(e.mass_flow_kg_s for e in incoming[i])+node.supplied_mass_flow_kg_s
        leaving=math.fsum(e.mass_flow_kg_s for e in outgoing[i])+node.withdrawn_mass_flow_kg_s
        residual=entering-leaving
        tolerance=q.absolute_mass_balance_tolerance_kg_s+q.relative_mass_balance_tolerance*max(entering,leaving)
        if abs(residual)>tolerance:
            raise ValueError("supplied hydraulic flows violate energy-node mass conservation: "+node.id)
        if leaving<=0:
            raise ValueError("a zero-throughflow node has no identified steady mixed energy: "+node.id)
        rows.append(i); cols.append(i); data.append(leaving)
        rhs[i]=(0.0 if node.supplied_specific_total_energy_J_kg is None else
                node.supplied_mass_flow_kg_s*node.supplied_specific_total_energy_J_kg)
        rhs[i]+=node.heat_into_node_W+math.fsum(e.heat_into_flow_W for e in incoming[i])
        for edge in incoming[i]:
            rows.append(i); cols.append(indices[edge.upstream_node_id]); data.append(-edge.mass_flow_kg_s)
        mass_audits.append(dict(node_id=node.id,residual_kg_s=residual,tolerance_kg_s=tolerance))
    matrix=coo_matrix((data,(rows,cols)),shape=(n,n)).tocsc()
    with warnings.catch_warnings():
        warnings.simplefilter("error",MatrixRankWarning)
        try:
            energy=np.asarray(spsolve(matrix,rhs),dtype=float)
        except (MatrixRankWarning,RuntimeError) as error:
            raise ValueError("energy network is unanchored or has no unique steady solution") from error
    if not np.isfinite(energy).all():
        raise ValueError("energy network returned an unidentifiable or nonfinite state")
    node_results=[]
    for i,node in enumerate(q.nodes):
        inflow_power=math.fsum(e.mass_flow_kg_s*float(energy[indices[e.upstream_node_id]])+e.heat_into_flow_W for e in incoming[i])
        supply=0.0 if node.supplied_specific_total_energy_J_kg is None else node.supplied_mass_flow_kg_s*node.supplied_specific_total_energy_J_kg
        outflow=(math.fsum(e.mass_flow_kg_s for e in outgoing[i])+node.withdrawn_mass_flow_kg_s)*float(energy[i])
        residual=inflow_power+supply+node.heat_into_node_W-outflow
        tolerance=q.absolute_energy_balance_tolerance_W+q.relative_energy_balance_tolerance*max(abs(inflow_power)+abs(supply)+abs(node.heat_into_node_W),abs(outflow))
        node_results.append(dict(node_id=node.id,specific_total_energy_J_kg=float(energy[i]),
                                 energy_residual_W=residual,tolerance_W=tolerance,within_budget=abs(residual)<=tolerance))
    supply=math.fsum(node.supplied_mass_flow_kg_s*(node.supplied_specific_total_energy_J_kg or 0.0) for node in q.nodes)
    withdrawal=math.fsum(node.withdrawn_mass_flow_kg_s*float(energy[i]) for i,node in enumerate(q.nodes))
    heat=math.fsum(e.heat_into_flow_W for e in q.edges)+math.fsum(node.heat_into_node_W for node in q.nodes)
    global_residual=supply+heat-withdrawal
    global_tolerance=q.absolute_energy_balance_tolerance_W+q.relative_energy_balance_tolerance*max(abs(supply)+abs(heat),abs(withdrawal))
    edges=[dict(edge_id=e.id,upstream_specific_total_energy_J_kg=float(energy[indices[e.upstream_node_id]]),
                downstream_arrival_specific_total_energy_J_kg=None if e.mass_flow_kg_s==0 else
                float(energy[indices[e.upstream_node_id]])+e.heat_into_flow_W/e.mass_flow_kg_s,
                advected_mass_flow_kg_s=e.mass_flow_kg_s) for e in q.edges]
    body=dict(schema="pipeline_integrity.energy_network.v1",request=q.model_dump(mode="json"),
              context=c.model_dump(mode="json"),nodes=node_results,edges=edges,mass_audit=mass_audits,
              global_energy_residual_W=global_residual,global_energy_tolerance_W=global_tolerance,
              external_supply_power_W=supply,external_withdrawal_power_W=withdrawal,heat_into_network_W=heat,
              status="computed" if abs(global_residual)<=global_tolerance and all(v["within_budget"] for v in node_results) else "out_of_scope",
              pressure_or_temperature_feedback_solved=False,numerical_validation_completed=False,
              implementation_sha256=file_digest(Path(__file__)),scipy_version=version("scipy"))
    body["record_digest"]=digest(body)
    return body


def verify_energy_network_record(record, *, request=None, context=None):
    if digest({k:v for k,v in record.items() if k!="record_digest"})!=record.get("record_digest"):
        raise ValueError("energy network receipt changed")
    q=EnergyNetworkRequest.model_validate_json(json.dumps(record["request"]))
    c=RunContext.model_validate_json(json.dumps(record["context"]))
    if request is not None and request.model_dump(mode="json")!=q.model_dump(mode="json"):
        raise ValueError("energy network caller request differs")
    if context is not None and context.model_dump(mode="json")!=c.model_dump(mode="json"):
        raise ValueError("energy network caller context differs")
    if evaluate_energy_network(q,c)!=record:
        raise ValueError("energy network replay differs")
    return True


def bind_energy_network_from_gas_result(source, specification, template: EnergyNetworkRequest,
                                        context: RunContext, *, mapping_evidence_id: str):
    """Reuse source-pipe mass rates without relabelling static h as total energy.

    External total-energy values remain caller supplied on the template basis.
    Only mass directions, supplies/withdrawals and the original pipe heat terms
    are mapped. The original hydraulic result is not recalculated or modified.
    """
    from .gas_network import GasNetwork, GasNetworkResult
    native=GasNetworkResult.model_validate_json(source.model_dump_json())
    native.check_integrity()
    network=GasNetwork.model_validate(specification.model_dump(mode="python"))
    q=EnergyNetworkRequest.model_validate(template.model_dump(mode="python"))
    c=RunContext.model_validate(context.model_dump(mode="python"))
    if native.status!="computed" or native.specification_digest!=digest(network):
        raise ValueError("energy mapping requires a computed result bound to the exact gas specification")
    if q.fluid_composition_id!=network.fluid:
        raise ValueError("energy-network fluid/composition identity must match the hydraulic source")
    if native.checks.get("pipe_segment_mass_continuity") is not True:
        raise ValueError("gas source pipe segment mass continuity is unresolved")
    if (q.asset_id,q.clock_id)!=(c.asset_id,c.clock_id) or (native.context.asset_id,native.context.clock_id,native.context.scenario_id)!=(c.asset_id,c.clock_id,c.scenario_id):
        raise ValueError("gas-to-energy context asset, clock or scenario differs")
    evidence={e.id:e for e in c.evidence}
    if mapping_evidence_id not in evidence or evidence[mapping_evidence_id].sha256 is None:
        raise ValueError("gas-to-total-energy mapping requires source-hash-pinned review evidence")
    if any(e.id not in evidence or evidence[e.id].model_dump(mode="json")!=e.model_dump(mode="json") for e in native.context.evidence):
        raise ValueError("gas source evidence must be preserved in the energy context")
    if any(c.metadata.get(k)!=v for k,v in native.context.metadata.items()):
        raise ValueError("gas source context metadata must be preserved")
    source_nodes={n.id for n in (*network.boundaries,*network.junctions)}
    if {n.id for n in q.nodes}!=source_nodes or {e.id for e in q.edges}!={p.id for p in network.pipes}:
        raise ValueError("energy and gas models must identify the same original nodes and pipes")
    summary=native.checks.get("pipe_flow_summary",{})
    if set(summary)!={p.id for p in network.pipes}:
        raise ValueError("gas source lacks an exact original-pipe flow summary")
    incoming={n:0.0 for n in source_nodes}
    outgoing={n:0.0 for n in source_nodes}
    edges=[]
    source_edges={e.id:e for e in q.edges}
    for pipe in network.pipes:
        flow=summary[pipe.id]
        rate=flow.get("mass_flow_signed_kg_s")
        if flow.get("segment_mass_continuity") is not True or rate is None:
            raise ValueError("an original gas pipe has no continuity-qualified signed mass flow")
        if (flow.get("start_node"),flow.get("end_node"))!=(pipe.start_node,pipe.end_node):
            raise ValueError("gas flow summary changed original pipe endpoints")
        upstream,downstream=(pipe.start_node,pipe.end_node) if rate>=0 else (pipe.end_node,pipe.start_node)
        previous=source_edges[pipe.id]
        if previous.heat_into_flow_W!=pipe.heat_into_fluid_w:
            raise ValueError("energy pipe heat input differs from the selected hydraulic-source scenario")
        payload=previous.model_dump(mode="json")
        payload.update(upstream_node_id=upstream,downstream_node_id=downstream,mass_flow_kg_s=abs(rate))
        edge=EnergyMixingEdge.model_validate_json(json.dumps(payload))
        edges.append(edge)
        outgoing[upstream]+=abs(rate)
        incoming[downstream]+=abs(rate)
    boundaries={n.id for n in network.boundaries}
    junctions={n.id:n for n in network.junctions}
    nodes=[]
    for node in q.nodes:
        payload=node.model_dump(mode="json")
        if node.id in boundaries:
            net=outgoing[node.id]-incoming[node.id]
            payload["supplied_mass_flow_kg_s"]=max(net,0.0)
            payload["withdrawn_mass_flow_kg_s"]=max(-net,0.0)
        else:
            payload["supplied_mass_flow_kg_s"]=0.0
            payload["withdrawn_mass_flow_kg_s"]=junctions[node.id].withdrawal_kg_s
        nodes.append(EnergyMixingNode.model_validate_json(json.dumps(payload)))
    payload=q.model_dump(mode="json")
    payload.update(nodes=[n.model_dump(mode="json") for n in nodes],edges=[e.model_dump(mode="json") for e in edges],
                   hydraulic_source_digest=native.result_digest,
                   evidence_ids=list(dict.fromkeys((*q.evidence_ids,mapping_evidence_id))))
    derived=EnergyNetworkRequest.model_validate_json(json.dumps(payload))
    body=dict(schema="pipeline_integrity.gas_energy_network_binding.v1",hydraulic_result_digest=native.result_digest,
              source_fluid=network.fluid,source_property_basis=network.property_basis,
              hydraulic_specification_digest=digest(network),template_digest=digest(q),derived_request=derived.model_dump(mode="json"),
              context_digest=digest(c),mapping_evidence_id=mapping_evidence_id,
              source_static_enthalpy_not_reinterpreted=True,boundary_total_energy_supplied_by_caller=True,
              pressure_feedback_solved=False)
    body["record_digest"]=digest(body)
    return derived,body
