"""Quasi-steady liquid-flow event from a stroke-limited valve characteristic.

The actuator and CdA curve are authored synthetic inputs. The flow oracle is
the incompressible orifice relation under a fixed pressure difference; no MOC,
water-hammer, or field qualification is claimed.
"""

import math

import pytest

from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.valve_motion import (
    ConductanceCurve,
    ValveCommand,
    ValveMotionRequest,
    simulate_valve_motion,
)


def _motion(step_s):
    evidence = (
        EvidenceRef(
            id="curve",
            citation="Authored synthetic effective-area characteristic",
            origin="synthetic",
            license_id="MIT",
        ),
        EvidenceRef(
            id="open-command",
            citation="Authored synthetic full-open command",
            origin="synthetic",
            license_id="MIT",
        ),
    )
    context = RunContext(
        asset_id="synthetic-control-valve",
        scenario_id=f"flow-event-dt-{step_s}",
        clock_id="elapsed-s",
        origin="synthetic",
        evidence=evidence,
    )
    count = round(12.0 / step_s)
    request = ValveMotionRequest(
        component_id=context.asset_id,
        initial_opening=0.0,
        full_stroke_time_s=12.0,
        sample_time_s=tuple(i * step_s for i in range(count + 1)),
        commands=(ValveCommand(id="open", time_s=0.0, target_opening=1.0, evidence_id="open-command"),),
        conductance=ConductanceCurve(
            opening=(0.0, 0.25, 1.0),
            effective_area_m2=(0.0, 1.0e-4, 1.0e-3),
            parameter_source_id="curve",
        ),
    )
    return simulate_valve_motion(request, context)


def test_stroke_to_liquid_flow_event_and_discharged_volume_match_closed_form_at_three_resolutions():
    density_kg_m3 = 998.2
    pressure_drop_pa = 50_000.0
    flow_per_area = math.sqrt(2.0 * pressure_drop_pa / density_kg_m3)
    threshold_flow_m3_s = 5.2e-4 * flow_per_area
    # CdA is linear from 1e-4 at opening .25 to 1e-3 at opening 1.
    threshold_opening = 0.25 + (5.2e-4 - 1.0e-4) * 0.75 / (1.0e-3 - 1.0e-4)
    exact_event_time_s = threshold_opening * 12.0
    # Integral of the two linear CdA segments over their exact stroke times.
    exact_area_time_m2_s = 0.5 * 1.0e-4 * 3.0 + 0.5 * (1.0e-4 + 1.0e-3) * 9.0
    exact_discharged_volume_m3 = flow_per_area * exact_area_time_m2_s

    observed_events = []
    observed_volumes = []
    for step_s in (2.0, 1.0, 0.5):
        motion = _motion(step_s)
        flow = tuple(area * flow_per_area for area in motion.effective_area_m2)
        crossing = next(i for i, value in enumerate(flow) if value >= threshold_flow_m3_s)
        i0, i1 = crossing - 1, crossing
        t0, t1 = motion.time_s[i0], motion.time_s[i1]
        q0, q1 = flow[i0], flow[i1]
        event_time_s = t0 + (threshold_flow_m3_s - q0) * (t1 - t0) / (q1 - q0)
        integrated_volume_m3 = math.fsum(
            0.5 * (a + b) * (tb - ta)
            for ta, tb, a, b in zip(motion.time_s, motion.time_s[1:], flow, flow[1:])
        )
        observed_events.append(event_time_s)
        observed_volumes.append(integrated_volume_m3)
        assert event_time_s == pytest.approx(exact_event_time_s, abs=2e-12)
        expected_quadrature_error_area_time = (1.0e-4 / 3.0) if step_s == 2.0 else 0.0
        expected_sampled_volume_m3 = exact_discharged_volume_m3 + flow_per_area * expected_quadrature_error_area_time
        assert integrated_volume_m3 == pytest.approx(expected_sampled_volume_m3, rel=2e-13, abs=1e-14)
        assert motion.opening[-1] == pytest.approx(1.0, abs=1e-15)
        assert motion.command_records[0]["completion_bracket_s"] == [12.0 - step_s, 12.0]
        assert motion.kinematics_only and not motion.hydraulic_or_field_validation

    assert observed_events == pytest.approx([7.2, 7.2, 7.2], abs=2e-12)
    volume_errors = [abs(value - exact_discharged_volume_m3) for value in observed_volumes]
    assert volume_errors[0] == pytest.approx(flow_per_area * (1.0e-4 / 3.0), rel=1e-12)
    assert volume_errors[1] <= 1e-14
    assert volume_errors[2] <= 1e-14
    assert volume_errors[2] <= volume_errors[1] + 1e-14
