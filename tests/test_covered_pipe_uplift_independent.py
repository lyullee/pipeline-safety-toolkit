"""Independent mass/displaced-volume ledger for submerged covered pipe."""
import math

import pytest

from pipeline_integrity.covered_pipe_uplift import CoveredPipeUpliftRequest as Request
from pipeline_integrity.covered_pipe_uplift import evaluate_covered_pipe_uplift as evaluate


BASE=dict(outer_diameter_m=.5,inner_diameter_m=.45,pipe_material_unit_weight_n_m3=77000.,
    water_unit_weight_n_m3=9810.,cover_depth_m=1.2,dry_backfill_unit_weight_n_m3=17000.,
    backfill_particle_relative_density=2.65,supplied_uplift_load_factor=1.1,
    fully_submerged_bare_pipe_and_cover_prism_reviewed=True)


def calc(**changes):return evaluate(Request(**(BASE|changes)))


@pytest.mark.parametrize('diameter,bore',[(.2,.18),(.5,.4),(1.,.9)])
@pytest.mark.parametrize('cover',[0.,.4,1.2])
def test_vertical_margin_matches_mass_and_displaced_solid_volume_ledger(diameter,bore,cover):
    g=9.80665;water_density=BASE['water_unit_weight_n_m3']/g
    particle_density=BASE['backfill_particle_relative_density']*water_density
    length=3.7
    metal_volume=math.pi*((diameter/2)**2-(bore/2)**2)*length
    pipe_mass=BASE['pipe_material_unit_weight_n_m3']/g*metal_volume
    dry_soil_mass=BASE['dry_backfill_unit_weight_n_m3']/g*diameter*cover*length
    submerged_solid_volume=dry_soil_mass/particle_density
    downward=g*(pipe_mass+dry_soil_mass-water_density*submerged_solid_volume)
    upward=g*water_density*math.pi*(diameter/2)**2*length
    result=calc(outer_diameter_m=diameter,inner_diameter_m=bore,cover_depth_m=cover)
    assert result.pipe_weight_n_m==pytest.approx(g*pipe_mass/length,rel=3e-15)
    assert result.buoyant_force_n_m==pytest.approx(upward/length,rel=3e-15)
    expected=(downward-BASE['supplied_uplift_load_factor']*upward)/length
    assert result.vertical_margin_n_m==pytest.approx(expected,rel=5e-15,abs=2e-11)
    assert result.supplied_criterion_satisfied is (expected>=0)


def test_geometric_similarity_scales_all_forces_per_length_quadratically():
    scale=3.5;a=calc();b=calc(outer_diameter_m=BASE['outer_diameter_m']*scale,
        inner_diameter_m=BASE['inner_diameter_m']*scale,cover_depth_m=BASE['cover_depth_m']*scale)
    for field in ('pipe_weight_n_m','submerged_cover_weight_n_m','buoyant_force_n_m','factored_uplift_n_m','vertical_margin_n_m'):
        assert getattr(b,field)==pytest.approx(scale**2*getattr(a,field),rel=3e-15)
    assert b.supplied_criterion_satisfied is a.supplied_criterion_satisfied


def test_zero_cover_does_not_require_backfill_properties():
    result=calc(cover_depth_m=0.,dry_backfill_unit_weight_n_m3=None,backfill_particle_relative_density=None)
    assert result.status=='computed'
    assert result.submerged_cover_weight_n_m==0


def test_equality_of_unfactored_solid_cylinder_and_water_weight_is_preserved():
    result=calc(inner_diameter_m=0.,cover_depth_m=0.,pipe_material_unit_weight_n_m3=9810.,
                supplied_uplift_load_factor=1.)
    assert result.vertical_margin_n_m==0
    assert result.supplied_criterion_satisfied is True


@pytest.mark.parametrize('field',['cover_depth_m','water_unit_weight_n_m3','supplied_uplift_load_factor',
                                 'backfill_particle_relative_density'])
def test_missing_load_or_material_parameter_is_withheld(field):
    result=calc(**{field:None})
    assert result.status=='needs_data'
    assert result.supplied_criterion_satisfied is None


@pytest.mark.parametrize('changes',[dict(inner_diameter_m=.5),dict(backfill_particle_relative_density=1.),
                                   dict(supplied_uplift_load_factor=0.)])
def test_invalid_geometry_or_force_basis_is_rejected(changes):
    with pytest.raises(ValueError):calc(**changes)
