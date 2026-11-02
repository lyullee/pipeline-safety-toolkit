"""Independent pressure-potential and path-conservation checks."""

import pytest

from pipeline_integrity.models.hydraulics import Hydrostatic, hydrostatic


CASES = (
    (500_000.0, 998.0, 35.0, 9.80665),
    (1_200_000.0, 850.0, -42.0, 9.81),
    (101_325.0, 1_000.0, 0.0, 9.80665),
    (800_000.0, 1_250.0, 12.5, 3.71),
    (2_000_000.0, 1_025.0, -4.0, 1.62),
)


@pytest.mark.parametrize("p_in,rho,delta_z,g", CASES)
def test_static_column_pressure_matches_gravitational_potential_balance(p_in, rho, delta_z, g):
    request = Hydrostatic(
        inlet_pressure_pa_abs=p_in,
        density_kg_m3=rho,
        outlet_minus_inlet_elevation_m=delta_z,
        gravity_m_s2=g,
    )
    result = hydrostatic(request)
    expected_drop = rho * g * delta_z
    assert result.status == "computed"
    assert result.values["pressure_difference_pa"] == pytest.approx(expected_drop, rel=2e-15, abs=1e-12)
    assert result.values["outlet_pressure_pa_abs"] == pytest.approx(p_in - expected_drop, rel=2e-15)
    assert (
        (p_in - result.values["outlet_pressure_pa_abs"]) / (rho * g)
        == pytest.approx(delta_z, abs=1e-12)
    )


def test_partitioned_static_column_is_path_independent_and_conservative():
    p_in, rho, g = 2_500_000.0, 1_030.0, 9.80665
    elevation_steps = (7.0, -11.0, 3.5, 6.0)
    pressure = p_in
    accumulated_drop = 0.0
    for delta_z in elevation_steps:
        result = hydrostatic(
            Hydrostatic(
                inlet_pressure_pa_abs=pressure,
                density_kg_m3=rho,
                outlet_minus_inlet_elevation_m=delta_z,
                gravity_m_s2=g,
            )
        )
        assert result.status == "computed"
        step_drop = rho * g * delta_z
        assert result.values["pressure_difference_pa"] == pytest.approx(step_drop, abs=1e-9)
        accumulated_drop += step_drop
        pressure = result.values["outlet_pressure_pa_abs"]

    net_elevation_change = sum(elevation_steps)
    direct = hydrostatic(
        Hydrostatic(
            inlet_pressure_pa_abs=p_in,
            density_kg_m3=rho,
            outlet_minus_inlet_elevation_m=net_elevation_change,
            gravity_m_s2=g,
        )
    )
    assert accumulated_drop == pytest.approx(rho * g * net_elevation_change, abs=1e-9)
    assert pressure == pytest.approx(direct.values["outlet_pressure_pa_abs"], abs=1e-9)


def test_nonpositive_absolute_pressure_is_withheld_at_hydrostatic_boundary():
    result = hydrostatic(
        Hydrostatic(
            inlet_pressure_pa_abs=1_000.0,
            density_kg_m3=1_000.0,
            outlet_minus_inlet_elevation_m=1.0,
        )
    )
    assert result.status == "out_of_scope"
    assert result.values == {}
