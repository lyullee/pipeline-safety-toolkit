"""Indentation scope and original receipt preservation; actual numerical checks run separately."""

import runpy
from copy import deepcopy

import pytest

from pipeline_integrity.adapters import ExternalResult
from pipeline_integrity.contracts import digest
from pipeline_integrity.hardness_exchange import REQUIRED, REVIEWS, evaluate_hardness, verify_hardness_record


def base():
    return runpy.run_path("examples/hardness_methods.py")["make_cases"]()


class OwnerSpy:
    """A fixed owner result checks delegation and binding without implementing indentation formulas."""

    def __init__(self, *, status="computed"):
        self.calls, self.status = [], status

    def describe(self, method):
        conventional = "conventional_HBW" if method == "testing.brinell" else "conventional_HV"
        return dict(
            model_id=method,
            model_version="1.0.0",
            input_schema=dict(type="object"),
            output_units={"indentation_hardness_Pa": "Pa", conventional: "kgf/mm^2"},
            probability_semantics={},
        )

    def evaluate(self, method, parameters, context, *, location_id, input_mode):
        self.calls.append((method, deepcopy(parameters), input_mode))
        description = self.describe(method)
        conventional = "conventional_HBW" if method == "testing.brinell" else "conventional_HV"
        payload = dict(
            schema_version="matintegrity.assessment.v1",
            model_id=method,
            model_version="1.0.0",
            inputs=deepcopy(parameters),
            context=dict(
                asset_id=context.asset_id,
                location_id=location_id,
                time_basis=context.clock_id,
                metadata=dict(
                    pipeline_context_digest=digest(context),
                    pipeline_scenario_id=context.scenario_id,
                    pipeline_origin=context.origin,
                    pipeline_input_validation_mode=input_mode,
                ),
            ),
            status=self.status,
            values={"indentation_hardness_Pa": 1e9, conventional: 100.0},
            units=description["output_units"],
            probability_semantics={},
            notes=["authored owner delegation response"],
        )
        payload["input_digest"] = digest(
            {key: payload[key] for key in ("model_id", "model_version", "inputs", "context")}
        )
        return ExternalResult(
            owner="matintegrity",
            owner_schema="matintegrity.assessment.v1",
            status=self.status,
            asset_id=context.asset_id,
            payload=payload,
            payload_digest=digest(payload),
        )


@pytest.mark.parametrize("method,key", [(method, key) for method, keys in REVIEWS.items() for key in keys])
def test_each_geometry_metrology_and_applicability_review_is_required_before_owner_import(method, key):
    cases, context = base()
    request = cases[method]
    reviews = request.applicability_reviews | {key: False}
    result = evaluate_hardness(request.model_copy(update={"applicability_reviews": reviews}), context)
    assert result["status"] == "needs_data" and result["native_receipt"] is None
    verify_hardness_record(result)


@pytest.mark.parametrize("method,key", [(method, key) for method, keys in REQUIRED.items() for key in keys])
def test_missing_original_force_or_dimension_never_gains_a_nominal_value(method, key):
    cases, context = base()
    request = cases[method]
    p = request.parameters.copy()
    p.pop(key)
    result = evaluate_hardness(request.model_copy(update={"parameters": p}), context)
    assert result["status"] == "needs_data" and f"parameters.{key}" in result["unknown_input_paths"]


@pytest.mark.parametrize(
    "field",
    [
        "dwell_s",
        "surface_condition_id",
        "procedure_id",
        "force_evidence_id",
        "length_evidence_id",
        "conditions_evidence_id",
        "geometry_applicable",
    ],
)
def test_nested_unknown_test_conditions_are_preserved_and_withheld(field):
    cases, context = base()
    request = cases["testing.vickers"]
    conditions = request.conditions.model_copy(update={field: None})
    result = evaluate_hardness(request.model_copy(update={"conditions": conditions}), context)
    assert result["status"] == "needs_data" and result["request"]["conditions"][field] is None
    assert f"conditions.{field}" in result["unknown_input_paths"]


def test_deep_unknown_parameter_cannot_be_hidden_by_a_known_outer_dictionary():
    cases, context = base()
    request = cases["testing.brinell"]
    p = request.parameters | dict(source_geometry=dict(measurements=[0.003, None]))
    result = evaluate_hardness(request.model_copy(update={"parameters": p}), context)
    assert result["status"] == "needs_data" and result["unknown_input_paths"] == [
        "parameters.source_geometry.measurements[1]"
    ]
    assert result["request"]["parameters"]["source_geometry"]["measurements"] == [0.003, None]


@pytest.mark.parametrize("method", list(REVIEWS))
def test_explicitly_inapplicable_geometry_is_out_of_scope_without_an_owner_call(method):
    cases, context = base()
    request = cases[method]
    request = request.model_copy(
        update={"conditions": request.conditions.model_copy(update={"geometry_applicable": False})}
    )
    result = evaluate_hardness(request, context)
    assert result["status"] == "out_of_scope" and result["native_receipt"] is None
    verify_hardness_record(result)


@pytest.mark.parametrize("method", list(REVIEWS))
def test_native_field_names_SI_values_and_test_conditions_are_preserved_in_distinct_scopes(method):
    cases, context = base()
    request, owner = cases[method], OwnerSpy()
    result = evaluate_hardness(request, context, client=owner)
    assert owner.calls == [(method, request.parameters, "json")]
    assert result["request"]["conditions"]["dwell_s"] == 10.0
    assert "dwell_s" not in result["native_receipt"]["job"]["parameters"]
    assert result["native_receipt"]["job"]["location_id"] == request.location_id
    assert (
        not result["acceptance_criteria_applied"]
        and not result["strength_brittleness_or_failure_probability_inferred"]
    )
    verify_hardness_record(result)


def test_asset_clock_material_state_and_condition_evidence_cannot_be_assumed_or_relabelled():
    cases, context = base()
    request = cases["testing.vickers"]
    with pytest.raises(ValueError, match="asset/clock"):
        evaluate_hardness(request, context.model_copy(update={"clock_id": "other"}))
    changed = request.conditions.model_copy(update={"force_evidence_id": "absent"})
    with pytest.raises(ValueError, match="evidence"):
        evaluate_hardness(request.model_copy(update={"conditions": changed}), context)
    result = evaluate_hardness(request.model_copy(update={"material_condition_id": None}), context)
    assert result["status"] == "needs_data" and result["unknown_input_paths"] == ["material_condition_id"]


def test_model_copy_cannot_bypass_positive_dwell_or_method_specific_review_schema():
    cases, context = base()
    request = cases["testing.vickers"]
    with pytest.raises(ValueError):
        evaluate_hardness(
            request.model_copy(update={"conditions": request.conditions.model_copy(update={"dwell_s": 0.0})}),
            context,
        )
    with pytest.raises(ValueError, match="selected method"):
        evaluate_hardness(
            request.model_copy(update={"applicability_reviews": {REVIEWS["testing.brinell"][0]: True}}),
            context,
        )


@pytest.mark.parametrize("status", ["needs_data", "out_of_scope"])
def test_native_withheld_status_and_notes_are_retained_without_acceptance(status):
    cases, context = base()
    result = evaluate_hardness(cases["testing.brinell"], context, client=OwnerSpy(status=status))
    assert result["status"] == status and result["issues"] == ["authored owner delegation response"]
    verify_hardness_record(result)


@pytest.mark.parametrize(
    "mutation", ["value", "native_value", "parameter", "geometry", "qualification", "receipt_removed"]
)
def test_recomputed_outer_hash_cannot_hide_changed_original_receipt_or_qualification(mutation):
    cases, context = base()
    result = evaluate_hardness(cases["testing.brinell"], context, client=OwnerSpy())
    changed = deepcopy(result)
    if mutation == "value":
        changed["values"]["indentation_hardness_Pa"] = 2e9
    elif mutation == "native_value":
        changed["native_receipt"]["native"]["payload"]["values"]["indentation_hardness_Pa"] = 2e9
    elif mutation == "parameter":
        changed["request"]["parameters"]["test_force_N"] = 2000.0
    elif mutation == "geometry":
        changed["request"]["conditions"]["geometry_applicable"] = False
    elif mutation == "qualification":
        changed["field_or_standard_qualification"] = True
    else:
        changed["native_receipt"] = None
    changed["record_digest"] = digest(
        {key: value for key, value in changed.items() if key != "record_digest"}
    )
    with pytest.raises(ValueError):
        verify_hardness_record(changed)


def test_unsealed_result_mutation_is_detected():
    cases, context = base()
    result = evaluate_hardness(cases["testing.vickers"], context, client=OwnerSpy())
    result["request"]["material_condition_id"] = "different_condition"
    with pytest.raises(ValueError, match="changed after sealing"):
        verify_hardness_record(result)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_model_copy_payload_is_invalid_instead_of_unknown(value):
    cases, context = base()
    request = cases["testing.vickers"]
    with pytest.raises(ValueError):
        evaluate_hardness(
            request.model_copy(update={"parameters": request.parameters | {"test_force_N": value}}),
            context,
        )
    with pytest.raises(ValueError):
        evaluate_hardness(request, context.model_copy(update={"metadata": {"invalid": value}}))
