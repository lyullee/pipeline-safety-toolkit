"""Representable tiny-growth/rate/mass witnesses for the selected geometry."""
from decimal import Decimal, localcontext
import json
import math

import pytest

from pipeline_integrity.growing_pit_leak import GrowingPitLeakRequest, evaluate_growing_pit_leak


@pytest.fixture(autouse=True)
def annotate(request):
    request.node.user_properties.extend((
        ('canonical_units', json.dumps(['proposal.leak.growing_tank_hole_integral'])),
        ('test_kind','extreme')))


def sphere(**changes):
    args=dict(geometry='spherical_intersection',wall_thickness_m=.01,initial_pit_radius_m=.01,
        radial_growth_m_s=1e-200,gravity_m_s2=2.,density_kg_m3=1.,
        history=({'time_s':0.,'liquid_head_m':1.,'discharge_coefficient':.5},
                 {'time_s':1.,'liquid_head_m':1.,'discharge_coefficient':.5}))
    return GrowingPitLeakRequest.model_validate(args|changes)


@pytest.mark.parametrize('wall,growth,density',[(.01,1e-200,1.),(1e100,1e-200,1.),(.01,1e-200,1e-100)])
def test_tiny_radial_increment_retains_opening_volume_and_mass(wall,growth,density):
    q=sphere(wall_thickness_m=wall,initial_pit_radius_m=wall,radial_growth_m_s=growth,density_kg_m3=density)
    r=evaluate_growing_pit_leak(q)
    with localcontext() as ctx:
        ctx.prec=100
        w=Decimal.from_float(wall);a=Decimal.from_float(growth);pi=Decimal.from_float(math.pi)
        area=float(pi*(2*w*a+a*a))
        volume=float(pi*(w*a+a*a/3))
        mass=float(Decimal.from_float(density)*pi*(w*a+a*a/3))
    assert r.status=='computed'
    assert r.opening_areas_m2[-1]>0 and r.cumulative_volume_m3[-1]>0 and r.cumulative_mass_kg[-1]>0
    assert r.opening_areas_m2[-1]==pytest.approx(area,rel=3e-13,abs=0.)
    assert r.cumulative_volume_m3[-1]==pytest.approx(volume,rel=3e-13,abs=0.)
    assert r.cumulative_mass_kg[-1]==pytest.approx(mass,rel=3e-13,abs=0.)


@pytest.mark.parametrize('area,gravity,head,density,expected',[
    (1e-200,1e308,1e100,1e-100,math.sqrt(2)*.5*1e4),
    (1e200,1e-200,1e-200,1e-200,math.sqrt(2)*.5),
])
def test_exponent_balanced_outflow_avoids_intermediate_head_product_loss(area,gravity,head,density,expected):
    q=GrowingPitLeakRequest(geometry='supplied_area_polynomial',
        area_coefficients_m2_m2_s_m2_s2=(area,0.,0.),gravity_m_s2=gravity,density_kg_m3=density,
        history=({'time_s':0.,'liquid_head_m':head,'discharge_coefficient':.5},
                 {'time_s':1.,'liquid_head_m':head,'discharge_coefficient':.5}))
    r=evaluate_growing_pit_leak(q)
    assert r.status=='computed'
    assert all(v>0 for v in r.outward_rates_m3_s)
    assert r.cumulative_volume_m3[-1]>0 and r.cumulative_mass_kg[-1]>0
    assert r.outward_rates_m3_s[-1]==pytest.approx(expected,rel=3e-13,abs=0.)
    assert r.cumulative_volume_m3[-1]==pytest.approx(expected,rel=3e-13,abs=0.)
    assert r.cumulative_mass_kg[-1]==pytest.approx(expected*density,rel=3e-13,abs=0.)


def test_real_zero_head_retains_known_zero_inventory_with_nonzero_opening():
    q=sphere(history=({'time_s':0.,'liquid_head_m':0.,'discharge_coefficient':.5},
                      {'time_s':1.,'liquid_head_m':0.,'discharge_coefficient':.5}))
    r=evaluate_growing_pit_leak(q)
    assert r.status=='computed'
    assert r.opening_areas_m2[-1]>0
    assert r.cumulative_volume_m3==(0.,0.) and r.cumulative_mass_kg==(0.,0.)


def test_penetration_event_integrates_only_the_original_spherical_opening_branch():
    q=sphere(initial_pit_radius_m=0.,radial_growth_m_s=.02,gravity_m_s2=.5,
        history=({'time_s':0.,'liquid_head_m':1.,'discharge_coefficient':1.},
                 {'time_s':1.,'liquid_head_m':1.,'discharge_coefficient':1.}))
    r=evaluate_growing_pit_leak(q)
    assert r.status=='computed'
    assert r.through_wall_time_s==.5
    assert r.cumulative_volume_m3[-1]==pytest.approx(math.pi/15000,rel=3e-13,abs=0.)


def test_positive_opening_below_float_range_is_not_reported_as_zero():
    with pytest.raises(ValueError,match='opening area.*floating'):
        evaluate_growing_pit_leak(sphere(radial_growth_m_s=1e-323))


def test_positive_mass_below_float_range_is_not_reported_as_zero():
    q=GrowingPitLeakRequest(geometry='supplied_area_polynomial',area_coefficients_m2_m2_s_m2_s2=(1e-200,0.,0.),
        gravity_m_s2=2.,density_kg_m3=1e-200,
        history=({'time_s':0.,'liquid_head_m':1.,'discharge_coefficient':.5},
                 {'time_s':1.,'liquid_head_m':1.,'discharge_coefficient':.5}))
    with pytest.raises(ValueError,match='floating representation'):
        evaluate_growing_pit_leak(q)


def test_penetration_time_above_float_range_is_explicit_without_fake_opening():
    r=evaluate_growing_pit_leak(sphere(wall_thickness_m=1.,initial_pit_radius_m=0.,radial_growth_m_s=1e-320))
    assert r.status=='computed'
    assert r.through_wall_time_s is None
    assert r.through_wall_time_outside_representation
    assert r.cumulative_volume_m3==(0.,0.)
