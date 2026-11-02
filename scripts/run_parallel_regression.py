"""Run the existing test inventory in independent local regression shards."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
BOOKS = ROOT.parent.parent
PRIVATE = ROOT.parent / 'tmp/analysis'

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def run():
    folder = ROOT / 'artifacts/regression-223' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    folder.mkdir(parents=True)
    files = [p for p in sorted((ROOT / 'tests').glob('test_*.py'))
             if not p.name.startswith(('test_integration_', 'test_end_to_end_'))]
    failed_only = '--failed-only' in sys.argv
    if failed_only:
        failed = json.loads((ROOT/'.pytest_cache/v/cache/lastfailed').read_text(encoding='utf-8'))
        selected = {key.split('::')[0] for key in failed}
        files = [p for p in files if p.relative_to(ROOT).as_posix() in selected]
    if '--repair-folder' in sys.argv:
        previous = Path(sys.argv[sys.argv.index('--repair-folder')+1])
        selected = set()
        for xml in previous.glob('shard-*.xml'):
            for case in ET.parse(xml).iter('testcase'):
                if any(case.find(tag) is not None for tag in ('failure','error','skipped')):
                    selected.add((case.get('file') or '').replace('\\','/'))
        files = [p for p in files if p.relative_to(ROOT).as_posix() in selected]
    bins = [[] for _ in range(4)]
    weights = [0]*4
    for path in sorted(files, key=lambda p: p.stat().st_size, reverse=True):
        index = min(range(4), key=lambda i: weights[i])
        bins[index].append(path.relative_to(ROOT).as_posix())
        weights[index] += path.stat().st_size
    material = BOOKS / '재료_부식 평가 툴 프로젝트/matintegrity/src'
    monitoring = BOOKS / '모니터링 결함 검지 프로젝트/process-monitoring-toolkit/src'
    lh2 = PRIVATE / 'repos/lh2dt_models/src'
    sources = [ROOT / 'src', ROOT, ROOT / 'tests', material, monitoring, lh2,
        PRIVATE / 'repos/human-reliability-toolkit/src', PRIVATE / 'repos/process-monitoring-toolkit/src',
        PRIVATE / 'repos/prism/lh2-tno/src', PRIVATE / 'repos/saga-py/python',
        PRIVATE / 'repos/prism/lh2-tno/src/prism_qra',
        PRIVATE / 'repos/pifira/src', PRIVATE / 'engine_sources/slabx-820e368ba645/src',
        PRIVATE / 'engine_sources/slabx-lh2-1ef613102819']
    sources = [p for p in sources if p.is_dir()]
    env = os.environ.copy()
    env.update(PYTHONPATH=os.pathsep.join(map(str,sources)),PYTEST_DISABLE_PLUGIN_AUTOLOAD='1',
        PIPELINE_FORMULA_MATERIAL_SRC=str(material), PIPELINE_MATERIAL_SRC=str(material),
        PIPELINE_SELECTED_MATERIAL_SOURCE_ROOT=str(material),PIPELINE_FORMULA_LH2DT_SRC=str(lh2),
        PIPELINE_IMPACT_REFERENCE_HELPER_ROOT=str(PRIVATE),PIPELINE_WAKE_REFERENCE_HELPER_ROOT=str(PRIVATE),
        PIPELINE_RATE_REFERENCE_HELPER_ROOT=str(PRIVATE),PIPELINE_S_N_HELPER_ROOT=str(PRIVATE),
        PIPELINE_PHYSICS_DATA_ROOT=str(ROOT/'data'),PIPELINE_HPC_RAW_ROOT=str(ROOT/'data/raw'))
    env.update(PHYSICS_PRISM_SOURCE=str(PRIVATE/'repos/prism/lh2-tno/src'),
        PHYSICS_LH2DT_ROOT=str(lh2.parent),
        PHYSICS_SLABX_ROOT=str(PRIVATE/'engine_sources/slabx-820e368ba645'),
        PHYSICS_SLABX_LH2_ROOT=str(PRIVATE/'engine_sources/slabx-lh2-1ef613102819'))
    replay = ROOT/'artifacts/regression-223/consequences-current-replay'
    if (replay/'wind_270.json').is_file():
        env['PIPELINE_RETAINED_CONSEQUENCE_ROOT']=str(replay)
    for key in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS'):
        env[key]='1'
    inventory = {'files': [p.relative_to(ROOT).as_posix() for p in files], 'shards':bins,
                 'sha256': {p.relative_to(ROOT).as_posix():sha(p) for p in files}}
    (folder/'inventory.json').write_text(json.dumps(inventory,indent=2),encoding='utf-8')
    def execute(index, selected):
        log, xml = folder/f'shard-{index}.log', folder/f'shard-{index}.xml'
        if not selected:
            ET.ElementTree(ET.Element('testsuites')).write(xml,encoding='utf-8',xml_declaration=True)
            log.write_text('No selected files in this shard.\n',encoding='utf-8')
            return {'shard':index,'files':0,'exit_code':0,'elapsed_s':0.,'junit':xml.name,'log':log.name}
        started=time.monotonic()
        worker_env = {**env, 'PIPELINE_TEST_EVENT_LOG':str(folder/f'shard-{index}-events.jsonl')}
        extra = ['--lf','--last-failed-no-failures=none'] if failed_only else []
        with log.open('w',encoding='utf-8') as out:
            result=subprocess.run([sys.executable,'-m','pytest',*selected,'-q','--tb=short',
                '-p','scripts.regression_events',*extra,'--continue-on-collection-errors','-o','junit_family=xunit1','--junitxml='+str(xml)],
                cwd=ROOT,env=worker_env,stdout=out,stderr=subprocess.STDOUT)
        value={'shard':index,'files':len(selected),'exit_code':result.returncode,
               'elapsed_s':time.monotonic()-started,'junit':xml.name,'log':log.name}
        print(json.dumps(value),flush=True)
        return value
    print(str(folder),flush=True)
    with ThreadPoolExecutor(max_workers=4) as executor:
        runs=[f.result() for f in as_completed([executor.submit(execute,i,b) for i,b in enumerate(bins)])]
    rows=[]
    for result in runs:
        path=folder/result['junit']
        if not path.is_file():
            continue
        for c in ET.parse(path).iter('testcase'):
            state=next((tag for tag in ('failure','error','skipped') if c.find(tag) is not None),'passed')
            row={'id':str(c.get('classname'))+'::'+str(c.get('name')),'status':state,'shard':result['shard']}
            if state!='passed':
                element=c.find(state)
                row['message']=element.get('message','')
                row['diagnostic']=element.text
            rows.append(row)
    report={'schema':'pipeline_integrity.regression_discovery.v1','runs':runs,'file_count':len(files),
            'cases':len(rows),**{state:sum(r['status']==state for r in rows) for state in ('passed','failure','error','skipped')},
            'rows':rows,'source_version_note':'Exploratory concurrent-development run; final evidence requires stable pre/post source snapshots.',
            'environment_roots':[str(p) for p in sources]}
    (folder/'receipt.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    (folder.parent/'latest.json').write_text(json.dumps({'receipt':str((folder/'receipt.json').relative_to(ROOT)),'sha256':sha(folder/'receipt.json')},indent=2),encoding='utf-8')
    print(json.dumps({k:report[k] for k in ('file_count','cases','passed','failure','error','skipped')}),flush=True)
    return int(any(r['exit_code'] for r in runs))

if __name__=='__main__':
    raise SystemExit(run())
