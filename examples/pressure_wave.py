"""Original single-edge traces with explicit, fictional arrival-model bounds."""

from pipeline_integrity.acoustics import RelativeTiming, SampledTrace
from pipeline_integrity.contracts import EvidenceRef, RunContext, digest
from pipeline_integrity.pressure_wave import PressureWaveRequest, boundary_metadata


def make_case(*, reviewed=False, left_index=40, right_index=55):
    traces = []
    for name, index in (("left", left_index), ("right", right_index)):
        values = tuple(100000.0 if i < index else 90000.0 for i in range(128))
        traces.append(
            SampledTrace(
                id=name,
                asset_id="authored_pipe",
                sensor_id=name,
                clock_id="authored_clock",
                start_time_s=0.0,
                sample_period_s=0.001,
                quantity_id="pressure",
                unit="Pa",
                origin="synthetic",
                source_digest=digest({"authored_edge_index": index}),
                values=values,
                valid=(True,) * len(values),
            )
        )
    q = PressureWaveRequest(
        id="authored_front",
        left=traces[0],
        right=traces[1],
        pressure_reference="absolute",
        minimum_negative_rate_pa_s=100000.0,
        minimum_rate_prominence_pa_s=100000.0,
        left_feature_to_arrival_error_s=0.0,
        right_feature_to_arrival_error_s=0.0,
        timing=RelativeTiming(
            right_minus_left_latency_s=0.0, absolute_error_bound_s=0.0, source_id="authored"
        ),
        topology="through_pair",
        source_event_id="authored_event",
        evidence_id="authored",
        feature_policy_reviewed=reviewed,
        same_event_reviewed=reviewed,
        topology_reviewed=reviewed,
    )
    c = RunContext(
        asset_id="authored_pipe",
        scenario_id="authored_front",
        clock_id="authored_clock",
        origin="synthetic",
        evidence=(
            EvidenceRef(
                id="authored",
                citation="Original step-feature timing example; not a leak experiment",
                origin="synthetic",
            ),
        ),
        metadata={"pressure_wave_boundary": boundary_metadata(q)},
    )
    return q, c


def make_moc_case(*, timestep_s=0.001):
    """Reuse the existing MOC engine for a known right-origin withdrawal front."""
    from pipeline_integrity.transient import (
        History,
        LiquidNode,
        LiquidPipe,
        LiquidTransient,
        simulate_liquid_transient,
    )

    context = RunContext(
        asset_id="moc_front",
        scenario_id="withdrawal_increase",
        clock_id="elapsed",
        origin="simulation",
        evidence=(
            EvidenceRef(
                id="authored",
                citation="Original single-phase MOC withdrawal increase; not a leak experiment",
                origin="simulation",
            ),
        ),
    )
    positions = (0.0, 300.0, 700.0, 1000.0)
    nodes = tuple(
        LiquidNode(
            id=str(i),
            elevation_m=0.0,
            initial_head_m=100.0,
            boundary="head" if i == 0 else "withdrawal",
            history=History(time_s=(0.0,), values=(100.0,))
            if i == 0
            else History(time_s=(0.0, 0.05, 0.051), values=(0.01, 0.01, 0.02))
            if i == 3
            else History(time_s=(0.0,), values=(0.0,)),
        )
        for i in range(4)
    )
    pipes = tuple(
        LiquidPipe(
            id=str(i),
            start_node=str(i),
            end_node=str(i + 1),
            length_m=positions[i + 1] - positions[i],
            diameter_m=0.2,
            wave_speed_m_s=1000.0,
            darcy_friction_factor=0.0,
            initial_volume_flow_m3_s=0.01,
        )
        for i in range(3)
    )
    specification = LiquidTransient(
        nodes=nodes,
        pipes=pipes,
        duration_s=0.9,
        requested_timestep_s=timestep_s,
        density_kg_m3=998.0,
        ambient_pressure_pa_abs=101325.0,
        vapour_pressure_pa_abs=2339.0,
    )
    native = simulate_liquid_transient(specification, context)
    request, _ = make_case(reviewed=True)
    traces = []
    for name, pipe in (("left", "0"), ("right", "1")):
        values = tuple(
            specification.ambient_pressure_pa_abs
            + specification.density_kg_m3 * specification.gravity_m_s2 * h
            for h in native["pipes"][pipe]["end_head_m"]
        )
        traces.append(
            request.left.model_copy(
                update={
                    "id": name,
                    "asset_id": context.asset_id,
                    "sensor_id": name,
                    "clock_id": context.clock_id,
                    "sample_period_s": native["timestep_s"],
                    "origin": context.origin,
                    "source_digest": digest(native),
                    "values": values,
                    "valid": (True,) * len(values),
                }
            )
        )
    request = request.model_copy(
        update={
            "id": "moc_front_features",
            "left": traces[0],
            "right": traces[1],
            "source_event_id": "withdrawal_increase",
        }
    )
    context = context.model_copy(
        update={
            "metadata": {
                "pressure_wave_boundary": boundary_metadata(request),
                "sensor_positions_m": {"left": 300.0, "right": 700.0},
                "model_source": native["model_source_digest"],
            }
        }
    )
    return request, context, native
