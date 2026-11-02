"""Curve-coordinate substitution, composition and scaling-convention checks."""
import math

import pytest

from pipeline_integrity.pump_curve_transform import SameCasingPumpCurveTransform, scale_same_casing_pump_curve


def request(**changes):
    return SameCasingPumpCurveTransform(**(dict(reference_flow_m3_s=(0.0,0.01,0.02,0.03),
        reference_head_m=(80.0,75.0,60.0,35.0),speed_ratio=1.0,impeller_diameter_ratio=1.0)|changes))


@pytest.mark.parametrize("speed,diameter", [(1.0,1.0),(1.2,1.0),(1.0,0.85),(0.9,0.95)])
def test_transformed_quadratic_curve_matches_independent_coordinate_substitution(speed,diameter):
    r=scale_same_casing_pump_curve(request(speed_ratio=speed,impeller_diameter_ratio=diameter)).values
    s=speed*diameter
    for flow,head in zip(r["flow_m3_s"],r["head_m"]):
        expected=s*s*(80.0-50000.0*(flow/s)**2)
        assert head == pytest.approx(expected,rel=2e-14,abs=1e-12)


def test_same_casing_diameter_flow_is_linear_and_not_geometric_volume_scaling():
    r=scale_same_casing_pump_curve(request(impeller_diameter_ratio=0.8)).values
    assert r["flow_m3_s"][1] == pytest.approx(0.008)
    assert r["flow_m3_s"][1] != pytest.approx(0.01*0.8**3)
    assert r["head_m"][1] == pytest.approx(75*0.8**2)


def test_successive_transforms_equal_composed_transform_on_arbitrary_reference_curve():
    q=request(reference_head_m=(70.0,72.0,61.0,34.0),speed_ratio=0.8,impeller_diameter_ratio=0.95)
    a=scale_same_casing_pump_curve(q).values
    b=scale_same_casing_pump_curve(SameCasingPumpCurveTransform(reference_flow_m3_s=tuple(a["flow_m3_s"]),reference_head_m=tuple(a["head_m"]),speed_ratio=1.1,impeller_diameter_ratio=0.97)).values
    c=scale_same_casing_pump_curve(q.model_copy(update={"speed_ratio":0.8*1.1,"impeller_diameter_ratio":0.95*0.97})).values
    assert b["flow_m3_s"] == pytest.approx(c["flow_m3_s"])
    assert b["head_m"] == pytest.approx(c["head_m"])


@pytest.mark.parametrize("changes", [dict(reference_head_m=(1.0,)),dict(reference_flow_m3_s=(0.0,0.01,0.01,0.03)),dict(speed_ratio=0.0),dict(impeller_diameter_ratio=float("nan"))])
def test_invalid_curve_or_transform_is_rejected(changes):
    with pytest.raises(ValueError):request(**changes)


def test_positive_transform_underflow_is_not_reported_as_a_stopped_pump():
    with pytest.raises(ValueError):
        scale_same_casing_pump_curve(request(speed_ratio=1e-200))


def test_subnormal_coordinate_collapse_is_not_returned_as_a_valid_curve():
    q=SameCasingPumpCurveTransform(reference_flow_m3_s=(1e-300,math.nextafter(1e-300,math.inf)),
        reference_head_m=(2.0,1.0),speed_ratio=1e-10)
    with pytest.raises(ValueError,match="distinct ordering"):
        scale_same_casing_pump_curve(q)
