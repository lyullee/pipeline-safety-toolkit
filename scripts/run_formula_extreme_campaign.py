"""Parallel, source-pinned reference and extreme-value tests for223 methods."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
from importlib.metadata import PackageNotFoundError, version
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
BOOKS = ROOT.parent.parent
TESTS = (
    'tests/test_formula_extreme_mechanics_223.py',
    'tests/test_formula_extreme_fluids_223.py',
    'tests/test_formula_extreme_properties_223.py',
    'tests/test_formula_extreme_root_misc_223.py',
    'tests/test_formula_extreme_root_223.py',
    'tests/test_growing_pit_numeric_extremes.py',
)

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def run():
    material = Path(os.environ.get('PIPELINE_FORMULA_MATERIAL_SRC', str(BOOKS / '재료_부식 평가 툴 프로젝트/matintegrity/src'))).resolve()
    lh2 = Path(os.environ.get('PIPELINE_FORMULA_LH2DT_SRC', str(ROOT.parent / 'tmp/analysis/repos/lh2dt_models/src'))).resolve()
    if not (material / 'matintegrity/__init__.py').is_file() or not (lh2 / 'lh2dt/pipe.py').is_file():
        raise FileNotFoundError('read-only model owner sources are required for the selected individual laws')
    folder = ROOT / 'artifacts/formula-extreme-campaign-223' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    folder.mkdir(parents=True)
    roots = (ROOT / 'src', ROOT / 'tests', ROOT / 'examples', material, lh2)
    def snapshot():
        paths = {Path(__file__)}
        for root in roots:
            for suffix in ('*.py', '*.json'):
                paths.update(root.rglob(suffix))
        return {str(p.resolve()): sha(p) for p in sorted(paths)}
    before = snapshot()
    (folder / 'source-before.json').write_text(json.dumps(before, ensure_ascii=False, indent=2), encoding='utf-8')
    env = os.environ.copy()
    env.update(PIPELINE_FORMULA_MATERIAL_SRC=str(material), PIPELINE_MATERIAL_SRC=str(material),
               PIPELINE_FORMULA_LH2DT_SRC=str(lh2), PYTEST_DISABLE_PLUGIN_AUTOLOAD='1')
    env['PYTHONPATH'] = os.pathsep.join(map(str, (ROOT / 'src', ROOT, ROOT / 'tests', material, lh2)))
    for key in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS'):
        env[key] = '1'
    def execute(test):
        stem = Path(test).stem
        xml, log = folder / (stem + '.xml'), folder / (stem + '.log')
        started = time.monotonic()
        with log.open('w', encoding='utf-8') as stream:
            result = subprocess.run([sys.executable, '-m', 'pytest', test, '-q', '--tb=short',
                '-o', 'junit_family=xunit1', '--junitxml=' + str(xml)], cwd=ROOT, env=env,
                stdout=stream, stderr=subprocess.STDOUT)
        return {'test': test, 'exit_code': result.returncode,
                'elapsed_s': time.monotonic() - started, 'junit': xml.name, 'log': log.name}
    started = time.monotonic()
    runs = []
    with ThreadPoolExecutor(max_workers=4) as executor:
        for future in as_completed([executor.submit(execute, test) for test in TESTS]):
            result = future.result()
            runs.append(result)
            print(json.dumps(result), flush=True)
    wall_seconds = time.monotonic() - started
    after = snapshot()
    (folder / 'source-after.json').write_text(json.dumps(after, ensure_ascii=False, indent=2), encoding='utf-8')
    changed = sorted(p for p in before.keys() | after.keys() if before.get(p) != after.get(p))
    methods = json.loads((ROOT / 'src/pipeline_integrity/catalog_data/engineering_methods.json').read_text(encoding='utf-8'))['methods']
    expected = {row['id'] for row in methods}
    if len(expected) != 223:
        raise ValueError('fixed method scope changed')
    coverage = {'formula': {}, 'extreme': {}}
    cases = []
    for result in runs:
        path = folder / result['junit']
        for case in ET.parse(path).iter('testcase'):
            props = {p.get('name'): p.get('value') for p in case.findall('./properties/property')}
            kind = props.get('test_kind')
            if kind not in coverage or 'canonical_units' not in props:
                raise ValueError('every executed case needs a method ID and one of the two test kinds')
            units = json.loads(props['canonical_units'])
            units = [u.removeprefix('proposal.') for u in units]
            if not units or not set(units) <= expected:
                raise ValueError('case membership differs from fixed scope')
            status = next((tag for tag in ('failure', 'error', 'skipped') if case.find(tag) is not None), 'passed')
            identity = case.get('classname') + '::' + case.get('name')
            entry = {'id': identity, 'kind': kind, 'models': units, 'status': status, 'test_file': result['test']}
            if status != 'passed':
                entry['diagnostic'] = next(case.find(tag).text for tag in ('failure', 'error', 'skipped') if case.find(tag) is not None)
            cases.append(entry)
            if status == 'passed':
                for unit in units:
                    coverage[kind].setdefault(unit, []).append(identity)
    if len({c['id'] for c in cases}) != len(cases):
        raise ValueError('duplicate executed case identities')
    missing = {kind: sorted(expected - set(rows)) for kind, rows in coverage.items()}
    packages = {}
    for name in ('pytest', 'pydantic', 'numpy', 'scipy', 'fluids', 'CoolProp', 'PyNiteFEA', 'scikit-fem'):
        try:
            packages[name] = version(name)
        except PackageNotFoundError:
            packages[name] = None
    report = {'schema': 'pipeline_integrity.formula_extreme_campaign.v1',
        'total_models': 223, 'formula_models': len(coverage['formula']), 'extreme_models': len(coverage['extreme']),
        'cases': len(cases), **{tag: sum(c['status'] == tag for c in cases) for tag in ('passed', 'failure', 'error', 'skipped')},
        'case_counts_by_kind': {kind: sum(c['kind'] == kind for c in cases) for kind in coverage},
        'missing_models_by_kind': missing, 'case_memberships': coverage, 'case_results': cases,
        'parallel_subprocesses': 4, 'elapsed_wall_s': wall_seconds, 'runs': runs,
        'source_files_changed_during_run': changed, 'source_sha256': after,
        'owner_source_roots': {'material': str(material), 'lh2dt': str(lh2)},
        'python': sys.version, 'python_executable_sha256': sha(Path(sys.executable)),
        'platform': platform.platform(), 'packages': packages,
        'artifact_sha256': {p.name: sha(p) for p in folder.iterdir() if p.is_file()},
        'scope': 'Executed independent/simple reference and valid numerical-extreme cases for declared individual model domains; empirical arithmetic is not calibration or whole-system qualification.',
        'other_test_categories_included': False}
    path = folder / 'receipt.json'
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    (folder.parent / 'latest.json').write_text(json.dumps({'receipt': path.relative_to(ROOT).as_posix(),
        'sha256': sha(path)}, indent=2), encoding='utf-8')
    print(json.dumps({k: report[k] for k in ('formula_models', 'extreme_models', 'cases', 'passed',
        'failure', 'error', 'skipped', 'case_counts_by_kind', 'source_files_changed_during_run')}), flush=True)
    print(str(path), flush=True)
    return int(any(r['exit_code'] for r in runs) or bool(changed) or any(missing.values()))

if __name__ == '__main__':
    raise SystemExit(run())
