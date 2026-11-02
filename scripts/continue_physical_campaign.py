"""Resume reviewed profiles against the same immutable dependency source."""
import json
import os
from pathlib import Path
import runpy
import sys

ROOT=Path(__file__).resolve().parents[1]
def main():
    status=json.loads((ROOT/'research/physical_verification_223.json').read_text(encoding='utf-8'))
    receipt=ROOT/status['latest_executed_evidence']['receipt']
    prior=json.loads(receipt.read_text(encoding='utf-8'))
    roots={Path(p).parent.parent for p in prior['source_sha256'] if Path(p).parts[-2:]==('matintegrity','__init__.py')}
    if len(roots)!=1:raise ValueError('one reviewed material owner source root is required')
    material=roots.pop()
    if not (material/'matintegrity/__init__.py').is_file():raise FileNotFoundError('frozen dependency source is unavailable')
    os.environ['PIPELINE_FORMULA_MATERIAL_SRC']=str(material)
    os.chdir(ROOT)
    sys.argv=[str(ROOT/'scripts/run_physical_campaign_223.py'),'--reuse-receipt',str(receipt)]
    runpy.run_path(sys.argv[0],run_name='__main__')
if __name__=='__main__':main()
