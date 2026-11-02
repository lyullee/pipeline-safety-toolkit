"""Force balance, geometry cases and similarity of uniform free-surface flow."""
import math

import pytest

from pipeline_integrity.gravity_uniform_flow import UniformChezyRequest as Request
from pipeline_integrity.gravity_uniform_flow import evaluate_uniform_chezy_flow as evaluate


def calc(area,perimeter,slope=.002,c=40.,**kwargs):
    return evaluate(Request(wetted_area_m2=area,wetted_perimeter_m=perimeter,
        signed_downhill_bed_slope=slope,chezy_coefficient_m_half_per_s=c,
        uniform_prismatic_free_surface_reviewed=True,**kwargs))


@pytest.mark.parametrize("slope", [-.01,-.002,0.,.002,.01])
def test_uniform_gravity_and_resistance_terms_balance_with_signed_flow(slope):
    area,perimeter,c,rho,g=1.5,4.,40.,1000.,9.80665
    result=calc(area,perimeter,slope,c)
    v=result.mean_velocity_m_s
    angle=math.atan(slope)
    drive=rho*g*area*math.sin(angle)
    resistance=rho*g*math.cos(angle)*perimeter*v*abs(v)/c**2
    assert drive==pytest.approx(resistance,rel=3e-14,abs=1e-14)
    assert result.volume_flow_m3_s==pytest.approx(area*v)
    assert v*slope >= 0


@pytest.mark.parametrize("radius", [.1,.5,2.])
def test_half_full_circle_has_half_radius_hydraulic_radius(radius):
    result=calc(math.pi*radius**2/2,math.pi*radius)
    assert result.hydraulic_radius_m==pytest.approx(radius/2,rel=2e-15)
    assert result.mean_velocity_m_s==pytest.approx(40*math.sqrt(radius*.002/2),rel=3e-15)


def test_geometric_similarity_scales_velocity_and_flow():
    scale=3.5
    a=calc(1.5,4.)
    b=calc(1.5*scale**2,4.*scale)
    assert b.mean_velocity_m_s/a.mean_velocity_m_s==pytest.approx(math.sqrt(scale))
    assert b.volume_flow_m3_s/a.volume_flow_m3_s==pytest.approx(scale**2.5)
    assert calc(1.5,4.,c=80.).volume_flow_m3_s==pytest.approx(2*a.volume_flow_m3_s)


def test_missing_coefficient_or_unreviewed_regime_withholds_velocity():
    for q in (Request(wetted_area_m2=1.,wetted_perimeter_m=2.,signed_downhill_bed_slope=.01),
              Request(wetted_area_m2=1.,wetted_perimeter_m=2.,signed_downhill_bed_slope=.01,
                      chezy_coefficient_m_half_per_s=40.)):
        result=evaluate(q)
        assert result.status=="needs_data"
        assert result.volume_flow_m3_s is None


@pytest.mark.parametrize("field", ["wetted_area_m2","wetted_perimeter_m"])
def test_dry_or_zero_perimeter_geometry_is_outside_this_wet_flow_relation(field):
    args=dict(wetted_area_m2=1.,wetted_perimeter_m=2.,signed_downhill_bed_slope=.01)
    args[field]=0.
    with pytest.raises(ValueError): Request(**args)
