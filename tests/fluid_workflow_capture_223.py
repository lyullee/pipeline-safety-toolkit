"""Real registry-call capture while independent fluid references execute."""
import inspect
import math
import runpy
import sys
from mechanics_workflow_cases_223 import ROOT,plain,encoded,leaves,computed,save_trace

NUMERIC=runpy.run_path(str(ROOT/'tests/test_formula_extreme_fluids_223.py'))
CASES={u:f for u,k,f in NUMERIC['CASES'] if k=='formula'}

def statusless_success(api, output):
    """Only three reviewed public APIs deliberately return statusless mappings."""
    if not isinstance(output,dict) or 'status' in output:return False
    finite=lambda value:type(value) in (int,float) and math.isfinite(value)
    if api=='pipeline_integrity.hydraulic_grade.evaluate_hazen_williams':
        return finite(output.get('head_loss_m'))
    if api=='pipeline_integrity.hydraulic_grade.evaluate_hydraulic_grade':
        rows=output.get('stations')
        return isinstance(rows,(tuple,list)) and len(rows)>=2 and all(
            isinstance(row,dict) and finite(row.get('pressure_pa_abs')) and row['pressure_pa_abs']>0 for row in rows)
    if api=='pipeline_integrity.liquid_valve_forward_capacity.evaluate_liquid_valve_forward_capacity':
        return finite(output.get('volume_flow_m3_s')) and output['volume_flow_m3_s']>=0
    return False


def run_upstream(unit,monkeypatch):
    from pipeline_integrity.engineering_methods import engineering_method_specs,get_engineering_method_callable
    spec=next(s for s in engineering_method_specs() if s.id==unit)
    codes={}
    for api in spec.apis:
        target=get_engineering_method_callable(unit,api)
        if inspect.isfunction(target):codes[target.__code__]=api
        elif inspect.isclass(target) and inspect.isfunction(getattr(target,"__call__",None)):
            codes[target.__call__.__code__]=api+".__call__"
    trace=[];requests={}
    previous=sys.getprofile()
    def profile(frame,event,arg):
        api=codes.get(frame.f_code)
        if api is None:return
        if event=='call':
            count=frame.f_code.co_argcount+frame.f_code.co_kwonlyargcount
            requests[id(frame)]={k:frame.f_locals[k] for k in frame.f_code.co_varnames[:count] if k in frame.f_locals}
        elif event=='return':
            kwargs=requests.pop(id(frame),{})
            if arg is None:return
            output=plain(arg)
            encoded(output)
            trace.append(dict(api=api,args=(),kwargs=kwargs,result=arg,output=output,successful=(statusless_success(api,output) or computed(arg) or (isinstance(output,dict) and output.get("status")=="ok") or (type(arg).__name__=="ThermodynamicState" and all(0<output.get(k,0)<float("inf") for k in ("pressure_pa_abs","temperature_k","density_kg_m3"))) or (isinstance(output,list) and any(True for _ in leaves(output))))))
    try:
        sys.setprofile(profile)
        CASES[unit]()
    finally:sys.setprofile(previous)
    successful=[t for t in trace if t['successful']]
    assert successful,'No successful registry API call captured for '+unit
    return successful

