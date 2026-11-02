"""Exact stroke-limited actuator trajectory/event reference through the native owner."""
import pytest

from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.valve_motion import (
    ConductanceCurve, ValveCommand, ValveMotionRequest, simulate_valve_motion,
)


def test_native_stroke_motion_matches_piecewise_exact_position_and_completion_events():
    evidence = tuple(EvidenceRef(id=name, citation="Authored actuator reference input",
                                 origin="synthetic", license_id="MIT")
                     for name in ("curve", "cmd_close", "cmd_reverse", "cmd_open"))
    context = RunContext(asset_id="valve_v", scenario_id="stroke_reference", clock_id="seconds",
                         origin="synthetic", evidence=evidence)
    times = (0.0, 0.13, 0.37, 0.60, 0.77, 1.0, 1.1, 1.4, 1.6)
    request = ValveMotionRequest(
        component_id=context.asset_id, initial_opening=0.8, full_stroke_time_s=1.0,
        sample_time_s=times,
        commands=(
            ValveCommand(id="close", time_s=0.0, target_opening=0.0, evidence_id="cmd_close"),
            ValveCommand(id="reverse", time_s=0.6, target_opening=0.6, evidence_id="cmd_reverse"),
            ValveCommand(id="open", time_s=1.1, target_opening=1.0, evidence_id="cmd_open"),
        ),
        conductance=ConductanceCurve(opening=(0.0, 0.25, 1.0),
            effective_area_m2=(0.0, 2.0e-4, 1.0e-3), parameter_source_id="curve"),
    )
    result = simulate_valve_motion(request, context)

    # The specified actuator moves at |dx/dt|=1/full_stroke_time until each target.
    expected = (0.8, 0.67, 0.43, 0.20, 0.37, 0.60, 0.60, 0.90, 1.0)
    assert result.time_s == times
    assert result.opening == pytest.approx(expected, abs=2e-14)
    assert result.target_opening == pytest.approx((0.0, 0.0, 0.0, 0.6, 0.6, 0.6, 1.0, 1.0, 1.0), abs=2e-14)

    def linear_area(opening):
        return 8.0e-4 * opening if opening <= 0.25 else 2.0e-4 + (opening - 0.25) * (8.0e-4 / 0.75)

    assert result.effective_area_m2 == pytest.approx(tuple(map(linear_area, expected)), rel=2e-13, abs=1e-16)
    records = {row["command_id"]: row for row in result.command_records}
    assert records["close"]["completion_bracket_s"] is None
    assert records["close"]["superseded_at_s"] == 0.6
    assert records["reverse"]["completion_bracket_s"] == [0.77, 1.0]
    assert records["open"]["completion_bracket_s"] == [1.4, 1.6]
    assert all(row["superseded_at_s"] is None for row in result.command_records[1:])
    assert result.kinematics_only and not result.hydraulic_or_field_validation
    result.check_integrity()
