"""Computed fluid observables feed distinct unit-aware estimation calculations."""
import json
import pytest
import test_integration_mechanics_223 as handoffs
from mechanics_fluids_workflow_cases_223 import *

@pytest.mark.parametrize('unit',UNITS,ids=UNITS)
def test_actual_fluid_output_to_next_calculation(unit,monkeypatch,record_property):
    record_property('canonical_units',json.dumps([unit]))
    record_property('test_kind','integration')
    trace=run_upstream(unit,monkeypatch)
    monkeypatch.setattr(handoffs,'OBSERVABLES',OBSERVABLES)
    entry,value=handoffs.upstream_output(unit,trace)
    # Capture profiler binds positional inputs as named arguments. Preserve the
    # actual typed request and identities for the shared downstream estimator.
    bound=list(entry['kwargs'].values())
    request=next((v for v in bound if hasattr(v,'model_dump')),None)
    if request is not None:entry=dict(entry,args=(request,))
    result=handoffs.physical_handoff(unit,entry,value)
    assert result['downstream_request']['observations'][0]['estimate']==value
    record_property('source_output_path',OBSERVABLES[unit][0])
    folder=ROOT/'artifacts/mechanics-fluids-workflow-traces-223/integration'
    folder.mkdir(parents=True,exist_ok=True)
    (folder/(unit+'.json')).write_text(encoded(dict(source_api=entry['api'],source_result=entry['output'],handoff=result)),encoding='utf-8')
