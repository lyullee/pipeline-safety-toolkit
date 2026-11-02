"""Compare an archived worker snapshot with current physics source and tests."""
import hashlib
import json
from pathlib import Path
import sys
import tarfile

root = Path(__file__).resolve().parents[1]
archive = Path(sys.argv[1])
snapshot = {}
receipts = []
hashes = {}
with tarfile.open(archive, 'r:gz') as bundle:
    for member in bundle:
        if member.name.endswith('/receipt.json'):
            receipts.append((member.name.rsplit('/', 1)[0], json.load(bundle.extractfile(member))))
        if member.isfile() and member.name.endswith(('/tests.xml', '/capture.json')) and '/work/' not in member.name:
            hashes[member.name] = hashlib.sha256(bundle.extractfile(member).read()).hexdigest()
        prefix = 'results/rank-00012/work/'
        if member.isfile() and member.name.startswith(prefix):
            relative = member.name[len(prefix):]
            if relative.startswith(('src/', 'tests/')) and relative.endswith(('.py', '.json')):
                snapshot[relative] = hashlib.sha256(bundle.extractfile(member).read()).hexdigest()
current = {}
for folder in ('src', 'tests'):
    for path in (root / folder).rglob('*'):
        if path.is_file() and path.suffix in ('.py', '.json'):
            current[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
report = {
    'job_id': '1185073',
    'receipt_count': len(receipts),
    'receipt_keys': sorted(receipts[0][1]) if receipts else [],
    'receipt_integrity_failures': [directory for directory, receipt in receipts
        if receipt.get('provenance_unchanged') is not True
        or receipt.get('pytest_exit_code') != 0
        or not receipt.get('junit_sha256')
        or receipt.get('junit_sha256') != hashes.get(directory + '/tests.xml')
        or not receipt.get('capture_sha256')
        or receipt.get('capture_sha256') != hashes.get(directory + '/capture.json')],
    'snapshot_files': len(snapshot),
    'changed_files': [p for p in snapshot if p in current and snapshot[p] != current[p]],
    'new_files': sorted(set(current) - set(snapshot)),
    'removed_files': sorted(set(snapshot) - set(current)),
    'meaning': 'Snapshot identity audit only; no capability qualification credit.',
}
output = root / 'artifacts/hpc/hangang-1185073-current-source-audit.json'
output.write_text(json.dumps(report, indent=2), encoding='utf-8')
print(json.dumps(report, indent=2))
