"""Independent hydrostatic surface traction and exact material-volume budgets."""
import math
import pytest
from scipy.integrate import quad
from pipeline_integrity.coated_pipe_weight import (
    CoatedPipeWeightRequest,evaluate_coated_pipe_weight,size_dense_coating_for_submerged_weight)


def request(**changes):
    d=dict(outer_diameter_m=.6,inner_diameter_m=.56,coating_thickness_m=.08,
        pipe_unit_weight_N_m3=77000.,coating_unit_weight_N_m3=23000.,
        surrounding_fluid_unit_weight_N_m3=10000.,
        fully_submerged_uniform_empty_sealed_pipe_and_continuous_coating_reviewed=True)
    d.update(changes);return CoatedPipeWeightRequest(**d)


@pytest.mark.parametrize('diameter,bore,coating,fluid', [(.6,.56,.08,10000.),(.12,.1,0.,9800.),(.4,0.,.3,14000.)])
def test_integrated_surface_traction_and_material_volume(diameter,bore,coating,fluid):
    q=request(outer_diameter_m=diameter,inner_diameter_m=bore,coating_thickness_m=coating,
        surrounding_fluid_unit_weight_N_m3=fluid)
    ro=diameter/2+coating
    # Independent hydrostatic force integral on the actual outer circumference.
    upward=quad(lambda angle:-(3e5-fluid*ro*math.sin(angle))*math.sin(angle)*ro,0,2*math.pi)[0]
    metal=quad(lambda radius:2*math.pi*radius*q.pipe_unit_weight_N_m3,bore/2,diameter/2)[0]
    coat=quad(lambda radius:2*math.pi*radius*q.coating_unit_weight_N_m3,diameter/2,ro)[0]
    r=evaluate_coated_pipe_weight(q)
    assert r.status=='computed'
    assert r.buoyant_force_N_m==pytest.approx(upward,rel=3e-13)
    assert r.bare_pipe_weight_N_m==pytest.approx(metal,rel=3e-13)
    assert r.coating_weight_N_m==pytest.approx(coat,rel=3e-13,abs=1e-12)
    assert r.downward_submerged_weight_N_m==pytest.approx(metal+coat-upward,rel=3e-13)


@pytest.mark.parametrize('scale', [.001,18.])
def test_cross_section_similarity(scale):
    q=request();a=evaluate_coated_pipe_weight(q)
    b=evaluate_coated_pipe_weight(request(outer_diameter_m=.6*scale,inner_diameter_m=.56*scale,coating_thickness_m=.08*scale))
    assert b.downward_submerged_weight_N_m==pytest.approx(a.downward_submerged_weight_N_m*scale**2)


@pytest.mark.parametrize('extra', [.01,23.,100000.])
def test_inverse_independent_annular_ballast_load(extra):
    q=request(coating_thickness_m=None)
    bare=evaluate_coated_pipe_weight(request(coating_thickness_m=0.))
    target=bare.downward_submerged_weight_N_m+extra
    r=size_dense_coating_for_submerged_weight(q,required_downward_submerged_weight_N_m=target)
    c=r.coating_thickness_m
    independent=quad(lambda radius:2*math.pi*radius*(23000.-10000.),.3,.3+c)[0]
    assert independent==pytest.approx(extra,rel=2e-10)
    assert r.downward_submerged_weight_N_m==pytest.approx(target,rel=2e-11,abs=1e-10)


def test_already_met_target_does_not_add_coating():
    r=size_dense_coating_for_submerged_weight(request(coating_thickness_m=None),required_downward_submerged_weight_N_m=-1e9)
    assert r.status=='computed' and r.coating_thickness_m==0


@pytest.mark.parametrize('weight', [0.,9000.,10000.])
def test_light_neutral_coating_cannot_supply_ballast(weight):
    q=request(coating_unit_weight_N_m3=weight)
    r=size_dense_coating_for_submerged_weight(q,required_downward_submerged_weight_N_m=1e5)
    assert r.status=='out_of_scope' and r.coating_thickness_m is None


@pytest.mark.parametrize('change', [dict(coating_thickness_m=None),dict(coating_unit_weight_N_m3=None),
    dict(surrounding_fluid_unit_weight_N_m3=None),dict(pipe_unit_weight_N_m3=None),
    dict(fully_submerged_uniform_empty_sealed_pipe_and_continuous_coating_reviewed=False)])
def test_missing_material_or_geometry_withheld(change):
    r=evaluate_coated_pipe_weight(request(**change))
    assert r.status=='needs_data' and r.downward_submerged_weight_N_m is None


def test_unknown_inverse_target_not_zero():
    r=size_dense_coating_for_submerged_weight(request(),required_downward_submerged_weight_N_m=None)
    assert r.status=='needs_data'


@pytest.mark.parametrize('change', [dict(inner_diameter_m=.6),dict(coating_thickness_m=-1),dict(outer_diameter_m=0)])
def test_invalid_geometry(change):
    with pytest.raises(ValueError):request(**change)


def test_thick_coating_uses_outer_displacement_not_thin_annulus():
    r=evaluate_coated_pipe_weight(request(coating_thickness_m=.6))
    assert r.coating_area_m2==pytest.approx(math.pi*(.9**2-.3**2))
    assert r.coating_area_m2>math.pi*.6*.6
