"""Run source-pinned handoffs, baseline regression and complete model cases together."""
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime,timezone
import hashlib,json,os,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
STAGES={'input_regression':('scripts/run_engineering_input_campaign.py','input-campaign-223'),
        'formula_extreme_regression':('scripts/run_formula_extreme_campaign.py','formula-extreme-campaign-223'),
        'integration_end_to_end':('scripts/run_integration_e2e_campaign.py','integration-e2e-campaign-223')}
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def main():
    folder=ROOT/'artifacts/remaining-campaign-223'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    folder.mkdir(parents=True)
    env=os.environ.copy()
    for key in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS'):env[key]='1'
    env['PYTEST_DISABLE_PLUGIN_AUTOLOAD']='1'
    def execute(kind,script,category):
        start=time.monotonic()
        with (folder/(kind+'.log')).open('w',encoding='utf-8') as output:
            result=subprocess.run([sys.executable,script],cwd=ROOT,env=env,stdout=output,stderr=subprocess.STDOUT)
        pointer=json.loads((ROOT/'artifacts'/category/'latest.json').read_text(encoding='utf-8'))
        receipt=ROOT/pointer['receipt']
        if sha(receipt)!=pointer['sha256']:raise ValueError('Stage receipt checksum mismatch')
        # A failed worker must never pick up an older successful receipt.
        if receipt.stat().st_mtime < (folder/(kind+'.log')).stat().st_ctime-2:raise ValueError('Stale stage receipt')
        data=json.loads(receipt.read_text(encoding='utf-8'))
        row={'kind':kind,'exit_code':result.returncode,'elapsed_s':time.monotonic()-start,'receipt':pointer['receipt'],'sha256':pointer['sha256']}
        print(json.dumps(row),flush=True)
        return row,data
    began=time.monotonic();results={};details={}
    with ThreadPoolExecutor(max_workers=3) as pool:
        for future in as_completed([pool.submit(execute,k,*v) for k,v in STAGES.items()]):
            row,data=future.result();results[row['kind']]=row;details[row['kind']]=data
    a=details['input_regression'];b=details['formula_extreme_regression'];c=details['integration_end_to_end']
    valid=all(r['exit_code']==0 for r in results.values()) and all(not d.get('source_files_changed_during_run') for d in details.values())
    valid=valid and all(d['failure']==d['error']==d['skipped']==0 for d in details.values())
    regression_ids=set(a['focused_model_ids']) & set(b['case_memberships']['formula']) & set(b['case_memberships']['extreme'])
    report={'schema':'pipeline_integrity.remaining_campaign.v1','total_models':223,'eligible_for_current_source_credit':valid,
      'elapsed_wall_s':time.monotonic()-began,'stage_results':results,
      'categories':{
       'integration':{'executed_models':len(c['executed_models_by_kind']['integration']),'passed_models':c['integration_models'] if valid else None,'cases':c['case_counts_by_kind']['integration'],'passed_cases':sum(x['kind']=='integration' and x['status']=='passed' for x in c['case_results'])},
       'regression':{'executed_models':len(regression_ids),'passed_models':len(regression_ids) if valid else None,'cases':a['cases']+b['cases'],'passed_cases':a['passed']+b['passed'],'basis':'Re-executed existing input, independent-formula and numerical-limit baselines; broader legacy inventory is reported separately.'},
       'end_to_end':{'executed_models':len(c['executed_models_by_kind']['end_to_end']),'passed_models':c['end_to_end_models'] if valid else None,'cases':c['case_counts_by_kind']['end_to_end'],'passed_cases':sum(x['kind']=='end_to_end' and x['status']=='passed' for x in c['case_results'])}},
      'unique_executed_cases':a['cases']+b['cases']+c['cases'],
      'failure':sum(x['failure'] for x in details.values()),'error':sum(x['error'] for x in details.values()),'skipped':sum(x['skipped'] for x in details.values()),
      'scope':'Individual declared model domains and synthetic end-to-end cases; not field validation or an assertion that every empirical calibration is acceptable.'}
    path=folder/'receipt.json';path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    (folder.parent/'latest.json').write_text(json.dumps({'receipt':path.relative_to(ROOT).as_posix(),'sha256':sha(path)},indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False),flush=True)
    return int(not valid or len(regression_ids)!=223 or c['integration_models']!=223 or c['end_to_end_models']!=223)
if __name__=='__main__':raise SystemExit(main())
