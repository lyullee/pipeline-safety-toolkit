"""Run only the input campaign and retain source, case and environment evidence."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = (
    'tests/test_engineering_input_contracts.py', 'tests/test_input_dataclass_223.py',
    'tests/test_input_root_223.py', 'tests/test_input_mechanics_223.py',
    'tests/test_input_fluids_223.py', 'tests/test_input_properties_223.py',
    'tests/test_input_root_remaining_223.py', 'tests/test_input_properties_remaining_223.py',
    'tests/test_input_fluids_remaining_223.py', 'tests/test_input_mechanics_remaining_223.py',
)

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def snapshot():
    paths = list((ROOT / 'src/pipeline_integrity').rglob('*.py'))
    paths += list((ROOT / 'tests').rglob('*.py'))
    paths += list((ROOT / 'examples').rglob('*.py'))
    paths += [ROOT / 'tests/data/mechanics_input_baselines_223.json']
    paths += [ROOT / p for p in TESTS]
    paths += [Path(__file__), ROOT / 'scripts/engineering_input_inventory.py',
              ROOT / 'src/pipeline_integrity/catalog_data/engineering_methods.json']
    return {p.relative_to(ROOT).as_posix(): digest(p) for p in sorted(set(paths))}

def run(existing_folder=None):
    if existing_folder is None:
        folder = ROOT / 'artifacts/input-campaign-223' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        folder.mkdir(parents=True)
        before = snapshot()
        (folder / 'source-before.json').write_text(json.dumps(before, indent=2), encoding='utf-8')
        command = [sys.executable, '-m', 'pytest', *TESTS, '-q', '--tb=short',
                   '-o', 'junit_family=xunit1', '--junitxml=' + str(folder / 'junit.xml')]
        env = os.environ.copy()
        env['PYTHONPATH'] = os.pathsep.join((str(ROOT / 'src'), str(ROOT)))
        with (folder / 'pytest.log').open('w', encoding='utf-8') as log:
            completed = subprocess.run(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
        after = snapshot()
        (folder / 'source-after.json').write_text(json.dumps(after, indent=2), encoding='utf-8')
        exit_code = completed.returncode
    else:
        folder = Path(existing_folder).resolve()
        folder.relative_to((ROOT / 'artifacts/input-campaign-223').resolve())
        before = json.loads((folder / 'source-before.json').read_text(encoding='utf-8'))
        after = json.loads((folder / 'source-after.json').read_text(encoding='utf-8'))
        exit_code = 0
    changed = sorted(p for p in before.keys() | after.keys() if before.get(p) != after.get(p))
    tree = ET.parse(folder / 'junit.xml')
    cases = []
    for case in tree.iter('testcase'):
        status = next((name for name in ('failure', 'error', 'skipped') if case.find(name) is not None), 'passed')
        cases.append({'id': case.get('classname') + '::' + case.get('name'), 'status': status,
                      'properties': {p.get('name'): p.get('value') for p in case.findall('./properties/property')}})
    sys.path.insert(0, str(ROOT / 'src'))
    sys.path.insert(0, str(ROOT))
    from scripts.engineering_input_inventory import request_inventory
    contracts, methods = request_inventory()
    passed_contracts = set()
    for case in cases:
        if case['status'] == 'passed' and 'test_request_field_rejects_invalid_input[' in case['id']:
            passed_contracts.add(case['id'].split('test_request_field_rejects_invalid_input[', 1)[1].split(':', 1)[0])
    # The dataclass tests exercise actual constructors with valid baselines.
    dataclass_contracts = {
        'pipeline_integrity.harmonic_response.HarmonicOscillator',
        'pipeline_integrity.stored_liquid_energy.LinearLiquidCompression',
        'pipeline_integrity.gas_acoustic_pipe.IdealGasInternalEnergyEOS',
        'pipeline_integrity.liquid_moc_friction.LiquidMOCFrictionState',
    }
    if all(c['status'] == 'passed' for c in cases if 'test_input_dataclass_223' in c['id']):
        passed_contracts.update(dataclass_contracts)
    model_rows = [{'id': m['id'], 'executed_field_contracts': sorted(set(m['contracts']) & passed_contracts),
                   'discovered_contracts': m['contracts'],
                   'field_screen_passed': bool(set(m['contracts']) & passed_contracts)} for m in methods]
    receipts = (
        ROOT / 'artifacts/input-mechanics-223/receipt.json',
        ROOT / 'artifacts/hpc/local-results/input-fluids-223-20261102/receipt.json',
        ROOT / 'artifacts/hpc/local-results/input-properties-223/evidence.json',
    )
    focused_units = set()
    for path in receipts:
        data = json.loads(path.read_text(encoding='utf-8'))
        units = data.get('canonical_units', data.get('unit_case_memberships_not_independent_cases', {}))
        focused_units.update(unit.removeprefix('proposal.') for unit in units)
    focused_units.update(('leak.growing_tank_hole_integral', 'fluid_pipe.closed_mass_compatibility',
                          'meter.supplied_gas_differential_flow', 'rheology.bingham_circular_laminar',
                          'rheology.power_law_circular_laminar', 'energy.linear_liquid_storage',
                          'support_force_movement'))
    for case in cases:
        if case['status'] != 'passed':
            continue
        encoded = case['properties'].get('canonical_units')
        if encoded is not None:
            units = json.loads(encoded)
            if isinstance(units, str):
                units = [units]
            if not isinstance(units, list) or any(not isinstance(u, str) for u in units):
                raise ValueError('invalid executed model membership')
            focused_units.update(u.removeprefix('proposal.') for u in units)
    known = {m['id'] for m in methods}
    if not focused_units <= known:
        raise ValueError('focused model membership outside the fixed catalog')
    if len({c['id'] for c in cases}) != len(cases):
        raise ValueError('duplicate executed case IDs; totals must not double count')
    summary = {
        'schema': 'pipeline_integrity.input_campaign.v1',
        'scope': 'input rejection only; no numerical correctness or physical qualification credit',
        'pytest_exit_code': exit_code or int(any(c['status'] in ('failure', 'error') for c in cases)),
        'cases': len(cases), **{s: sum(c['status'] == s for c in cases) for s in ('passed', 'failure', 'error', 'skipped')},
        'field_screen_models': sum(r['field_screen_passed'] for r in model_rows), 'total_models': len(methods),
        'executed_contract_classes': len(passed_contracts & contracts.keys()),
        'focused_valid_baseline_models': len(focused_units), 'focused_model_ids': sorted(focused_units),
        'remaining_models_without_focused_relational_cases': sorted(known - focused_units),
        'coverage_limit': 'Field rejection is checked at its exact error location. It does not establish complete multi-field/domain consistency. Focused cases use valid baselines; additional model-specific relationships remain to be exercised.',
        'source_files_changed_during_run': changed, 'source_sha256': after,
        'python': sys.version, 'platform': platform.platform(), 'python_executable_sha256': digest(Path(sys.executable)),
        'packages': {name: version(name) for name in ('pytest', 'pydantic', 'numpy', 'scipy', 'fluids')},
        'junit_sha256': digest(folder / 'junit.xml'), 'log_sha256': digest(folder / 'pytest.log'),
        'report_builder_current_sha256': digest(Path(__file__)),
        'models': model_rows, 'case_results': cases,
    }
    path = folder / 'receipt.json'
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    latest = {'receipt': path.relative_to(ROOT).as_posix(), 'sha256': digest(path)}
    (ROOT / 'artifacts/input-campaign-223/latest.json').write_text(json.dumps(latest, indent=2), encoding='utf-8')
    print(json.dumps({k: summary[k] for k in ('cases', 'passed', 'failure', 'error', 'skipped',
        'field_screen_models', 'total_models', 'executed_contract_classes', 'focused_valid_baseline_models',
        'source_files_changed_during_run')}))
    print(str(path))
    return summary['pytest_exit_code'] or int(bool(changed))

if __name__ == '__main__':
    raise SystemExit(run(sys.argv[1] if len(sys.argv) == 2 else None))
