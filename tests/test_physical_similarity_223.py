"""Physical similarity and independent integrated budgets for ideal release laws."""
import json,math,runpy
from functools import lru_cache
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1]
@lru_cache(None)
def fixture(name):return runpy.run_path(str(ROOT/'tests'/name))
def mark(record_property,unit):
    record_property('canonical_units',json.dumps([unit]))
    record_property('physical_criterion','independent_integral_and_dimensional_similarity')
@pytest.mark.parametrize('scale',[.1,1.,10.])
def test_growing_opening_time_similarity_and_exact_volume(scale,record_property):
    mark(record_property,'leak.growing_tank_hole_integral')
    make=fixture('test_formula_extreme_root_223.py')['pit']
    rate=.001*scale;duration=2./scale
    result=make(rate,duration)
    # Integral of the spherical intersection area times the constant jet speed.
    expected=math.pi*(.01*rate*duration**2+rate**2*duration**3/3)
    assert result.status=='computed'
    assert result.cumulative_volume_m3[-1]==pytest.approx(expected,rel=2e-12,abs=0)
    assert result.cumulative_mass_kg[-1]==pytest.approx(1000*expected,rel=2e-12,abs=0)
    reference=make()
    assert result.opening_areas_m2[-1]==pytest.approx(reference.opening_areas_m2[-1],rel=2e-12,abs=0)
    assert result.cumulative_volume_m3[-1]*scale==pytest.approx(reference.cumulative_volume_m3[-1],rel=2e-12,abs=0)
@pytest.mark.parametrize('scale',[.1,1.,10.])
def test_porous_darcy_permeability_time_similarity_and_storage(scale,record_property):
    mark(record_property,'porous.constant_pressure_sharp_front')
    m=fixture('test_porous_release_independent.py');q=m['sph']();base=m['sphere'](q)
    altered=q.model_copy(update={'permeability_m2':q.permeability_m2*scale,'times_s':tuple(t/scale for t in q.times_s)})
    result=m['sphere'](altered)
    assert result.status=='computed'
    assert result.radius_m==pytest.approx(base.radius_m,rel=2e-12,abs=0)
    assert result.cumulative_intruded_liquid_m3==pytest.approx(base.cumulative_intruded_liquid_m3,rel=2e-12,abs=0)
    for radius,volume in zip(result.radius_m,result.cumulative_intruded_liquid_m3):
        expected=4*math.pi*q.porosity/3*(radius**3-q.source_radius_m**3)
        assert volume==pytest.approx(expected,rel=3e-12,abs=1e-20)
    for actual,reference in zip(result.instantaneous_feed_m3_s[1:],base.instantaneous_feed_m3_s[1:]):
        assert actual==pytest.approx(scale*reference,rel=3e-12,abs=0)
@pytest.mark.parametrize('scale',[.1,1.,10.])
def test_viscous_spill_feed_viscosity_similarity_and_supplied_inventory(scale,record_property):
    mark(record_property,'spill.constant_feed_viscous_spread')
    m=fixture('test_porous_release_independent.py');q=m['srf']();base=m['surface'](q)
    altered=q.model_copy(update={'feed_m3_s':q.feed_m3_s*scale,'kinematic_viscosity_m2_s':q.kinematic_viscosity_m2_s*scale**3})
    result=m['surface'](altered)
    assert result.status=='computed'
    assert result.radius_m==pytest.approx(base.radius_m,rel=3e-12,abs=0)
    assert result.feed_volume_m3==pytest.approx(tuple(scale*v for v in base.feed_volume_m3),rel=2e-12,abs=0)
    for t,v in zip(altered.times_s,result.feed_volume_m3):assert v==pytest.approx(altered.feed_m3_s*t,rel=2e-12,abs=0)

@pytest.mark.parametrize('pressure',[1e5,1e6,5e6])
def test_nominal_outside_diameter_hoop_stress_converges_to_lame_thin_limit(pressure,record_property):
    mark(record_property,'pressure.nominal_membrane')
    from pipeline_integrity.nominal_pipe_pressure import NominalHoopStressInput,nominal_hoop_stress_Pa
    radius=.5;errors=[]
    for ratio in (.1,.05,.025,.0125):
        wall=radius*ratio;outer=radius+wall
        nominal=nominal_hoop_stress_Pa(NominalHoopStressInput(differential_pressure_Pa=pressure,
            outside_diameter_m=2*outer,wall_thickness_m=wall))
        # Exact inner-surface hoop traction of a thick cylinder with zero outer pressure.
        lame=pressure*(outer**2+radius**2)/(outer**2-radius**2)
        relative=nominal/lame-1
        assert relative>0
        assert relative==pytest.approx(ratio/(2+2*ratio+ratio**2),rel=2e-11,abs=0)
        errors.append(relative)
    assert all(a/b>1.8 for a,b in zip(errors,errors[1:]))
    assert errors[-1]<.0063
