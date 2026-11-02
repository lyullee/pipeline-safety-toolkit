"""Specimen meaning, unknowns and native delegation without copying test equations."""

import json
import runpy
from copy import deepcopy

import pytest

from pipeline_integrity.adapters import ExternalResult
from pipeline_integrity.characterization import REVIEWS, CharacterizationRequest, evaluate_characterization
from pipeline_integrity.contracts import digest
from pipeline_integrity.material_exchange import prepare_material_job


def cases():
    return runpy.run_path("examples/characterization.py")["make_cases"]()


class OwnerSpy:
    def __init__(self):
        self.calls = []
        self.version = "spy-v1"

    def describe(self, method):
        return {
            "model_id": method,
            "model_version": self.version,
            "input_schema": {"type": "object"},
            "output_units": {"owner_value": "1"},
            "probability_semantics": {},
        }

    def evaluate(self, method, parameters, context, *, location_id, input_mode):
        self.calls.append((method, deepcopy(parameters), input_mode))
        payload = {
            "schema_version": "matintegrity.assessment.v1",
            "model_id": method,
            "model_version": self.version,
            "inputs": deepcopy(parameters),
            "context": {
                "asset_id": context.asset_id,
                "location_id": location_id,
                "time_basis": context.clock_id,
                "metadata": {
                    "pipeline_context_digest": digest(context),
                    "pipeline_scenario_id": context.scenario_id,
                    "pipeline_origin": context.origin,
                    "pipeline_input_validation_mode": input_mode,
                },
            },
            "units": {"owner_value": "1"},
            "probability_semantics": {},
            "values": {"owner_value": 123.0},
            "status": "computed",
            "notes": [],
        }
        payload["input_digest"] = digest(
            {k: payload[k] for k in ("model_id", "model_version", "inputs", "context")}
        )
        return ExternalResult(
            owner="matintegrity",
            owner_schema="matintegrity.assessment.v1",
            status="computed",
            asset_id=context.asset_id,
            payload=payload,
            payload_digest=digest(payload),
        )


@pytest.mark.parametrize("method", list(REVIEWS))
def test_method_specific_reviews_and_evidence_are_required_before_owner_import(method):
    requests, context = cases()
    for changes in ({"applicability_reviews": {}}, {"review_evidence_ids": {}}, {"parameters": None}):
        result = evaluate_characterization(requests[method].model_copy(update=changes), context)
        assert result["status"] == "needs_data" and result["native_receipt"] is None
        assert result["values"] == {}


@pytest.mark.parametrize("method", list(REVIEWS))
def test_owner_receipt_preserves_specimen_input_context_and_units(method):
    requests, context = cases()
    spy = OwnerSpy()
    request = requests[method]
    record = evaluate_characterization(request, context, client=spy)
    assert record["status"] == "computed" and record["values"] == {"owner_value": 123.0}
    assert spy.calls == [(method, request.parameters, "json")]
    assert record["request"]["specimen_id"] == request.specimen_id
    assert record["context"] == context.model_dump(mode="json")
    assert record["record_digest"] == digest({k: v for k, v in record.items() if k != "record_digest"})
    assert not record["material_or_test_acceptance"]


def test_false_uniform_declaration_and_zero_values_are_retained():
    requests, context = cases()
    request = requests["materials.tensile_point"]
    parameters = request.parameters | {
        "force_N": 0.0,
        "extension_m": 0.0,
        "uniform_deformation_declared": False,
    }
    spy = OwnerSpy()
    record = evaluate_characterization(
        request.model_copy(update={"parameters": parameters}), context, client=spy
    )
    assert record["status"] == "computed"
    assert spy.calls[0][1]["uniform_deformation_declared"] is False


@pytest.mark.parametrize(
    "field", ["force_N", "initial_area_m2", "extension_m", "gauge_length_m", "uniform_deformation_declared"]
)
def test_unknown_tensile_inputs_are_not_zero_or_default_declarations(field):
    requests, context = cases()
    request = requests["materials.tensile_point"]
    result = evaluate_characterization(
        request.model_copy(update={"parameters": request.parameters | {field: None}}), context
    )
    assert result["status"] == "needs_data" and result["request"]["parameters"][field] is None


def test_unknown_branch_rows_are_not_removed_before_native_intersection():
    requests, context = cases()
    request = requests["materials.offset_yield"]
    result = evaluate_characterization(
        request.model_copy(
            update={"parameters": request.parameters | {"engineering_stress_Pa": [0.0, None, 3e8, 4e8]}}
        ),
        context,
    )
    assert result["status"] == "needs_data"
    assert len(result["request"]["parameters"]["engineering_stress_Pa"]) == 4
    with pytest.raises(ValueError, match="budget"):
        evaluate_characterization(request.model_copy(update={"maximum_branch_samples": 3}), context)


def test_specimen_job_pin_rejects_changed_parameters_or_schema():
    requests, context = cases()
    request = requests["materials.tensile_point"]
    spy = OwnerSpy()
    job = prepare_material_job(
        request.method,
        request.parameters,
        context,
        location_id=request.location_id,
        evidence_ids=request.evidence_ids,
        client=spy,
    )
    assert evaluate_characterization(request, context, client=spy, pinned_job=job)["status"] == "computed"
    with pytest.raises(ValueError, match="specimen request"):
        evaluate_characterization(
            request.model_copy(update={"parameters": request.parameters | {"force_N": 1.0}}),
            context,
            client=spy,
            pinned_job=job,
        )
    spy.version = "spy-v2"
    with pytest.raises(ValueError, match="schema, version"):
        evaluate_characterization(request, context, client=spy, pinned_job=job)


def test_revalidation_preserves_explicit_units_clock_and_review_provenance():
    requests, context = cases()
    request = requests["materials.tensile_point"]
    for changes, match in [
        ({"input_units": request.input_units | {"extension_m": "mm"}}, "exact declared"),
        ({"clock_id": "another"}, "source clock"),
        ({"review_evidence_ids": dict.fromkeys(REVIEWS[request.method], "missing")}, "review evidence"),
        ({"applicability_reviews": {"invented": True}}, "selected method"),
    ]:
        with pytest.raises(ValueError, match=match):
            evaluate_characterization(request.model_copy(update=changes), context)
    payload = json.loads(request.model_dump_json())
    payload["maximum_branch_samples"] = True
    with pytest.raises(ValueError):
        CharacterizationRequest.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_clone_inputs_are_rejected_before_json_can_turn_them_into_null(value):
    requests, context = cases()
    request = requests["materials.tensile_point"]
    with pytest.raises(ValueError):
        evaluate_characterization(
            request.model_copy(update={"parameters": request.parameters | {"force_N": value}}), context
        )
