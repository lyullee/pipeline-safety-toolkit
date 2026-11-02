"""Synthetic analytic checks for directed earliest-arrival pipe-network acoustics.

The graph and delays are authored references, not measurements.
"""
import pytest

from pipeline_integrity.acoustic_network import (
    AcousticDelayChannel, AcousticGraphEdge, AcousticGraphSensor, AcousticGraphSource,
    AcousticNetworkRequest, evaluate_acoustic_network, verify_acoustic_network_record,
)
from pipeline_integrity.contracts import EvidenceRef, RunContext, digest


def _ev(identity):
    return EvidenceRef(id=identity, citation="Authored synthetic acoustic graph", origin="synthetic",
                       sha256=digest(("acoustic-network-analytic", identity)))


def test_network_arrivals_match_directional_route_minima_and_signed_delay():
    ids = ("applicability", "edge-fast", "edge-slow", "edge-return", "edge-direct",
           "source-left", "source-right", "sensor-left", "sensor-right", "timing")
    evidence = tuple(_ev(identity) for identity in ids)
    context = RunContext(asset_id="pipe-network", scenario_id="analytic-graph", clock_id="common-clock",
                         origin="synthetic", evidence=evidence)
    network = AcousticNetworkRequest(
        id="analytic-network", asset_id="pipe-network", clock_id="common-clock",
        selected_band_hz=(100.0, 1000.0), nodes=("A", "B", "C"),
        edges=(
            AcousticGraphEdge(id="fast-A-B", left_node_id="A", right_node_id="B", length_m=100.0,
                              wave_speed_m_s=1000.0, velocity_left_to_right_m_s=100.0, evidence_id="edge-fast"),
            AcousticGraphEdge(id="slow-A-B", left_node_id="A", right_node_id="B", length_m=200.0,
                              wave_speed_m_s=1000.0, velocity_left_to_right_m_s=0.0, evidence_id="edge-slow"),
            AcousticGraphEdge(id="B-C", left_node_id="B", right_node_id="C", length_m=300.0,
                              wave_speed_m_s=1000.0, evidence_id="edge-return"),
            AcousticGraphEdge(id="direct-A-C", left_node_id="A", right_node_id="C", length_m=500.0,
                              wave_speed_m_s=1000.0, evidence_id="edge-direct"),
        ),
        sources=(
            AcousticGraphSource(candidate_id="leak-at-A", node_id="A", location_id="loc-A",
                                physical_port_id="port-A", evidence_id="source-left"),
            AcousticGraphSource(candidate_id="leak-at-C", node_id="C", location_id="loc-C",
                                physical_port_id="port-C", evidence_id="source-right"),
        ),
        sensors=(
            AcousticGraphSensor(id="sensor-A", node_id="A", evidence_id="sensor-left"),
            AcousticGraphSensor(id="sensor-C", node_id="C", evidence_id="sensor-right"),
        ),
        channels=(AcousticDelayChannel(id="C-minus-A", left_sensor_id="sensor-A", right_sensor_id="sensor-C",
            row_sensor_id="sensor-C", physical_port_id="port-C", delay_definition_id="arrival-difference-v1",
            right_minus_left_latency_s=0.002, evidence_id="timing"),),
        evidence_ids=("applicability",), geometry_flow_and_earliest_arrival_applicability_reviewed=True,
    )

    record = evaluate_acoustic_network(network, context)
    rows = {row["candidate_id"]: row for row in record["predictions"]}
    expected_A = 100.0 / (1000.0 + 100.0) + 300.0 / 1000.0 + 0.002
    expected_C = -(100.0 / (1000.0 - 100.0) + 300.0 / 1000.0) + 0.002
    assert record["status"] == "computed"
    assert rows["leak-at-A"]["right_minus_left_delay_s"]["C-minus-A"] == pytest.approx(expected_A)
    assert rows["leak-at-C"]["right_minus_left_delay_s"]["C-minus-A"] == pytest.approx(expected_C)
    chosen = {(r["from_node"], r["to_node"]): r for r in record["directed_edge_times"]}
    assert chosen[("A", "B")]["travel_time_s"] == pytest.approx(100.0 / 1100.0)
    assert chosen[("A", "B")]["selected_parallel_edge_id"] == "fast-A-B"
    assert chosen[("B", "A")]["travel_time_s"] == pytest.approx(100.0 / 900.0)
    assert verify_acoustic_network_record(record, request=network, context=context)
    assert record["probability_calculated"] is False
