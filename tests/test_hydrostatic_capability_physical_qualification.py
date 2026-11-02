"""Bounded qualification checks for constant-density static-column pressure."""

import pytest

from pipeline_integrity import evaluate
from pipeline_integrity.contracts import RunContext


CASES = (
    (500_000.0, 998.0, 35.0, 9.80665),
    (1_200_000.0, 850.0, -42.0, 9.81),
    (101_325.0, 1_000.0, 0.0, 9.80665),
    (800_000.0, 1_250.0, 12.5, 3.71),
    (2_000_000.0, 1_025.0, -4.0, 1.62),
)


def _context() -> RunContext:
    return RunContext(
        asset_id="static-column-reference",
        scenario_id="constant-density-static-column",
        clock_id="steady-state",
        origin="synthetic",
    )


@pytest.mark.parametrize("p_in,rho,delta_z,g", CASES)
def test_catalog_capability_matches_independent_hydrostatic_balance(p_in, rho, delta_z, g):
    result = evaluate(
        "hydraulics.hydrostatic",
        {
            "inlet_pressure_pa_abs": p_in,
            "density_kg_m3": rho,
            "outlet_minus_inlet_elevation_m": delta_z,
            "gravity_m_s2": g,
        },
        _context(),
    )

    # Independent reference: static force balance for a constant-density column.
    expected_pressure_difference = rho * g * delta_z
    expected_outlet_pressure = p_in - expected_pressure_difference
    assert expected_outlet_pressure > 0.0
    assert result.status == "computed"
    assert result.model_id == "hydraulics.hydrostatic"
    assert result.values["pressure_difference_pa"] == pytest.approx(
        expected_pressure_difference, rel=2e-15, abs=1e-12
    )
    assert result.values["outlet_pressure_pa_abs"] == pytest.approx(
        expected_outlet_pressure, rel=2e-15, abs=1e-12
    )


def test_catalog_capability_preserves_static_column_path_conservation():
    p_in, rho, gravity = 2_500_000.0, 1_030.0, 9.80665
    elevation_steps = (7.0, -11.0, 3.5, 6.0)
    pressure = p_in
    accumulated_pressure_difference = 0.0
    for delta_z in elevation_steps:
        result = evaluate(
            "hydraulics.hydrostatic",
            {
                "inlet_pressure_pa_abs": pressure,
                "density_kg_m3": rho,
                "outlet_minus_inlet_elevation_m": delta_z,
                "gravity_m_s2": gravity,
            },
            _context(),
        )
        assert result.status == "computed"
        accumulated_pressure_difference += result.values["pressure_difference_pa"]
        pressure = result.values["outlet_pressure_pa_abs"]

    direct = evaluate(
        "hydraulics.hydrostatic",
        {
            "inlet_pressure_pa_abs": p_in,
            "density_kg_m3": rho,
            "outlet_minus_inlet_elevation_m": sum(elevation_steps),
            "gravity_m_s2": gravity,
        },
        _context(),
    )
    expected_net_difference = rho * gravity * sum(elevation_steps)
    assert accumulated_pressure_difference == pytest.approx(expected_net_difference, abs=1e-9)
    assert pressure == pytest.approx(direct.values["outlet_pressure_pa_abs"], abs=1e-9)


def test_catalog_capability_withholds_nonpositive_absolute_pressure():
    result = evaluate(
        "hydraulics.hydrostatic",
        {
            "inlet_pressure_pa_abs": 1_000.0,
            "density_kg_m3": 1_000.0,
            "outlet_minus_inlet_elevation_m": 1.0,
            "gravity_m_s2": 9.80665,
        },
        _context(),
    )
    assert result.status == "out_of_scope"
    assert result.values == {}
