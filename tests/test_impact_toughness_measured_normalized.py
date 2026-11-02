"""Separate measured empirical comparisons from algebraic SI coordinate checks."""
import hashlib,json,os,sys
sys.path.insert(0,os.environ['PIPELINE_IMPACT_REFERENCE_HELPER_ROOT'])
from decimal import Decimal,localcontext
import math
import pytest
from jstage_impact_reference import ROOT,fixture,calibrate,interval
from pipeline_integrity.fracture_property_coordinates import (
 NormalizedImpactToughnessRequest,ImpactToughnessRequest,
 evaluate_normalized_impact_toughness_coordinate,evaluate_impact_toughness_coordinate)

def request(x,cal,**updates):
    data=dict(impact_energy_over_yield_strength_m3=x,
        supplied_energy_strength_coefficient_per_m2=cal['coefficient_per_m2'],
        supplied_squared_toughness_over_strength_offset_m=cal['offset_m'],
        material_temperature_specimen_and_unit_basis_id='reviewed-static-upper-shelf-normalized-material-groups',
        impact_to_elastic_toughness_correlation_support_reviewed=True)
    data.update(updates);return NormalizedImpactToughnessRequest(**data)

def test_original_static_group_points_partition_and_source_are_preserved():
    f=fixture();assert len(f['rows'])==5
    assert all(r['static_open_symbol'] for r in f['rows'])
    assert [r['material_id'] for r in f['rows'] if r['partition']=='calibration']==['A508_Cl1','A508_Cl2','A533_GrB_Cl1']
    assert hashlib.sha256((ROOT/'jsms35_873.pdf').read_bytes()).hexdigest()==f['source_pdf_sha256']
    assert f['axes']['grid_line_uncertainty_pixel']==2

@pytest.mark.parametrize('material',['A508_Cl3_A','A508_Cl3_B'])
def test_actual_held_material_normalized_toughness_not_used_for_fit(material,record_property):
    rows=fixture()['rows'];cal=calibrate(rows);r=next(r for r in rows if r['material_id']==material)
    assert material not in cal['training_materials']
    x=r['energy_over_strength_m3'];assert cal['support'][0]<x<cal['support'][1]
    result=evaluate_normalized_impact_toughness_coordinate(request(x,cal))
    predicted=result['estimated_squared_toughness_over_strength_m'];actual=r['measured_squared_toughness_over_strength_m']
    statistical,pixel=interval(cal,r)
    assert abs(predicted-actual)<=statistical+pixel
    record_property('independent_material',json.dumps(dict(material_id=material,calibration=cal,
        measured_normalized_square_m=actual,predicted_normalized_square_m=predicted,
        conditional_95_scatter_half_width_m=statistical,digitization_bound_m=pixel,
        actual_absolute_temperature_or_strength_reconstructed=False,
        claim='two held material-group points; one calibration residual degree of freedom, not specimen-level confidence or universal steel validation')))

def test_calibration_normal_equations_have_independent_decimal_solution():
    rows=fixture()['rows'];cal=calibrate(rows);train=[r for r in rows if r['partition']=='calibration']
    with localcontext() as c:
        c.prec=50;x=[Decimal(str(r['energy_over_strength_m3'])) for r in train];y=[Decimal(str(r['measured_squared_toughness_over_strength_m'])) for r in train]
        n=Decimal(len(x));slope=(n*sum(a*b for a,b in zip(x,y))-sum(x)*sum(y))/(n*sum(a*a for a in x)-sum(x)**2)
        intercept=(sum(y)-slope*sum(x))/n
    assert cal['coefficient_per_m2']==pytest.approx(float(slope),rel=1e-12)
    assert cal['offset_m']==pytest.approx(float(-intercept),rel=1e-12)

@pytest.mark.parametrize('yield_pa',[300e6,500e6,1700e6])
def test_raw_and_normalized_SI_units_commute_without_claiming_measured_strength(yield_pa):
    # Synthetic unit-coordinate cases only; these are not measured material rows.
    cal=calibrate(fixture()['rows']);x=.5e-6
    normalized=evaluate_normalized_impact_toughness_coordinate(request(x,cal))
    raw=evaluate_impact_toughness_coordinate(ImpactToughnessRequest(impact_energy_j=x*yield_pa,yield_stress_pa=yield_pa,
        supplied_energy_strength_coefficient_per_m2=cal['coefficient_per_m2'],
        supplied_squared_toughness_over_strength_offset_m=cal['offset_m'],
        material_temperature_specimen_and_unit_basis_id='synthetic-unit-coordinate-check-only',
        impact_to_elastic_toughness_correlation_support_reviewed=True))
    assert (raw['estimated_elastic_toughness_coordinate_pa_sqrt_m']/yield_pa)**2==pytest.approx(normalized['estimated_squared_toughness_over_strength_m'],rel=1e-14)
    assert .5/yield_pa*1e6==pytest.approx(.5/(yield_pa/1e6))

@pytest.mark.parametrize('factor',[0.,.5,1.])
def test_nonpositive_radicand_is_outside_calibrated_coordinate_not_zero_toughness(factor):
    cal=calibrate(fixture()['rows']);threshold=cal['offset_m']/cal['coefficient_per_m2']
    r=evaluate_normalized_impact_toughness_coordinate(request(threshold*factor,cal))
    assert r['status']=='out_of_scope'
    assert 'estimated_squared_toughness_over_strength_m' not in r

def test_missing_correlation_review_and_nonfinite_inputs_are_not_inferred():
    cal=calibrate(fixture()['rows'])
    assert evaluate_normalized_impact_toughness_coordinate(request(.5e-6,cal,impact_to_elastic_toughness_correlation_support_reviewed=False))['status']=='needs_data'
    with pytest.raises(ValueError):request(float('inf'),cal)

def test_heldout_values_cannot_modify_training_coefficients():
    rows=fixture()['rows'];original=calibrate(rows)
    altered=[dict(r,measured_squared_toughness_over_strength_m=999.) if r['partition']=='holdout' else r for r in rows]
    assert calibrate(altered)==original

@pytest.mark.parametrize('direction',[-math.inf,math.inf])
def test_one_representable_step_from_threshold_cannot_claim_resolved_toughness(direction):
    cal=calibrate(fixture()['rows']);threshold=cal['offset_m']/cal['coefficient_per_m2']
    q=request(math.nextafter(threshold,direction),cal)
    result=evaluate_normalized_impact_toughness_coordinate(q)
    assert result['status']=='out_of_scope'
    assert result['radicand_resolution_guard_m']>0
    assert 'estimated_squared_toughness_over_strength_m' not in result

@pytest.mark.parametrize('scale',[1e-9,1.,1e9])
def test_resolved_positive_boundary_is_retained_with_scale_aware_budget(scale):
    cal=calibrate(fixture()['rows'])
    scaled=dict(cal,coefficient_per_m2=cal['coefficient_per_m2']*scale,offset_m=cal['offset_m']*scale)
    threshold=scaled['offset_m']/scaled['coefficient_per_m2'];x=threshold
    for _ in range(32):x=math.nextafter(x,math.inf)
    result=evaluate_normalized_impact_toughness_coordinate(request(x,scaled))
    assert result['status']=='computed'
    assert result['estimated_squared_toughness_over_strength_m']>0
