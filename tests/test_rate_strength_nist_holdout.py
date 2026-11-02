"""Measured rate-strength references with explicit specimen/offset conventions."""
import hashlib,os,sys
from pathlib import Path
import pytest

helper=os.environ.get('PIPELINE_RATE_REFERENCE_HELPER_ROOT')
if helper is None:
    pytest.skip('Optional acquired NIST experimental reference not selected',allow_module_level=True)
sys.path.insert(0,helper)
from nist_rate_strength_reference import REFERENCE,ROOT,calibrate_and_reference
from pipeline_integrity.reviewed_dynamic_correlations import DynamicStrengthCoordinateRequest,evaluate_dynamic_strength_coordinate


@pytest.mark.parametrize('strength_kind',['yield','ultimate'])
@pytest.mark.parametrize('law',['log_rate_ratio','power_rate_ratio'])
@pytest.mark.parametrize('held_rate',[60.,95.,236.])
def test_independent_measured_interior_rate_not_used_for_curve_fit(strength_kind,law,held_rate,record_property):
    ref=calibrate_and_reference(strength_kind,law,held_rate)
    q=DynamicStrengthCoordinateRequest(law=law,strain_rate_magnitude_per_s=held_rate,
        reference_strain_rate_per_s=ref['reference_rate'],minimum_reviewed_rate_per_s=.000107,
        maximum_reviewed_rate_per_s=367.,supplied_static_reference_strength_pa=ref['anchor_pa'],
        supplied_ratio_intercept=ref['ratio_intercept'],supplied_ratio_multiplier=ref['ratio_multiplier'],
        supplied_power_exponent=.2 if law=='power_rate_ratio' else None,
        material_temperature_rate_and_strength_kind_basis_id='N99-C3M1-L-HSR-TN-room-'+strength_kind,
        material_temperature_rate_law_and_units_reviewed=True)
    result=evaluate_dynamic_strength_coordinate(q)
    assert result['status']=='computed'
    assert result['dynamic_strength_coordinate_pa']==pytest.approx(ref['predicted_pa'],rel=2e-13)
    assert result['material_calibration_or_dynamic_allowable_qualified'] is False
    assert ref['held']['specimen'] not in {r['specimen'] for r in ref['training']}
    error=result['dynamic_strength_coordinate_pa']-ref['observed_pa']
    record_property('observed_strength_pa',ref['observed_pa'])
    record_property('predicted_strength_pa',result['dynamic_strength_coordinate_pa'])
    record_property('prediction_error_pa',error)
    record_property('prediction_interval_halfwidth_pa',ref['predictive_halfwidth_pa'])
    record_property('training_residual_df',ref['residual_df'])
    assert abs(error)<=ref['predictive_halfwidth_pa']


def test_same_material_geometry_and_offset_definition_are_not_pooled_with_other_rows():
    group=REFERENCE['selected_group']
    assert group['parent_piece']=='N99-C3M1' and group['rolling_direction']=='L'
    assert group['thickness_in']==.0625 and group['nominal_gauge_length_in']==1.
    assert group['yield_observable'].startswith('1-percent-offset')
    assert len({r['specimen'] for r in REFERENCE['observations']})==5
    for r in REFERENCE['observations']:
        assert r['yield_rate_per_s']==r['ultimate_rate_per_s']
        assert r['ultimate_mpa']>r['yield_mpa']>0


def test_actual_downloaded_draft_source_pin_is_not_relabelled_as_final_edition():
    assert hashlib.sha256((ROOT/'ncstar1-3d.pdf').read_bytes()).hexdigest()==REFERENCE['source_sha256']
    assert 'Draft' in REFERENCE['edition']
