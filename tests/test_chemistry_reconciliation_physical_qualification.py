"""Independent interval-arithmetic checks for chemistry reconciliation."""

from copy import deepcopy
from runpy import run_path

import pytest
from pydantic import ValidationError

from pipeline_integrity.adapters import MaterialClient


REQUESTS, CONTEXT = run_path("examples/material_quality.py")["make_cases"]()
BASE = REQUESTS["materials.chemistry_reconciliation"]
CLIENT = MaterialClient()


def evaluate(parameters):
    # The owner contract declares evidence collections as tuples. Keep the
    # synthetic fixture structurally faithful before passing it through the API.
    for row in ("certificate", "verification", "requirements"):
        parameters[row]["evidence"] = tuple(parameters[row]["evidence"])
    return CLIENT.evaluate(
        "materials.chemistry_reconciliation",
        parameters,
        CONTEXT,
        location_id=BASE.location_id,
    )


def changed(**updates):
    values = deepcopy(BASE.parameters)
    for path, replacement in updates.items():
        node = values
        *parents, leaf = path.split(".")
        for key in parents:
            node = node[key]
        node[leaf] = replacement
    return values


def test_contained_certificate_and_verification_intervals_satisfy_declared_chemistry_limits():
    result = evaluate(deepcopy(BASE.parameters))
    assert result.status == "computed"
    values = result.payload["values"]
    assert values["outcome"] == "satisfied"
    assert all(item["outcome"] == "satisfied" for item in values["checks"])
    assert values["elements"] == [
        {
            "element": "Ni",
            "limits": {"lower": 0.1, "upper": 0.3},
            "certificate_outcome": "satisfied",
            "verification_outcome": "satisfied",
            "interval_overlap_outcome": "satisfied",
        }
    ]


def test_fully_disjoint_assay_interval_is_not_satisfied_by_midpoint_or_overlap_heuristics():
    p = changed(**{"verification.element_intervals": {"Ni": {"lower": 0.31, "upper": 0.32}}})
    values = evaluate(p).payload["values"]
    assert values["outcome"] == "not_satisfied"
    ni = values["elements"][0]
    assert ni["verification_outcome"] == "not_satisfied"
    assert ni["interval_overlap_outcome"] == "not_satisfied"


def test_interval_partly_crossing_limit_is_unknown_not_forced_pass_or_fail():
    p = changed(
        **{
            "certificate.element_intervals": {"Ni": {"lower": 0.29, "upper": 0.31}},
            "verification.element_intervals": {"Ni": {"lower": 0.28, "upper": 0.3}},
        }
    )
    values = evaluate(p).payload["values"]
    assert values["elements"][0]["certificate_outcome"] == "unknown"
    assert values["outcome"] == "unknown"


def test_missing_interval_remains_unknown_and_contributes_no_imputed_value():
    p = changed(**{"verification.element_intervals": {"Ni": None}})
    values = evaluate(p).payload["values"]
    assert values["outcome"] == "unknown"
    assert values["elements"][0]["verification_outcome"] == "unknown"
    assert values["elements"][0]["interval_overlap_outcome"] == "unknown"


def test_disjoint_certificate_and_verification_intervals_fail_declared_overlap_consistency():
    p = changed(**{
        "certificate.element_intervals": {"Ni": {"lower": 0.12, "upper": 0.14}},
        "verification.element_intervals": {"Ni": {"lower": 0.2, "upper": 0.22}},
    })
    values = evaluate(p).payload["values"]
    assert values["elements"][0]["interval_overlap_outcome"] == "not_satisfied"
    assert values["outcome"] == "not_satisfied"


def test_false_optional_identity_policies_do_not_emit_heat_or_verification_identity_checks():
    p = changed(
        **{
            "verification.heat_id": None,
            "verification.designation": None,
            "require_matching_heat_ids": False,
            "require_verification_designation": False,
        }
    )
    result = evaluate(p)
    assert result.status == "computed"
    check_ids = {row["check_id"] for row in result.payload["values"]["checks"]}
    assert "heat_traceability" not in check_ids
    assert "verification_designation" not in check_ids
    assert result.payload["values"]["outcome"] == "satisfied"


def test_reversed_intervals_and_impossible_lower_fraction_totals_are_rejected():
    with pytest.raises(ValidationError):
        p = changed(**{"certificate.element_intervals": {"Ni": {"lower": 0.21, "upper": 0.19}}})
        evaluate(p)
    with pytest.raises(ValidationError):
        p = changed(
            **{
                "certificate.element_intervals": {
                    "Ni": {"lower": 0.7, "upper": 0.8},
                    "Cr": {"lower": 0.4, "upper": 0.5},
                }
            }
        )
        evaluate(p)
