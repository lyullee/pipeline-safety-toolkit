"""Complete per-model input replay through approved public engineering APIs."""
import json

import pytest

from mechanics_fluids_workflow_cases_223 import UNITS,run_upstream,save_trace


@pytest.mark.parametrize('unit',UNITS,ids=UNITS)
def test_complete_request_public_calculation_final_reference(unit,monkeypatch,record_property):
    record_property('canonical_units',json.dumps([unit]))
    record_property('test_kind','end_to_end')
    trace=run_upstream(unit,monkeypatch)
    save_trace(unit,trace,'end_to_end')
    record_property('actual_public_apis',json.dumps(sorted({t['api'] for t in trace})))
