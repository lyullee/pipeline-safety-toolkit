"""Locate actual request contracts for engineering APIs without running models."""
from __future__ import annotations

import ast
import dataclasses
import importlib
import inspect
import json
import types
import typing
import textwrap
from importlib.resources import files

from pydantic import BaseModel


def _models(annotation):
    if inspect.isclass(annotation) and dataclasses.is_dataclass(annotation):
        return [annotation]
    if inspect.isclass(annotation) and issubclass(annotation, BaseModel):
        return [annotation]
    if typing.get_origin(annotation) in (typing.Union, types.UnionType, typing.Annotated):
        return [model for item in typing.get_args(annotation) for model in _models(item)]
    return []


def request_inventory():
    doc = json.loads(files('pipeline_integrity.catalog_data').joinpath('engineering_methods.json').read_text(encoding='utf-8'))
    contracts = {}
    methods = []
    for method in doc['methods']:
        primary = set()
        unresolved = []
        for pin in method['source_implementations']:
            module = importlib.import_module(pin['module'])
            target = module
            for name in pin['qualname'].split('.'):
                target = getattr(target, name)
            signature = inspect.signature(target)
            try:
                hints = typing.get_type_hints(target)
            except (NameError, TypeError):
                hints = {}
            candidates = []
            for name, parameter in signature.parameters.items():
                if name in ('self', 'cls', 'context', 'c', 'client', 'provider', 'property_provider'):
                    continue
                candidates.extend(_models(hints.get(name, parameter.annotation)))
            # Older APIs validate their unannotated first argument explicitly.
            # Only classes used by the selected function's own AST are inferred.
            try:
                tree = ast.parse(textwrap.dedent(inspect.getsource(target)))
            except (OSError, TypeError, SyntaxError, IndentationError):
                tree = None
            if tree is not None:
                names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
                for name in names:
                    candidate = getattr(module, name, None)
                    if name.endswith(('Request', 'Input', 'Parameters', 'Spec', 'Settings')) and inspect.isclass(candidate):
                        candidates.extend(_models(candidate))
                    helper = getattr(module, name, None)
                    if name in ('_inputs', '_checked') and inspect.isfunction(helper):
                        helper_tree = ast.parse(textwrap.dedent(inspect.getsource(helper)))
                        for item in ast.walk(helper_tree):
                            if isinstance(item, ast.Name):
                                candidates.extend(_models(getattr(module, item.id, None)))
            candidates = [model for model in candidates if model.__name__ not in ('RunContext', 'Contract', 'Calculation', 'AssessmentResult')]
            if not candidates:
                unresolved.append(pin['api'])
            for model in candidates:
                key = model.__module__ + '.' + model.__qualname__
                contracts[key] = model
                primary.add(key)
        methods.append({'id': method['id'], 'contracts': sorted(primary),
                        'apis_without_direct_request_contract': unresolved})
    return contracts, methods
