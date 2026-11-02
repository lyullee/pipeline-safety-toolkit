"""Independent per-length normalization, coefficient-convention and phase references."""
import math

import pytest
from pydantic import ValidationError

from pipeline_integrity.cylinder_hydrodynamics import (
    StationaryCylinderLoad, evaluate_stationary_cylinder_load,
)


def request(**updates):
    return StationaryCylinderLoad(**(dict(fluid_density_kg_m3=1000.0,
        outside_diameter_m=0.4,transverse_velocity_m_s=2.0,
        transverse_acceleration_m_s2=0.0,drag_coefficient=1.2,
        lift_coefficient_upward=0.5,inertia_coefficient=2.0,
        inertia_coefficient_kind="total_inertia")|updates))


def test_section_load_equals_independent_whole_segment_pressure_area_normalization():
    # A 5 m segment presents 2 m2: kinetic pressure=2000 Pa, force=4800 N.
    q=evaluate_stationary_cylinder_load(request())
    assert q.drag_horizontal_n_m == pytest.approx(4800.0/5.0)
    assert q.lift_upward_n_m == pytest.approx(2000.0/5.0)
    assert q.total_inertia_horizontal_n_m == 0.0
    assert q.body_motion_modelled is False


def test_velocity_reversal_changes_drag_but_lift_uses_its_own_signed_axis():
    a=evaluate_stationary_cylinder_load(request())
    b=evaluate_stationary_cylinder_load(request(transverse_velocity_m_s=-2.0))
    c=evaluate_stationary_cylinder_load(request(lift_coefficient_upward=-0.5))
    assert b.drag_horizontal_n_m == -a.drag_horizontal_n_m
    assert b.lift_upward_n_m == a.lift_upward_n_m
    assert c.lift_upward_n_m == -a.lift_upward_n_m


def test_total_and_added_conventions_match_and_keep_displaced_fluid_component():
    a=evaluate_stationary_cylinder_load(request(transverse_velocity_m_s=0.0,
                                             transverse_acceleration_m_s2=-3.0))
    b=evaluate_stationary_cylinder_load(request(transverse_velocity_m_s=0.0,
        transverse_acceleration_m_s2=-3.0,inertia_coefficient=1.0,
        inertia_coefficient_kind="added_mass"))
    # A metre displaces 40*pi kg; each Cm=2 component is -120*pi N/m.
    assert a.displaced_fluid_mass_kg_m == pytest.approx(40.0*math.pi)
    assert a.displaced_fluid_inertia_horizontal_n_m == pytest.approx(-120.0*math.pi)
    assert a.added_mass_inertia_horizontal_n_m == pytest.approx(-120.0*math.pi)
    assert a.total_inertia_horizontal_n_m == pytest.approx(-240.0*math.pi)
    assert a == b


def test_zero_added_mass_retains_displaced_fluid_acceleration_load():
    q=evaluate_stationary_cylinder_load(request(transverse_acceleration_m_s2=1.0,
        inertia_coefficient=0.0,inertia_coefficient_kind="added_mass"))
    assert q.added_mass_inertia_horizontal_n_m == 0.0
    assert q.total_inertia_horizontal_n_m == pytest.approx(40.0*math.pi)
    assert q.total_inertia_coefficient == 1.0


def test_diameter_and_velocity_scaling_distinguish_area_from_projected_width():
    a=evaluate_stationary_cylinder_load(request(transverse_acceleration_m_s2=1.0))
    b=evaluate_stationary_cylinder_load(request(outside_diameter_m=0.8,
                                             transverse_acceleration_m_s2=1.0))
    c=evaluate_stationary_cylinder_load(request(transverse_velocity_m_s=4.0,
                                             transverse_acceleration_m_s2=1.0))
    assert b.drag_horizontal_n_m/a.drag_horizontal_n_m == pytest.approx(2.0)
    assert b.lift_upward_n_m/a.lift_upward_n_m == pytest.approx(2.0)
    assert b.total_inertia_horizontal_n_m/a.total_inertia_horizontal_n_m == pytest.approx(4.0)
    assert c.drag_horizontal_n_m/a.drag_horizontal_n_m == pytest.approx(4.0)
    assert c.total_inertia_horizontal_n_m == a.total_inertia_horizontal_n_m


def test_acceleration_impulse_matches_displaced_mass_times_velocity_change():
    # Constant acceleration -0.5 m/s2 over 4 s produces delta-u=-2 m/s.
    q=evaluate_stationary_cylinder_load(request(transverse_velocity_m_s=0.0,
        transverse_acceleration_m_s2=-0.5,inertia_coefficient=1.5))
    assert q.total_inertia_horizontal_n_m*4.0 == pytest.approx(-120.0*math.pi)
    assert q.total_horizontal_n_m == q.total_inertia_horizontal_n_m


def test_signed_inertia_can_cancel_drag_without_adding_magnitudes():
    q=evaluate_stationary_cylinder_load(request(transverse_acceleration_m_s2=-1.0))
    assert q.total_horizontal_n_m == pytest.approx(960.0-80.0*math.pi)
    assert q.total_horizontal_n_m < q.drag_horizontal_n_m


@pytest.mark.parametrize("updates", [{"fluid_density_kg_m3":0.0},
    {"outside_diameter_m":0.0},{"drag_coefficient":-0.1},
    {"inertia_coefficient":0.5},{"inertia_coefficient_kind":"ambiguous"},
    {"lift_coefficient_upward":float("nan")},{"transverse_velocity_m_s":True}])
def test_invalid_or_ambiguous_inputs_are_rejected(updates):
    with pytest.raises(ValidationError): request(**updates)


def test_cloned_invalid_and_unrepresentable_values_do_not_escape():
    with pytest.raises(ValidationError):
        evaluate_stationary_cylinder_load(request().model_copy(update={"outside_diameter_m":-1.0}))
    with pytest.raises(ValueError,match="finite numerical"):
        evaluate_stationary_cylinder_load(request(transverse_velocity_m_s=1e300))
