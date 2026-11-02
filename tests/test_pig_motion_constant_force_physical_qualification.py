"""Independent Newtonian limit for prescribed-boundary pig motion."""
import importlib.util
import sys
from pathlib import Path
import pytest
from pydantic import ValidationError
from pipeline_integrity.pig_motion import evaluate_pig_motion

spec = importlib.util.spec_from_file_location("pig_motion_constant_force_example", Path(__file__).parents[1] / "examples" / "pig_motion.py")
example = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = example
spec.loader.exec_module(example)


def run_case(*, maximum_step_s, force_N=10.0, mass_kg=2.0, duration_s=2.0):
    window = example.PigBoundaryWindow(id="constant_force", duration_s=duration_s,
        upstream_pressure_pa_abs=100000.0, downstream_pressure_pa_abs=100000.0,
        external_axial_force_N=force_N, provider_id="independent_force", provider_version="1",
        evidence_id="authored_pig_boundary")
    request, context = example.make_case(reviewed=True, windows=(window,), mass_kg=mass_kg,
        initial_position_m=1.0, initial_velocity_m_s=0.0, inclination_rad=0.0,
        gravity_m_s2=0.0, static_friction_N=0.0, kinetic_friction_N=0.0,
        quadratic_drag_N_s2_m2=0.0, maximum_step_s=maximum_step_s,
        relative_tolerance=1e-10, position_atol_m=1e-10, velocity_atol_m_s=1e-10)
    return evaluate_pig_motion(request, context)


@pytest.mark.parametrize("step", [0.5, 0.1, 0.025])
def test_constant_force_no_drag_exact_trajectory_work_and_impulse(step):
    result = run_case(maximum_step_s=step)
    assert result["status"] == "computed_diagnostic", result["notes"]
    v = result["values"]
    # m=2 kg, F=10 N, t=2 s, x0=1 m: a=5, v=10, x=11, W=100, J=20.
    assert v["final_position_m"] == pytest.approx(11.0, abs=2e-8)
    assert v["final_velocity_m_s"] == pytest.approx(10.0, abs=2e-8)
    assert result["history"][-1]["external_work_J"] == pytest.approx(100.0, abs=2e-8)
    assert result["history"][-1]["net_impulse_N_s"] == pytest.approx(20.0, abs=2e-8)
    assert v["actual_duration_s"] == pytest.approx(2.0, abs=2e-10)
    assert all(row["within_budget"] for row in result["history"])


def test_force_to_mass_scaling_matches_newtonian_solution():
    base = run_case(maximum_step_s=0.05, force_N=10.0, mass_kg=2.0, duration_s=1.0)["values"]
    force2 = run_case(maximum_step_s=0.05, force_N=20.0, mass_kg=2.0, duration_s=1.0)["values"]
    mass2 = run_case(maximum_step_s=0.05, force_N=10.0, mass_kg=4.0, duration_s=1.0)["values"]
    assert force2["final_velocity_m_s"] == pytest.approx(2*base["final_velocity_m_s"], abs=2e-8)
    assert mass2["final_velocity_m_s"] == pytest.approx(0.5*base["final_velocity_m_s"], abs=2e-8)
    assert force2["final_position_m"] - 1 == pytest.approx(2*(base["final_position_m"]-1), abs=2e-8)
    assert mass2["final_position_m"] - 1 == pytest.approx(0.5*(base["final_position_m"]-1), abs=2e-8)


def test_outward_initial_velocity_at_upstream_endpoint_is_rejected():
    window = example.PigBoundaryWindow(id="window", duration_s=1.0,
        upstream_pressure_pa_abs=100000.0, downstream_pressure_pa_abs=100000.0,
        external_axial_force_N=0.0, provider_id="authored", provider_version="1",
        evidence_id="authored_pig_boundary")
    with pytest.raises(ValidationError):
        example.make_case(reviewed=True, windows=(window,), initial_position_m=0.0,
            initial_velocity_m_s=-0.1, static_friction_N=0.0, kinetic_friction_N=0.0,
            quadratic_drag_N_s2_m2=0.0)
