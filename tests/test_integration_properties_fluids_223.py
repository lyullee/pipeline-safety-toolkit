"""Real fluid model outputs enter a separate typed correction/covariance budget."""
import json
import pytest
from pydantic import ValidationError
from pipeline_integrity.metrology import MeasurementBudgetRequest,propagate_measurement_uncertainty
from properties_fluids_pipeline_cases_223 import SPECS,produce,budget_for,save_receipt


@pytest.mark.parametrize('unit',tuple(SPECS))
def test_actual_fluid_observable_handoff_preserves_exact_quantity(unit,monkeypatch,record_property):
    record_property('canonical_units',json.dumps([unit]));record_property('test_kind','integration')
    observation=produce(unit,monkeypatch)
    request,context,scale=budget_for(observation)
    result=propagate_measurement_uncertainty(request,context)
    assert result['status']=='computed'
    assert request.inputs[0].estimate==observation.value
    assert request.inputs[0].basis==observation.basis
    output=result['outputs']['corrected-observable']
    assert output['basis']==observation.basis.model_dump(mode='json')
    assert output['estimate']==pytest.approx(observation.value+.01*scale,rel=3e-14,abs=0.)
    assert result['output_covariance'][0][0]==pytest.approx(.0007*scale*scale,rel=3e-14,abs=0.)
    assert result['context']['origin']=='simulation'
    with pytest.raises(ValueError,match='context must match the asset and clock'):
        propagate_measurement_uncertainty(request.model_copy(update={'clock_id':'mismatched-clock'}),context)
    changed=request.model_dump(mode='python');changed['inputs'][0]['basis']['definition_id']='different-physical-observable'
    with pytest.raises(ValidationError,match='sensitivities must bind the exact input'):
        MeasurementBudgetRequest.model_validate(changed)
    save_receipt('integration',unit,observation,result)
