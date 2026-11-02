"""Loss support, unknowns, bounded work and original owner receipt identity."""

import runpy
from copy import deepcopy

import pytest

from pipeline_integrity.adapters import ExternalResult
from pipeline_integrity.contracts import digest
from pipeline_integrity.corrosion_wear import (
    REVIEWS,
    CorrosionWearRequest,
    LossDomain,
    evaluate_corrosion_wear,
    verify_corrosion_wear_record,
)
from pipeline_integrity.material_exchange import prepare_material_job


def cases():
    return runpy.run_path("examples/corrosion_wear.py")["make_cases"]()


class OwnerSpy:
    def __init__(self):
        self.calls = []
        self.version = "spy-v1"

    @staticmethod
    def semantics(method):
        return (
            {"area_depth_exceedance_probability": "spatial_size_exceedance_at_one_exposure"}
            if method == "corrosion.gumbel_area_maximum"
            else {}
        )

    def describe(self, method):
        return {
            "model_id": method,
            "model_version": self.version,
            "input_schema": {"type": "object"},
            "output_units": {"owner_value": "1"},
            "probability_semantics": self.semantics(method),
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
            "probability_semantics": self.semantics(method),
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
def test_owner_delegation_retains_context_meaning_and_receipt(method):
    requests, context = cases()
    request, spy = requests[method], OwnerSpy()
    result = evaluate_corrosion_wear(request, context, client=spy)
    assert result["status"] == "computed" and result["values"] == {"owner_value": 123.0}
    assert spy.calls == [(method, request.parameters, "json")]
    assert result["source_pin"] is None and result["context"] == context.model_dump(mode="json")
    assert not result["spatial_exceedance_is_period_failure_probability"]
    verify_corrosion_wear_record(result)


@pytest.mark.parametrize("method", list(REVIEWS))
@pytest.mark.parametrize(
    "change",
    [
        {"applicability_reviews": {}},
        {"review_evidence_ids": {}},
        {"parameters": None},
        {"material_condition_id": None},
        {"quantity_meaning": "unresolved"},
    ],
)
def test_unknown_or_unreviewed_loss_does_not_import_or_call_native(method, change):
    requests, context = cases()
    spy = OwnerSpy()
    result = evaluate_corrosion_wear(requests[method].model_copy(update=change), context, client=spy)
    assert result["status"] == "needs_data" and result["values"] == {} and not spy.calls
    verify_corrosion_wear_record(result)


@pytest.mark.parametrize("key", ["normal_force_N", "sliding_distance_m", "dimensionless_wear_coefficient"])
def test_explicit_zero_and_unknown_are_distinct(key):
    requests, context = cases()
    request = requests["degradation.archard_wear"]
    spy = OwnerSpy()
    zero = request.model_copy(update={"parameters": request.parameters | {key: 0.0}})
    assert evaluate_corrosion_wear(zero, context, client=spy)["status"] == "computed"
    assert spy.calls[-1][1][key] == 0
    unknown = request.model_copy(update={"parameters": request.parameters | {key: None}})
    assert evaluate_corrosion_wear(unknown, context)["status"] == "needs_data"


@pytest.mark.parametrize("change", [{"exposure_origin_id": None}, {"domain": None}])
def test_logger_clock_or_missing_calibration_is_not_physical_age(change):
    requests, context = cases()
    result = evaluate_corrosion_wear(requests["corrosion.power_law"].model_copy(update=change), context)
    assert result["status"] == "needs_data"


@pytest.mark.parametrize(
    "field,value",
    [
        ("exposure_origin_id", "other_origin"),
        ("material_condition_id", "other_material"),
        ("population_id", "other_population"),
    ],
)
def test_different_physical_support_is_out_of_scope(field, value):
    requests, context = cases()
    result = evaluate_corrosion_wear(
        requests["corrosion.power_law"].model_copy(update={field: value}), context
    )
    assert result["status"] == "out_of_scope" and result["native_receipt"] is None


@pytest.mark.parametrize(
    "method,key,value",
    [
        ("corrosion.power_law", "exposure_s", 1001.0),
        ("degradation.archard_wear", "hardness_Pa", 1e7),
        ("degradation.archard_wear", "normal_force_N", 1001.0),
        ("degradation.archard_wear", "sliding_distance_m", 101.0),
    ],
)
def test_extrapolation_outside_reviewed_support_is_not_evaluated(method, key, value):
    requests, context = cases()
    request = requests[method]
    result = evaluate_corrosion_wear(
        request.model_copy(update={"parameters": request.parameters | {key: value}}), context
    )
    assert result["status"] == "out_of_scope" and result["native_receipt"] is None


def test_spatial_population_and_meaning_cannot_be_relabelled():
    requests, context = cases()
    request = requests["corrosion.gumbel_area_maximum"]
    for update in (
        {"quantity_meaning": "mean_penetration"},
        {"population_id": "other"},
        {"exposure_condition_id": "different"},
    ):
        with pytest.raises(ValueError):
            evaluate_corrosion_wear(request.model_copy(update=update), context)
    spy = OwnerSpy()
    changed = request.model_copy(
        update={"parameters": request.parameters | {"independent_equivalent_blocks": False}}
    )
    evaluate_corrosion_wear(changed, context, client=spy)
    assert spy.calls[0][1]["independent_equivalent_blocks"] is False


def test_unknown_maxima_rows_and_work_budgets_are_not_trimmed():
    requests, context = cases()
    request = requests["corrosion.gumbel_area_maximum"]
    values = request.parameters["maximum_depth_m"].copy()
    values[3] = None
    result = evaluate_corrosion_wear(
        request.model_copy(update={"parameters": request.parameters | {"maximum_depth_m": values}}), context
    )
    assert result["status"] == "needs_data" and len(result["request"]["parameters"]["maximum_depth_m"]) == 32
    with pytest.raises(ValueError, match="budget"):
        evaluate_corrosion_wear(request.model_copy(update={"maximum_records": 31}), context)
    with pytest.raises(ValueError, match="budget"):
        evaluate_corrosion_wear(
            request.model_copy(
                update={
                    "parameters": request.parameters | {"parameter_resamples": 100},
                    "maximum_total_resamples": 198,
                }
            ),
            context,
        )


def test_nonfinite_bypassed_copy_and_wrong_units_are_rejected():
    requests, context = cases()
    request = requests["corrosion.power_law"]
    for value in (float("nan"), float("inf"), -float("inf")):
        with pytest.raises(ValueError):
            evaluate_corrosion_wear(
                request.model_copy(update={"parameters": request.parameters | {"coefficient_m": value}}),
                context,
            )
    with pytest.raises(ValueError):
        CorrosionWearRequest.model_validate(
            request.model_dump() | {"input_units": request.input_units | {"coefficient_m": "mm"}}
        )
    with pytest.raises(ValueError, match="ordered"):
        LossDomain.model_validate(request.domain.model_dump() | {"exposure_range_s": (10.0, 0.0)})


def test_evidence_context_and_pinned_schema_must_match():
    requests, context = cases()
    request = requests["corrosion.power_law"]
    spy = OwnerSpy()
    for update in ({"asset_id": "other"}, {"clock_id": "other"}, {"evidence_ids": ("missing",)}):
        with pytest.raises(ValueError):
            evaluate_corrosion_wear(request.model_copy(update=update), context, client=spy)
    job = prepare_material_job(
        request.method,
        request.parameters,
        context,
        location_id=request.location_id,
        evidence_ids=request.evidence_ids,
        client=spy,
    )
    assert evaluate_corrosion_wear(request, context, pinned_job=job, client=spy)["status"] == "computed"
    with pytest.raises(ValueError, match="differs"):
        evaluate_corrosion_wear(
            request.model_copy(update={"parameters": request.parameters | {"coefficient_m": 0.01}}),
            context,
            client=spy,
            pinned_job=job,
        )
    spy.version = "changed"
    with pytest.raises(ValueError):
        evaluate_corrosion_wear(request, context, pinned_job=job, client=spy)


def test_sealed_record_rejects_transplant_even_after_outer_digest_recomputed():
    requests, context = cases()
    record = evaluate_corrosion_wear(requests["corrosion.power_law"], context, client=OwnerSpy())
    for mutation in ("request", "context", "values", "meaning"):
        changed = deepcopy(record)
        if mutation == "request":
            changed["request"]["parameters"]["coefficient_m"] = 1.0
        elif mutation == "context":
            changed["context"]["scenario_id"] = "other"
        elif mutation == "values":
            changed["values"]["owner_value"] = 999.0
        else:
            changed["spatial_exceedance_is_period_failure_probability"] = True
        changed["record_digest"] = digest({k: v for k, v in changed.items() if k != "record_digest"})
        with pytest.raises(ValueError):
            verify_corrosion_wear_record(changed)


@pytest.mark.parametrize("mutation", ["status", "values", "output_units", "probability_semantics", "context"])
def test_no_native_receipt_cannot_acquire_invented_results_or_context(mutation):
    requests, context = cases()
    request = requests["corrosion.power_law"].model_copy(update={"parameters": None})
    record = evaluate_corrosion_wear(request, context)
    if mutation == "status":
        record["status"] = "unsupported_status"
    elif mutation == "context":
        record["context"]["asset_id"] = "different"
    else:
        record[mutation] = {"invented": 0.9}
    record["record_digest"] = digest({k: v for k, v in record.items() if k != "record_digest"})
    with pytest.raises(ValueError):
        verify_corrosion_wear_record(record)
