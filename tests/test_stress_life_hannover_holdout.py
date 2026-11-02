"""Independent held-out experimental lives, conditional on one calibrated weld group."""
import hashlib
import math
import os
from pathlib import Path
import sys

import pytest
from scipy.special import ndtr

helper=os.environ.get('PIPELINE_S_N_HELPER_ROOT')
if helper is None:
    pytest.skip('Optional acquired CC BY experimental reference not selected',allow_module_level=True)
sys.path.insert(0,helper)
from hannover_sn_holdout import prepare,fit,PROTOCOL,ROOT
from pipeline_integrity.contracts import EvidenceRef,RunContext
from pipeline_integrity.stress_life_coordinate import StressLifeCoordinateRequest,evaluate_stress_life_coordinate

DATA=prepare()
CTX=RunContext(asset_id='hannover-s355-aw-v1',scenario_id='independent-holdout',clock_id='whole-cycles',origin='laboratory',
    evidence=(EvidenceRef(id='sn-data',citation='Christian Daenekas; DOI10.25835/0dojws6u; CC BY4.0; S355MLO AW V1 19mm R0.1 only',origin='laboratory'),))


@pytest.fixture(scope='module')
def calibrated():
    return fit(DATA['calibration'])


def prediction(row,calibrated,semantics='full_range'):
    factor=.5 if semantics=='effective_amplitude' else 1.
    request=StressLifeCoordinateRequest(asset_id=CTX.asset_id,location_id=row['specimen'],clock_id=CTX.clock_id,time_s=0.,
        coordinate_semantics=semantics,query_stress_coordinate_pa=factor*row['stress_range_pa'],
        reference_stress_coordinate_pa=factor*PROTOCOL['reference_range_pa'],
        reference_life_cycles=calibrated['reference_life_cycles'],positive_inverse_log_slope=calibrated['exponent'],
        calibration_basis_id='S355MLO-AW-V1-19mm-R0.1-training-only-censored-fit',curve_evidence_id='sn-data',curve_and_cycle_coordinate_reviewed=True)
    result=evaluate_stress_life_coordinate(request,CTX)
    assert result.status=='computed'
    assert result.field_or_material_fatigue_qualification is False
    return result.curve_life_cycles


@pytest.mark.parametrize('row',DATA['heldout'],ids=lambda row:row['specimen'])
def test_heldout_experimental_life_or_censoring_interval(row,calibrated,record_property):
    # The predictive scatter was estimated from calibration observations only.
    # A measured failure is an independent observation, not the curve equation
    # used for the native calculation. A runout is a lower bound on life.
    training_stresses=[r['stress_range_pa'] for r in DATA['calibration']]
    assert min(training_stresses)<=row['stress_range_pa']<=max(training_stresses)
    median=prediction(row,calibrated)
    z=math.log(row['cycles']/median)/calibrated['sigma_log_life']
    record_property('measured_cycles',row['cycles']);record_property('predicted_median_cycles',median)
    record_property('right_censored',row['right_censored']);record_property('standardized_log_residual',z)
    if row['right_censored']:
        # Do not assert median life equals, or must exceed, the runout count.
        survival=float(ndtr(-z));record_property('predicted_survival_at_runout',survival)
        assert survival>=.025
    else:
        assert abs(z)<=1.959963984540054


def test_independent_specimen_split_and_history_preservation():
    training={r['specimen'] for r in DATA['calibration']};held={r['specimen'] for r in DATA['heldout']}
    assert training.isdisjoint(held)
    assert len(training)==len(DATA['calibration']) and len(held)==len(DATA['heldout'])
    for item in DATA['heldout']:
        assert int(hashlib.sha256(item['specimen'].encode()).hexdigest(),16)%4==0
    reused={r['specimen'] for r in DATA['excluded'] if 'reused_specimen_history' in r['reasons']}
    assert reused and not (training|held)&reused
    assert any(r['right_censored'] for r in DATA['calibration'])


def test_selected_material_treatment_ratio_and_observable_are_not_mixed():
    for row in DATA['calibration']+DATA['heldout']:
        assert row['metadata']['Material']=='S355MLO'
        assert row['metadata']['Condition']=='AW' and row['metadata']['Geometry']=='V1'
        assert row['metadata']['Thickness_mm']=='19' and row['raw']['StressRatio']=='0.1'
        assert row['stress_range_pa']==float(row['raw']['NominalStressRange_MPa'])*1e6
        assert row['raw']['Outlier']=='0'
        assert row['cycles']==float(row['raw']['LoadCyclesToFailure'])
        assert row['right_censored']==(row['raw']['RunOut']=='1')


def test_native_amplitude_and_range_share_the_same_whole_cycle_curve(calibrated):
    row=DATA['heldout'][0]
    assert prediction(row,calibrated,'effective_amplitude')==pytest.approx(prediction(row,calibrated),rel=1e-13)


def test_raw_reference_hashes_and_license_are_preserved():
    expected={'fatigue_test_data.txt':'a7f7f11b96ad83e62d7e24d81222c97652243754e1f662f689e1866051722187',
              'metadata.txt':'7981c1baba850f5adc3803d410a3b4f817c2898e8ed29c755c3e44fed687d7c2',
              'LICENSE.txt':'54cf68fffd89afab85e196c4be0921637313cc1f8487cc1ebd7cfb9fa69998ce'}
    for name,pin in expected.items():
        assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==pin
    assert 'Creative Commons Attribution 4.0' in (ROOT/'LICENSE.txt').read_text(encoding='utf8')
