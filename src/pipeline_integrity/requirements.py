"""Caller-reviewed numerical requirements remain separate from model completion."""

from __future__ import annotations

import operator
from typing import Literal

from .contracts import AssessmentResult, Contract, Name, digest


class DeclaredRequirement(Contract):
    id: Name
    model_id: Name
    output_name: Name
    comparison: Literal["lt", "le", "eq", "ge", "gt"]
    limit: float
    unit: Name
    criterion_source: Name
    applicability: Name


def evaluate_requirement(rule: DeclaredRequirement, result: AssessmentResult):
    if rule.model_id != result.model_id:
        raise ValueError("requirement targets a different model")
    record = {
        "requirement_id": rule.id,
        "rule_digest": digest(rule),
        "result_digest": digest(result),
        "criterion_source": rule.criterion_source,
        "applicability": rule.applicability,
        "whole_standard_compliance_claim": False,
    }
    if result.status != "computed":
        return record | {"outcome": "withheld", "reason": "a numerical model result is unavailable"}
    if result.output_units.get(rule.output_name) != rule.unit:
        raise ValueError("requirement units or pressure reference differ from the calculated quantity")
    value = result.values.get(rule.output_name)
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise ValueError("numerical requirements need a scalar numerical output")
    operations = {
        "lt": operator.lt,
        "le": operator.le,
        "eq": operator.eq,
        "ge": operator.ge,
        "gt": operator.gt,
    }
    passed = operations[rule.comparison](value, rule.limit)
    return record | {
        "outcome": "satisfied" if passed else "not_satisfied",
        "value": value,
        "limit": rule.limit,
        "comparison": rule.comparison,
        "unit": rule.unit,
    }
