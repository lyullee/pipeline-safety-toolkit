"""Analytic steady total-energy mixing references for authored synthetic streams."""
import pytest

from pipeline_integrity.contracts import EvidenceRef, RunContext, digest
from pipeline_integrity.energy_network import (
    EnergyMixingEdge, EnergyMixingNode, EnergyNetworkRequest,
    evaluate_energy_network, verify_energy_network_record,
)


def _context():
    ids = ("review", "basis", "source-1", "source-2", "mix", "sink-1", "sink-2",
           "pipe-1", "pipe-2", "out-1", "out-2")
    refs = tuple(EvidenceRef(id=identity, citation="Authored synthetic network reference", origin="synthetic",
                            sha256=digest(("energy-mix-reference", identity))) for identity in ids)
    return RunContext(asset_id="mixing-network", scenario_id="two-inlet-reference", clock_id="snapshot",
                      origin="synthetic", evidence=refs)


def _request(shift=0.0):
    return EnergyNetworkRequest(
        id="three-way-mixer", asset_id="mixing-network", clock_id="snapshot", time_s=0.0,
        fluid_composition_id="fixed-mixture", total_energy_reference_id="declared-reference-zero",
        hydraulic_source_digest="a" * 64,
        nodes=(
            EnergyMixingNode(id="source-1", supplied_mass_flow_kg_s=1.0,
                supplied_specific_total_energy_J_kg=100.0 + shift, evidence_id="source-1"),
            EnergyMixingNode(id="source-2", supplied_mass_flow_kg_s=2.0,
                supplied_specific_total_energy_J_kg=200.0 + shift, evidence_id="source-2"),
            EnergyMixingNode(id="mixer", heat_into_node_W=30.0, evidence_id="mix"),
            EnergyMixingNode(id="sink-1", withdrawn_mass_flow_kg_s=1.0, evidence_id="sink-1"),
            EnergyMixingNode(id="sink-2", withdrawn_mass_flow_kg_s=2.0, evidence_id="sink-2"),
        ),
        edges=(
            EnergyMixingEdge(id="feed-1", upstream_node_id="source-1", downstream_node_id="mixer",
                             mass_flow_kg_s=1.0, evidence_id="pipe-1"),
            EnergyMixingEdge(id="feed-2", upstream_node_id="source-2", downstream_node_id="mixer",
                             mass_flow_kg_s=2.0, evidence_id="pipe-2"),
            EnergyMixingEdge(id="product-1", upstream_node_id="mixer", downstream_node_id="sink-1",
                             mass_flow_kg_s=1.0, evidence_id="out-1"),
            EnergyMixingEdge(id="product-2", upstream_node_id="mixer", downstream_node_id="sink-2",
                             mass_flow_kg_s=2.0, evidence_id="out-2"),
        ),
        fixed_flow_complete_mixing_and_common_energy_basis_reviewed=True,
        evidence_ids=("review", "basis"),
    )


def test_two_inlet_mixer_matches_independent_energy_balance_and_shift_invariance():
    context = _context()
    baseline_request = _request()
    shifted_request = _request(shift=73.0)
    baseline = evaluate_energy_network(baseline_request, context)
    shifted = evaluate_energy_network(shifted_request, context)

    # Independent steady first-law balance: m1*h1 + m2*h2 + Q = mout*h_mix.
    mixed = (1.0 * 100.0 + 2.0 * 200.0 + 30.0) / 3.0
    base_nodes = {row["node_id"]: row["specific_total_energy_J_kg"] for row in baseline["nodes"]}
    shifted_nodes = {row["node_id"]: row["specific_total_energy_J_kg"] for row in shifted["nodes"]}
    assert baseline["status"] == shifted["status"] == "computed"
    assert base_nodes["mixer"] == pytest.approx(mixed, abs=1e-12)
    assert base_nodes["sink-1"] == pytest.approx(mixed, abs=1e-12)
    assert base_nodes["sink-2"] == pytest.approx(mixed, abs=1e-12)
    assert shifted_nodes["mixer"] - base_nodes["mixer"] == pytest.approx(73.0, abs=1e-12)
    assert baseline["external_supply_power_W"] == pytest.approx(500.0)
    assert baseline["heat_into_network_W"] == pytest.approx(30.0)
    assert baseline["external_withdrawal_power_W"] == pytest.approx(530.0, abs=1e-10)
    assert baseline["global_energy_residual_W"] == pytest.approx(0.0, abs=1e-10)
    assert all(row["within_budget"] for row in baseline["nodes"])
    assert verify_energy_network_record(baseline, request=baseline_request, context=context)
    assert verify_energy_network_record(shifted, request=shifted_request, context=context)
