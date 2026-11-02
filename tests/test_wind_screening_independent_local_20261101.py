"""Independent pressure/force and dimensional-frequency references."""
import math

import pytest
from pydantic import ValidationError

from pipeline_integrity.wind_screening import (
    VortexFrequencyInput, WindLoadInput, evaluate_vortex_frequency, evaluate_wind_load,
)


def wind(**updates):
    return WindLoadInput(**(dict(air_density_kg_m3=1.2, wind_speed_m_s=20.0,
        drag_coefficient=0.8, projected_area_m2=3.0) | updates))


def test_wind_pressure_matches_kinetic_energy_per_volume_and_area_load():
    # 1 m3 carries 1.2 kg; its kinetic energy is 240 J. Energy density is pressure.
    got = evaluate_wind_load(wind())
    assert got.dynamic_pressure_Pa == pytest.approx(240.0)
    assert got.wind_force_N == pytest.approx(576.0)
    # Force acting through 2 m does 1152 J of work at this constant load.
    assert got.wind_force_N * 2.0 == pytest.approx(1152.0)
    assert got.structural_response_assessed is False


def test_velocity_density_area_and_reviewed_multipliers_have_distinct_scalings():
    base = evaluate_wind_load(wind())
    faster = evaluate_wind_load(wind(wind_speed_m_s=40.0))
    larger = evaluate_wind_load(wind(air_density_kg_m3=2.4, projected_area_m2=6.0))
    factored = evaluate_wind_load(wind(pressure_multiplier=1.5, force_multiplier=0.75))
    assert faster.wind_force_N / base.wind_force_N == pytest.approx(4.0)
    assert larger.wind_force_N / base.wind_force_N == pytest.approx(4.0)
    assert factored.dynamic_pressure_Pa == base.dynamic_pressure_Pa
    assert factored.applied_pressure_Pa == pytest.approx(360.0)
    assert factored.wind_force_N == pytest.approx(648.0)


@pytest.mark.parametrize("updates", [{"wind_speed_m_s":0.0}, {"projected_area_m2":0.0}, {"drag_coefficient":0.0}])
def test_zero_load_limits(updates):
    assert evaluate_wind_load(wind(**updates)).wind_force_N == 0.0


def test_shedding_frequencies_recover_dimensionless_strouhal_and_harmonic():
    req = VortexFrequencyInput(cross_flow_speed_m_s=12.0, outside_diameter_m=0.3,
                              strouhal_number=0.25)
    got = evaluate_vortex_frequency(req)
    assert got.lift_frequency_Hz == pytest.approx(10.0)
    assert got.drag_frequency_Hz == pytest.approx(20.0)
    assert got.lift_frequency_Hz * req.outside_diameter_m / req.cross_flow_speed_m_s == pytest.approx(0.25)
    scaled = evaluate_vortex_frequency(req.model_copy(update={"outside_diameter_m":0.6,
                                                             "cross_flow_speed_m_s":24.0}))
    assert scaled.lift_frequency_Hz == got.lift_frequency_Hz
    assert got.lift_within_supplied_band is None
    assert got.amplitude_or_fatigue_assessed is False


def test_caller_band_is_closed_and_compares_each_harmonic_separately():
    req = VortexFrequencyInput(cross_flow_speed_m_s=10.0, outside_diameter_m=0.5,
        strouhal_number=0.25, natural_frequency_Hz=4.0, relative_nearness_band=0.25)
    got = evaluate_vortex_frequency(req)
    assert got.lift_relative_offset == 0.25 and got.lift_within_supplied_band is True
    assert got.drag_relative_offset == 1.5 and got.drag_within_supplied_band is False
    stricter = evaluate_vortex_frequency(req.model_copy(update={"relative_nearness_band":0.249}))
    assert stricter.lift_within_supplied_band is False


def test_still_flow_has_no_shedding_frequency():
    got = evaluate_vortex_frequency(VortexFrequencyInput(cross_flow_speed_m_s=0.0,
        outside_diameter_m=0.5, strouhal_number=0.2))
    assert got.lift_frequency_Hz == got.drag_frequency_Hz == 0.0


@pytest.mark.parametrize("field,value", [("air_density_kg_m3",0.0), ("wind_speed_m_s",-1.0),
    ("drag_coefficient",-0.1), ("projected_area_m2",float("inf")), ("pressure_multiplier",float("nan")),
    ("wind_speed_m_s",True)])
def test_invalid_wind_inputs_fail(field,value):
    with pytest.raises(ValidationError): wind(**{field:value})


@pytest.mark.parametrize("updates", [{"outside_diameter_m":0.0}, {"strouhal_number":0.0},
    {"natural_frequency_Hz":4.0}, {"relative_nearness_band":0.1},
    {"natural_frequency_Hz":4.0,"relative_nearness_band":1.1}])
def test_invalid_or_unpaired_frequency_inputs_fail(updates):
    with pytest.raises(ValidationError):
        VortexFrequencyInput(**(dict(cross_flow_speed_m_s=5.0,outside_diameter_m=0.5,
                                    strouhal_number=0.2)|updates))


def test_cloned_invalid_values_and_nonfinite_results_do_not_escape_validation():
    with pytest.raises(ValidationError): evaluate_wind_load(wind().model_copy(update={"wind_speed_m_s":math.nan}))
    with pytest.raises(ValueError,match="finite numerical"):
        evaluate_wind_load(wind(wind_speed_m_s=1e300))
    with pytest.raises(ValueError,match="finite numerical"):
        evaluate_vortex_frequency(VortexFrequencyInput(cross_flow_speed_m_s=1e300,
            outside_diameter_m=1e-200,strouhal_number=0.2))
