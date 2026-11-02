import pint
import pytest

from pipeline_integrity.units import convert_quantity


def test_explicit_volume_and_offset_temperature_conversions():
    assert convert_quantity(36.0, "m3/h", "m3/s") == pytest.approx(0.01)
    assert convert_quantity(20.0, "degC", "K") == pytest.approx(293.15)
    with pytest.raises(pint.DimensionalityError):
        convert_quantity(1.0, "m", "s")


def test_unit_conversion_does_not_infer_pressure_basis_or_unknown_values():
    for value, unit in [(True, "m"), (float("nan"), "m"), (1.0, "Pa_abs"), (1.0, "bar_unspecified")]:
        with pytest.raises(ValueError):
            convert_quantity(value, unit, "Pa")
