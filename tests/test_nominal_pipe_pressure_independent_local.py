"""Outside-diameter convention, exact unit changes and inverse consistency."""

import pytest
from pydantic import ValidationError

from pipeline_integrity.nominal_pipe_pressure import (
    NominalHoopStressInput, NominalThicknessInput,
    nominal_hoop_stress_Pa, nominal_wall_thickness_m,
)


@pytest.mark.parametrize("pressure,diameter,wall,expected", [
    (1.0e6, 0.4, 0.01, 20.0e6),
    (2.5e6, 0.6, 0.015, 50.0e6),
    (0.0, 0.5, 0.005, 0.0),
])
def test_authored_nominal_reference_points(pressure, diameter, wall, expected):
    result = nominal_hoop_stress_Pa(NominalHoopStressInput(
        differential_pressure_Pa=pressure, outside_diameter_m=diameter, wall_thickness_m=wall))
    assert result == pytest.approx(expected, rel=2e-15, abs=1e-14)


def test_exact_customary_unit_normalization_preserves_nominal_stress():
    inch, psi = 0.0254, 6894.757293168
    result = nominal_hoop_stress_Pa(NominalHoopStressInput(
        differential_pressure_Pa=600.0*psi, outside_diameter_m=20.0*inch, wall_thickness_m=0.2*inch))
    assert result/psi == pytest.approx(30000.0, rel=2e-15)


def test_outside_diameter_is_not_silently_replaced_by_mean_diameter():
    # At D/t=40, the mean-diameter value is 2.5% below the outside-diameter value.
    result = nominal_hoop_stress_Pa(NominalHoopStressInput(
        differential_pressure_Pa=1.0e6, outside_diameter_m=0.4, wall_thickness_m=0.01))
    mean_diameter_value = 19.5e6
    assert result - mean_diameter_value == pytest.approx(0.5e6)
    assert result != mean_diameter_value


@pytest.mark.parametrize("pressure,stress,expected", [(2e6, 50e6, 0.012), (0.0, 50e6, 0.0)])
def test_inverse_reference_points_and_zero_pressure(pressure, stress, expected):
    assert nominal_wall_thickness_m(NominalThicknessInput(
        differential_pressure_Pa=pressure, outside_diameter_m=0.6,
        supplied_nominal_stress_Pa=stress)) == pytest.approx(expected, abs=1e-15)


def test_inverse_round_trip_uses_same_explicit_diameter_convention():
    for wall in (0.002, 0.007, 0.02):
        stress = nominal_hoop_stress_Pa(NominalHoopStressInput(
            differential_pressure_Pa=3e6, outside_diameter_m=0.5, wall_thickness_m=wall))
        assert nominal_wall_thickness_m(NominalThicknessInput(
            differential_pressure_Pa=3e6, outside_diameter_m=0.5,
            supplied_nominal_stress_Pa=stress)) == pytest.approx(wall, rel=2e-15)


@pytest.mark.parametrize("changes", [
    {"differential_pressure_Pa": -1.0}, {"outside_diameter_m": 0.0},
    {"wall_thickness_m": 0.2}, {"wall_thickness_m": float("nan")},
])
def test_invalid_stress_inputs(changes):
    p = dict(differential_pressure_Pa=1e6, outside_diameter_m=0.4, wall_thickness_m=0.01)
    p.update(changes)
    with pytest.raises(ValidationError):
        NominalHoopStressInput(**p)


def test_inverse_rejects_no_hollow_section_and_nonpositive_supplied_stress():
    with pytest.raises(ValueError, match="positive internal diameter"):
        nominal_wall_thickness_m(NominalThicknessInput(
            differential_pressure_Pa=20e6, outside_diameter_m=0.4, supplied_nominal_stress_Pa=10e6))
    with pytest.raises(ValidationError):
        NominalThicknessInput(differential_pressure_Pa=1e6, outside_diameter_m=0.4,
                              supplied_nominal_stress_Pa=0.0)


def test_arithmetic_overflow_is_not_a_finite_result():
    with pytest.raises(ValueError, match="finite"):
        nominal_hoop_stress_Pa(NominalHoopStressInput(
            differential_pressure_Pa=1e308, outside_diameter_m=1.0, wall_thickness_m=0.001))
