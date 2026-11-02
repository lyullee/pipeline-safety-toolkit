import math

import numpy as np
import pytest

from pipeline_integrity.contracts import RunContext
from pipeline_integrity.transient import (
    History,
    LiquidElevationPoint,
    LiquidNode,
    LiquidPipe,
    LiquidTransient,
    simulate_liquid_transient,
)

CONTEXT = RunContext(
    asset_id="acoustic_reference", scenario_id="closure", clock_id="elapsed", origin="synthetic"
)


def single_pipe(timestep=0.025, withdrawal=None):
    return LiquidTransient(
        nodes=(
            LiquidNode(
                id="reservoir",
                elevation_m=0.0,
                initial_head_m=100.0,
                boundary="head",
                history=History(time_s=(0.0,), values=(100.0,)),
            ),
            LiquidNode(
                id="outlet",
                elevation_m=0.0,
                initial_head_m=100.0,
                boundary="withdrawal",
                history=withdrawal or History(time_s=(0.0, 0.5, 0.6), values=(0.01, 0.01, 0.0)),
            ),
        ),
        pipes=(
            LiquidPipe(
                id="pipe",
                start_node="reservoir",
                end_node="outlet",
                length_m=1000.0,
                diameter_m=0.2,
                wave_speed_m_s=1000.0,
                darcy_friction_factor=0.0,
                initial_volume_flow_m3_s=0.01,
            ),
        ),
        duration_s=6.0,
        requested_timestep_s=timestep,
        density_kg_m3=998.0,
        ambient_pressure_pa_abs=101325.0,
        vapour_pressure_pa_abs=2339.0,
    )


def test_closed_flow_wave_matches_joukowsky_and_reflects():
    result = simulate_liquid_transient(single_pipe(), CONTEXT)
    head = np.array(result["pipes"]["pipe"]["end_head_m"])
    expected = 1000 * (0.01 / (math.pi * 0.2**2 / 4)) / 9.80665
    assert result["status"] == "computed"
    assert head.max() - 100 == pytest.approx(expected, rel=1e-10)
    assert 100 - head.min() == pytest.approx(expected, rel=1e-10)
    assert result["time_s"][-1] == 6.0
    assert result["maximum_junction_residual_m3_s"] < 1e-12
    assert result["volume_balance"]["maximum_absolute_residual_m3"] < 1e-10
    assert result["vapour_pressure_margin_pa"] > 0
    assert result["minimum_absolute_pressure_location"]["pipe_id"] == "pipe"
    assert len(result["vapour_pressure_margin_history_pa"]) == len(result["time_s"])


def test_constant_flow_remains_steady_and_grid_refinement_agrees():
    constant = History(time_s=(0.0,), values=(0.01,))
    result = simulate_liquid_transient(single_pipe(withdrawal=constant), CONTEXT)
    assert np.max(np.abs(np.array(result["pipes"]["pipe"]["end_head_m"]) - 100)) < 1e-10
    coarse = simulate_liquid_transient(single_pipe(0.025), CONTEXT)
    fine = simulate_liquid_transient(single_pipe(0.0125), CONTEXT)
    assert np.array(coarse["pipes"]["pipe"]["end_head_m"]) == pytest.approx(
        np.array(fine["pipes"]["pipe"]["end_head_m"])[::2], abs=1e-9
    )


def test_initial_unbalanced_flows_and_invalid_wave_adjustment_rejected():
    spec = single_pipe()
    wrong = spec.pipes[0].model_copy(update={"initial_volume_flow_m3_s": 0.02})
    with pytest.raises(ValueError, match="balanced"):
        simulate_liquid_transient(spec.model_copy(update={"pipes": (wrong,)}), CONTEXT)
    with pytest.raises(ValueError, match="wave-speed"):
        simulate_liquid_transient(spec.model_copy(update={"requested_timestep_s": 0.37}), CONTEXT)


def test_declared_acoustic_domain_cannot_be_silently_exceeded():
    spec = single_pipe()
    fast = simulate_liquid_transient(spec.model_copy(update={"maximum_acoustic_mach": 0.00001}), CONTEXT)
    assert fast["status"] == "out_of_scope" and "Mach" in " ".join(fast["notes"])
    strain = simulate_liquid_transient(
        spec.model_copy(update={"maximum_linearized_storage_strain": 1e-5}), CONTEXT
    )
    assert strain["status"] == "out_of_scope" and "strain" in " ".join(strain["notes"])


def test_vapour_boundary_withholds_column_separation():
    spec = single_pipe(withdrawal=History(time_s=(0.0, 0.5, 0.6), values=(0.01, 0.01, 0.1)))
    result = simulate_liquid_transient(spec, CONTEXT)
    assert result["status"] == "out_of_scope"
    assert result["time_s"][-1] < spec.duration_s
    assert "vapour" in " ".join(result["notes"])
    event = result["first_vapour_pressure_boundary"]
    assert event["time_s"] == result["time_s"][-1]
    assert event["pipe_id"] == "pipe"
    assert event["vapour_pressure_margin_pa"] <= 0


def test_surveyed_high_point_is_included_in_absolute_pressure_screen():
    spec = single_pipe(withdrawal=History(time_s=(0.0,), values=(0.01,)))
    pipe = spec.pipes[0].model_copy(
        update={
            "elevation_profile": (
                LiquidElevationPoint(chainage_m=0.0, elevation_m=0.0),
                LiquidElevationPoint(chainage_m=500.0, elevation_m=120.0),
                LiquidElevationPoint(chainage_m=1000.0, elevation_m=0.0),
            )
        }
    )
    result = simulate_liquid_transient(spec.model_copy(update={"pipes": (pipe,)}), CONTEXT)
    assert result["status"] == "out_of_scope"
    assert result["minimum_absolute_pressure_location"]["segment_index"] == 20
    assert result["minimum_absolute_pressure_location"]["fraction_along_pipe"] == pytest.approx(0.5)
    assert result["first_vapour_pressure_boundary"]["time_s"] == 0.0


def test_surveyed_elevation_chainage_is_pipe_oriented_under_reverse_flow():
    profile = (
        LiquidElevationPoint(chainage_m=0.0, elevation_m=0.0),
        LiquidElevationPoint(chainage_m=500.0, elevation_m=120.0),
        LiquidElevationPoint(chainage_m=1000.0, elevation_m=0.0),
    )
    forward = single_pipe(withdrawal=History(time_s=(0.0,), values=(0.01,)))
    forward_pipe = forward.pipes[0].model_copy(update={"elevation_profile": profile})
    forward_result = simulate_liquid_transient(
        forward.model_copy(update={"pipes": (forward_pipe,)}), CONTEXT
    )

    reverse = single_pipe(withdrawal=History(time_s=(0.0,), values=(-0.01,)))
    reverse_nodes = (
        reverse.nodes[0].model_copy(update={"boundary": "withdrawal", "history": History(time_s=(0.0,), values=(0.01,))}),
        reverse.nodes[1].model_copy(update={"boundary": "head", "history": History(time_s=(0.0,), values=(100.0,))}),
    )
    reverse_pipe = reverse.pipes[0].model_copy(
        update={"initial_volume_flow_m3_s": -0.01, "elevation_profile": profile}
    )
    reverse_result = simulate_liquid_transient(
        reverse.model_copy(update={"nodes": reverse_nodes, "pipes": (reverse_pipe,)}), CONTEXT
    )

    for result in (forward_result, reverse_result):
        location = result["minimum_absolute_pressure_location"]
        assert location["pipe_id"] == "pipe"
        assert location["fraction_along_pipe"] == pytest.approx(0.5)
        assert result["first_vapour_pressure_boundary"]["time_s"] == 0.0
    assert forward_result["minimum_absolute_pressure_pa"] == pytest.approx(
        reverse_result["minimum_absolute_pressure_pa"]
    )


@pytest.mark.parametrize(
    "profile",
    (
        ((100.0, 0.0), (1000.0, 0.0)),
        ((0.0, 1.0), (1000.0, 0.0)),
        ((0.0, 0.0), (700.0, 2.0), (600.0, 3.0), (1000.0, 0.0)),
    ),
)
def test_surveyed_elevation_profile_requires_full_ordered_endpoint_binding(profile):
    spec = single_pipe()
    pipe = spec.pipes[0].model_copy(
        update={
            "elevation_profile": tuple(
                LiquidElevationPoint(chainage_m=chainage, elevation_m=elevation)
                for chainage, elevation in profile
            )
        }
    )
    with pytest.raises(ValueError, match="profile|chainage"):
        simulate_liquid_transient(spec.model_copy(update={"pipes": (pipe,)}), CONTEXT)


def test_surveyed_elevation_profile_obeys_declared_input_budget():
    spec = single_pipe()
    pipe = spec.pipes[0].model_copy(
        update={
            "elevation_profile": (
                LiquidElevationPoint(chainage_m=0.0, elevation_m=0.0),
                LiquidElevationPoint(chainage_m=500.0, elevation_m=2.0),
                LiquidElevationPoint(chainage_m=1000.0, elevation_m=0.0),
            )
        }
    )
    with pytest.raises(ValueError, match="point budget"):
        simulate_liquid_transient(
            spec.model_copy(update={"pipes": (pipe,), "maximum_elevation_profile_points": 2}), CONTEXT
        )


def test_branch_reflection_and_junction_mass_conservation():
    spec = single_pipe()
    nodes = (
        spec.nodes[0],
        LiquidNode(
            id="junction",
            elevation_m=0.0,
            initial_head_m=100.0,
            boundary="withdrawal",
            history=History(time_s=(0.0,), values=(0.0,)),
        ),
        spec.nodes[1],
        LiquidNode(
            id="second_outlet",
            elevation_m=0.0,
            initial_head_m=100.0,
            boundary="withdrawal",
            history=History(time_s=(0.0,), values=(0.01,)),
        ),
    )
    pipes = (
        spec.pipes[0].model_copy(update={"end_node": "junction", "initial_volume_flow_m3_s": 0.02}),
        spec.pipes[0].model_copy(update={"id": "branch1", "start_node": "junction", "length_m": 500.0}),
        spec.pipes[0].model_copy(
            update={"id": "branch2", "start_node": "junction", "end_node": "second_outlet", "length_m": 500.0}
        ),
    )
    result = simulate_liquid_transient(spec.model_copy(update={"nodes": nodes, "pipes": pipes}), CONTEXT)
    assert result["status"] == "computed"
    assert result["maximum_junction_residual_m3_s"] < 1e-12
    assert result["volume_balance"]["maximum_absolute_residual_m3"] < 1e-10
    assert result["pipes"]["pipe"]["end_head_m"] == pytest.approx(result["pipes"]["branch1"]["start_head_m"])
    expected = (2 / 3) * 1000 * 0.01 / (math.pi * 0.2**2 / 4) / 9.80665
    index = np.argmin(np.abs(np.array(result["time_s"]) - 1.2))
    assert result["pipes"]["pipe"]["end_head_m"][index] - 100 == pytest.approx(expected, rel=1e-9)


@pytest.mark.parametrize("direction", [1, -1])
def test_declared_friction_remains_in_steady_equilibrium(direction):
    spec = single_pipe(withdrawal=History(time_s=(0.0,), values=(0.01 * direction,)))
    velocity = 0.01 / (math.pi * 0.2**2 / 4)
    loss = direction * 0.02 * 1000 / 0.2 * velocity**2 / (2 * spec.gravity_m_s2)
    outlet = spec.nodes[1].model_copy(update={"initial_head_m": 100 - loss})
    pipe = spec.pipes[0].model_copy(
        update={"darcy_friction_factor": 0.02, "initial_volume_flow_m3_s": direction * 0.01}
    )
    result = simulate_liquid_transient(
        spec.model_copy(update={"nodes": (spec.nodes[0], outlet), "pipes": (pipe,)}), CONTEXT
    )
    assert result["status"] == "computed"
    assert np.array(result["pipes"]["pipe"]["end_head_m"]) == pytest.approx(100 - loss, abs=1e-9)


def test_atmospheric_emitter_preserves_algebraic_mass_and_records_scope():
    spec = single_pipe(withdrawal=History(time_s=(0.0,), values=(0.01,)))
    node = spec.nodes[1].model_copy(
        update={"atmospheric_emitter": History(time_s=(0.0, 0.5, 0.6), values=(0.0, 0.0, 0.001))}
    )
    result = simulate_liquid_transient(spec.model_copy(update={"nodes": (spec.nodes[0], node)}), CONTEXT)
    index = np.argmin(np.abs(np.array(result["time_s"]) - 0.7))
    head = result["pipes"]["pipe"]["end_head_m"][index]
    impedance = 1000 / (spec.gravity_m_s2 * math.pi * 0.2**2 / 4)
    assert head + impedance * 0.001 * math.sqrt(head) == pytest.approx(100.0, rel=1e-10)
    assert result["maximum_junction_residual_m3_s"] < 1e-12
