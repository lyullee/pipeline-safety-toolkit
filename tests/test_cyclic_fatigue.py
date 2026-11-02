"""Meaning, strict inputs, review gates and native receipt integrity for cyclic exchange."""

import json
import runpy
from contextlib import contextmanager
from copy import deepcopy

import pytest

from pipeline_integrity.adapters import ExternalResult
from pipeline_integrity.contracts import digest
from pipeline_integrity.cyclic_fatigue import REVIEWS, CyclicFatigueRequest, evaluate_cyclic_fatigue
from pipeline_integrity.material_exchange import prepare_material_job


def cases():
    return runpy.run_path("examples/cyclic_fatigue.py")["make_cases"]()


class OwnerSpy:
    def __init__(self, mutate=None):
        self.calls = []
        self.version = "spy-v1"
        self.mutate = mutate

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
            "values": {"owner_value": -123.0, "closed_work": None},
            "status": "computed",
            "notes": [],
        }
        if self.mutate:
            self.mutate(payload)
        payload["input_digest"] = digest(
            {key: payload[key] for key in ("model_id", "model_version", "inputs", "context")}
        )
        return ExternalResult(
            owner="matintegrity",
            owner_schema="matintegrity.assessment.v1",
            status="computed",
            asset_id=context.asset_id,
            payload=payload,
            payload_digest=digest(payload),
        )


@pytest.mark.parametrize("method", REVIEWS)
@pytest.mark.parametrize(
    "changes",
    [
        {"applicability_reviews": {}},
        {"review_evidence_ids": {}},
        {"parameters": None},
        {"population_id": None},
        {"parameter_support_id": None},
        {"response_regime": "monotonic"},
        {"stress_measure": None},
    ],
)
def test_missing_review_meaning_or_parameters_stays_local_without_native_import(method, changes):
    requests, context = cases()
    record = evaluate_cyclic_fatigue(requests[method].model_copy(update=changes), context)
    assert record["status"] == "needs_data" and record["native_receipt"] is None
    assert record["values"] == {} and record["failure_probability"] is None


@pytest.mark.parametrize("method", REVIEWS)
def test_receipt_preserves_parameters_context_units_population_and_signed_unknown_outputs(method):
    requests, context = cases()
    request, spy = requests[method], OwnerSpy()
    record = evaluate_cyclic_fatigue(request, context, client=spy)
    assert spy.calls == [(method, request.parameters, "json")]
    assert record["request"] == request.model_dump(mode="json")
    assert record["context"] == context.model_dump(mode="json")
    assert record["values"] == {"owner_value": -123.0, "closed_work": None}
    assert record["record_digest"] == digest({k: v for k, v in record.items() if k != "record_digest"})
    assert record["remaining_service_life_s"] is record["cumulative_damage"] is None
    assert not record["material_or_code_acceptance"] and not record["field_or_population_validation"]


@pytest.mark.parametrize("key", ["alternating_stress_Pa", "mean_stress_Pa", "ultimate_tensile_strength_Pa"])
def test_none_is_never_zero_or_native_default(key):
    requests, context = cases()
    request = requests["fatigue.goodman"]
    result = evaluate_cyclic_fatigue(
        request.model_copy(update={"parameters": request.parameters | {key: None}}), context
    )
    assert result["status"] == "needs_data" and result["request"]["parameters"][key] is None


def test_zero_mean_zero_amplitude_false_closure_and_repeated_time_are_preserved():
    requests, context = cases()
    request, spy = requests["fatigue.goodman"], OwnerSpy()
    evaluate_cyclic_fatigue(
        request.model_copy(
            update={"parameters": request.parameters | {"alternating_stress_Pa": 0.0, "mean_stress_Pa": 0.0}}
        ),
        context,
        client=spy,
    )
    assert spy.calls[0][1]["alternating_stress_Pa"] == spy.calls[0][1]["mean_stress_Pa"] == 0.0
    path = requests["fatigue.hysteresis_path"]
    result = evaluate_cyclic_fatigue(
        path.model_copy(
            update={
                "sample_time_s": (0.0, 0.0, 0.2, 0.3, 0.4),
                "parameters": path.parameters | {"cycle_boundary_declared": False},
            }
        ),
        context,
        client=spy,
    )
    assert result["request"]["sample_time_s"][:2] == [0.0, 0.0]
    assert spy.calls[-1][1]["cycle_boundary_declared"] is False


def test_unknown_path_point_and_clock_preserve_all_rows_before_import():
    requests, context = cases()
    request = requests["fatigue.hysteresis_path"]
    for changes in [
        {"path_sample_ids": ()},
        {"parameters": request.parameters | {"strain_fraction": [0.0, 0.0, None, 0.002, 0.0]}},
        {"sample_time_s": (0.0, 0.1, None, 0.3, 0.4)},
    ]:
        result = evaluate_cyclic_fatigue(request.model_copy(update=changes), context)
        assert result["status"] == "needs_data" and result["native_receipt"] is None
        assert len(result["request"]["parameters"]["strain_fraction"]) == 5


@pytest.mark.parametrize(
    "changes,match",
    [
        ({"maximum_path_samples": 3}, "budget"),
        ({"path_sample_ids": ("one",)}, "sample IDs"),
        ({"path_sample_ids": ("one",) * 5}, "unique"),
        ({"sample_time_s": (0.0, 0.1)}, "align"),
        ({"sample_time_s": (0.0, 0.2, 0.1, 0.3, 0.4)}, "decreases"),
        ({"observation_period_s": (0.1, 0.4)}, "outside"),
        ({"observation_period_s": (0.4, 0.0)}, "end"),
    ],
)
def test_path_budget_order_clock_and_period_are_checked_without_sorting(changes, match):
    requests, context = cases()
    with pytest.raises(ValueError, match=match):
        evaluate_cyclic_fatigue(requests["fatigue.hysteresis_path"].model_copy(update=changes), context)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
@pytest.mark.parametrize("where", ["parameters", "context", "pin"])
def test_nonfinite_nested_python_values_fail_before_json_clone(value, where):
    requests, context = cases()
    request, spy = requests["fatigue.goodman"], OwnerSpy()
    job = prepare_material_job(
        request.method,
        request.parameters,
        context,
        location_id=request.location_id,
        evidence_ids=request.evidence_ids,
        client=spy,
    )
    if where == "parameters":
        request = request.model_copy(update={"parameters": request.parameters | {"nested": [{"x": value}]}})
    elif where == "context":
        context = context.model_copy(update={"metadata": {"nested": [value]}})
    else:
        job = job.model_copy(update={"parameters": job.parameters | {"nested": [value]}})
    with pytest.raises(ValueError):
        evaluate_cyclic_fatigue(request, context, client=spy, pinned_job=job)
    assert spy.calls == []


@pytest.mark.parametrize(
    "changes,match",
    [
        ({"clock_id": "other"}, "clock"),
        ({"input_units": {"alternating_stress_Pa": "MPa"}}, "exact SI"),
        ({"review_evidence_ids": {"tensile_mean_and_half_range_amplitude": "absent"}}, "selected evidence"),
        ({"applicability_reviews": {"all_good": True}}, "selected method"),
        ({"strain_measure": "engineering"}, "no strain input"),
    ],
)
def test_context_unit_and_review_revalidation(changes, match):
    requests, context = cases()
    with pytest.raises(ValueError, match=match):
        evaluate_cyclic_fatigue(requests["fatigue.goodman"].model_copy(update=changes), context)


@pytest.mark.parametrize(
    "field,value",
    [
        ("maximum_path_samples", True),
        ("applicability_reviews", {"tensile_mean_and_half_range_amplitude": 1}),
        ("observation_period_s", [False, 1]),
    ],
)
def test_strict_json_rejects_boolean_numbers_and_nonboolean_reviews(field, value):
    requests, _ = cases()
    payload = requests["fatigue.goodman"].model_dump(mode="json") | {field: value}
    with pytest.raises(ValueError):
        CyclicFatigueRequest.model_validate_json(json.dumps(payload))


def test_job_pin_rejects_parameters_version_schema_or_probability_drift():
    requests, context = cases()
    request, spy = requests["fatigue.goodman"], OwnerSpy()
    job = prepare_material_job(
        request.method,
        request.parameters,
        context,
        location_id=request.location_id,
        evidence_ids=request.evidence_ids,
        client=spy,
    )
    assert evaluate_cyclic_fatigue(request, context, client=spy, pinned_job=job)["status"] == "computed"
    changed = request.model_copy(update={"parameters": request.parameters | {"mean_stress_Pa": 0.0}})
    with pytest.raises(ValueError, match="reviewed request"):
        evaluate_cyclic_fatigue(changed, context, client=spy, pinned_job=job)
    for update in [
        {"model_version": "other"},
        {"input_schema_digest": "0" * 64},
        {"probability_semantics": {"owner_value": "period_failure"}},
    ]:
        with pytest.raises(ValueError, match="schema, version"):
            evaluate_cyclic_fatigue(request, context, client=spy, pinned_job=job.model_copy(update=update))


@pytest.mark.parametrize(
    "mutate,match",
    [
        (lambda p: p.update(model_id="different"), "model, status"),
        (lambda p: p.update(units={"owner_value": "Pa"}), "model, status"),
        (lambda p: p["inputs"].update(mean_stress_Pa=0.0), "supplied input"),
        (lambda p: p["context"].update(time_basis="other"), "clock"),
    ],
)
def test_even_rehashed_native_mutations_are_rejected(mutate, match):
    requests, context = cases()
    with pytest.raises(ValueError, match=match):
        evaluate_cyclic_fatigue(requests["fatigue.goodman"], context, client=OwnerSpy(mutate))


def test_optional_source_pin_requires_loaded_owner_root_and_checks_before_and_after(monkeypatch):
    requests, context = cases()
    request = requests["fatigue.goodman"].model_copy(update={"owner_source_tree_sha256": "a" * 64})
    spy = OwnerSpy()
    with pytest.raises(ValueError, match="both source_root"):
        evaluate_cyclic_fatigue(request, context, client=spy)
    with pytest.raises(ValueError, match="reviewed source pin"):
        evaluate_cyclic_fatigue(request, context, client=spy, owner_source_root=".")
    assert spy.calls == []

    @contextmanager
    def changed_source(root, expected, client):
        assert (root, expected, client) == (".", "a" * 64, spy)
        yield {"before": "a" * 64, "after": None}
        raise ValueError("source changed during calculation")

    monkeypatch.setattr("pipeline_integrity.cyclic_fatigue.material_source_guard", changed_source)
    with pytest.raises(ValueError, match="changed during"):
        evaluate_cyclic_fatigue(request, context, client=spy, owner_source_root=".")


def test_source_guard_receipt_is_serialized_after_context_exit(monkeypatch):
    requests, context = cases()
    request = requests["fatigue.goodman"].model_copy(update={"owner_source_tree_sha256": "a" * 64})

    @contextmanager
    def stable_source(root, expected, client):
        pin = {"before": {"source_tree_sha256": expected}, "after": None, "source_unchanged": False}
        yield pin
        pin.update(after=pin["before"], source_unchanged=True)

    monkeypatch.setattr("pipeline_integrity.cyclic_fatigue.material_source_guard", stable_source)
    record = evaluate_cyclic_fatigue(request, context, client=OwnerSpy(), owner_source_root=".")
    assert record["source_pin"]["source_unchanged"]
    assert record["source_pin"]["before"] == record["source_pin"]["after"]
