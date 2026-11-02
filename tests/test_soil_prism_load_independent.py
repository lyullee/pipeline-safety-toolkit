"""Prism load against independently summed unit-length layer weights."""
import math

import pytest

from pipeline_integrity.soil_prism_load import SoilPrismLoadRequest as Request
from pipeline_integrity.soil_prism_load import evaluate_soil_prism_load as evaluate


def calc(**changes):
    data=dict(burial_depth_m=2.,dry_bulk_unit_weight_n_m3=17000.,water_head_m=0.,
        uniform_prism_and_water_basis_reviewed=True)
    data.update(changes)
    return evaluate(Request(**data))


@pytest.mark.parametrize('height,gamma',[(.5,15000.),(2.,17000.),(4.,21000.)])
def test_dry_prism_pressure_equals_declared_unit_length_weight_per_area(height,gamma):
    width,length=1.7,3.2
    weight=gamma*width*length*height
    result=calc(burial_depth_m=height,dry_bulk_unit_weight_n_m3=gamma)
    assert result.total_vertical_pressure_pa==pytest.approx(weight/(width*length),rel=2e-15)
    assert result.pore_water_pressure_pa==0


@pytest.mark.parametrize('head',[.3,1.2,2.])
@pytest.mark.parametrize('reduction',[0.,.33,.8,1.])
def test_partly_wet_prism_matches_independent_layer_weight_ledger(head,reduction):
    height,width,length,gamma,gamma_w=2.,.8,1.,17000.,9810.
    weights=[]
    for index in range(20):
        bottom_depth=(index+.5)*height/20
        under_water=bottom_depth>height-head
        layer_soil_weight=gamma*(1-reduction if under_water else 1)*width*length*height/20
        layer_water_weight=gamma_w*width*length*height/20 if under_water else 0.
        weights.extend((layer_soil_weight,layer_water_weight))
    result=calc(water_head_m=head,submerged_soil_weight_reduction_fraction=reduction,
                water_unit_weight_n_m3=gamma_w,loaded_width_m=width)
    assert result.total_vertical_pressure_pa==pytest.approx(math.fsum(weights)/(width*length),rel=3e-15)
    assert result.vertical_line_load_n_m==pytest.approx(math.fsum(weights)/length,rel=3e-15)
    assert result.total_vertical_pressure_pa==pytest.approx(
        result.reduced_soil_weight_pressure_pa+result.pore_water_pressure_pa,rel=2e-15)


def test_zero_cover_has_zero_weight_and_optional_width_remains_unknown():
    result=calc(burial_depth_m=0.,water_head_m=0.)
    assert result.total_vertical_pressure_pa==0
    assert result.vertical_line_load_n_m is None


def test_width_scales_line_load_without_changing_pressure():
    left=calc(loaded_width_m=.4);right=calc(loaded_width_m=1.2)
    assert right.total_vertical_pressure_pa==left.total_vertical_pressure_pa
    assert right.vertical_line_load_n_m==pytest.approx(3*left.vertical_line_load_n_m)


@pytest.mark.parametrize('changes',[dict(water_head_m=None),dict(dry_bulk_unit_weight_n_m3=None),
    dict(water_head_m=.8,water_unit_weight_n_m3=9810.)])
def test_missing_geometry_or_submerged_reduction_is_withheld(changes):
    result=calc(**changes)
    assert result.status=='needs_data'
    assert result.total_vertical_pressure_pa is None


def test_water_level_above_ground_is_not_silently_extrapolated():
    with pytest.raises(ValueError):calc(water_head_m=2.1)
