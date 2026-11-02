"""Independent closed-form checks for the prescribed-boundary pig solver.

The reference is the exact solution of m*x'' = F - k*x'*abs(x') for a
constant positive force, zero Coulomb friction, and a stationary start. It
does not validate pressure prediction, sealing, or receiver impact physics.
"""

import importlib.util
import math
import sys
from pathlib import Path

import pytest

from pipeline_integrity.pig_motion import evaluate_pig_motion


_spec = importlib.util.spec_from_file_location(
    "pig_motion_reference_example", Path(__file__).parents[1] / "examples" / "pig_motion.py"
)
_example = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _example
_spec.loader.exec_module(_example)


def _case(*, allow_budget_rejection=False, **changes):
    options = {
        "static_friction_N": 0.0,
        "kinetic_friction_N": 0.0,
        "quadratic_drag_N_s2_m2": 1.0,
        "maximum_step_s": 0.1,
    } | changes
    request, context = _example.make_case(reviewed=True, **options)
    result = evaluate_pig_motion(request, context)
    accepted = result["status"] == "computed_diagnostic"
    assert accepted or (
        allow_budget_rejection
        and result["status"] == "diagnostic_rejected"
        and result["values"] is not None
    ), result["notes"]
    return request, context, result


def _reference(time_s):
    # F=100 N, m=10 kg, k=1 N s^2/m^2; gamma=sqrt(F*k)/m=1/s.
    return 10.0 * math.log(math.cosh(time_s)), 10.0 * math.tanh(time_s)


@pytest.mark.parametrize("duration_s", [0.05, 0.25, 1.0, 2.0])
def test_quadratic_drag_trajectory_matches_independent_closed_form(duration_s):
    _, _, result = _case(windows=(_example.PigBoundaryWindow(
        id="constant", duration_s=duration_s, upstream_pressure_pa_abs=110000.0,
        downstream_pressure_pa_abs=100000.0, external_axial_force_N=0.0,
        provider_id="constant", provider_version="1", evidence_id="authored_pig_boundary",
    ),))
    dx, velocity = _reference(duration_s)
    assert result["values"]["final_position_m"] == pytest.approx(1.0 + dx, abs=2e-7)
    assert result["values"]["final_velocity_m_s"] == pytest.approx(velocity, abs=2e-7)
    assert all(row["within_budget"] for row in result["history"])


def test_drag_event_reference_and_budget_response_at_coarse_and_fine_steps():
    length = 5.0
    exact_time = math.acosh(math.exp((length - 1.0) / 10.0))
    exact_position, exact_velocity = _reference(exact_time)
    assert 1.0 + exact_position == pytest.approx(length, abs=1e-14)

    runs = []
    for step, tolerance in ((0.25, 1e-5), (0.025, 1e-9)):
        _, _, result = _case(
            allow_budget_rejection=(step == 0.25),
            length_m=length,
            maximum_step_s=step,
            relative_tolerance=tolerance,
            position_atol_m=tolerance * 0.1,
            velocity_atol_m_s=tolerance * 0.1,
        )
        assert result["values"]["termination"] == "downstream_boundary"
        assert result["values"]["final_position_m"] == pytest.approx(length, abs=2e-7)
        energy_error = abs(result["values"]["endpoint_impact_energy_J"] - 5.0 * exact_velocity**2)
        assert energy_error <= (5e-5 if step == 0.25 else 2e-5)
        result["reference_errors"] = {
            "arrival_time_s": abs(result["values"]["actual_duration_s"] - exact_time),
            "arrival_velocity_m_s": abs(result["values"]["final_velocity_m_s"] - exact_velocity),
            "arrival_energy_J": energy_error,
        }
        assert result["reference_errors"]["arrival_time_s"] <= 2e-6
        assert result["reference_errors"]["arrival_velocity_m_s"] <= 2e-6
        runs.append(result)

    coarse, fine = runs
    # At rtol=1e-5, coarse work quadrature can exceed the strict default
    # conservation budget. The solver must expose that rejection while keeping
    # its trajectory available for this independent analytic check.
    assert coarse["status"] == "diagnostic_rejected"
    assert any(not row["within_budget"] for row in coarse["history"])
    assert fine["status"] == "computed_diagnostic"
    assert all(row["within_budget"] for row in fine["history"])
    for quantity in ("arrival_time_s", "arrival_velocity_m_s", "arrival_energy_J"):
        assert fine["reference_errors"][quantity] <= coarse["reference_errors"][quantity]
