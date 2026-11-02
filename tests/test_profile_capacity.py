"""Profile meaning and provenance around delegated native calculations."""

import runpy
from copy import deepcopy

import pytest

from pipeline_integrity.contracts import Pressure, digest
from pipeline_integrity.material_exchange import prepare_material_job
from pipeline_integrity.profile_capacity import evaluate_profile_capacity, verify_profile_capacity_record

OwnerBase = runpy.run_path("tests/test_characterization.py")["OwnerSpy"]


class OwnerSpy(OwnerBase):
    def evaluate(self, *args, **kwargs):
        result = super().evaluate(*args, **kwargs)
        payload = result.payload | {
            "values": {
                "pressure_basis": "internal_minus_external",
                "declared_model_pressure_difference_capacity_Pa": 9e6,
            }
        }
        return result.model_copy(update={"payload": payload, "payload_digest": digest(payload)})


def case():
    return runpy.run_path("examples/profile_capacity.py")["make_case"]()


@pytest.mark.parametrize(
    "update",
    [
        {"applicability_reviews": {}},
        {"review_evidence_ids": {}},
        {"parameters": None},
        {"source_point_ids": ()},
    ],
)
def test_unknown_scope_is_withheld_before_native_import(update):
    request, context = case()
    result = evaluate_profile_capacity(request.model_copy(update=update), context)
    assert result["status"] == "needs_data" and result["values"] == {} and result["native_receipt"] is None
    assert result["demand_comparison"]["status"] == "withheld"
    verify_profile_capacity_record(result)


def test_native_receipt_and_optional_source_checksum_preserved():
    request, context = case()
    spy = OwnerSpy()
    result = evaluate_profile_capacity(request, context, client=spy)
    assert spy.calls == [("structural.profile_capacity", request.parameters, "json")]
    assert (
        result["native_receipt"]["job"]["parameters"]["reference_capacity"]["evidence"][0]["sha256"] is None
    )
    assert result["demand_comparison"]["demand_to_declared_capacity_ratio"] == pytest.approx(5 / 9)
    assert result["demand_comparison"]["demand_not_above_declared_model_capacity"]
    assert not result["field_or_standard_qualification"]
    verify_profile_capacity_record(result)


def test_no_demand_and_above_capacity_do_not_change_native_calculation_status():
    request, context = case()
    result = evaluate_profile_capacity(
        request.model_copy(update={"demand": None}), context, client=OwnerSpy()
    )
    assert result["status"] == "computed" and result["demand_comparison"] == {"status": "not_requested"}
    above = request.model_copy(update={"demand": Pressure(value=10.0, unit="MPa", reference="differential")})
    result = evaluate_profile_capacity(above, context, client=OwnerSpy())
    assert (
        result["status"] == "computed"
        and not result["demand_comparison"]["demand_not_above_declared_model_capacity"]
    )
    assert not result["demand_comparison"]["standard_or_design_acceptance"]


@pytest.mark.parametrize(
    "pressure",
    [
        Pressure(value=5.0, unit="MPa", reference="absolute"),
        Pressure(value=-1.0, unit="Pa", reference="differential"),
    ],
)
def test_absolute_or_external_dominant_demand_is_not_compared(pressure):
    request, context = case()
    with pytest.raises(ValueError, match="pressure difference"):
        evaluate_profile_capacity(request.model_copy(update={"demand": pressure}), context)


@pytest.mark.parametrize(
    "key,value",
    [
        ("geometry_id", "other_geometry"),
        ("material_condition_id", "other_material"),
        ("asset_id", "other_asset"),
        ("clock_id", "other_clock"),
        ("source_point_ids", ("one",)),
    ],
)
def test_source_context_and_point_mapping_are_exact(key, value):
    request, context = case()
    with pytest.raises(ValueError):
        evaluate_profile_capacity(request.model_copy(update={key: value}), context)


@pytest.mark.parametrize(
    "key,value",
    [
        ("license_id", "other_license"),
        ("citation", "other_citation"),
        ("sha256", "0" * 64),
        ("source_id", "missing"),
    ],
)
def test_provider_evidence_cannot_be_substituted(key, value):
    request, context = case()
    p = deepcopy(request.parameters)
    p["reference_capacity"]["evidence"][0][key] = value
    with pytest.raises(ValueError):
        evaluate_profile_capacity(request.model_copy(update={"parameters": p}), context)


def test_unknown_point_and_false_declarations_are_not_removed_or_filled():
    request, context = case()
    p = request.parameters | {"measured_remaining_thickness_m": [0.008, None, 0.008]}
    result = evaluate_profile_capacity(request.model_copy(update={"parameters": p}), context)
    assert (
        result["status"] == "needs_data"
        and len(result["request"]["parameters"]["measured_remaining_thickness_m"]) == 3
    )
    spy = OwnerSpy()
    p = request.parameters | {
        "full_axial_extent_confirmed": False,
        "measured_remaining_thickness_m": [0.0, 0.008, 0.008],
    }
    evaluate_profile_capacity(request.model_copy(update={"parameters": p}), context, client=spy)
    assert spy.calls[0][1]["full_axial_extent_confirmed"] is False
    assert spy.calls[0][1]["measured_remaining_thickness_m"][0] == 0


@pytest.mark.parametrize("update", [{"maximum_points": 2}, {"maximum_nodal_windows": 2}])
def test_profile_budget_precedes_serialization_without_trim(update):
    request, context = case()
    with pytest.raises(ValueError, match="budget"):
        evaluate_profile_capacity(request.model_copy(update=update), context)


def test_provider_knots_and_nonfinite_input_are_bounded():
    request, context = case()
    p = deepcopy(request.parameters)
    p["bulging_table"].update(window_length_m=[0.0, 0.5, 1.0], factor=[1.0, 1.5, 2.0])
    with pytest.raises(ValueError, match="budget"):
        evaluate_profile_capacity(
            request.model_copy(update={"parameters": p, "maximum_provider_knots": 2}), context
        )
    for bad in (float("nan"), float("inf")):
        with pytest.raises(ValueError):
            evaluate_profile_capacity(
                request.model_copy(
                    update={"parameters": request.parameters | {"reference_wall_thickness_m": bad}}
                ),
                context,
            )


def test_job_pin_rejects_changed_profile_parameters_and_schema():
    request, context = case()
    spy = OwnerSpy()
    job = prepare_material_job(
        "structural.profile_capacity",
        request.parameters,
        context,
        location_id=request.location_id,
        evidence_ids=request.evidence_ids,
        client=spy,
    )
    evaluate_profile_capacity(request, context, pinned_job=job, client=spy)
    with pytest.raises(ValueError, match="differs"):
        evaluate_profile_capacity(
            request.model_copy(
                update={"parameters": request.parameters | {"reference_wall_thickness_m": 0.011}}
            ),
            context,
            pinned_job=job,
            client=spy,
        )
    spy.version = "changed"
    with pytest.raises(ValueError):
        evaluate_profile_capacity(request, context, pinned_job=job, client=spy)


@pytest.mark.parametrize("mutation", ["parameters", "values", "context", "comparison", "qualification"])
def test_recomputed_outer_digest_cannot_transplant_native_meaning(mutation):
    request, context = case()
    record = evaluate_profile_capacity(request, context, client=OwnerSpy())
    if mutation == "parameters":
        record["request"]["parameters"]["reference_wall_thickness_m"] = 0.1
    elif mutation == "values":
        record["values"]["declared_model_pressure_difference_capacity_Pa"] = 1.0
    elif mutation == "context":
        record["context"]["scenario_id"] = "changed"
    elif mutation == "comparison":
        record["demand_comparison"]["standard_or_design_acceptance"] = True
    else:
        record["field_or_standard_qualification"] = True
    record["record_digest"] = digest({k: v for k, v in record.items() if k != "record_digest"})
    with pytest.raises(ValueError):
        verify_profile_capacity_record(record)


def test_no_native_record_cannot_acquire_a_result():
    request, context = case()
    record = evaluate_profile_capacity(request.model_copy(update={"parameters": None}), context)
    record["values"] = {"fabricated": 1.0}
    record["record_digest"] = digest({k: v for k, v in record.items() if k != "record_digest"})
    with pytest.raises(ValueError):
        verify_profile_capacity_record(record)
