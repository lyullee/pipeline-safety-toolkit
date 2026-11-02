"""Analytical correlated budgets, state bindings and reconstructed receipt decisions."""

import copy
import math

import numpy as np
import pytest
from pydantic import ValidationError

from pipeline_integrity.contracts import EvidenceRef, RunContext, digest
from pipeline_integrity.metrology import (
    CoverageDeclaration,
    MeasurementBasis,
    MeasurementBudgetRequest,
    MeasurementDecisionRule,
    MeasurementInput,
    MeasurementLinearization,
    MeasurementOutput,
    MeasurementReferenceState,
    StandardCovariance,
    propagate_measurement_uncertainty,
    verify_measurement_uncertainty,
)


def context():
    return RunContext(
        asset_id="meter_a",
        scenario_id="authored_budget",
        clock_id="calibration_clock",
        origin="synthetic",
        evidence=(EvidenceRef(id="authored", citation="Original analytical cases", origin="synthetic"),),
    )


def basis(unit="kg/s", **updates):
    return MeasurementBasis(
        quantity="mass_flow", unit=unit, definition_id="mass_flow_at_meter", evidence_id="authored", **updates
    )


def request(*, matrix=((0.04, 0.01), (0.01, 0.09)), jacobian=((2.0, -1.0),), **updates):
    inputs = tuple(
        MeasurementInput(id=name, estimate=value, basis=basis(), evidence_id="authored", valid=True)
        for name, value in (("reference", 10.0), ("indication", 11.0))
    )
    values = dict(
        id="budget",
        asset_id="meter_a",
        clock_id="calibration_clock",
        event_id="steady_run_1",
        time_s=12.0,
        inputs=inputs,
        linearization=MeasurementLinearization(
            model_id="original_linear_combination",
            model_version="1",
            provider_source_sha256="a" * 64,
            configuration_sha256=digest({"coefficients": jacobian}),
            inputs_sha256=digest(inputs),
            outputs=tuple(
                MeasurementOutput(
                    id=f"result_{i}",
                    estimate=sum(a * v.estimate for a, v in zip(row, inputs, strict=True)),
                    basis=basis(),
                )
                for i, row in enumerate(jacobian)
            ),
            jacobian=jacobian,
            evidence_id="authored",
            local_approximation_reviewed=True,
        ),
        covariance=StandardCovariance(
            input_ids=("reference", "indication"),
            values=matrix,
            evidence_id="authored",
            definition_reviewed=True,
        ),
    )
    values.update(updates)
    return MeasurementBudgetRequest(**values)


def replace_request(req, **updates):
    data = req.model_dump(mode="json")
    data.update(updates)
    return MeasurementBudgetRequest.model_validate_json(__import__("json").dumps(data))


def test_correlated_sum_has_signed_pairs_and_not_independent_rss():
    result = propagate_measurement_uncertainty(request(), context())
    assert result["status"] == "computed"
    output = result["outputs"]["result_0"]
    assert output["estimate"] == 9.0
    assert output["standard_uncertainty"] == pytest.approx(math.sqrt(0.21))
    assert output["paired_covariance_contributions"][0]["variance_contribution"] == -0.04
    assert output["diagonal_variance_contributions"] == {"reference": 0.16, "indication": 0.09}
    assert not result["coverage_probability_computed"]
    assert not result["calibration_fit_performed"] and not result["custody_transfer_certified"]
    assert verify_measurement_uncertainty(result)


def test_multiple_outputs_match_independent_matrix_product():
    matrix = np.array([[0.04, -0.03], [-0.03, 0.09]])
    jacobian = np.array([[2.0, 3.0], [1.0, -1.0], [-2.0, 5.0]])
    result = propagate_measurement_uncertainty(
        request(matrix=tuple(map(tuple, matrix)), jacobian=tuple(map(tuple, jacobian))), context()
    )
    np.testing.assert_allclose(result["output_covariance"], jacobian @ matrix @ jacobian.T)


@pytest.mark.parametrize("correlation", [-1.0, -0.5, 0.0, 0.5, 1.0])
def test_singular_and_correlated_reference_difference(correlation):
    result = propagate_measurement_uncertainty(
        request(matrix=((0.25, correlation * 0.25), (correlation * 0.25, 0.25)), jacobian=((1.0, -1.0),)),
        context(),
    )
    assert result["status"] == "computed"
    assert result["output_covariance"][0][0] == pytest.approx(0.5 * (1 - correlation))
    assert not result["input_matrix_diagnostics"]["matrix_repaired"]


def test_zero_variance_is_explicit_known_not_unknown():
    result = propagate_measurement_uncertainty(request(matrix=((0.0, 0.0), (0.0, 0.0))), context())
    assert result["outputs"]["result_0"]["standard_uncertainty"] == 0
    assert result["input_matrix_diagnostics"]["zero_variance_inputs"] == ["reference", "indication"]


@pytest.mark.parametrize("matrix", [((0.04, None), (None, 0.09)), ((None, None), (None, None))])
def test_missing_covariance_does_not_supply_zero_or_independence(matrix):
    result = propagate_measurement_uncertainty(request(matrix=matrix), context())
    assert result["status"] == "needs_data" and not result["outputs"]
    assert verify_measurement_uncertainty(result)


@pytest.mark.parametrize(
    "change", ["linearization", "covariance", "missing_input", "unreviewed_model", "unreviewed_cov"]
)
def test_unresolved_model_inputs_and_reviews_withhold(change):
    data = request().model_dump(mode="json")
    if change in ("linearization", "covariance"):
        data[change] = None
    elif change == "missing_input":
        data["inputs"][0]["estimate"] = None
        data["inputs"][0]["valid"] = None
        data["linearization"]["inputs_sha256"] = digest(data["inputs"])
    elif change == "unreviewed_model":
        data["linearization"]["local_approximation_reviewed"] = False
    else:
        data["covariance"]["definition_reviewed"] = False
    result = propagate_measurement_uncertainty(
        MeasurementBudgetRequest.model_validate_json(__import__("json").dumps(data)), context()
    )
    assert result["status"] == "needs_data" and result["output_covariance"] is None


def test_indefinite_covariance_is_preserved_and_rejected_without_repair():
    result = propagate_measurement_uncertainty(request(matrix=((0.04, 0.2), (0.2, 0.09))), context())
    assert result["status"] == "out_of_scope" and not result["outputs"]
    assert result["input_matrix_diagnostics"]["minimum_scaled_eigenvalue"] < 0
    assert verify_measurement_uncertainty(result)


@pytest.mark.parametrize(
    "matrix", [((0.04, 0.01), (0.02, 0.09)), ((-0.01, 0.0), (0.0, 0.09)), ((0.0, 0.1), (0.1, 0.09))]
)
def test_invalid_covariance_contract(matrix):
    with pytest.raises(ValidationError):
        request(matrix=matrix)


@pytest.mark.parametrize("unit", ["g/s", "kg/hour", "bar", "degC", "percent"])
def test_scaled_and_offset_units_need_explicit_prior_conversion(unit):
    with pytest.raises(ValidationError):
        basis(unit)


@pytest.mark.parametrize("unit", ["kg/s", "m^3/s", "K", "1", "kg*m/s^2"])
def test_coherent_si_units(unit):
    assert basis(unit).unit == unit


def test_pressure_reference_and_reference_volume_basis_are_preserved():
    with pytest.raises(ValidationError):
        basis("Pa")
    pressure = basis("Pa", pressure_reference="absolute")
    assert pressure.pressure_reference == "absolute"
    state = MeasurementReferenceState(
        pressure_pa_abs=101325.0,
        temperature_k=293.15,
        composition_id="dry_ch4",
        moisture_basis="dry",
        evidence_id="authored",
    )
    volume = basis("m^3/s", reference_state=state)
    assert volume.reference_state.temperature_k == 293.15


@pytest.mark.parametrize("change", ["estimate", "unit", "basis", "covariance_order", "jacobian", "output_id"])
def test_stale_linearization_or_alignment_fails(change):
    data = request().model_dump(mode="json")
    if change == "estimate":
        data["inputs"][0]["estimate"] = 20.0
    elif change == "unit":
        data["inputs"][0]["basis"]["unit"] = "kg"
    elif change == "basis":
        data["inputs"][0]["basis"]["definition_id"] = "another_state"
    elif change == "covariance_order":
        data["covariance"]["input_ids"].reverse()
    elif change == "jacobian":
        data["linearization"]["jacobian"][0].append(2.0)
    else:
        data["linearization"]["outputs"].append(data["linearization"]["outputs"][0])
    with pytest.raises(ValidationError):
        MeasurementBudgetRequest.model_validate_json(__import__("json").dumps(data))


def test_context_and_evidence_are_not_relabelled():
    with pytest.raises(ValueError, match="asset and clock"):
        propagate_measurement_uncertainty(request(), context().model_copy(update={"clock_id": "other"}))
    with pytest.raises(ValueError, match="context evidence"):
        propagate_measurement_uncertainty(request(), context().model_copy(update={"evidence": ()}))


def coverage(reviewed=True):
    return CoverageDeclaration(
        factor=2.0,
        evidence_id="authored",
        reviewed=reviewed,
        supplied_probability=0.95,
        probability_basis="Caller-authored normal approximation, not computed from k",
    )


@pytest.mark.parametrize(
    "lower,upper,expected",
    [
        (7.0, 11.0, "accepted_by_declared_rule"),
        (11.0, None, "rejected_by_declared_rule"),
        (9.0, 12.0, "indeterminate"),
    ],
)
def test_expanded_interval_explicit_accept_reject_overlap(lower, upper, expected):
    rule = MeasurementDecisionRule(
        output_id="result_0",
        lower_limit=lower,
        upper_limit=upper,
        comparison="expanded_interval",
        evidence_id="authored",
        reviewed=True,
    )
    result = propagate_measurement_uncertainty(
        request(coverage=coverage(), decision_rules=(rule,)), context()
    )
    output = result["outputs"]["result_0"]
    assert output["decision"]["status"] == expected
    assert output["expanded_uncertainty"] == pytest.approx(2 * math.sqrt(0.21))
    assert not result["coverage_probability_computed"]


@pytest.mark.parametrize("unreviewed", ["coverage", "rule"])
def test_unreviewed_decision_inputs_are_indeterminate(unreviewed):
    rule = MeasurementDecisionRule(
        output_id="result_0",
        upper_limit=20.0,
        comparison="expanded_interval",
        evidence_id="authored",
        reviewed=unreviewed != "rule",
    )
    result = propagate_measurement_uncertainty(
        request(coverage=coverage(unreviewed != "coverage"), decision_rules=(rule,)), context()
    )
    assert result["outputs"]["result_0"]["decision"]["status"] == "indeterminate"


def test_nominal_rule_does_not_need_coverage_but_preserves_guardband():
    rule = MeasurementDecisionRule(
        output_id="result_0",
        upper_limit=10.0,
        guard_band=1.0,
        comparison="nominal",
        evidence_id="authored",
        reviewed=True,
    )
    result = propagate_measurement_uncertainty(request(decision_rules=(rule,)), context())
    assert result["outputs"]["result_0"]["decision"]["status"] == "accepted_by_declared_rule"
    assert result["outputs"]["result_0"]["expanded_uncertainty"] is None


@pytest.mark.parametrize(
    "mutation", ["uncertainty", "nominal", "covariance", "decision", "source", "certification"]
)
def test_rehashed_receipt_mutations_are_reconstructed(mutation):
    rule = MeasurementDecisionRule(
        output_id="result_0",
        upper_limit=10.0,
        comparison="expanded_interval",
        evidence_id="authored",
        reviewed=True,
    )
    record = copy.deepcopy(
        propagate_measurement_uncertainty(request(coverage=coverage(), decision_rules=(rule,)), context())
    )
    if mutation == "uncertainty":
        record["outputs"]["result_0"]["standard_uncertainty"] *= 0.5
    elif mutation == "nominal":
        record["outputs"]["result_0"]["estimate"] += 1
    elif mutation == "covariance":
        record["output_covariance"][0][0] *= 2
    elif mutation == "decision":
        record["outputs"]["result_0"]["decision"]["status"] = "rejected_by_declared_rule"
    elif mutation == "source":
        record["implementation_sha256"] = "b" * 64
    else:
        record["custody_transfer_certified"] = True
    record["record_digest"] = digest({k: v for k, v in record.items() if k != "record_digest"})
    with pytest.raises(ValueError):
        verify_measurement_uncertainty(record)


def test_extreme_propagation_is_withheld_not_infinite():
    result = propagate_measurement_uncertainty(request(jacobian=((1e200, 0.0),)), context())
    assert result["status"] == "out_of_scope" and not result["outputs"]


def test_large_sensitivity_small_variance_does_not_square_before_scaling():
    result = propagate_measurement_uncertainty(
        request(matrix=((1e-300, 0.0), (0.0, 0.0)), jacobian=((1e200, 0.0),)), context()
    )
    assert result["status"] == "computed"
    assert result["outputs"]["result_0"]["standard_uncertainty"] == pytest.approx(1e50)


@pytest.mark.parametrize(
    "valid,estimate,status",
    [(False, -1.0, "out_of_scope"), (None, 10.0, "needs_data"), (None, None, "needs_data")],
)
def test_invalid_and_unknown_input_quality_are_preserved(valid, estimate, status):
    data = request().model_dump(mode="json")
    data["inputs"][0].update(estimate=estimate, valid=valid)
    data["linearization"]["inputs_sha256"] = digest(data["inputs"])
    result = propagate_measurement_uncertainty(
        MeasurementBudgetRequest.model_validate_json(__import__("json").dumps(data)), context()
    )
    assert result["status"] == status and not result["outputs"]
    assert result["request"]["inputs"][0]["estimate"] == estimate
    assert verify_measurement_uncertainty(result)


def test_valid_missing_input_is_invalid_contract():
    with pytest.raises(ValidationError):
        MeasurementInput(id="bad", estimate=None, basis=basis(), evidence_id="authored", valid=True)


def test_static_event_budget_preserves_unknown_time_without_inventing_samples():
    result = propagate_measurement_uncertainty(request(time_s=None), context())
    assert result["status"] == "computed" and result["request"]["time_s"] is None
    assert verify_measurement_uncertainty(result)


@pytest.mark.parametrize("field", ["covariance", "estimate", "output", "metadata_nan", "metadata_inf"])
def test_cloned_nonfinite_values_fail_before_json_can_turn_them_into_null(field):
    req, ctx = request(), context()
    if field == "covariance":
        covariance = req.covariance.model_copy(update={"values": ((math.nan, 0.01), (0.01, 0.09))})
        req = req.model_copy(update={"covariance": covariance})
    elif field == "estimate":
        inputs = (req.inputs[0].model_copy(update={"estimate": math.nan}), req.inputs[1])
        req = req.model_copy(update={"inputs": inputs})
    elif field == "output":
        outputs = (req.linearization.outputs[0].model_copy(update={"estimate": math.inf}),)
        req = req.model_copy(
            update={"linearization": req.linearization.model_copy(update={"outputs": outputs})}
        )
    else:
        ctx = ctx.model_copy(
            update={"metadata": {"invalid": math.nan if field == "metadata_nan" else math.inf}}
        )
    with pytest.raises(ValidationError):
        propagate_measurement_uncertainty(req, ctx)


def test_output_covariance_is_exactly_symmetric_for_contract_chaining():
    native = (
        (0.11432784272625618, 0.1917743575935109),
        (0.1953941544050074, 0.47713398189152945),
    )
    rows = [native, *(tuple(map(tuple, v)) for v in np.random.default_rng(141).normal(size=(25, 2, 2)))]
    for jacobian in rows:
        result = propagate_measurement_uncertainty(request(jacobian=jacobian), context())
        output = result["output_covariance"]
        assert output[0][1] == output[1][0]
        StandardCovariance(
            input_ids=("result_0", "result_1"), values=tuple(map(tuple, output)), evidence_id="authored"
        )
        np.testing.assert_allclose(
            output,
            np.asarray(jacobian) @ np.array([[0.04, 0.01], [0.01, 0.09]]) @ np.asarray(jacobian).T,
            atol=1e-15,
        )


def test_nonzero_uncertainty_cannot_underflow_into_exact_zero_variance():
    result = propagate_measurement_uncertainty(
        request(matrix=((1e-320, 0.0), (0.0, 0.0)), jacobian=((1e-10, 0.0),)), context()
    )
    assert result["status"] == "out_of_scope" and not result["outputs"]
    assert "zero output variance" in result["issues"][0]


def test_subnormal_perfect_correlation_true_cancellation_remains_known_zero():
    result = propagate_measurement_uncertainty(
        request(matrix=((1e-320, 1e-320), (1e-320, 1e-320)), jacobian=((1e-10, -1e-10),)), context()
    )
    assert result["status"] == "computed"
    assert result["outputs"]["result_0"]["standard_uncertainty"] == 0.0
