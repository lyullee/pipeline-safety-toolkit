"""Index explicit model calls for review; static reachability is not a pass result."""
import ast
import hashlib
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
rows = []
for path in sorted((root / 'tests').glob('test_*.py')):
    tree = ast.parse(path.read_text(encoding='utf-8-sig'))
    imports = {}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith('pipeline_integrity'):
            for alias in node.names:
                imports[alias.asname or alias.name] = node.module + '.' + alias.name
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith('pipeline_integrity'):
                    imports[alias.asname or alias.name] = alias.name
    for function in ast.walk(tree):
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        calls = set()
        for node in ast.walk(function):
            if isinstance(node, ast.Call):
                name = ast.unparse(node.func)
                head, _, tail = name.partition('.')
                if head in imports:
                    calls.add(imports[head] + ('.' + tail if tail else ''))
        if calls:
            rows.append(dict(file=path.relative_to(root).as_posix(), function=function.name,
                is_test=function.name.startswith('test_'), line=function.lineno,
                file_sha256=hashlib.sha256(path.read_bytes()).hexdigest(), calls=sorted(calls)))
result = dict(schema='pipeline_integrity.physics_test_call_index.v1',
    limitations=['Direct imports/calls only; helpers and dynamic dispatch require manual review.',
                  'Names, call presence and source hashes do not prove physics accuracy or runtime coverage.'],
    functions=rows, qualification_credit=0)
output = root / 'artifacts/hpc/physics-test-call-index.json'
output.write_text(json.dumps(result, indent=2), encoding='utf-8')
print(json.dumps(dict(indexed_functions=len(rows), explicit_apis=len({c for r in rows for c in r['calls']}))))
