"""Closed-form references for two individual circular-pipe constitutive laws."""

import importlib.util
import math
from pathlib import Path

import pytest

from pipeline_integrity import slurry_transport as st

_spec = importlib.util.spec_from_file_location(
    "rheology_reference_example", Path(__file__).parents[1] / "examples/slurry_transport.py"
)
_example = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_example)


def evaluate(rheology, n, yield_stress, signed_flow):
    q, _ = _example.make_case(reviewed=True)
    properties = q.properties.model_copy(update={
        "rheology": rheology, "flow_index": n, "yield_stress_Pa": yield_stress,
        "supported_absolute_velocity_m_s": (0.0, 20.0),
    })
    q, context = _example.make_case(
        reviewed=True, properties=properties, volume_flow_m3_s=signed_flow,
        root_absolute_stress_tolerance_Pa=1e-11,
        quadrature_absolute_error_m3_s=1e-15,
    )
    result = st.evaluate_slurry_transport(q, context)
    assert result["status"] == "computed_diagnostic", result["notes"]
    return q, result["values"]


@pytest.mark.parametrize("n", [0.4, 0.7, 1.0, 1.4, 2.0])
@pytest.mark.parametrize("sign", [-1, 1])
def test_power_law_inverse_matches_integrated_velocity_solution(n, sign):
    radius, consistency, wall_rate = 0.025, 0.1, 5.0
    tau = consistency * wall_rate**n
    flow = math.pi * radius**3 * n / (3*n+1) * wall_rate
    q, values = evaluate("power_law", n, 0.0, sign*flow)
    assert values["wall_shear_stress_Pa"] == pytest.approx(tau, rel=2e-8)
    assert values["wall_shear_rate_s_inv"] == pytest.approx(wall_rate, rel=2e-8)
    pressure = sign * 2*q.length_m*tau/radius
    assert values["signed_friction_pressure_drop_Pa"] == pytest.approx(pressure, rel=2e-8)
    assert values["plug_radius_fraction"] == 0
    assert values["dissipated_power_W"] == pytest.approx(flow*abs(pressure), rel=2e-8)


@pytest.mark.parametrize("excess_stress", [0.25, 2.0, 10.0, 40.0])
@pytest.mark.parametrize("sign", [-1, 1])
def test_bingham_inverse_matches_closed_form_and_plug_radius(excess_stress, sign):
    radius, viscosity, yield_stress = 0.025, 0.1, 5.0
    tau = yield_stress + excess_stress
    a = yield_stress/tau
    # Factored polynomial remains accurate when the yielded annulus is thin.
    factor = (1-a)**2 * (a*a+2*a+3)/3
    flow = math.pi*radius**3*tau/(4*viscosity)*factor
    q, values = evaluate("bingham", 1.0, yield_stress, sign*flow)
    assert values["wall_shear_stress_Pa"] == pytest.approx(tau, rel=2e-8)
    assert values["plug_radius_fraction"] == pytest.approx(a, rel=2e-8)
    assert values["signed_friction_pressure_drop_Pa"] == pytest.approx(
        sign*2*q.length_m*tau/radius, rel=2e-8
    )
    assert values["dissipated_power_W"] > 0


@pytest.mark.parametrize("yield_stress", [0.0, 5.0])
def test_stationary_limit_preserves_yield_pressure_nonuniqueness(yield_stress):
    q, values = evaluate("bingham", 1.0, yield_stress, 0.0)
    assert values["dissipated_power_W"] == 0
    assert values["pressure_prediction_unique"] is (yield_stress == 0)
    if yield_stress:
        lower, upper = values["zero_flow_formal_outlet_pressure_interval_Pa"]
        span = 4*q.length_m*yield_stress/q.inner_diameter_m
        assert (lower, upper) == pytest.approx(
            (q.inlet_pressure_pa_abs-span, q.inlet_pressure_pa_abs+span)
        )
        assert values["outlet_pressure_pa_abs"] is None
