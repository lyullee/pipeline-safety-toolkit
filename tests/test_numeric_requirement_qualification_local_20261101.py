"""Independent arithmetic and boundary checks for caller-declared numeric requirements."""
import pytest
from pipeline_integrity.contracts import AssessmentResult, RunContext
from pipeline_integrity.requirements import DeclaredRequirement, evaluate_requirement


def result(*, status="computed", values=None, units=None):
    return AssessmentResult(
        model_id="toy_model", model_version="1", status=status,
        inputs={}, values={"quantity": 2.5} if values is None else values,
        input_units={}, output_units={"quantity": "m"} if units is None else units,
        assumptions=("authored synthetic input",), notes=(), source_ids=(),
        context=RunContext(asset_id="synthetic", scenario_id="numeric_requirement", clock_id="t", origin="synthetic"),
        input_digest="authored-input", model_source_digest="authored-model", runtime={},
        qualification="engineering_screening",
    )


def rule(comparison, limit=2.5, *, model_id="toy_model", output_name="quantity", unit="m"):
    return DeclaredRequirement(
        id="limit", model_id=model_id, output_name=output_name,
        comparison=comparison, limit=limit, unit=unit,
        criterion_source="authored criterion", applicability="synthetic example only",
    )


@pytest.mark.parametrize(("comparison", "value", "expected"), [
    ("lt", 2.49, True), ("lt", 2.5, False),
    ("le", 2.5, True), ("le", 2.51, False),
    ("eq", 2.5, True), ("eq", 2.5000001, False),
    ("ge", 2.5, True), ("ge", 2.49, False),
    ("gt", 2.5001, True), ("gt", 2.5, False),
])
def test_each_comparison_matches_independent_scalar_operator(comparison, value, expected):
    # The oracle is the corresponding Python scalar comparison, written independently here.
    reference = {"lt": value < 2.5, "le": value <= 2.5, "eq": value == 2.5,
                 "ge": value >= 2.5, "gt": value > 2.5}[comparison]
    assert reference is expected
    got = evaluate_requirement(rule(comparison), result(values={"quantity": value}))
    assert got["outcome"] == ("satisfied" if expected else "not_satisfied")
    assert got["value"] == value and got["limit"] == 2.5 and got["unit"] == "m"
    assert got["whole_standard_compliance_claim"] is False


def test_uncomputed_result_is_withheld_before_unit_or_value_comparison():
    got = evaluate_requirement(rule("ge", unit="Pa_abs"), result(status="needs_data", units={}))
    assert got["outcome"] == "withheld"
    assert got["reason"] == "a numerical model result is unavailable"


def test_requirement_cannot_be_rebound_to_a_different_model_or_unit():
    with pytest.raises(ValueError, match="different model"):
        evaluate_requirement(rule("ge", model_id="other"), result())
    with pytest.raises(ValueError, match="units"):
        evaluate_requirement(rule("ge", unit="cm"), result())


@pytest.mark.parametrize("bad_value", [True, None, "2.5"])
def test_non_scalar_or_boolean_quantities_are_rejected(bad_value):
    with pytest.raises(ValueError, match="scalar numerical"):
        evaluate_requirement(rule("ge"), result(values={"quantity": bad_value}))
