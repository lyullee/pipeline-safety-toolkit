"""Input-field rejection sweep, distinct from relational or numerical tests.

Each assertion checks the target field's error location and category. Other
missing fields never establish success for the field under test. The sweep
exercises actual request classes; it does not fabricate substitute schemas.
"""
from __future__ import annotations

import json
import math
import types
import typing

import pytest
from pydantic import ValidationError

from scripts.engineering_input_inventory import request_inventory

CONTRACTS, METHODS = request_inventory()


def _base_types(annotation):
    if typing.get_origin(annotation) in (typing.Union, types.UnionType, typing.Annotated):
        return {item for arg in typing.get_args(annotation) for item in _base_types(arg)}
    return {annotation} if isinstance(annotation, type) else set()


def _cases():
    for name, model in sorted(CONTRACTS.items()):
        if not hasattr(model, 'model_fields'):
            continue  # Dataclass constructors have separate valid-baseline cases.
        if model.model_config.get('extra') == 'forbid':
            yield name, '__unexpected_input_probe__', 'extra_forbidden', {'__unexpected_input_probe__': 1}
        for field, info in model.model_fields.items():
            if info.is_required():
                yield name, field, 'missing', {}
            base = _base_types(info.annotation)
            if float in base:
                for label, number in (('nan', math.nan), ('positive_infinity', math.inf), ('negative_infinity', -math.inf)):
                    yield name, field, label, {field: number}
            if float in base or int in base:
                yield name, field, 'unit_string_in_numeric_field', {field: '1 invalid_unit'}
                yield name, field, 'boolean_in_numeric_field', {field: True}
            if int in base and bool not in base:
                yield name, field, 'fractional_integer', {field: 1.25}
            if typing.get_origin(info.annotation) is typing.Literal:
                yield name, field, 'invalid_literal', {field: '__not_a_declared_choice__'}
            for metadata in info.metadata:
                for constraint in ('gt', 'ge'):
                    limit = getattr(metadata, constraint, None)
                    if limit is not None and float in base | ({float} if int in base else set()):
                        yield name, field, 'below_' + constraint, {field: limit - max(1., abs(limit))}
                        if constraint == 'gt':
                            yield name, field, 'at_strict_lower_bound', {field: limit}
                for constraint in ('lt', 'le'):
                    limit = getattr(metadata, constraint, None)
                    if limit is not None and (float in base or int in base):
                        value = limit if constraint == 'lt' else limit + max(1., abs(limit))
                        yield name, field, 'violates_' + constraint, {field: value}
                if getattr(metadata, 'min_length', None):
                    if str in base:
                        value = ''
                    elif typing.get_origin(info.annotation) is dict:
                        value = {}
                    else:
                        value = []
                    yield name, field, 'below_min_length', {field: value}


CASES = list(_cases())


@pytest.mark.parametrize('contract,field,kind,payload', CASES,
                         ids=[f'{name}:{field}:{kind}' for name, field, kind, _ in CASES])
def test_request_field_rejects_invalid_input(contract, field, kind, payload):
    with pytest.raises(ValidationError) as caught:
        CONTRACTS[contract].model_validate_json(json.dumps(payload))
    errors = [e for e in caught.value.errors() if e['loc'] and e['loc'][0] == field]
    assert errors, f'target field {field!r} was not rejected; unrelated errors do not count'
    if kind in ('missing', 'extra_forbidden'):
        assert any(e['type'] == kind for e in errors)
    else:
        assert any(e['type'] not in ('missing', 'extra_forbidden') for e in errors)
