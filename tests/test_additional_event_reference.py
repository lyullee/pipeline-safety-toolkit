"""Independent nonlinear Coulomb/quadratic-drag stopping references.

For a horizontal unforced body, integrate m dv/dt=-F-k*v**2 up to
v=0. The resulting atan stop time and logarithmic stopping distance
exercise the event locator; the subsequent static interval must not drift.
These authored inputs verify prescribed-boundary mechanics, not seal data
or a coupled hydraulic prediction.
"""
import importlib.util
import math
from pathlib import Path

import pytest

from pipeline_integrity import pig_motion as pm

_spec = importlib.util.spec_from_file_location(
    'additional_pig_reference_example',
    Path(__file__).resolve().parents[1] / 'examples' / 'pig_motion.py',
)
_example = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_example)


@pytest.mark.parametrize('direction', [-1.0, 1.0])
@pytest.mark.parametrize('maximum_step', [0.5, 0.1])
def test_nonlinear_drag_stop_event_matches_atan_time_log_distance_and_static_memory(
    direction, maximum_step,
):
    mass, friction, drag, speed, position, start = 10.0, 2.0, 4.0, 2.0, 50.0, 37.0
    stop_time = mass / math.sqrt(friction * drag) * math.atan(speed * math.sqrt(drag / friction))
    stop_distance = mass / (2.0 * drag) * math.log1p(drag * speed**2 / friction)
    window = pm.PigBoundaryWindow(
        id='unforced_decay', duration_s=10.0,
        upstream_pressure_pa_abs=100000.0, downstream_pressure_pa_abs=100000.0,
        external_axial_force_N=0.0, provider_id='authored_equal_pressure',
        provider_version='1', evidence_id='authored_pig_boundary',
    )
    request, context = _example.make_case(
        reviewed=True, windows=(window,), mass_kg=mass,
        initial_position_m=position, initial_velocity_m_s=direction * speed,
        start_time_s=start, static_friction_N=3.0, kinetic_friction_N=friction,
        quadratic_drag_N_s2_m2=drag, maximum_step_s=maximum_step,
        relative_tolerance=1e-10, position_atol_m=1e-10, velocity_atol_m_s=1e-10,
    )
    record = pm.evaluate_pig_motion(request, context)
    assert record['status'] == 'computed_diagnostic', record['notes']
    stops = [event for event in record['transitions'] if event['event'] == 'velocity_zero']
    assert len(stops) == 1
    assert stops[0]['time_s'] == pytest.approx(start + stop_time, abs=2e-7)
    expected_position = position + direction * stop_distance
    assert stops[0]['raw_position_m'] == pytest.approx(expected_position, abs=2e-7)
    assert record['values']['final_position_m'] == pytest.approx(expected_position, abs=2e-7)
    assert record['values']['final_velocity_m_s'] == 0.0
    assert record['history'][-1]['friction_work_J'] == pytest.approx(-mass * speed**2 / 2, abs=2e-6)
    assert record['history'][-1]['net_impulse_N_s'] == pytest.approx(-direction * mass * speed, abs=2e-6)
    for row in record['history']:
        assert row['within_budget']
        if row['time_s'] > start + stop_time + 1e-6:
            assert row['position_m'] == pytest.approx(expected_position, abs=2e-7)
            assert row['velocity_m_s'] == 0.0
