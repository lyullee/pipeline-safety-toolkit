"""Independent smooth/fully-rough limits of the Darcy friction coefficient."""
import math

import pytest

from pipeline_integrity import evaluate
from pipeline_integrity.contracts import RunContext


def factor(reynolds, relative_roughness):
    # Fixed geometry and speed; viscosity is a synthetic correlation coordinate.
    diameter, velocity = 0.1, 1.0
    result = evaluate(
        "hydraulics.liquid_head_loss",
        {"diameter_m": diameter, "length_m": 1.0,
         "volume_flow_m3_s": velocity * math.pi * diameter**2 / 4,
         "kinematic_viscosity_m2_s": velocity * diameter / reynolds,
         "roughness_m": relative_roughness * diameter,
         "friction_method": "Colebrook"},
        RunContext(asset_id="friction-limit", scenario_id="synthetic-coefficient",
                   clock_id="steady", origin="synthetic"),
    )
    assert result.status == "computed"
    return result.values["darcy_friction_factor"]


@pytest.mark.parametrize("roughness", [0.001, 0.005, 0.01])
def test_finite_reynolds_converges_from_above_to_fully_rough_limit(roughness):
    asymptote = 1 / (-2 * math.log10(roughness / 3.7))**2
    values = [factor(re, roughness) for re in (1e5, 1e6, 1e7, 1e8)]
    errors = [value - asymptote for value in values]
    assert all(error > 0 for error in errors)
    assert all(later < earlier for earlier, later in zip(errors, errors[1:]))
    assert errors[-1] / asymptote < 1e-3
    # The asymptotic correction is first order in inverse Reynolds number.
    assert errors[-1] / errors[-2] == pytest.approx(0.1, rel=0.02)


def test_smooth_pipe_satisfies_smooth_limit_and_decreases_with_reynolds():
    reynolds = (5e4, 1e5, 1e6, 1e7)
    values = [factor(re, 0.0) for re in reynolds]
    assert all(later < earlier for earlier, later in zip(values, values[1:]))
    for re, f in zip(reynolds, values):
        inverse_root = 1 / math.sqrt(f)
        residual = inverse_root + 2 * math.log10(2.51 * inverse_root / re)
        assert abs(residual) < 1e-9
