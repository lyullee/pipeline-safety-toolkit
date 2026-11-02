"""Directed earliest acoustic arrivals through an explicitly reviewed pipe graph.

The shortest-time path includes alternative network routes and directional
flow effects. Reflections, frequency-domain transfer amplitudes and path
superposition are not inferred from earliest arrivals.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from pydantic import Field, model_validator
from .contracts import Contract, Name, Positive, RunContext, digest
from .datasets import file_digest
from .inverse_localization import LocalizationRequest


class AcousticGraphEdge(Contract):
    id: Name
    left_node_id: Name
    right_node_id: Name
    length_m: Positive
    wave_speed_m_s: Positive
    velocity_left_to_right_m_s: float = 0.0
    evidence_id: Name

    @model_validator(mode="after")
    def propagation(self):
        if self.left_node_id==self.right_node_id:
            raise ValueError("acoustic graph edges need distinct endpoints")
        if abs(self.velocity_left_to_right_m_s)>=self.wave_speed_m_s:
            raise ValueError("both graph propagation directions must have positive speed")
        return self


class AcousticGraphSource(Contract):
    candidate_id: Name
    node_id: Name
    location_id: Name
    physical_port_id: Name
    evidence_id: Name
    coordinate_frame_id: Name = "declared_graph_nodes"
    position_m: tuple[float,float,float] | None = None


class AcousticGraphSensor(Contract):
    id: Name
    node_id: Name
    evidence_id: Name


class AcousticDelayChannel(Contract):
    id: Name
    left_sensor_id: Name
    right_sensor_id: Name
    row_sensor_id: Name
    physical_port_id: Name
    delay_definition_id: Name
    right_minus_left_latency_s: float = 0.0
    evidence_id: Name


class AcousticNetworkRequest(Contract):
    id: Name
    asset_id: Name
    clock_id: Name
    selected_band_hz: tuple[Positive,Positive]
    nodes: tuple[Name, ...] = Field(min_length=2,max_length=5000)
    edges: tuple[AcousticGraphEdge, ...] = Field(min_length=1,max_length=20000)
    sources: tuple[AcousticGraphSource, ...] = Field(min_length=1,max_length=256)
    sensors: tuple[AcousticGraphSensor, ...] = Field(min_length=2,max_length=512)
    channels: tuple[AcousticDelayChannel, ...] = Field(min_length=1,max_length=512)
    maximum_source_nodes: int = Field(default=2000000,ge=2,le=10000000)
    geometry_flow_and_earliest_arrival_applicability_reviewed: bool = False
    evidence_ids: tuple[Name, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def graph(self):
        if self.selected_band_hz[0]>=self.selected_band_hz[1]:
            raise ValueError("acoustic selected band must increase")
        if len(set(self.nodes))!=len(self.nodes):
            raise ValueError("acoustic graph nodes must be unique")
        for seq,field in ((self.edges,"id"),(self.sources,"candidate_id"),(self.sensors,"id"),(self.channels,"id")):
            if len({getattr(v,field) for v in seq})!=len(seq):
                raise ValueError("acoustic graph object IDs must be unique")
        nodes=set(self.nodes)
        if any(e.left_node_id not in nodes or e.right_node_id not in nodes for e in self.edges):
            raise ValueError("acoustic edge endpoints must identify existing graph nodes")
        if any(s.node_id not in nodes for s in (*self.sources,*self.sensors)):
            raise ValueError("acoustic sources and sensors must identify graph nodes")
        sensors={s.id for s in self.sensors}
        if any(c.left_sensor_id not in sensors or c.right_sensor_id not in sensors or c.left_sensor_id==c.right_sensor_id for c in self.channels):
            raise ValueError("acoustic delay channels need two distinct existing sensors")
        if len(self.sources)*len(self.nodes)>self.maximum_source_nodes:
            raise ValueError("acoustic graph shortest-path work exceeds its source-node budget")
        return self


def evaluate_acoustic_network(request: AcousticNetworkRequest, context: RunContext):
    from scipy.sparse import csr_matrix
    from scipy.sparse.csgraph import dijkstra
    from importlib.metadata import version
    q=AcousticNetworkRequest.model_validate(request.model_dump(mode="python"))
    c=RunContext.model_validate(context.model_dump(mode="python"))
    if not q.geometry_flow_and_earliest_arrival_applicability_reviewed:
        raise ValueError("acoustic graph and earliest-arrival applicability review is required")
    if (q.asset_id,q.clock_id)!=(c.asset_id,c.clock_id):
        raise ValueError("acoustic graph asset/clock differs from context")
    required=set(q.evidence_ids)|{v.evidence_id for v in (*q.edges,*q.sources,*q.sensors,*q.channels)}
    if not required <= {e.id for e in c.evidence}:
        raise ValueError("acoustic graph propagation/geometry/timing evidence is absent")
    index={node:i for i,node in enumerate(q.nodes)}
    # Sparse constructors sum duplicate entries. Parallel pipe routes instead
    # require the minimum travel time in each directed node pair.
    directed={}
    for edge in q.edges:
        for left,right,speed in ((edge.left_node_id,edge.right_node_id,edge.wave_speed_m_s+edge.velocity_left_to_right_m_s),
                                 (edge.right_node_id,edge.left_node_id,edge.wave_speed_m_s-edge.velocity_left_to_right_m_s)):
            travel=edge.length_m/speed
            if not math.isfinite(travel) or travel<=0:
                raise ValueError("acoustic graph travel time is not finite and positive")
            pair=(index[left],index[right])
            if pair not in directed or travel<directed[pair][0]:
                directed[pair]=(travel,edge.id)
    entries=list(directed)
    graph=csr_matrix(([directed[p][0] for p in entries],([p[0] for p in entries],[p[1] for p in entries])),shape=(len(q.nodes),len(q.nodes)))
    distances=dijkstra(graph,directed=True,indices=[index[s.node_id] for s in q.sources])
    predictions=[]
    for row,source in zip(distances,q.sources):
        arrivals={sensor.id:float(row[index[sensor.node_id]]) if math.isfinite(row[index[sensor.node_id]]) else None for sensor in q.sensors}
        delays={}
        for channel in q.channels:
            left,right=arrivals[channel.left_sensor_id],arrivals[channel.right_sensor_id]
            delays[channel.id]=None if left is None or right is None else right-left+channel.right_minus_left_latency_s
        predictions.append(dict(candidate_id=source.candidate_id,node_id=source.node_id,
                                arrival_time_s=arrivals,right_minus_left_delay_s=delays,
                                status="computed" if all(v is not None for v in delays.values()) else "withheld"))
    body=dict(schema="pipeline_integrity.acoustic_network.v1",request=q.model_dump(mode="json"),
              context=c.model_dump(mode="json"),predictions=predictions,
              status="computed" if all(p["status"]=="computed" for p in predictions) else "partial",
              directed_edge_times=[dict(from_node=q.nodes[p[0]],to_node=q.nodes[p[1]],travel_time_s=v[0],selected_parallel_edge_id=v[1]) for p,v in directed.items()],
              reflection_or_amplitude_model=False,probability_calculated=False,numerical_validation_completed=False,
              implementation_sha256=file_digest(Path(__file__)),scipy_version=version("scipy"))
    body["record_digest"]=digest(body)
    return body


def verify_acoustic_network_record(record, *, request=None, context=None):
    if digest({k:v for k,v in record.items() if k!="record_digest"})!=record.get("record_digest"):
        raise ValueError("acoustic network receipt changed")
    q=AcousticNetworkRequest.model_validate_json(json.dumps(record["request"]))
    c=RunContext.model_validate_json(json.dumps(record["context"]))
    if request is not None and request.model_dump(mode="json")!=q.model_dump(mode="json"):
        raise ValueError("acoustic network caller request differs")
    if context is not None and context.model_dump(mode="json")!=c.model_dump(mode="json"):
        raise ValueError("acoustic network caller context differs")
    if evaluate_acoustic_network(q,c)!=record:
        raise ValueError("acoustic network replay differs")
    return True


def acoustic_network_provider_identity(network: AcousticNetworkRequest):
    return dict(provider_id="pipeline_acoustic_earliest_arrival",provider_version="1",
                provider_source_sha256=file_digest(Path(__file__)),
                provider_configuration_sha256=digest(network))


class AcousticNetworkLocalizationProvider:
    """Supply graph delay predictions to the existing finite-candidate inverse API."""
    id="pipeline_acoustic_earliest_arrival"
    version="1"

    def __init__(self, network: AcousticNetworkRequest, localization_request):
        from .inverse_localization import LocalizationRequest
        self.network=AcousticNetworkRequest.model_validate(network.model_dump(mode="python"))
        self.request=LocalizationRequest.model_validate(localization_request.model_dump(mode="python"))
        q=self.request
        expected=acoustic_network_provider_identity(self.network)
        if any(getattr(q,k)!=v for k,v in expected.items()):
            raise ValueError("acoustic network inverse provider identity differs from the request")
        if (q.asset_id,q.clock_id)!=(self.network.asset_id,self.network.clock_id):
            raise ValueError("acoustic graph and inverse request use different assets or clocks")
        if q.parameter_bases or any(candidate.parameters for candidate in q.candidates):
            raise ValueError("node-candidate acoustic inversion fixes all propagation parameters explicitly")
        sources={s.candidate_id:s for s in self.network.sources}
        if set(sources)!={candidate.id for candidate in q.candidates}:
            raise ValueError("acoustic graph candidates and inverse candidates must match exactly")
        for candidate in q.candidates:
            source=sources[candidate.id]
            if (candidate.location_id,candidate.physical_port_id,candidate.coordinate_frame_id,candidate.position_m,candidate.evidence_id)!=(
                source.location_id,source.physical_port_id,source.coordinate_frame_id,source.position_m,source.evidence_id):
                raise ValueError("acoustic graph candidate mapping differs from inverse geometry/evidence")
        channels={channel.id:channel for channel in self.network.channels}
        if {row.channel_id for row in q.rows}!=set(channels):
            raise ValueError("acoustic graph channels and inverse observation channels differ")
        for row in q.rows:
            channel=channels[row.channel_id]
            if row.basis.unit!="s" or row.basis.pressure_reference is not None:
                raise ValueError("acoustic network inverse rows must represent delay seconds")
            if row.basis.definition_id!=channel.delay_definition_id:
                raise ValueError("acoustic inverse delay definition differs from the reviewed channel/band")
            if (row.sensor_id,row.physical_port_id)!=(channel.row_sensor_id,channel.physical_port_id):
                raise ValueError("acoustic inverse channel sensor/port mapping differs")
        self._request_digest=digest(q)
        self._cache_key=None
        self._cache=None

    @property
    def source_sha256(self):
        return file_digest(Path(__file__))

    @property
    def configuration_sha256(self):
        return digest(self.network)

    def __call__(self,candidate,time_s,context):
        from .inverse import Prediction
        from .inverse_localization import prediction_binding
        if digest(self.request)!=self._request_digest:
            raise ValueError("acoustic inverse request changed after provider binding")
        q=self.request
        if tuple(time_s)!=tuple(sorted({row.time_s for row in q.rows})):
            raise ValueError("acoustic provider report times differ from inverse observation times")
        originals=[v for v in q.candidates if v.id==candidate.id]
        if len(originals)!=1 or originals[0].model_dump(mode="json")!=candidate.model_dump(mode="json"):
            raise ValueError("acoustic provider received another candidate definition")
        if (q.provider_source_sha256,q.provider_configuration_sha256)!=(self.source_sha256,self.configuration_sha256):
            raise ValueError("acoustic forward source or graph configuration changed")
        key=digest(dict(network=self.network,context=context,source=self.source_sha256))
        if key!=self._cache_key:
            self._cache=evaluate_acoustic_network(self.network,context)
            self._cache_key=key
        if self._cache["record_digest"]!=digest({k:v for k,v in self._cache.items() if k!="record_digest"}):
            raise ValueError("cached acoustic network calculation changed")
        source=next(v for v in self._cache["predictions"] if v["candidate_id"]==candidate.id)
        values={channel.id:tuple(source["right_minus_left_delay_s"][channel.id] for _ in time_s)
                for channel in self.network.channels}
        provisional=Prediction(status=source["status"],asset_id=q.asset_id,clock_id=q.clock_id,time_s=tuple(time_s),
            values=values,units={channel.id:"s" for channel in self.network.channels},
            provider_source_digest=self.source_sha256,evaluation_digest="0"*64,
            diagnostics=dict(graph_record_digest=self._cache["record_digest"],configuration_sha256=self.configuration_sha256,
                             candidate_node_id=source["node_id"],static_earliest_arrival_model=True,
                             reflection_or_amplitude_model=False))
        return provisional.model_copy(update={"evaluation_digest":prediction_binding(q,candidate,context,provisional)})


class AcousticNetworkInverseRequest(Contract):
    network: AcousticNetworkRequest
    localization: LocalizationRequest


def bind_acoustic_network_provider_request(network: AcousticNetworkRequest, localization: LocalizationRequest):
    payload=localization.model_dump(mode="json")
    payload.update(acoustic_network_provider_identity(network))
    result=LocalizationRequest.model_validate_json(json.dumps(payload))
    AcousticNetworkLocalizationProvider(network,result)
    return result


def bind_acoustic_network_inverse_context(request: AcousticNetworkInverseRequest, context: RunContext):
    from .inverse_localization import boundary_metadata
    q=AcousticNetworkInverseRequest.model_validate(request.model_dump(mode="python"))
    c=RunContext.model_validate(context.model_dump(mode="python"))
    if (q.localization.asset_id,q.localization.clock_id)!=(c.asset_id,c.clock_id):
        raise ValueError("acoustic network inverse context uses another asset or clock")
    payload=c.model_dump(mode="json")
    payload["metadata"]["inverse_localization_boundary"]=boundary_metadata(q.localization)
    return RunContext.model_validate_json(json.dumps(payload))


def evaluate_acoustic_network_inverse(request: AcousticNetworkInverseRequest, context: RunContext):
    from .inverse_localization import localize_leak
    q=AcousticNetworkInverseRequest.model_validate(request.model_dump(mode="python"))
    c=RunContext.model_validate(context.model_dump(mode="python"))
    provider=AcousticNetworkLocalizationProvider(q.network,q.localization)
    native=localize_leak(q.localization,c,provider=provider)
    body=dict(schema="pipeline_integrity.acoustic_network_inverse.v1",request=q.model_dump(mode="json"),
              context=c.model_dump(mode="json"),status=native["status"],localization=native,
              forward_record=provider._cache,leak_existence_probability_inferred=False,
              numerical_validation_completed=False,implementation_sha256=file_digest(Path(__file__)))
    body["record_digest"]=digest(body)
    return body


def verify_acoustic_network_inverse_record(record, *, request=None, context=None):
    if digest({k:v for k,v in record.items() if k!="record_digest"})!=record.get("record_digest"):
        raise ValueError("acoustic network inverse receipt changed")
    q=AcousticNetworkInverseRequest.model_validate_json(json.dumps(record["request"]))
    c=RunContext.model_validate_json(json.dumps(record["context"]))
    if request is not None and request.model_dump(mode="json")!=q.model_dump(mode="json"):
        raise ValueError("acoustic network inverse caller request differs")
    if context is not None and context.model_dump(mode="json")!=c.model_dump(mode="json"):
        raise ValueError("acoustic network inverse caller context differs")
    if evaluate_acoustic_network_inverse(q,c)!=record:
        raise ValueError("acoustic network inverse replay differs")
    return True
