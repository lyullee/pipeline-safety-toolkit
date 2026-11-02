"""Real public-call tracing for mechanical end-to-end and handoff cases."""
import dataclasses
import hashlib
import importlib
import inspect
import json
import math
import runpy
from pathlib import Path

from pydantic import BaseModel

ROOT=Path(__file__).resolve().parents[1]
NUMERIC=runpy.run_path(str(ROOT/'tests/test_formula_extreme_mechanics_223.py'))
NUMERIC_CASES={u:(file,fn,overrides) for u,k,file,fn,overrides in NUMERIC['CASES'] if k=='formula'}
UNITS=sorted(set(NUMERIC_CASES)|set(NUMERIC['CUSTOM_FORMULA']))
assert len(UNITS)==79


def plain(value):
    if isinstance(value,BaseModel):return value.model_dump(mode='json')
    if dataclasses.is_dataclass(value):return plain(dataclasses.asdict(value))
    if isinstance(value,dict):return {str(k):plain(v) for k,v in value.items()}
    if isinstance(value,(tuple,list)):return [plain(v) for v in value]
    if hasattr(value,'tolist'):return plain(value.tolist())
    return value


def encoded(value):
    return json.dumps(plain(value),sort_keys=True,allow_nan=False)


def clone(value):
    if isinstance(value,BaseModel):
        copied=type(value).model_validate_json(value.model_dump_json())
        assert encoded(copied)==encoded(value)
        return copied
    if dataclasses.is_dataclass(value):
        return type(value)(**json.loads(encoded(value)))
    return value


def leaves(value,prefix=''):
    if isinstance(value,dict):
        for k,v in value.items():yield from leaves(v,prefix+'.'+k if prefix else k)
    elif isinstance(value,list):
        for i,v in enumerate(value):yield from leaves(v,prefix+f'[{i}]')
    elif type(value) in (int,float):yield prefix,value


def computed(result):
    if dataclasses.is_dataclass(result):return True
    value=plain(result)
    if isinstance(value,dict):
        status=value.get('status')
        if status is None and 'native' in value:status=value['native'].get('status')
        if status is None and isinstance(result,BaseModel) and (type(result).__name__.endswith(('Result','Response','Loads'))
                or type(result).__name__=='CylinderLoadPerLength'):
            return any(True for _ in leaves(value))
        return status in ('computed','computed_diagnostic','completed_history','rupture_depth_reached')
    return isinstance(value,(int,float)) or dataclasses.is_dataclass(result)


def run_upstream(unit,monkeypatch):
    """Replay complete inputs through approved callables, retaining real outputs."""
    from pipeline_integrity.engineering_methods import engineering_method_specs,get_engineering_method_callable
    spec=next(s for s in engineering_method_specs() if s.id==unit)
    if unit in NUMERIC_CASES:
        file,fn,overrides=NUMERIC_CASES[unit]
        ns=NUMERIC['namespace'](file)
        if unit=='support_force_movement':
            fn='test_frequency_response_matches_separately_integrated_steady_oscillator'
    else:ns=NUMERIC
    trace=[]; originals={}
    for api in spec.apis:
        target=get_engineering_method_callable(unit,api)
        if inspect.isfunction(target):originals[target]=api
    def wrap(original,api):
        def replay(*args,**kwargs):
            new_args=tuple(clone(v) for v in args)
            new_kwargs={k:clone(v) for k,v in kwargs.items()}
            result=original(*new_args,**new_kwargs)
            output=plain(result)
            encoded(output)
            trace.append(dict(api=api,args=new_args,kwargs=new_kwargs,result=result,output=output,
                              successful=computed(result)))
            return result
        return replay
    wrappers={original:wrap(original,api) for original,api in originals.items()}
    for original,wrapper in wrappers.items():
        module=importlib.import_module(original.__module__)
        if getattr(module,original.__name__,None) is original:
            monkeypatch.setattr(module,original.__name__,wrapper)
    spaces=[ns,NUMERIC,*NUMERIC['MODULES'].values()]
    for space in list(spaces):
        spaces.extend(v.__globals__ for v in list(space.values()) if inspect.isfunction(v)
                      and v.__globals__.get('__name__')=='<run_path>')
    for space in {id(s):s for s in spaces}.values():
        for name,value in list(space.items()):
            if inspect.isfunction(value) and value in wrappers:
                monkeypatch.setitem(space,name,wrappers[value])
    if unit=='damage.fatigue_cycle_fraction':
        from pipeline_integrity.contracts import EvidenceRef,RunContext
        from pipeline_integrity.material_exchange import prepare_material_job,evaluate_material_job
        context=RunContext(asset_id='workflow-pipe',clock_id='cycles',scenario_id='supplied-cycle-life',origin='synthetic',
            evidence=(EvidenceRef(id='workflow',citation='Authored cycle counts and whole-cycle lives',origin='synthetic'),))
        job=prepare_material_job('fatigue.miner',dict(cycle_counts=[10.,20.],cycles_to_failure=[100.,100.],declared_damage_limit=1.),
            context,location_id='section',evidence_ids=('workflow',))
        result=evaluate_material_job(job,context)
        assert result.native.status=='computed'
        assert math.isclose(result.native.payload['values']['damage'],.3,rel_tol=2e-14,abs_tol=0.)
    elif unit in NUMERIC_CASES:
        function=ns[fn]
        function(**(NUMERIC['arguments'](function,ns)|overrides))
    else:
        NUMERIC['test_authored_missing_reference_and_extreme'](unit,'formula',lambda *args:None)
    successful=[entry for entry in trace if entry['successful']]
    assert successful,'an approved public numerical calculation must execute for '+unit
    assert any(any(math.isfinite(v) for _,v in leaves(e['output'])) for e in successful)
    return successful


def save_trace(unit,trace,kind):
    folder=ROOT/'artifacts/mechanics-workflow-traces-223'/kind
    folder.mkdir(parents=True,exist_ok=True)
    final=trace[-1]
    body=dict(canonical_unit=unit,category=kind,
        calls=[dict(api=t['api'],result_sha256=hashlib.sha256(encoded(t['output']).encode()).hexdigest(),
                    physical_output_leaves=list(leaves(t['output']))[:100]) for t in trace],
        final_api=final['api'],final_result=final['output'])
    (folder/(unit+'.json')).write_text(json.dumps(body,ensure_ascii=False,indent=2),encoding='utf-8')
