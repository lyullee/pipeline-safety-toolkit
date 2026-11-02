"""Independent checks for static-energy accounting at gas plenums.

These authored cases exercise only low-Mach, zero-velocity mixing boundaries.
They do not establish field applicability or finite-port mixing validity.
"""

import math

import pytest

from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.gas_network import GasBoundary, GasNetwork, GasPipe
from pipeline_integrity.gas_potential_network import (
    GasPotentialPlenumRequest,
    evaluate_gas_potential_plenums,
    gas_potential_path_metadata,
    verify_gas_potential_plenums_record,
)


GRAVITY_M_S2 = 9.80665


def _case(*, segments: int, elevation_change_m: float, reverse: bool = False):
    source_id, sink_id = ("high", "low") if not reverse else ("low", "high")
    source_pressure, sink_pressure = (1_030_000.0, 1_000_000.0)
    source_elevation, sink_elevation = (0.0, elevation_change_m)
    if reverse:
        source_pressure, sink_pressure = sink_pressure, source_pressure
        source_elevation, sink_elevation = sink_elevation, source_elevation

    network = GasNetwork(
        fluid="Methane",
        backend="HEOS",
        property_basis="CoolProp HEOS, shared within the authored plenum case",
        parameter_source_id="authored-plenum-network",
        boundaries=(
            GasBoundary(
                id=source_id,
                pressure_pa_abs=source_pressure,
                temperature_k=300.0,
                elevation_m=source_elevation,
            ),
            GasBoundary(
                id=sink_id,
                pressure_pa_abs=sink_pressure,
                temperature_k=290.0,
                elevation_m=sink_elevation,
            ),
        ),
        pipes=(
            GasPipe(
                id="line",
                start_node=source_id,
                end_node=sink_id,
                length_m=100.0,
                diameter_m=0.1,
                roughness_m=1e-5,
                local_loss_coefficient=0.25,
                heat_into_fluid_w=500.0,
                segments=segments,
            ),
        ),
        relative_closure_tolerance=1e-7,
        mass_tolerance_kg_s=1e-9,
        static_energy_tolerance_w=1e-5,
    )
    context = RunContext(
        asset_id="authored-gas-plenum-line",
        scenario_id=f"segments-{segments}-rise-{elevation_change_m:g}-reverse-{reverse}",
        clock_id="steady-snapshot",
        origin="synthetic",
        evidence=(
            EvidenceRef(
                id="authored-plenum-network",
                citation="Authored fixed-boundary gas line inputs for a static-energy closure check",
                origin="synthetic",
            ),
            EvidenceRef(
                id="authored-plenum-review",
                citation="Authored zero-velocity plenum and supplied-port-loss assumptions",
                origin="synthetic",
            ),
        ),
    )
    request = GasPotentialPlenumRequest(
        id="plenum-static-energy-check",
        asset_id=context.asset_id,
        clock_id=context.clock_id,
        network=network,
        expected_owner_pipe_sha256=gas_potential_path_metadata()["owner_pipe_sha256"],
        plenum_and_port_loss_evidence_id="authored-plenum-review",
        all_nodes_are_zero_velocity_well_mixed_plenums_reviewed=True,
        entrance_exit_and_junction_losses_in_supplied_K_reviewed=True,
        potential_work_and_common_property_reference_reviewed=True,
    )
    expected_mass_direction = -1.0 if reverse else 1.0
    return request, context, expected_mass_direction


@pytest.mark.parametrize(
    "segments,elevation_change_m,reverse",
    [(1, 0.0, False), (4, 25.0, False), (4, 25.0, True)],
)
def test_plenum_static_energy_and_potential_work_close_across_direction_and_partition(
    segments, elevation_change_m, reverse
):
    request, context, expected_mass_direction = _case(
        segments=segments, elevation_change_m=elevation_change_m, reverse=reverse
    )
    record = evaluate_gas_potential_plenums(request, context)

    assert record["status"] == "computed_diagnostic", {
        "failure": record["failure"],
        "qualification_notes": (
            None
            if record["static_surrogate_qualification"] is None
            else record["static_surrogate_qualification"]["notes"]
        ),
    }
    assert record["failure"] is None
    assert record["finite_port_velocity_mixing_solved"] is False
    assert record["prescribed_pressure_reservoir_sink_temperature_predicted"] is False
    assert record["static_surrogate_qualification"]["status"] == "computed"
    assert record["total_energy_mixing_record"]["status"] == "computed"

    qualification = record["static_surrogate_qualification"]
    flow_summary = qualification["checks"]["pipe_flow_summary"]["line"]
    signed_flow = flow_summary["mass_flow_signed_kg_s"]
    assert math.copysign(1.0, signed_flow) == expected_mass_direction
    start, end = request.network.pipes[0].start_node, request.network.pipes[0].end_node
    elevations = {node.id: node.elevation_m for node in request.network.boundaries}
    elevation_delta = elevations[end] - elevations[start]
    rates = [row["potential_work_W"] for row in record["segment_potential_work"]]
    expected_potential_work = signed_flow * GRAVITY_M_S2 * elevation_delta
    assert math.fsum(rates) == pytest.approx(expected_potential_work, rel=1e-9, abs=1e-9)
    assert all(
        abs(row["momentum_residual_pa"]) <= request.network.momentum_tolerance_pa
        for row in qualification["checks"]["segments"]
    )
    assert qualification["checks"]["global_closed"] is True
    assert all(
        abs(row["total_energy_feedback_residual_W"]) <= row["tolerance_W"]
        for row in record["node_energy_feedback_checks"]
    )
    assert verify_gas_potential_plenums_record(record, request=request, context=context)
