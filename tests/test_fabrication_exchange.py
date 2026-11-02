"""Review/identity gates and actual owner calls; no fabrication equations are copied."""

import copy
import importlib.util
import runpy
from pathlib import Path

import pytest

from pipeline_integrity.adapters import MaterialClient
from pipeline_integrity.contracts import digest
from pipeline_integrity.fabrication_exchange import (
    REVIEWS,
    FabricationResult,
    evaluate_fabrication,
)


def cases():
    return runpy.run_path("examples/fabrication_methods.py")["make_cases"]()


class UnusedClient:
    def describe(self, *args, **kwargs):
        pytest.fail("an unresolved fabrication request reached the optional owner")


@pytest.fixture
def native(monkeypatch):
    root = Path(__file__).resolve().parents[3] / "재료_부식 평가 툴 프로젝트/matintegrity"
    if (root / "src/matintegrity").is_dir():
        monkeypatch.syspath_prepend(str(root / "src"))
    if importlib.util.find_spec("matintegrity") is None:
        pytest.skip("optional material-owner package is unavailable")
    return MaterialClient()


@pytest.mark.parametrize("method", tuple(REVIEWS))
def test_each_method_review_and_its_evidence_are_required_before_import(method):
    requests, context = cases()
    request = requests[method]
    for changes in ({"applicability_reviews": {}}, {"review_evidence_ids": {}}):
        result = evaluate_fabrication(request.model_copy(update=changes), context, client=UnusedClient())
        assert result.status == "needs_data" and result.native_receipt is None
        assert result.requirement_outcome == "unknown"
        result.check_integrity()


@pytest.mark.parametrize("method", tuple(REVIEWS))
def test_unknown_parameters_and_parameter_bases_never_get_defaults(method):
    requests, context = cases()
    request = requests[method]
    for changes in (
        {"parameters": None},
        {"parameters": {}},
        {"input_units": {}},
        {"parameter_evidence_ids": {}},
    ):
        result = evaluate_fabrication(request.model_copy(update=changes), context, client=UnusedClient())
        assert result.status == "needs_data" and not result.values and result.native_receipt is None
        assert not result.friction_coefficient_or_material_strength_inferred


@pytest.mark.parametrize("key", ["torque_N_m", "nominal_diameter_m", "nut_factor", "tensile_area_m2"])
def test_unknown_torque_factor_or_effective_area_is_not_nominal_or_zero(key):
    requests, context = cases()
    request = requests["fabrication.bolt_preload"]
    request = request.model_copy(update={"parameters": request.parameters | {key: None}})
    result = evaluate_fabrication(request, context, client=UnusedClient())
    assert result.status == "needs_data" and result.request.parameters[key] is None


@pytest.mark.parametrize(
    "change",
    [
        {"asset_id": "foreign"},
        {"clock_id": "foreign"},
        {"evidence_ids": ("absent",)},
        {"review_evidence_ids": dict.fromkeys(REVIEWS["fabrication.bolt_preload"], "absent")},
        {"parameter_evidence_ids": {"torque_N_m": "absent"}},
        {"input_units": {"torque_N_m": "lbf*ft"}},
        {"applicability_reviews": {"generic_approval": True}},
        {"parameters": {"unrecognized_native_field": 1.0}},
    ],
)
def test_wrong_scope_units_evidence_or_schema_keys_are_rejected(change):
    requests, context = cases()
    with pytest.raises(ValueError):
        evaluate_fabrication(
            requests["fabrication.bolt_preload"].model_copy(update=change), context, client=UnusedClient()
        )


@pytest.mark.parametrize("value", [True, "100", float("nan"), float("inf"), [100.0]])
def test_cloned_native_numbers_are_strict_json_finite_scalars(value):
    requests, context = cases()
    request = requests["fabrication.bolt_preload"]
    with pytest.raises(ValueError):
        evaluate_fabrication(
            request.model_copy(update={"parameters": request.parameters | {"torque_N_m": value}}),
            context,
            client=UnusedClient(),
        )


def test_duplicate_context_evidence_cannot_be_hidden_by_set_conversion():
    requests, context = cases()
    with pytest.raises(ValueError, match="unique"):
        evaluate_fabrication(
            requests["fabrication.bend_strain"],
            context.model_copy(update={"evidence": context.evidence + (context.evidence[0],)}),
            client=UnusedClient(),
        )


def test_native_three_methods_keep_calculation_and_requirements_separate(native):
    requests, context = cases()
    results = {
        method: evaluate_fabrication(request, context, client=native) for method, request in requests.items()
    }
    assert all(result.status == "computed" for result in results.values())
    assert results["fabrication.bend_strain"].values["outer_fiber_strain"] == pytest.approx(0.1)
    assert results["fabrication.bend_strain"].requirement_outcome == "not_satisfied"
    bolt = results["fabrication.bolt_preload"]
    assert bolt.values == {"preload_N": 25000.0, "nominal_tensile_stress_Pa": 100000000.0}
    wall = results["fabrication.thickness_budget"]
    assert wall.values["nominal_budget_structural_thickness_m"] == pytest.approx(0.0071)
    assert wall.values["measured_structural_thickness_m"] == pytest.approx(0.0075)
    for method, result in results.items():
        result.check_integrity()
        receipt = result.native_receipt
        assert receipt.job.model_id == method and receipt.job.model_version == "1.0.0"
        assert receipt.native.payload["context"]["metadata"]["pipeline_input_validation_mode"] == "json"
        assert receipt.native.payload["context"]["location_id"] == requests[method].location_id
        assert not result.field_or_code_acceptance and not result.leak_tightness_demonstrated
        assert not result.human_error_probability_calculated and not result.pressure_capacity_calculated


def test_zero_torque_is_a_computed_zero_indicator_not_seal_or_work_success(native):
    requests, context = cases()
    request = requests["fabrication.bolt_preload"]
    result = evaluate_fabrication(
        request.model_copy(update={"parameters": request.parameters | {"torque_N_m": 0.0}}),
        context,
        client=native,
    )
    assert result.status == "computed"
    assert result.values == {"preload_N": 0.0, "nominal_tensile_stress_Pa": 0.0}
    assert result.requirement_outcome == "not_satisfied" and not result.leak_tightness_demonstrated


def test_native_bend_support_boundary_and_exhausted_wall_are_out_of_scope(native):
    requests, context = cases()
    bend = requests["fabrication.bend_strain"]
    for radius in (0.06, 0.05):
        result = evaluate_fabrication(
            bend.model_copy(update={"parameters": bend.parameters | {"centerline_bend_radius_m": radius}}),
            context,
            client=native,
        )
        assert (
            result.status == "out_of_scope" and not result.values and result.requirement_outcome == "unknown"
        )
    wall = requests["fabrication.thickness_budget"]
    result = evaluate_fabrication(
        wall.model_copy(update={"parameters": wall.parameters | {"manufacturing_minus_tolerance_m": 0.01}}),
        context,
        client=native,
    )
    assert result.status == "out_of_scope" and result.native_receipt.native.status == "out_of_scope"


def test_measured_wall_is_separate_and_absence_is_retained(native):
    requests, context = cases()
    request = requests["fabrication.thickness_budget"]
    result = evaluate_fabrication(
        request.model_copy(
            update={"parameters": request.parameters | {"measured_postfabrication_thickness_m": None}}
        ),
        context,
        client=native,
    )
    assert result.status == "computed"
    assert result.values["measured_structural_thickness_m"] is None
    assert result.values["measured_margin_m"] is None and result.requirement_outcome == "unknown"
    # A postfabrication measured value already contains manufacturing/forming effects.
    measured = evaluate_fabrication(request, context, client=native)
    assert measured.values["measured_structural_thickness_m"] == pytest.approx(0.0075)
    assert measured.values["measured_structural_thickness_m"] != pytest.approx(0.00575)


def test_reviewed_threshold_equality_and_unreviewed_requirement(native):
    requests, context = cases()
    request = requests["fabrication.bend_strain"]
    rule = request.requirements[0].model_copy(update={"threshold": 0.1})
    result = evaluate_fabrication(
        request.model_copy(update={"requirements": (rule,)}), context, client=native
    )
    assert result.status == "computed" and result.requirement_outcome == "satisfied"
    unknown = evaluate_fabrication(
        request.model_copy(
            update={"requirements": (rule.model_copy(update={"interpretation_reviewed": False}),)}
        ),
        context,
        client=native,
    )
    assert unknown.status == "computed" and unknown.requirement_outcome == "unknown"
    no_rule = evaluate_fabrication(request.model_copy(update={"requirements": ()}), context, client=native)
    assert no_rule.requirement_outcome == "not_requested"


def test_native_strict_schema_rejects_invalid_positive_domain(native):
    requests, context = cases()
    request = requests["fabrication.bolt_preload"]
    with pytest.raises(ValueError):
        evaluate_fabrication(
            request.model_copy(update={"parameters": request.parameters | {"nut_factor": 0.0}}),
            context,
            client=native,
        )


class ChangedClient:
    def __init__(self, base, *, description=None, payload=None):
        self.base, self.description, self.payload = base, description, payload

    def describe(self, method):
        result = copy.deepcopy(self.base.describe(method))
        if self.description:
            self.description(result)
        return result

    def evaluate(self, *args, **kwargs):
        assert kwargs["input_mode"] == "json"
        native = self.base.evaluate(*args, **kwargs)
        payload = copy.deepcopy(native.payload)
        if self.payload:
            self.payload(payload)
        return native.model_copy(update={"payload": payload, "payload_digest": digest(payload)})


@pytest.mark.parametrize("change", ["model_version", "input_schema", "output_units"])
def test_reused_job_pin_refuses_live_schema_version_or_unit_drift(native, change):
    requests, context = cases()
    request = requests["fabrication.bolt_preload"]
    result = evaluate_fabrication(request, context, client=native)
    pinned = result.native_receipt.job

    def changed(description):
        if change == "model_version":
            description[change] = "changed"
        elif change == "input_schema":
            description[change]["properties"]["torque_N_m"]["description"] = "changed contract"
        else:
            description[change]["preload_N"] = "kN"

    with pytest.raises(ValueError, match="changed"):
        evaluate_fabrication(
            request, context, client=ChangedClient(native, description=changed), pinned_job=pinned
        )
    repeated = evaluate_fabrication(request, context, client=native, pinned_job=pinned)
    assert repeated.values == result.values
    with pytest.raises(ValueError, match="pinned"):
        evaluate_fabrication(
            request.model_copy(update={"location_id": "other"}), context, client=native, pinned_job=pinned
        )


@pytest.mark.parametrize("change", ["context", "inputs", "probability_semantics"])
def test_native_receipt_cannot_rebind_context_inputs_or_probability_meaning(native, change):
    requests, context = cases()

    def changed(payload):
        if change == "context":
            payload["context"]["time_basis"] = "foreign_clock"
        elif change == "inputs":
            payload["inputs"]["nut_factor"] = 0.3
        else:
            payload["probability_semantics"] = {"preload_N": "HEP"}

    with pytest.raises(ValueError, match="changed"):
        evaluate_fabrication(
            requests["fabrication.bolt_preload"], context, client=ChangedClient(native, payload=changed)
        )


@pytest.mark.parametrize("field", ["request", "context", "values", "status", "requirement_records"])
def test_result_and_nested_receipt_tampering_is_detected(native, field):
    requests, context = cases()
    result = evaluate_fabrication(requests["fabrication.bolt_preload"], context, client=native)
    changes = {
        "request": result.request.model_copy(update={"location_id": "other"}),
        "context": context.model_copy(update={"clock_id": "other"}),
        "values": result.values | {"preload_N": 1.0},
        "status": "needs_data",
        "requirement_records": (result.requirement_records[0] | {"outcome": "unknown"},),
    }
    with pytest.raises(ValueError, match="changed"):
        result.model_copy(update={field: changes[field]}).check_integrity()
    restored = FabricationResult.model_validate_json(result.model_dump_json())
    restored.check_integrity()
    restored.native_receipt.native.payload["values"]["preload_N"] = 1.0
    with pytest.raises(ValueError, match="changed"):
        restored.check_integrity()
