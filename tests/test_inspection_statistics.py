"""Inspection meaning, original order, strict evidence gates and audited native delegation."""

import json
import runpy
from copy import deepcopy

import pytest

from pipeline_integrity.adapters import ExternalResult
from pipeline_integrity.contracts import digest
from pipeline_integrity.inspection_statistics import (
    REVIEWS,
    InspectionStatisticsRequest,
    check_inspection_statistics_record,
    evaluate_inspection_statistics,
)
from pipeline_integrity.material_exchange import prepare_material_job


def cases():
    return runpy.run_path("examples/inspection_statistics.py")["make_cases"]()


class OwnerSpy:
    def __init__(self, status="computed"):
        self.calls = []
        self.descriptions = []
        self.version = "spy-v1"
        self.status = status
        self.schema = {"type": "object"}
        self.units = {"owner_value": "1"}
        self.meanings = {"owner_value": "detection_given_flaw_and_procedure"}
        self.alter = lambda payload: None

    def describe(self, method):
        self.descriptions.append(method)
        return {
            "model_id": method,
            "model_version": self.version,
            "input_schema": deepcopy(self.schema),
            "output_units": deepcopy(self.units),
            "probability_semantics": deepcopy(self.meanings),
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
            "units": deepcopy(self.units),
            "probability_semantics": deepcopy(self.meanings),
            "values": {"owner_value": 0.25} if self.status == "computed" else {},
            "status": self.status,
            "notes": ["Original owner diagnostic retained"],
            "requirements": {"declared_check": "not_satisfied"},
        }
        payload["input_digest"] = digest(
            {k: payload[k] for k in ("model_id", "model_version", "inputs", "context")}
        )
        self.alter(payload)
        return ExternalResult(
            owner="matintegrity",
            owner_schema="matintegrity.assessment.v1",
            status=self.status,
            asset_id=context.asset_id,
            payload=payload,
            payload_digest=digest(payload),
        )


@pytest.mark.parametrize("method", list(REVIEWS))
@pytest.mark.parametrize("missing", ["applicability_reviews", "review_evidence_ids", "parameters"])
def test_unreviewed_or_unknown_requests_withhold_before_owner_discovery(method, missing):
    requests, context = cases()
    spy = OwnerSpy()
    changes = {missing: None if missing == "parameters" else {}}
    record = evaluate_inspection_statistics(requests[method].model_copy(update=changes), context, client=spy)
    assert record["status"] == "needs_data" and record["native_receipt"] is None
    assert spy.calls == spy.descriptions == []


@pytest.mark.parametrize("method", list(REVIEWS))
@pytest.mark.parametrize("status", ["computed", "needs_data", "out_of_scope"])
def test_native_states_units_probability_and_requirement_details_survive(method, status):
    requests, context = cases()
    request, spy = requests[method], OwnerSpy(status)
    record = evaluate_inspection_statistics(request, context, client=spy)
    assert record["status"] == status
    assert record["probability_semantics"] == spy.meanings
    assert record["output_units"] == spy.units
    assert record["native_receipt"]["native"]["payload"]["requirements"] == {
        "declared_check": "not_satisfied"
    }
    assert spy.calls == [(method, request.parameters, "json")]
    assert record["period_failure_probability"] is record["human_error_probability"] is None
    assert not record["inspection_or_material_acceptance"]
    assert check_inspection_statistics_record(record)


@pytest.mark.parametrize("method", ["inspection.binomial_evidence", "inspection.hit_miss_pod"])
@pytest.mark.parametrize("field", ["truth_flawed", "detected"])
def test_unknown_truth_or_detection_is_neither_false_nor_a_miss(method, field):
    requests, context = cases()
    request, spy = requests[method], OwnerSpy()
    trials = (request.trials[0].model_copy(update={field: None}), *request.trials[1:])
    record = evaluate_inspection_statistics(
        request.model_copy(update={"trials": trials}), context, client=spy
    )
    assert record["status"] == "needs_data" and not spy.descriptions
    assert record["request"]["trials"][0][field] is None


@pytest.mark.parametrize("method", ["inspection.binomial_evidence", "inspection.hit_miss_pod"])
def test_independence_is_not_inferred_from_rows_or_shared_cluster(method):
    requests, context = cases()
    request = requests[method]
    for change in (
        {"parameters": request.parameters | {"independent_trials_declared": False}},
        {"trials": tuple(t.model_copy(update={"cluster_id": "one_cluster"}) for t in request.trials)},
        {"trials": tuple(t.model_copy(update={"specimen_id": "one_specimen"}) for t in request.trials)},
        {"trials": ()},
    ):
        spy = OwnerSpy()
        record = evaluate_inspection_statistics(request.model_copy(update=change), context, client=spy)
        assert record["status"] == "needs_data" and not spy.descriptions


def test_zero_false_calls_and_blank_truth_have_separate_detection_semantics():
    requests, context = cases()
    request = requests["inspection.binomial_evidence"]
    trials = tuple(
        t.model_copy(update={"truth_flawed": False, "detected": False, "flaw_size_m": None})
        for t in request.trials
    )
    blank = request.model_copy(
        update={
            "trials": trials,
            "declared_detection_size_m": None,
            "parameters": request.parameters
            | {"positive_responses": 0, "response_kind": "false_call_on_blank"},
        }
    )
    spy = OwnerSpy()
    record = evaluate_inspection_statistics(blank, context, client=spy)
    assert record["status"] == "computed" and spy.calls[0][1]["positive_responses"] == 0
    assert record["request"]["trials"][0]["truth_flawed"] is False
    assert record["request"]["trials"][0]["flaw_size_m"] is None
    wrong = blank.model_copy(
        update={"parameters": blank.parameters | {"response_kind": "detection_at_declared_size"}}
    )
    assert evaluate_inspection_statistics(wrong, context)["status"] == "needs_data"


@pytest.mark.parametrize("field", ["opportunities", "positive_responses"])
def test_binomial_counts_must_match_truth_witnesses_and_are_not_boolean(field):
    requests, context = cases()
    request = requests["inspection.binomial_evidence"]
    for invalid in (9, True):
        with pytest.raises(ValueError, match="counts|integer"):
            evaluate_inspection_statistics(
                request.model_copy(update={"parameters": request.parameters | {field: invalid}}), context
            )


def test_pod_original_row_order_binary_values_and_size_are_checked_without_repair():
    requests, context = cases()
    request = requests["inspection.hit_miss_pod"]
    for key, value in (
        ("specimen_ids", list(reversed(request.parameters["specimen_ids"]))),
        ("detected", [1 if v else 0 for v in request.parameters["detected"]]),
        ("flaw_size_m", [0.003] * len(request.trials)),
    ):
        with pytest.raises(ValueError, match="ordered|binary"):
            evaluate_inspection_statistics(
                request.model_copy(update={"parameters": request.parameters | {key: value}}), context
            )
    spy = OwnerSpy()
    evaluate_inspection_statistics(request, context, client=spy)
    assert spy.calls[0][1]["query_sizes_m"] == [0.004, 0.001, 0.002]


def test_tofd_same_frame_causal_reference_and_zero_delay_are_explicit():
    requests, context = cases()
    request = requests["inspection.tofd_depth"]
    for changes in (
        {"lateral_arrival_s": None},
        {"diffraction_arrival_s": 4e-6},
        {"diffraction_frame_id": "different_frame"},
        {"backwall_arrival_s": 4e-6},
    ):
        spy = OwnerSpy()
        record = evaluate_inspection_statistics(
            request.model_copy(update={"tofd_reference": request.tofd_reference.model_copy(update=changes)}),
            context,
            client=spy,
        )
        assert record["status"] == "needs_data" and not spy.descriptions
    zero = request.model_copy(
        update={
            "parameters": request.parameters | {"delay_from_lateral_wave_s": 0.0},
            "tofd_reference": request.tofd_reference.model_copy(update={"diffraction_arrival_s": 5e-6}),
        }
    )
    spy = OwnerSpy()
    assert evaluate_inspection_statistics(zero, context, client=spy)["status"] == "computed"
    assert spy.calls[0][1]["delay_from_lateral_wave_s"] == 0.0
    with pytest.raises(ValueError, match="supplied delay"):
        evaluate_inspection_statistics(
            request.model_copy(
                update={"parameters": request.parameters | {"delay_from_lateral_wave_s": 2e-6}}
            ),
            context,
        )


def test_grid_none_cells_original_placement_and_all_missing_map_are_preserved():
    requests, context = cases()
    request, spy = requests["geometry.thickness_grid"], OwnerSpy()
    record = evaluate_inspection_statistics(request, context, client=spy)
    assert spy.calls[0][1]["cell_thickness_m"] == [[0.012, 0.006, None], [0.008, None, 0.004]]
    assert record["request"]["grid_layout"]["row_ids"] == ["row_original_1", "row_original_2"]
    missing = request.model_copy(
        update={"parameters": request.parameters | {"cell_thickness_m": [[None] * 3, [None] * 3]}}
    )
    spy = OwnerSpy()
    assert evaluate_inspection_statistics(missing, context, client=spy)["status"] == "needs_data"
    assert not spy.descriptions
    with pytest.raises(ValueError, match="dimensions"):
        evaluate_inspection_statistics(
            request.model_copy(update={"parameters": request.parameters | {"cell_thickness_m": [[0.01]]}}),
            context,
        )


@pytest.mark.parametrize("field", ["asset_id", "clock_id"])
def test_request_and_source_context_identity_mismatches_fail(field):
    requests, context = cases()
    with pytest.raises(ValueError, match="asset or source clock"):
        evaluate_inspection_statistics(
            requests["inspection.binomial_evidence"].model_copy(update={field: "different"}), context
        )


def test_trial_clock_and_tofd_sampling_clock_cannot_be_relabelled():
    requests, context = cases()
    request = requests["inspection.hit_miss_pod"]
    with pytest.raises(ValueError, match="acquisition clock"):
        evaluate_inspection_statistics(
            request.model_copy(
                update={
                    "trials": (
                        request.trials[0].model_copy(update={"clock_id": "other"}),
                        *request.trials[1:],
                    )
                }
            ),
            context,
        )
    request = requests["inspection.tofd_depth"]
    with pytest.raises(ValueError, match="acquisition clock"):
        evaluate_inspection_statistics(
            request.model_copy(
                update={"tofd_reference": request.tofd_reference.model_copy(update={"clock_id": "other"})}
            ),
            context,
        )


@pytest.mark.parametrize("method", list(REVIEWS))
def test_units_review_keys_evidence_and_budgets_revalidate_nested_mutable_inputs(method):
    requests, context = cases()
    request = requests[method]
    for changes, match in (
        ({"input_units": request.input_units | {next(iter(request.input_units)): "mm"}}, "exact SI"),
        ({"applicability_reviews": {"arbitrary": True}}, "selected method"),
        ({"review_evidence_ids": dict.fromkeys(REVIEWS[method], "missing")}, "review evidence"),
        ({"evidence_ids": ("missing",)}, "present in context"),
        ({"maximum_observations": True}, "integer"),
    ):
        with pytest.raises(ValueError, match=match):
            evaluate_inspection_statistics(request.model_copy(update=changes), context)
    mutable = deepcopy(request)
    mutable.input_units[next(iter(mutable.input_units))] = "mm"
    with pytest.raises(ValueError, match="exact SI"):
        evaluate_inspection_statistics(mutable, context)
    if request.trials:
        with pytest.raises(ValueError, match="budget"):
            evaluate_inspection_statistics(request.model_copy(update={"maximum_observations": 1}), context)
    if request.grid_layout:
        with pytest.raises(ValueError, match="budget"):
            evaluate_inspection_statistics(request.model_copy(update={"maximum_grid_cells": 1}), context)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_python_nonfinite_values_fail_before_json_can_erase_their_meaning(value):
    requests, context = cases()
    request = requests["inspection.tofd_depth"]
    for changes in (
        {"parameters": request.parameters | {"wall_thickness_m": value}},
        {"tofd_reference": request.tofd_reference.model_copy(update={"lateral_arrival_s": value})},
    ):
        with pytest.raises(ValueError):
            evaluate_inspection_statistics(request.model_copy(update=changes), context)
    context.metadata["nested"] = {"bad": value}
    with pytest.raises(ValueError):
        evaluate_inspection_statistics(request, context)


def test_pin_rejects_parameter_location_model_schema_version_units_and_meaning_drift():
    requests, context = cases()
    request, spy = requests["inspection.binomial_evidence"], OwnerSpy()
    job = prepare_material_job(
        request.method,
        request.parameters,
        context,
        location_id=request.location_id,
        evidence_ids=request.evidence_ids,
        client=spy,
    )
    assert (
        evaluate_inspection_statistics(request, context, client=spy, pinned_job=job)["status"] == "computed"
    )
    for updates in (
        {"parameters": job.parameters | {"confidence_level": 0.9}},
        {"location_id": "other"},
        {"model_id": "inspection.hit_miss_pod"},
    ):
        with pytest.raises(ValueError, match="reviewed request"):
            evaluate_inspection_statistics(
                request, context, client=spy, pinned_job=job.model_copy(update=updates)
            )
    for field, change in (
        ("version", "spy-v2"),
        ("schema", {"type": "object", "required": ["new"]}),
        ("units", {"owner_value": "m"}),
        ("meanings", {"owner_value": "period_failure_probability"}),
    ):
        original = deepcopy(getattr(spy, field))
        setattr(spy, field, change)
        with pytest.raises(ValueError, match="schema, version"):
            evaluate_inspection_statistics(request, context, client=spy, pinned_job=job)
        setattr(spy, field, original)
    with pytest.raises(ValueError):
        evaluate_inspection_statistics(
            request,
            context,
            client=spy,
            pinned_job=job.model_copy(
                update={"parameters": job.parameters | {"confidence_level": float("nan")}}
            ),
        )


def test_receipt_rejects_native_input_and_location_changes_and_later_nested_tamper():
    requests, context = cases()
    request = requests["inspection.binomial_evidence"]
    for mutate in (
        lambda payload: payload["inputs"].update(positive_responses=0),
        lambda payload: payload["context"].update(location_id="other"),
        lambda payload: payload.update(probability_semantics={}),
    ):
        spy = OwnerSpy()
        spy.alter = mutate
        with pytest.raises(ValueError):
            evaluate_inspection_statistics(request, context, client=spy)
    record = evaluate_inspection_statistics(request, context, client=OwnerSpy())
    for mutate in (
        lambda r: r["values"].update(owner_value=0.99),
        lambda r: r["request"]["trials"][0].update(detected=False),
        lambda r: r["native_receipt"]["job"]["parameters"].update(positive_responses=0),
    ):
        changed = deepcopy(record)
        mutate(changed)
        with pytest.raises(ValueError, match="changed"):
            check_inspection_statistics_record(changed)
    record["native_receipt"]["native"]["payload"]["inputs"]["positive_responses"] = 0
    record["record_digest"] = digest({k: v for k, v in record.items() if k != "record_digest"})
    with pytest.raises(ValueError, match="changed"):
        check_inspection_statistics_record(record)


def test_source_pin_mismatch_is_rejected_before_owner_discovery(tmp_path):
    requests, context = cases()
    root = tmp_path / "owner"
    (root / "src").mkdir(parents=True)
    (root / "src" / "owner.py").write_text("# authored source fixture\n", encoding="utf-8")
    spy = OwnerSpy()
    with pytest.raises(ValueError, match="reviewed source pin"):
        evaluate_inspection_statistics(
            requests["inspection.binomial_evidence"],
            context,
            client=spy,
            source_root=root,
            expected_source_sha256="0" * 64,
        )
    assert not spy.descriptions
    with pytest.raises(ValueError, match="both source_root"):
        evaluate_inspection_statistics(
            requests["inspection.binomial_evidence"], context, expected_source_sha256="0" * 64
        )


def test_unreviewed_request_does_not_open_or_import_even_a_pinned_owner(tmp_path):
    requests, context = cases()
    request = requests["inspection.binomial_evidence"].model_copy(update={"applicability_reviews": {}})
    spy = OwnerSpy()
    record = evaluate_inspection_statistics(
        request, context, client=spy, source_root=tmp_path / "absent_owner", expected_source_sha256="0" * 64
    )
    assert record["status"] == "needs_data" and record["source_pin"] is None
    assert not spy.descriptions


def test_strict_json_does_not_accept_numeric_binary_truth():
    requests, _ = cases()
    payload = json.loads(requests["inspection.binomial_evidence"].model_dump_json())
    payload["trials"][0]["truth_flawed"] = 1
    with pytest.raises(ValueError):
        InspectionStatisticsRequest.model_validate_json(json.dumps(payload))
