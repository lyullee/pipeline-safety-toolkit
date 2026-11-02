"""Independent passive-restriction, acoustic-wave and conservation references."""

import math

import numpy as np
import pytest

from pipeline_integrity.contracts import RunContext
from pipeline_integrity.transient import (
    History,
    LiquidNode,
    LiquidPipe,
    LiquidTransient,
    LiquidValve,
    simulate_liquid_transient,
)

CONTEXT = RunContext(asset_id="valve_network", scenario_id="closure", clock_id="elapsed", origin="synthetic")


def network(*, direction=1.0, timestep=0.025, closing=False, outlet_only=False):
    left, right = (100.0, 95.0) if direction > 0 else (95.0, 100.0)
    area = 0.01 / math.sqrt(2 * 9.80665 * 5)
    nodes = tuple(
        LiquidNode(
            id=key,
            elevation_m=0.0,
            initial_head_m=head,
            boundary=kind,
            history=History(time_s=(0.0,), values=(head if kind == "head" else 0.0,)),
        )
        for key, head, kind in (
            ("source", left, "head"),
            ("left", left, "withdrawal"),
            ("right", right, "head" if outlet_only else "withdrawal"),
            *(() if outlet_only else (("sink", right, "head"),)),
        )
    )
    pipes = tuple(
        LiquidPipe(
            id=key,
            start_node=start,
            end_node=end,
            length_m=1000.0,
            diameter_m=0.2,
            wave_speed_m_s=1000.0,
            darcy_friction_factor=0.0,
            initial_volume_flow_m3_s=direction * 0.01,
        )
        for key, start, end in (
            ("upstream", "source", "left"),
            *(() if outlet_only else (("downstream", "right", "sink"),)),
        )
    )
    history = (
        History(time_s=(0.0, 0.2, 0.3), values=(area, area, 0.0))
        if closing
        else History(time_s=(0.0,), values=(area,))
    )
    valve = LiquidValve(
        id="V",
        start_node="left",
        end_node="right",
        effective_area_m2=history,
        initial_volume_flow_m3_s=direction * 0.01,
        parameter_source_id="authored:orifice_reference",
    )
    return LiquidTransient(
        nodes=nodes,
        pipes=pipes,
        valves=(valve,),
        duration_s=2.0,
        requested_timestep_s=timestep,
        density_kg_m3=998.0,
        ambient_pressure_pa_abs=101325.0,
        vapour_pressure_pa_abs=2339.0,
    )


@pytest.mark.parametrize("direction", [1.0, -1.0])
def test_forward_and_reverse_restrictions_remain_steady_and_dissipative(direction):
    spec = network(direction=direction)
    result = simulate_liquid_transient(spec, CONTEXT)
    assert result["status"] == "computed"
    assert np.array(result["valves"]["V"]["volume_flow_m3_s"]) == pytest.approx(direction * 0.01, abs=1e-11)
    assert min(result["valves"]["V"]["dissipated_power_w"]) > 0
    assert result["maximum_pipe_volume_residual_m3"] < 1e-11
    assert result["maximum_junction_residual_m3_s"] < spec.valve_flow_tolerance_m3_s


def test_pressure_dependent_closure_matches_two_sided_acoustic_jump_and_retains_downstream_flow():
    result = simulate_liquid_transient(network(closing=True), CONTEXT)
    jump = 1000 * 0.01 / (math.pi * 0.2**2 / 4) / 9.80665
    assert result["status"] == "computed"
    assert max(result["pipes"]["upstream"]["end_head_m"]) - 100 == pytest.approx(jump, abs=1e-7)
    assert 95 - min(result["pipes"]["downstream"]["start_head_m"]) == pytest.approx(jump, abs=1e-7)
    closed = next(i for i, t in enumerate(result["time_s"]) if t >= 0.3)
    assert abs(result["valves"]["V"]["volume_flow_m3_s"][closed]) < 1e-12
    assert result["pipes"]["downstream"]["end_flow_m3_s"][closed] == pytest.approx(0.01, abs=1e-12)
    assert result["maximum_pipe_volume_residual_m3"] < 1e-11


def test_parallel_valves_equal_the_sum_of_conductances_without_duplicating_pipe_storage():
    spec = network(closing=True)
    single = simulate_liquid_transient(spec, CONTEXT)
    original = spec.valves[0]
    valves = tuple(
        LiquidValve(
            id=key,
            start_node="left",
            end_node="right",
            initial_volume_flow_m3_s=0.005,
            effective_area_m2=History(
                time_s=original.effective_area_m2.time_s,
                values=tuple(a / 2 for a in original.effective_area_m2.values),
            ),
            parameter_source_id="authored:parallel_half_area",
        )
        for key in ("A", "B")
    )
    parallel = simulate_liquid_transient(spec.model_copy(update={"valves": valves}), CONTEXT)
    assert parallel["status"] == "computed"
    assert np.array(parallel["pipes"]["upstream"]["end_head_m"]) == pytest.approx(
        single["pipes"]["upstream"]["end_head_m"], abs=1e-7
    )
    total = (
        np.array(parallel["valves"]["A"]["volume_flow_m3_s"]) + parallel["valves"]["B"]["volume_flow_m3_s"]
    )
    assert total == pytest.approx(single["valves"]["V"]["volume_flow_m3_s"], abs=1e-10)
    assert parallel["maximum_pipe_volume_residual_m3"] < 1e-11


def test_a_valve_can_discharge_directly_to_a_fixed_head_without_a_dummy_pipe():
    result = simulate_liquid_transient(network(closing=True, outlet_only=True), CONTEXT)
    assert result["status"] == "computed" and set(result["pipes"]) == {"upstream"}
    assert result["maximum_pipe_volume_residual_m3"] < 1e-11
    assert max(result["pipe_volume_balance"]["upstream"]["storage_change_m3"]) > 0.005


def test_signed_orifice_reference_between_two_reservoir_nodes():
    spec = network(closing=True)
    nodes = tuple(
        LiquidNode(
            **(
                n.model_dump(exclude={"history"})
                | {"boundary": "head", "history": History(time_s=(0.0,), values=(n.initial_head_m,))}
            )
        )
        for n in spec.nodes
    )
    result = simulate_liquid_transient(spec.model_copy(update={"nodes": nodes}), CONTEXT)
    expected = [spec.valves[0].effective_area_m2.at(t) * math.sqrt(2 * 9.80665 * 5) for t in result["time_s"]]
    assert result["status"] == "computed"
    assert result["valves"]["V"]["volume_flow_m3_s"] == pytest.approx(expected, abs=1e-11)


def test_nonlinear_emitter_and_valve_share_junction_conservation():
    spec = network(closing=True)
    nodes = tuple(
        n.model_copy(update={"atmospheric_emitter": History(time_s=(0.0,), values=(0.0002,))})
        if n.id == "left"
        else n
        for n in spec.nodes
    )
    pipes = (spec.pipes[0].model_copy(update={"initial_volume_flow_m3_s": 0.012}), spec.pipes[1])
    result = simulate_liquid_transient(spec.model_copy(update={"nodes": nodes, "pipes": pipes}), CONTEXT)
    assert result["status"] == "computed", result["notes"]
    assert result["maximum_junction_residual_m3_s"] < 1e-10
    assert result["maximum_pipe_volume_residual_m3"] < 1e-11


def test_common_time_samples_agree_after_characteristic_grid_refinement():
    coarse = simulate_liquid_transient(network(closing=True), CONTEXT)
    fine = simulate_liquid_transient(network(closing=True, timestep=0.0125), CONTEXT)
    assert coarse["status"] == fine["status"] == "computed"
    assert np.array(fine["pipes"]["upstream"]["end_head_m"])[::2] == pytest.approx(
        coarse["pipes"]["upstream"]["end_head_m"], abs=1e-6
    )


def test_initial_losses_and_mutated_conductance_are_rejected():
    spec = network()
    wrong = LiquidValve(
        **(
            spec.valves[0].model_dump(exclude={"binding_digest", "effective_area_m2"})
            | {"effective_area_m2": History(time_s=(0.0,), values=(0.01,))}
        )
    )
    with pytest.raises(ValueError, match="inconsistent"):
        simulate_liquid_transient(spec.model_copy(update={"valves": (wrong,)}), CONTEXT)
    changed = spec.valves[0].model_copy(update={"initial_volume_flow_m3_s": 0.02})
    with pytest.raises(ValueError, match="binding evidence changed"):
        simulate_liquid_transient(spec.model_copy(update={"valves": (changed,)}), CONTEXT)


def test_failed_algebraic_root_does_not_advance_the_physical_history():
    spec = network(closing=True).model_copy(update={"maximum_valve_solver_evaluations": 1})
    result = simulate_liquid_transient(spec, CONTEXT)
    assert result["status"] == "withheld" and result["time_s"] == [0.0]
    assert not result["valve_solver"][0]["root_qualified"]
    assert result["valve_solver"][0]["function_evaluations"] == 1
    with pytest.raises(ValueError, match="unknown budget"):
        simulate_liquid_transient(network().model_copy(update={"maximum_valve_unknowns": 1}), CONTEXT)
