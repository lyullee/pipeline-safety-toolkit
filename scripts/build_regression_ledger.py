"""Latest whole-file legacy outcomes, preserving version-specific run evidence."""
import hashlib,json,sys,xml.etree.ElementTree as ET
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
    files={};runs=[]
    for value in sys.argv[1:]:
        folder=Path(value).resolve();inventory=json.loads((folder/'inventory.json').read_text(encoding='utf-8'))
        receipt=json.loads((folder/'receipt.json').read_text(encoding='utf-8'))
        included=[]
        for shard in receipt['runs']:
            if not shard['files'] or shard['exit_code'] not in (0,1):continue
            selected=set(inventory['shards'][shard['shard']]);grouped={f:[] for f in selected}
            xml=folder/shard['junit']
            for case in ET.parse(xml).iter('testcase'):
                file=(case.get('file') or '').replace('\\','/')
                # Reused test functions retain their defining source filename;
                # pytest's collecting module owns their distinct case identity.
                parts=(case.get('classname') or '').split('.')
                owner='/'.join(parts[:2])+'.py' if len(parts)>=2 else ''
                if owner in selected:file=owner
                if file not in selected:raise ValueError('Unexpected file in whole-file ledger')
                state=next((s for s in ('failure','error','skipped') if case.find(s) is not None),'passed')
                row={'id':case.get('classname','')+'::'+case.get('name',''),'status':state}
                if state!='passed':row['diagnostic']=case.find(state).text
                grouped[file].append(row)
            for file,rows in grouped.items():
                files[file]={'run':folder.relative_to(ROOT).as_posix(),'test_sha256':inventory['sha256'][file],
                    'junit':xml.relative_to(ROOT).as_posix(),'junit_sha256':sha(xml),'cases':rows}
                included.append(file)
        runs.append({'folder':folder.relative_to(ROOT).as_posix(),'receipt_sha256':sha(folder/'receipt.json'),'included_files':included})
    cases=[dict(file=f,**r) for f,d in files.items() for r in d['cases']]
    report={'schema':'pipeline_integrity.legacy_regression_ledger.v1','file_count':len(files),'cases':len(cases),
       **{s:sum(r['status']==s for r in cases) for s in ('passed','failure','error','skipped')},
       'source_policy':'Latest complete whole-file execution supersedes earlier outcomes for that file. This ledger combines version-specific discovery and repair runs; it is not a single source-immutable global batch. Current-source223-method campaign is separate.',
       'runs':runs,'file_evidence':files,'nonpassing_cases':[r for r in cases if r['status']!='passed']}
    target=ROOT/'artifacts/regression-223/current-ledger.json';target.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:report[k] for k in ('file_count','cases','passed','failure','error','skipped')}))
if __name__=='__main__':main()
