"""Transport conventions, support, work bounds and original owner receipt identity."""

import runpy
from copy import deepcopy

import pytest

from pipeline_integrity.contracts import digest
from pipeline_integrity.hydrogen_transport import (
    EQUILIBRIUM,
    KINETIC,
    SEMI,
    evaluate_hydrogen_transport,
    verify_hydrogen_transport_record,
)
from pipeline_integrity.material_exchange import prepare_material_job

OwnerSpy = runpy.run_path("tests/test_characterization.py")["OwnerSpy"]


def cases():
    return runpy.run_path("examples/hydrogen_transport.py")["make_cases"]()


@pytest.mark.parametrize("method", [SEMI, EQUILIBRIUM, KINETIC])
@pytest.mark.parametrize(
    "update",
    [
        {"parameters": None},
        {"applicability_reviews": {}},
        {"review_evidence_ids": {}},
        {"concentration_basis": "unresolved"},
        {"population_id": None},
    ],
)
def test_unresolved_physical_meaning_and_reviews_precede_owner_import(method, update):
    requests, context = cases()
    result = evaluate_hydrogen_transport(requests[method].model_copy(update=update), context)
    assert result["status"] == "needs_data" and result["values"] == {} and result["native_receipt"] is None
    verify_hydrogen_transport_record(result)


@pytest.mark.parametrize("method", [SEMI, EQUILIBRIUM, KINETIC])
def test_owner_delegation_preserves_original_input_context_units_and_nulls(method):
    requests, context = cases()
    request, spy = requests[method], OwnerSpy()
    result = evaluate_hydrogen_transport(request, context, client=spy)
    assert result["status"] == "computed" and spy.calls == [(method, request.parameters, "json")]
    assert result["context"] == context.model_dump(mode="json")
    assert not result["transport_is_embrittlement_or_failure_probability"]
    verify_hydrogen_transport_record(result)


def test_binding_ratio_release_null_and_zero_histories_are_not_unknown():
    requests, context = cases()
    request = requests[KINETIC]
    p = deepcopy(request.parameters)
    p["traps"][0]["capture_frequency_per_s"] = [0.0] * 3
    p["maximum_step_s"] = None
    spy = OwnerSpy()
    result = evaluate_hydrogen_transport(request.model_copy(update={"parameters": p}), context, client=spy)
    assert result["status"] == "computed" and spy.calls[0][1]["traps"][0]["release_frequency_per_s"] is None
    assert spy.calls[0][1]["traps"][0]["capture_frequency_per_s"] == [0.0] * 3


@pytest.mark.parametrize(
    "field,value",
    [
        ("concentration_measure", "point_lattice"),
        ("flux_sign_convention", "positive_inward_at_declared_surface"),
        ("binding_energy_reference", "not_used"),
    ],
)
def test_cell_means_and_boundary_signs_cannot_be_relabelled(field, value):
    requests, context = cases()
    with pytest.raises(ValueError):
        evaluate_hydrogen_transport(requests[KINETIC].model_copy(update={field: value}), context)


def test_temperature_outside_supported_parameters_is_withheld_without_native():
    requests, context = cases()
    result = evaluate_hydrogen_transport(
        requests[KINETIC].model_copy(update={"temperature_support_K": (290.0, 299.0)}), context
    )
    assert result["status"] == "out_of_scope" and result["native_receipt"] is None
    verify_hydrogen_transport_record(result)


@pytest.mark.parametrize(
    "update",
    [
        {"reference_lattice_id": "other"},
        {"material_condition_id": "other"},
        {"elapsed_time_origin_id": "other"},
        {"cell_ids": ("one",)},
        {"time_knot_ids": ("one",)},
        {"asset_id": "other"},
        {"clock_id": "other"},
    ],
)
def test_source_identity_and_complete_sample_alignment_are_exact(update):
    requests, context = cases()
    with pytest.raises(ValueError):
        evaluate_hydrogen_transport(requests[KINETIC].model_copy(update=update), context)


def test_unknown_history_point_and_independent_release_are_not_filled():
    requests, context = cases()
    request = requests[KINETIC]
    p = deepcopy(request.parameters)
    p["uniform_temperature_K"][1] = None
    result = evaluate_hydrogen_transport(request.model_copy(update={"parameters": p}), context)
    assert (
        result["status"] == "needs_data"
        and len(result["request"]["parameters"]["uniform_temperature_K"]) == 3
    )
    p = deepcopy(request.parameters)
    p["traps"][0]["rate_model"] = "independent_frequencies"
    result = evaluate_hydrogen_transport(request.model_copy(update={"parameters": p}), context)
    assert result["status"] == "needs_data"


@pytest.mark.parametrize(
    "update",
    [
        {"maximum_cells": 7},
        {"maximum_time_knots": 2},
        {"maximum_trap_populations": 0},
        {"maximum_state_values": 47},
        {"maximum_step_attempts": 100},
    ],
)
def test_spatial_temporal_state_and_solver_budgets_refuse_without_truncating(update):
    requests, context = cases()
    with pytest.raises(ValueError, match="budget"):
        evaluate_hydrogen_transport(requests[KINETIC].model_copy(update=update), context)


def test_depth_order_duplicate_values_and_unknown_are_preserved():
    requests, context = cases()
    request = requests[SEMI]
    spy = OwnerSpy()
    evaluate_hydrogen_transport(request, context, client=spy)
    assert spy.calls[0][1]["depth_m"] == [0.0, 0.0002, 0.0006, 0.0002]
    result = evaluate_hydrogen_transport(
        request.model_copy(
            update={"parameters": request.parameters | {"depth_m": [0.0, None, 0.0006, 0.0002]}}
        ),
        context,
    )
    assert result["status"] == "needs_data" and result["request"]["parameters"]["depth_m"][1] is None
    with pytest.raises(ValueError, match="budget"):
        evaluate_hydrogen_transport(request.model_copy(update={"maximum_depth_points": 3}), context)


def test_nonfinite_values_evidence_and_input_units_are_not_silently_changed():
    requests, context = cases()
    request = requests[EQUILIBRIUM]
    for value in (float("nan"), float("inf")):
        with pytest.raises(ValueError):
            evaluate_hydrogen_transport(
                request.model_copy(update={"parameters": request.parameters | {"temperature_K": value}}),
                context,
            )
    for update in ({"input_units": {}}, {"evidence_ids": ("missing",)}):
        with pytest.raises(ValueError):
            evaluate_hydrogen_transport(request.model_copy(update=update), context)
    p = deepcopy(request.parameters)
    p["traps"][0]["evidence"][0]["citation"] = "changed"
    with pytest.raises(ValueError):
        evaluate_hydrogen_transport(request.model_copy(update={"parameters": p}), context)


def test_job_pin_rejects_changed_input_or_schema():
    requests, context = cases()
    request, spy = requests[SEMI], OwnerSpy()
    job = prepare_material_job(
        SEMI,
        request.parameters,
        context,
        location_id=request.location_id,
        evidence_ids=request.evidence_ids,
        client=spy,
    )
    evaluate_hydrogen_transport(request, context, client=spy, pinned_job=job)
    with pytest.raises(ValueError, match="differs"):
        evaluate_hydrogen_transport(
            request.model_copy(update={"parameters": request.parameters | {"exposure_s": 5.0}}),
            context,
            client=spy,
            pinned_job=job,
        )
    spy.version = "changed"
    with pytest.raises(ValueError):
        evaluate_hydrogen_transport(request, context, client=spy, pinned_job=job)


@pytest.mark.parametrize("mutation", ["request", "context", "values", "meaning"])
def test_receipt_projection_and_meaning_refuse_transplant_after_reseal(mutation):
    requests, context = cases()
    record = evaluate_hydrogen_transport(requests[SEMI], context, client=OwnerSpy())
    if mutation == "request":
        record["request"]["parameters"]["exposure_s"] = 5.0
    elif mutation == "context":
        record["context"]["scenario_id"] = "changed"
    elif mutation == "values":
        record["values"]["owner_value"] = 999.0
    else:
        record["transport_is_embrittlement_or_failure_probability"] = True
    record["record_digest"] = digest({k: v for k, v in record.items() if k != "record_digest"})
    with pytest.raises(ValueError):
        verify_hydrogen_transport_record(record)


def test_no_native_record_cannot_acquire_uncomputed_results():
    requests, context = cases()
    record = evaluate_hydrogen_transport(requests[SEMI].model_copy(update={"parameters": None}), context)
    record["values"] = {"fabricated": 0.0}
    record["record_digest"] = digest({k: v for k, v in record.items() if k != "record_digest"})
    with pytest.raises(ValueError):
        verify_hydrogen_transport_record(record)
