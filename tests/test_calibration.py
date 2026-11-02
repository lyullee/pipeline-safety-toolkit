"""Numerical EIV identities, physical cohort separation and adversarial receipts."""

import builtins
import json
import runpy
from copy import deepcopy
from pathlib import Path

import numpy as np
import pytest

from pipeline_integrity.calibration import (
    CalibrationProvider,
    CalibrationRequest,
    affine_provider,
    boundary_metadata,
    covariance_order,
    fit_calibration,
    model_identity,
    predict_calibration,
    verify_calibration_record,
)
from pipeline_integrity.contracts import digest

MAKE_CASE = runpy.run_path(str(Path(__file__).resolve().parents[1] / "examples/calibration.py"))["make_case"]


def context_for(q, ctx):
    return ctx.model_copy(update={"metadata": {**ctx.metadata, "calibration_boundary": boundary_metadata(q)}})


def revised(q, ctx, **changes):
    q = q.model_copy(update=changes)
    return q, context_for(q, ctx)


@pytest.fixture(scope="module")
def baseline():
    q, c, p = MAKE_CASE(reviewed=True)
    return q, c, p, fit_calibration(q, c, p)


def test_default_unknown_and_verifier_do_not_import_scipy_or_odrpack(monkeypatch):
    # MeasurementBasis uses the mandatory unit registry, whose supplier may probe SciPy.
    # Gate the service's native path after constructing the common unit contracts.
    q, c, p = MAKE_CASE()
    original = builtins.__import__

    def guarded(name, *args, **kwargs):
        if name.startswith(("scipy", "odrpack")):
            pytest.fail("optional backend imported before input/review gates")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    r = fit_calibration(q, c, p)
    assert r["status"] == "needs_data" and not r["native_called"] and r["model_calls"] == 0
    assert verify_calibration_record(r, p)["valid"]


def test_homoscedastic_reference_is_closed_form_deming(baseline):
    q, c, p, r = baseline
    x = np.array([v.x for v in q.observations])
    y = np.array([v.y for v in q.observations])
    xx, yy = x - x.mean(), y - y.mean()
    sxx, syy, sxy = xx @ xx, yy @ yy, xx @ yy
    ratio = 4.0
    slope = (syy - ratio * sxx + np.sqrt((syy - ratio * sxx) ** 2 + 4 * ratio * sxy**2)) / (2 * sxy)
    assert r["status"] == "computed"
    assert r["values"]["parameters"]["slope"] == pytest.approx(slope, rel=1e-8)
    assert r["values"]["parameters"]["offset"] == pytest.approx(y.mean() - slope * x.mean(), abs=1e-7)
    assert r["audits"]["degrees_of_freedom"] == 4
    assert not r["audits"]["covariance_residual_scaled"]
    assert verify_calibration_record(r, p, request=q, context=c)["valid"]


def test_full_cross_axis_shared_systematic_covariance_is_retained(baseline):
    q, c, p, _ = baseline
    u = np.array(q.covariance.values)
    shared = np.r_[np.full(6, 0.04), np.full(6, 0.08)]
    u += np.outer(shared, shared)
    q, c = revised(q, c, covariance=q.covariance.model_copy(update={"values": tuple(map(tuple, u))}))
    r = fit_calibration(q, c, p)
    assert r["status"] == "computed"
    assert r["native_calls"][0]["fit_covariance"][0][-1] == pytest.approx(0.0032)
    assert r["values"]["parameter_standard_covariance"][0][0] > 0.069


def test_uncertainty_scaling_changes_covariance_not_point_estimates(baseline):
    q, c, p, before = baseline
    values = tuple(tuple(9 * v for v in row) for row in q.covariance.values)
    q, c = revised(q, c, covariance=q.covariance.model_copy(update={"values": values}))
    r = fit_calibration(q, c, p)
    assert list(r["values"]["parameters"].values()) == pytest.approx(
        list(before["values"]["parameters"].values()), rel=1e-7
    )
    assert np.array(r["values"]["parameter_standard_covariance"]) == pytest.approx(
        9 * np.array(before["values"]["parameter_standard_covariance"]), rel=1e-6
    )
    assert r["values"]["weighted_sum_squares"] == pytest.approx(
        before["values"]["weighted_sum_squares"] / 9, rel=1e-6
    )


def exact_case(q, c):
    n = len(q.observations)
    values = tuple(tuple(0.04 if i == j and i >= n else 0.0 for j in range(2 * n)) for i in range(2 * n))
    return revised(
        q,
        c,
        covariance=q.covariance.model_copy(update={"values": values}),
        exact_x_ids=tuple(v.id for v in q.observations),
        exact_x_evidence_id="source",
    )


def test_declared_exact_input_limit_matches_weighted_linear_algebra(baseline):
    q, c, p, _ = baseline
    q, c = exact_case(q, c)
    r = fit_calibration(q, c, p)
    design = np.column_stack((np.ones(6), [v.x for v in q.observations]))
    beta = np.linalg.lstsq(design, [v.y for v in q.observations], rcond=None)[0]
    covariance = 0.04 * np.linalg.inv(design.T @ design)
    assert list(r["values"]["parameters"].values()) == pytest.approx(beta, rel=1e-8)
    assert r["values"]["parameter_standard_covariance"] == pytest.approx(covariance, rel=1e-8)
    assert r["native_calls"][0]["latent_x_ids"] == []


@pytest.mark.parametrize(
    "change", ["no_declaration", "declared_no_zero", "exact_y", "singular", "indefinite"]
)
def test_singular_or_undeclared_exact_errors_are_not_repaired(baseline, change):
    q, c, p, _ = baseline
    if change == "no_declaration":
        q, c = exact_case(q, c)
        q, c = revised(q, c, exact_x_ids=())
    elif change == "declared_no_zero":
        q, c = revised(q, c, exact_x_ids=(q.observations[0].id,), exact_x_evidence_id="source")
    else:
        u = np.array(q.covariance.values)
        if change == "exact_y":
            u[6, 6] = 0.0
        else:
            u[0, 1] = u[1, 0] = 0.01 if change == "singular" else 0.02
        q, c = revised(q, c, covariance=q.covariance.model_copy(update={"values": tuple(map(tuple, u))}))
    assert fit_calibration(q, c, p)["status"] == "out_of_scope"


@pytest.mark.parametrize("field", ["x", "y", "valid", "run_id", "population_id"])
def test_unknowns_preserved_before_native(baseline, field):
    q, c, p, _ = baseline
    rows = (q.observations[0].model_copy(update={field: None}), *q.observations[1:])
    q, c = revised(q, c, observations=rows)
    r = fit_calibration(q, c, p)
    assert r["status"] == "needs_data" and not r["native_called"] and r["backend"] is None


@pytest.mark.parametrize(
    "case",
    [
        "unknown_covariance",
        "unknown_correlation",
        "unreviewed_covariance",
        "no_provider",
        "source_pin_unknown",
    ],
)
def test_missing_covariance_and_provider_gates(baseline, case):
    q, c, p, _ = baseline
    if case == "unknown_covariance":
        q, c = revised(q, c, covariance=None)
    elif case == "unknown_correlation":
        u = [list(row) for row in q.covariance.values]
        u[0][1] = u[1][0] = None
        q, c = revised(q, c, covariance=q.covariance.model_copy(update={"values": tuple(map(tuple, u))}))
    elif case == "unreviewed_covariance":
        q, c = revised(q, c, covariance=q.covariance.model_copy(update={"definition_reviewed": False}))
    elif case == "no_provider":
        p = None
    else:
        q, c = revised(q, c, provider_source_sha256=None)
    r = fit_calibration(q, c, p)
    assert r["status"] == "needs_data" and r["backend"] is None


def heldout(q, c, *, same_run=False, shared=False, independent=True):
    rows = tuple(
        v.model_copy(
            update={"role": "validation", "run_id": q.observations[0].run_id if same_run else "heldout-run"}
        )
        if i >= 4
        else v
        for i, v in enumerate(q.observations)
    )
    changes = dict(
        observations=rows, split_policy="separate_runs", require_independent_validation=independent
    )
    if shared:
        u = np.array(q.covariance.values)
        u += np.ones_like(u) * 0.001
        changes["covariance"] = q.covariance.model_copy(update={"values": tuple(map(tuple, u))})
    return revised(q, c, **changes)


def test_heldout_values_never_change_fitted_parameters(baseline):
    q, c, p, _ = baseline
    q, c = heldout(q, c)
    before = fit_calibration(q, c, p)
    rows = tuple(
        v.model_copy(update={"y": v.y + 1000.0}) if v.role == "validation" else v for v in q.observations
    )
    q, c = revised(q, c, observations=rows)
    after = fit_calibration(q, c, p)
    assert before["status"] == after["status"] == "computed"
    assert before["values"]["parameters"] == after["values"]["parameters"]
    assert before["native_calls"][0] == after["native_calls"][0]
    assert min(abs(v) for v in after["values"]["validation"]["residual_y"]) > 999


@pytest.mark.parametrize(
    "same,shared,independent,status",
    [
        (True, False, True, "out_of_scope"),
        (False, True, True, "out_of_scope"),
        (False, True, False, "computed"),
    ],
)
def test_physical_split_and_shared_error_honesty(baseline, same, shared, independent, status):
    q, c, p, _ = baseline
    q, c = heldout(q, c, same_run=same, shared=shared, independent=independent)
    r = fit_calibration(q, c, p)
    assert r["status"] == status
    if status == "computed":
        assert r["audits"]["fit_validation_shared_covariance"]
        assert not r["values"]["validation"]["statistically_independent_errors"]


@pytest.mark.parametrize("target", ["asset", "clock", "evidence", "boundary", "provider"])
def test_boundary_and_model_pins_fail_closed(baseline, target):
    q, c, p, _ = baseline
    if target == "asset":
        c = c.model_copy(update={"asset_id": "other"})
    elif target == "clock":
        c = c.model_copy(update={"clock_id": "other"})
    elif target == "evidence":
        c = c.model_copy(update={"evidence": ()})
    elif target == "boundary":
        c = c.model_copy(update={"metadata": {}})
    else:
        q = q.model_copy(update={"configuration_sha256": "a" * 64})
    with pytest.raises(ValueError):
        fit_calibration(q, c, p)


@pytest.mark.parametrize(
    "target", ["parameters", "covariance", "native", "backend", "status", "clock", "split"]
)
def test_rehashed_semantic_corruptions_refused(baseline, target):
    q, c, p, original = baseline
    r = deepcopy(original)
    if target == "parameters":
        r["values"]["parameters"]["slope"] += 0.1
    elif target == "covariance":
        r["values"]["parameter_standard_covariance"][0][0] *= 4
    elif target == "native":
        r["native_calls"][0]["result"]["cost"] += 1
    elif target == "backend":
        r["backend"]["scipy_version"] = "unknown"
    elif target == "status":
        r["status"] = "needs_data"
    elif target == "clock":
        r["context"]["clock_id"] = "other"
    else:
        r["request"]["observations"][-1]["role"] = "validation"
    r["record_digest"] = digest({k: v for k, v in r.items() if k != "record_digest"})
    assert not verify_calibration_record(r, p)["valid"]


def nonlinear(x, parameters, config):
    return parameters["offset"] * np.exp(parameters["slope"] * x)


def test_nonlinear_exact_input_model_and_local_covariance(baseline):
    q, c, _, _ = baseline
    q, c = exact_case(q, c)
    provider = CalibrationProvider("exponential", "1", "1", "1", {"offset": "1", "slope": "1"}, nonlinear)
    rows = tuple(v.model_copy(update={"y": float(2 * np.exp(0.3 * v.x))}) for v in q.observations)
    q, c = revised(q, c, observations=rows, model_id="exponential", **model_identity(provider))
    r = fit_calibration(q, c, provider)
    assert r["status"] == "computed"
    assert list(r["values"]["parameters"].values()) == pytest.approx([2.0, 0.3], rel=1e-7)
    x = np.array([v.x for v in rows])
    j = np.column_stack((np.exp(0.3 * x), 2 * x * np.exp(0.3 * x)))
    assert r["values"]["parameter_standard_covariance"] == pytest.approx(
        0.04 * np.linalg.inv(j.T @ j), rel=1e-5
    )


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_clone_nonfinite_is_invalid_not_missing(baseline, value):
    q, c, p, _ = baseline
    q = q.model_copy(
        update={"observations": (q.observations[0].model_copy(update={"x": value}), *q.observations[1:])}
    )
    with pytest.raises(ValueError):
        fit_calibration(q, c, p)


def test_application_interpolation_and_no_silent_input_uncertainty(baseline):
    _, c, p, r = baseline
    output = predict_calibration(r, [2.0, 4.0], c, p)
    assert output["uncertainty"] is None
    assert output["y"][0] == pytest.approx(
        r["values"]["parameters"]["offset"] + 2 * r["values"]["parameters"]["slope"]
    )
    for xx in ([0.999], [6.001], [float("nan")], []):
        with pytest.raises(ValueError):
            predict_calibration(r, xx, c, p)


def test_affine_unit_mismatch_is_rejected():
    with pytest.raises(ValueError):
        affine_provider(x_unit="m", y_unit="kg", slope_unit="1")


def test_actual_callback_bytecode_is_pinned_even_when_source_file_unchanged(baseline, monkeypatch):
    q, c, p, _ = baseline
    code = p.predict.__code__
    constants = tuple("slope" if v == "offset" else v for v in code.co_consts)
    assert constants != code.co_consts
    monkeypatch.setattr(p.predict, "__code__", code.replace(co_consts=constants))
    with pytest.raises(ValueError, match="source or configuration"):
        fit_calibration(q, c, p)


def test_source_example_never_invents_independent_errors_or_repairs_empty_source():
    with pytest.raises(KeyError):
        MAKE_CASE(reviewed=True, source_inputs={})
    inputs = {
        "x": [0.01, 0.02, 0.03],
        "y": [0.96, 0.93, 0.90],
        "x_unit": "1",
        "y_unit": "1",
        "source_citation": "Original withheld source fixture",
        "source_license_id": "MIT",
    }
    q, c, p = MAKE_CASE(reviewed=True, source_inputs=inputs)
    assert q.covariance is None
    result = fit_calibration(q, c, p)
    assert result["status"] == "needs_data" and result["backend"] is None


def test_invalid_observation_and_model_call_budget_are_not_silently_dropped(baseline):
    q, c, p, _ = baseline
    q, c = revised(
        q, c, observations=(q.observations[0].model_copy(update={"valid": False}), *q.observations[1:])
    )
    assert fit_calibration(q, c, p)["status"] == "out_of_scope"
    q, c, p = MAKE_CASE(reviewed=True)
    q, c = revised(q, c, max_model_calls=1)
    result = fit_calibration(q, c, p)
    assert result["status"] == "out_of_scope" and result["native_called"]
    assert result["native_calls"] and "budget" in result["notes"][0]


def test_json_roundtrip_keeps_current_source_replay(baseline):
    _, _, p, r = baseline
    assert verify_calibration_record(json.loads(json.dumps(r)), p)["valid"]


def bad_derivatives(x, parameters, config):
    return {"offset": np.ones_like(x), "slope": np.zeros_like(x)}, np.full_like(x, parameters["slope"])


def zero_model(x, parameters, config):
    return np.full_like(x, parameters["offset"])


def test_provider_derivative_or_local_rank_failure_is_refused(baseline):
    q, c, p, _ = baseline
    for callback, derivatives in ((p.predict, bad_derivatives), (zero_model, None)):
        provider = CalibrationProvider(
            p.model_id,
            p.version,
            p.x_unit,
            p.y_unit,
            p.parameter_units,
            callback,
            p.configuration,
            derivatives,
        )
        qq, cc = revised(q, c, **model_identity(provider))
        result = fit_calibration(qq, cc, provider)
        assert result["status"] == "out_of_scope" and result["values"] is None
        assert result["native_calls"][0]["result"]


def test_active_parameter_bound_never_publishes_unconstrained_covariance(baseline):
    q, c, p, _ = baseline
    params = (q.parameters[0], q.parameters[1].model_copy(update={"upper": 1.0, "initial": 0.8}))
    q, c = revised(q, c, parameters=params)
    result = fit_calibration(q, c, p)
    assert result["status"] == "out_of_scope" and result["values"] is None


def test_indefinite_validation_covariance_cannot_hide_in_fit_principal_block(baseline):
    q, c, p, _ = baseline
    q, c = heldout(q, c)
    u = np.array(q.covariance.values)
    u[4, 5] = u[5, 4] = 0.02
    q, c = revised(q, c, covariance=q.covariance.model_copy(update={"values": tuple(map(tuple, u))}))
    result = fit_calibration(q, c, p)
    assert result["status"] == "out_of_scope" and not result["native_called"]


def test_separate_population_split_requires_distinct_populations(baseline):
    q, c, p, _ = baseline
    q, c = heldout(q, c)
    q, c = revised(q, c, split_policy="separate_populations")
    assert fit_calibration(q, c, p)["status"] == "out_of_scope"


def test_model_domain_and_covariance_identifier_order_are_strict(baseline):
    q, c, p, _ = baseline
    qq, cc = revised(q, c, x_domain=(0.0, 4.0))
    assert fit_calibration(qq, cc, p)["status"] == "out_of_scope"
    cov = q.covariance.model_copy(update={"input_ids": tuple(reversed(q.covariance.input_ids))})
    with pytest.raises(ValueError):
        fit_calibration(q.model_copy(update={"covariance": cov}), c, p)


def test_missing_validation_not_hidden_as_fit_only_success(baseline):
    q, c, p, _ = baseline
    q, c = revised(q, c, split_policy="separate_runs", require_independent_validation=True)
    assert fit_calibration(q, c, p)["status"] == "out_of_scope"


def test_authored_maximum_full_covariance_and_budget_no_source_dependency(baseline):
    q, c, p, _ = baseline
    rows = tuple(
        q.observations[0].model_copy(
            update={"id": f"row-{i}", "x": float(i) / 5 + 1, "y": 3 + 2 * float(i) / 5}
        )
        for i in range(32)
    )
    ids = tuple(v.id for v in rows)
    u = np.diag([0.01] * 32 + [0.04] * 32) + np.ones((64, 64)) * 0.001
    q, c = revised(
        q,
        c,
        observations=rows,
        x_domain=(0.0, 9.0),
        covariance=q.covariance.model_copy(
            update={"input_ids": covariance_order(ids), "values": tuple(map(tuple, u))}
        ),
    )
    r = fit_calibration(q, c, p)
    assert r["status"] == "computed" and len(r["values"]["latent_x"]) == 32
    assert list(r["values"]["parameters"].values()) == pytest.approx([1.0, 2.0], rel=1e-8)
    with pytest.raises(ValueError):
        CalibrationRequest.model_validate(
            q.model_copy(
                update={"observations": (*rows, rows[-1].model_copy(update={"id": "row-33"}))}
            ).model_dump(mode="python")
        )
