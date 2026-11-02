"""Swept volume, independent engineering-unit conversion and similarity."""
import math

import pytest

from pipeline_integrity.reciprocating_capacity import SingleActingCapacityRequest as Request
from pipeline_integrity.reciprocating_capacity import evaluate_single_acting_capacity as evaluate


def calc(**changes):
    args=dict(bore_diameter_m=.1,stroke_length_m=.15,delivery_strokes_per_second=2.,
              volumetric_efficiency=.95,single_acting_geometry_and_delivery_rate_reviewed=True)
    args.update(changes)
    return evaluate(Request(**args))


@pytest.mark.parametrize("bore_in,stroke_in,spm",[(3.,5.,60.),(4.,6.,80.),(2.5,3.,100.)])
def test_capacity_matches_us_volume_rate_and_stated_shortcut_precision(bore_in,stroke_in,spm):
    result=calc(bore_diameter_m=bore_in*.0254,stroke_length_m=stroke_in*.0254,
                delivery_strokes_per_second=spm/60.)
    barrels_per_day=result.delivered_volume_flow_m3_s*86400/(42*231*.0254**3)
    exact_bpd=math.pi/4*bore_in**2*stroke_in*spm*1440*.95/(42*231)
    assert barrels_per_day==pytest.approx(exact_bpd,rel=3e-15)
    normalized=barrels_per_day/(bore_in**2*stroke_in*spm)
    assert .105 <= normalized <= .115


@pytest.mark.parametrize("field,factor,power",[("bore_diameter_m",1.7,2),("stroke_length_m",1.7,1),
    ("delivery_strokes_per_second",1.7,1),("active_plungers",3,1)])
def test_independent_displacement_and_delivery_scalings(field,factor,power):
    base=dict(bore_diameter_m=.1,stroke_length_m=.15,delivery_strokes_per_second=2.,active_plungers=1)
    reference=calc()
    changed=calc(**{field:base[field]*factor})
    assert changed.delivered_volume_flow_m3_s/reference.delivered_volume_flow_m3_s==pytest.approx(factor**power)


@pytest.mark.parametrize("field",["delivery_strokes_per_second","volumetric_efficiency"])
def test_zero_event_rate_or_zero_delivery_efficiency_preserves_zero_flow(field):
    assert calc(**{field:0.}).delivered_volume_flow_m3_s==0


def test_unknown_efficiency_does_not_assume_a_fixed_slip_percentage():
    result=calc(volumetric_efficiency=None)
    assert result.status=="needs_data"
    assert result.theoretical_volume_flow_m3_s>0 and result.delivered_volume_flow_m3_s is None


def test_unreviewed_single_acting_assumptions_withhold_delivered_capacity():
    assert calc(single_acting_geometry_and_delivery_rate_reviewed=False).status=="needs_data"
