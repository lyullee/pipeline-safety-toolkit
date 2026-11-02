"""Public model request through synthetic reading/correction to a final receipt."""
import json
import math

import pytest

from pipeline_integrity.metrology import propagate_measurement_uncertainty,verify_measurement_uncertainty
from properties_pipeline_cases_223 import SPECS,budget_for,produce,save_receipt


@pytest.mark.parametrize('unit',tuple(SPECS))
def test_public_request_reading_correction_decision_and_receipt(unit,tmp_path,record_property):
    record_property('canonical_units',json.dumps(['proposal.'+unit]))
    record_property('test_kind','end_to_end')
    observation=produce(unit,tmp_path,json_boundary=True)
    request,context,scale=budget_for(observation,end_to_end=True)
    request=type(request).model_validate_json(request.model_dump_json())
    result=propagate_measurement_uncertainty(request,context)
    assert result['status']=='computed'
    output=result['outputs']['corrected-observable']
    assert output['estimate']==pytest.approx(observation.value,rel=3e-14,abs=8*math.ulp(scale))
    expected_standard=math.sqrt(.0007)*scale
    assert output['standard_uncertainty']==pytest.approx(expected_standard,rel=3e-14,abs=0.)
    assert output['expanded_uncertainty']==pytest.approx(2*expected_standard,rel=3e-14,abs=0.)
    assert output['decision']['status']=='accepted_by_declared_rule'
    assert output['decision']['evaluated_interval']==pytest.approx(
        [observation.value-2*expected_standard,observation.value+2*expected_standard],rel=3e-14,abs=8*math.ulp(scale))
    assert verify_measurement_uncertainty(result)
    assert result['context']['origin']=='simulation'
    assert not result['custody_transfer_certified'] and not result['calibration_fit_performed']
    path=save_receipt('end-to-end',unit,observation,result)
    saved=json.loads(path.read_text(encoding='utf-8'))
    assert saved['source_origin']=='simulation'
    assert verify_measurement_uncertainty(saved['downstream'])
