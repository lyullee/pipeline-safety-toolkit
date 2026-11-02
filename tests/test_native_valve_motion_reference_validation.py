"""Native rate-limited actuator against an authored piecewise trajectory.

Command reversals occur before stroke completion. An independent continuous
position reference checks both coarse irregular and dense sampling; completion
is only bracketed by published samples. No liquid/gas flow or fluid properties
are needed, and kinematic agreement is not actuator field calibration.
"""
import pytest

from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.valve_motion import (
    ConductanceCurve, ValveCommand, ValveMotionRequest, simulate_valve_motion,
)


def _position(normalized_time):
    if normalized_time <= 3.0:
        return 0.2 + normalized_time / 10.0
    if normalized_time <= 6.0:
        return 0.5 - (normalized_time - 3.0) / 10.0
    if normalized_time <= 8.0:
        return 0.2 + (normalized_time - 6.0) / 10.0
    return 0.4


@pytest.mark.parametrize('time_scale', [0.25, 4.0])
@pytest.mark.parametrize('dense', [False, True])
def test_native_actuator_reversal_path_time_scaling_and_completion_brackets(time_scale, dense):
    samples = tuple(i / 4.0 for i in range(49)) if dense else (0.0, 2.0, 5.0, 9.0, 12.0)
    request = ValveMotionRequest(component_id='valve-reference', initial_opening=0.2,
        full_stroke_time_s=10.0 * time_scale,
        sample_time_s=tuple(t * time_scale for t in samples),
        commands=tuple(ValveCommand(id=name, time_s=time * time_scale,
            target_opening=opening, evidence_id='authored-command')
            for name, time, opening in (('open', 0.0, 1.0), ('reverse', 3.0, 0.0), ('partial', 6.0, 0.4))),
        conductance=ConductanceCurve(opening=(0.0, 1.0), effective_area_m2=(0.0, 0.0001),
            parameter_source_id='authored-linear-area'))
    context = RunContext(asset_id=request.component_id, scenario_id='command_reversals',
        clock_id='elapsed_seconds', origin='simulation', evidence=(
            EvidenceRef(id='authored-command', origin='synthetic',
                citation='Authored piecewise target command times'),
            EvidenceRef(id='authored-linear-area', origin='synthetic',
                citation='Authored linear conductance for kinematic projection'),))
    record = simulate_valve_motion(request, context)
    record.check_integrity()
    assert record.provider['id'] == 'lh2dt_models.StrokeLimitedCommandedValve'
    assert record.provider['fluid_evaluation_enabled'] is False
    for time, opening, area in zip(record.time_s, record.opening, record.effective_area_m2, strict=True):
        expected = _position(time / time_scale)
        assert opening == pytest.approx(expected, abs=2e-12)
        assert area == pytest.approx(0.0001 * expected, abs=2e-16)
    events = {row['command_id']: row for row in record.command_records}
    assert events['open']['opening_at_issue'] == 0.2
    assert events['reverse']['opening_at_issue'] == pytest.approx(0.5, abs=2e-12)
    assert events['partial']['opening_at_issue'] == pytest.approx(0.2, abs=2e-12)
    assert events['open']['superseded_at_s'] == 3.0 * time_scale
    assert events['reverse']['superseded_at_s'] == 6.0 * time_scale
    assert events['open']['completion_bracket_s'] is None
    assert events['reverse']['completion_bracket_s'] is None
    assert events['partial']['superseded_at_s'] is None
    lower, upper = events['partial']['completion_bracket_s']
    assert lower <= 8.0 * time_scale <= upper
    assert upper - lower <= (0.25 if dense else 3.0) * time_scale + 1e-12
    assert record.opening[-1] == pytest.approx(0.4, abs=2e-12)
    assert record.kinematics_only is True
    assert record.hydraulic_or_field_validation is False
