"""Discover reviewed engineering methods and their original typed Python APIs.

Resolving a method returns the existing callable unchanged, preserving its
request, provider, context and result contracts. Numerical qualification and
calibration remain properties of the model and its evidence, not this catalog.
"""
from __future__ import annotations

import ast
import hashlib
import importlib
import inspect
import json
import re
from dataclasses import dataclass
from importlib.resources import files
from typing import Callable


@dataclass(frozen=True)
class EngineeringMethodSpec:
    id: str
    apis: tuple[str, ...]
    supported_domain: tuple[str, ...]
    implementation_complete: bool


def _catalog_document():
    document = json.loads(files('pipeline_integrity.catalog_data').joinpath(
        'engineering_methods.json').read_text(encoding='utf-8'))
    if (not isinstance(document, dict)
            or document.get('schema') != 'pipeline_integrity.engineering_methods.v1'
            or not isinstance(document.get('methods'), list)):
        raise ValueError('invalid engineering method catalog schema')
    for row in document['methods']:
        if not isinstance(row, dict) or type(row.get('implementation_complete')) is not bool:
            raise ValueError('method implementation_complete must be a boolean')
        if not isinstance(row.get('id'), str) or not row['id']:
            raise ValueError('method ID must be a nonempty string')
        if (not isinstance(row.get('apis'), list)
                or any(not isinstance(api, str) for api in row['apis'])
                or len(set(row['apis'])) != len(row['apis'])):
            raise ValueError('method APIs must be distinct strings')
        if (not isinstance(row.get('supported_domain'), list)
                or not row['supported_domain']
                or any(not isinstance(v, str) or not v.strip() for v in row['supported_domain'])):
            raise ValueError('method must declare supported domains')
        pins = row.get('source_implementations', [])
        if not isinstance(pins, list):
            raise ValueError('source implementations must be a list')
        seen = set()
        for pin in pins:
            if not isinstance(pin, dict):
                raise ValueError('invalid source implementation binding')
            api, module, qualname = (pin.get(k) for k in ('api', 'module', 'qualname'))
            if (not all(isinstance(v, str) for v in (api, module, qualname))
                    or not module.startswith('pipeline_integrity.')
                    or any(not part.isidentifier() for part in (module+'.'+qualname).split('.'))
                    or api != module+'.'+qualname or api not in row['apis'] or api in seen):
                raise ValueError('source API/module/qualified-name binding is inconsistent')
            seen.add(api)
            expected = 'src/'+module.replace('.', '/')+'.py'
            if not isinstance(pin.get('source'), str) or pin['source'].replace('\\', '/') != expected:
                raise ValueError('source path does not match the bound module')
            if not isinstance(pin.get('sha256'), str) or not re.fullmatch(r'[a-f0-9]{64}', pin['sha256']):
                raise ValueError('invalid source SHA-256')
    return document


def _concrete_declaration(node):
    if isinstance(node, ast.ClassDef):
        return any(_concrete_declaration(child) for child in node.body
                   if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                   and child.name not in ('__init__', '__post_init__'))
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return False
    body = [n for n in node.body if not (isinstance(n, ast.Expr)
            and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str))]
    return bool(body) and any(not (
        isinstance(n, (ast.Pass, ast.Raise))
        or (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant) and n.value.value is Ellipsis)
        or (isinstance(n, ast.Return) and (n.value is None or
            isinstance(n.value, ast.Constant) and n.value.value in (None, Ellipsis, NotImplemented)
            or isinstance(n.value, ast.Name) and n.value.id == 'NotImplemented'))
    ) for n in body)


def engineering_method_implementation_progress() -> dict:
    """Count reviewed implementations whose concrete source still matches.

    Source inspection performs no numerical model runs or optional-engine
    imports. A changed file requires renewed review rather than silently keeping
    its implementation credit.
    """
    document = _catalog_document()
    specs = {row.id: row for row in engineering_method_specs()}
    rows = []
    cache = {}
    for row in document['methods']:
        issues = []
        pins = row.get('source_implementations', [])
        if {pin['api'] for pin in pins} != set(specs[row['id']].apis):
            issues.append('API source bindings are incomplete')
        for pin in pins:
            relative = pin['source'].replace('\\', '/').split('/')
            if relative[:2] != ['src', 'pipeline_integrity'] or any(p in ('..', '') for p in relative):
                raise ValueError('invalid catalog source path')
            key = '/'.join(relative[2:])
            if key not in cache:
                try:
                    raw = files('pipeline_integrity').joinpath(*relative[2:]).read_bytes()
                    cache[key] = (hashlib.sha256(raw).hexdigest(), ast.parse(raw.decode('utf-8-sig')))
                except (OSError, SyntaxError, UnicodeError) as exc:
                    issues.append('source unavailable or invalid: ' + key + ': ' + type(exc).__name__)
                    continue
            sha, tree = cache[key]
            if sha != pin['sha256']:
                issues.append('source changed: ' + pin['api'])
            body = tree.body
            node = None
            for name in pin['qualname'].split('.'):
                node = next((n for n in body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.name == name), None)
                if node is None:
                    break
                body = node.body
            if node is None or not _concrete_declaration(node):
                issues.append('concrete API declaration absent: ' + pin['api'])
        complete = bool(row['implementation_complete'] and pins and not issues)
        rows.append({'id': row['id'], 'implementation_complete': complete, 'issues': issues})
    completed = sum(row['implementation_complete'] for row in rows)
    return {'scope': 'engineering_methods_223', 'implemented': completed,
            'total': len(rows), 'remaining': len(rows) - completed,
            'percent': round(100 * completed / len(rows), 2),
            'basis': 'reviewed per-method algorithm/API bindings and matching concrete source',
            'numerical_qualification_inferred': False, 'rows': rows}


def engineering_method_specs() -> tuple[EngineeringMethodSpec, ...]:
    document = _catalog_document()
    rows = tuple(EngineeringMethodSpec(
        id=row['id'], apis=tuple(row['apis']),
        supported_domain=tuple(row['supported_domain']),
        implementation_complete=row['implementation_complete'],
    ) for row in document['methods'])
    if len(rows) != 223 or len({row.id for row in rows}) != len(rows):
        raise ValueError('engineering method catalog must contain 223 distinct methods')
    for row in rows:
        if row.implementation_complete and not row.apis:
            raise ValueError('implemented method has no API binding: ' + row.id)
        if any(not api.startswith('pipeline_integrity.') for api in row.apis):
            raise ValueError('engineering APIs must belong to the declared package')
    return rows


def get_engineering_method_callable(method_id: str, api: str | None = None) -> Callable:
    """Resolve one allowlisted API; ambiguous methods require explicit selection.

    Pass the callable its documented typed request and any required providers or
    run context. Input dictionaries are not silently reinterpreted as a different
    model's parameters.
    """
    spec = next((row for row in engineering_method_specs() if row.id == method_id), None)
    if spec is None:
        raise KeyError('unknown engineering method: ' + str(method_id))
    if api is None:
        if len(spec.apis) != 1:
            raise ValueError('select one of the method APIs explicitly: ' + repr(spec.apis))
        api = spec.apis[0]
    if api not in spec.apis:
        raise ValueError('API is not bound to the selected engineering method')
    document = _catalog_document()
    method = next(row for row in document['methods'] if row['id'] == method_id)
    if not method['implementation_complete']:
        raise ValueError('selected engineering method implementation is incomplete')
    pin = next((row for row in method['source_implementations'] if row['api'] == api), None)
    if pin is None:
        raise ValueError('selected engineering API has no reviewed source binding')
    relative = pin['source'].replace('\\', '/').split('/')[2:]
    try:
        raw = files('pipeline_integrity').joinpath(*relative).read_bytes()
        body = ast.parse(raw.decode('utf-8-sig')).body
    except (OSError, UnicodeError, SyntaxError) as exc:
        raise ValueError('selected engineering API source unavailable or invalid') from exc
    if hashlib.sha256(raw).hexdigest() != pin['sha256']:
        raise ValueError('selected engineering API source changed since review')
    node = None
    for name in pin['qualname'].split('.'):
        node = next((n for n in body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.name == name), None)
        if node is None:
            break
        body = node.body
    if node is None or not _concrete_declaration(node):
        raise ValueError('selected engineering API has no concrete source declaration')
    target = importlib.import_module(pin['module'])
    for attribute in pin['qualname'].split('.'):
        target = getattr(target, attribute)
    if not callable(target):
        raise TypeError('bound engineering API is not callable')
    return target


def describe_engineering_method(method_id: str) -> dict:
    """Expose signatures and original contract annotations for API consumers."""
    spec = next((row for row in engineering_method_specs() if row.id == method_id), None)
    if spec is None:
        raise KeyError('unknown engineering method: ' + str(method_id))
    return {
        'id': spec.id, 'supported_domain': spec.supported_domain,
        'implementation_complete': next(row['implementation_complete']
            for row in engineering_method_implementation_progress()['rows'] if row['id'] == method_id),
        'apis': {api: str(inspect.signature(get_engineering_method_callable(method_id, api)))
                 for api in spec.apis},
        'qualification_inferred_from_catalog': False,
    }
