"""Typed fluid request replay to corrected reading, declared decision and verified receipt."""
import json,math
import pytest
from pipeline_integrity.metrology import propagate_measurement_uncertainty,verify_measurement_uncertainty
from fluid_pipeline_cases_223 import SPECS,produce,budget_for,save_receipt


@pytest.mark.parametrize('unit',tuple(SPECS))
def test_fluid_public_request_to_final_corrected_decision(unit,monkeypatch,record_property):
    record_property('canonical_units',json.dumps([unit]));record_property('test_kind','end_to_end')
    observation=produce(unit,monkeypatch,json_boundary=True)
    request,context,scale=budget_for(observation,end_to_end=True)
    request=type(request).model_validate_json(request.model_dump_json())
    result=propagate_measurement_uncertainty(request,context)
    assert result['status']=='computed'
    output=result['outputs']['corrected-observable'];sigma=math.sqrt(.0007)*scale
    assert output['estimate']==pytest.approx(observation.value,rel=3e-14,abs=8*math.ulp(scale))
    assert output['standard_uncertainty']==pytest.approx(sigma,rel=3e-14,abs=0)
    assert output['decision']['status']=='accepted_by_declared_rule'
    assert output['decision']['evaluated_interval']==pytest.approx([observation.value-2*sigma,observation.value+2*sigma],rel=3e-14,abs=8*math.ulp(scale))
    assert verify_measurement_uncertainty(result)
    assert result['context']['origin']=='simulation'
    assert not result['custody_transfer_certified'] and not result['calibration_fit_performed']
    saved=json.loads(save_receipt('end-to-end',unit,observation,result).read_text(encoding='utf-8'))
    assert saved['source_api']==observation.source_api
    assert saved['observable_basis']['unit']==observation.basis.unit
    assert verify_measurement_uncertainty(saved['downstream'])

