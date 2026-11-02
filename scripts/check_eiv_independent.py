"""Independent linear nuisance-GLS, covariance cancellation and receipt probes."""

import json
import math
import runpy
from copy import deepcopy
from pathlib import Path

import numpy as np

from pipeline_integrity.calibration import (
    CalibrationProvider,
    boundary_metadata,
    fit_calibration,
    model_identity,
    predict_calibration,
    verify_calibration_record,
)
from pipeline_integrity.contracts import digest
from pipeline_integrity.datasets import file_digest
from pipeline_integrity.inverse import FitParameter

ROOT = Path(__file__).resolve().parents[1]


def known_slope(x, parameters, configuration):
    return parameters["offset"] + configuration["slope"] * x


def known_slope_derivatives(x, parameters, configuration):
    return {"offset": np.ones_like(x)}, np.full_like(x, configuration["slope"])


def unidentified(x, parameters, configuration):
    return configuration["slope"] * x


def recontext(request, context):
    return context.model_copy(
        update={"metadata": {**context.metadata, "calibration_boundary": boundary_metadata(request)}}
    )


def covariance(request, values):
    return request.model_copy(
        update={
            "covariance": request.covariance.model_copy(
                update={"values": tuple(tuple(float(v) for v in row) for row in values)}
            )
        }
    )


def seal(record):
    record["record_digest"] = digest({k: v for k, v in record.items() if k != "record_digest"})
    return record


def main():
    checks, cases = [], {}
    producer = file_digest(ROOT / "src/pipeline_integrity/calibration.py")
    assert producer == "9923ffbf89e3bcecb8fa2a20a4a8e4c4b1dad14332a3d15028ba4c7d490e7167"
    make = runpy.run_path(str(ROOT / "examples/calibration.py"))["make_case"]
    q, c, _ = make(reviewed=True)
    p = CalibrationProvider(
        "independent_known_slope_offset",
        "1",
        "1",
        "1",
        {"offset": "1"},
        known_slope,
        {"slope": 2.3},
        known_slope_derivatives,
    )
    q = q.model_copy(
        update={
            "model_id": p.model_id,
            "model_version": p.version,
            **model_identity(p),
            "parameters": (FitParameter(id="offset", unit="1", lower=-5.0, upper=5.0, initial=0.5),),
        }
    )
    # A dense, SPD measurement law has cross-point and cross-axis dependencies.
    n = len(q.observations)
    vector1 = np.r_[np.linspace(0.03, 0.08, n), np.linspace(0.09, -0.04, n)]
    vector2 = np.r_[np.linspace(-0.02, 0.04, n), np.linspace(0.08, 0.015, n)]
    C = (
        np.diag(np.r_[np.linspace(0.006, 0.014, n), np.linspace(0.02, 0.06, n)])
        + np.outer(vector1, vector1)
        + np.outer(vector2, vector2)
    )
    x = np.array([1.05, 1.98, 3.04, 4.01, 4.96, 6.02])
    y = 0.7 + 2.3 * np.arange(1.0, n + 1) + np.array([0.01, -0.04, 0.02, 0.03, -0.02, 0.015])
    q = covariance(
        q.model_copy(
            update={
                "observations": tuple(
                    o.model_copy(update={"x": float(a), "y": float(b)})
                    for o, a, b in zip(q.observations, x, y, strict=True)
                )
            }
        ),
        C,
    )
    c = recontext(q, c)

    def check(name, passed, observed=None):
        checks.append({"id": name, "passed": bool(passed), "observed": observed})
        assert passed, (name, observed)

    def fit(name, request, context, provider=p):
        r = fit_calibration(request, context, provider)
        cases[name] = r
        return r

    # Eliminate latent coordinates analytically.  r=y-b*x-a has covariance V.
    b = 2.3
    V = C[n:, n:] + b * b * C[:n, :n] - b * (C[:n, n:] + C[n:, :n])
    ones = np.ones(n)
    inverse_ones = np.linalg.solve(V, ones)
    reference_a = float(inverse_ones @ (y - b * x) / (ones @ inverse_ones))
    reference_var = float(1 / (ones @ inverse_ones))
    # A second independent expression retains every nuisance coordinate explicitly.
    A = np.block([[np.zeros((n, 1)), np.eye(n)], [np.ones((n, 1)), b * np.eye(n)]])
    information = A.T @ np.linalg.solve(C, A)
    gls = np.linalg.solve(information, A.T @ np.linalg.solve(C, np.r_[x, y]))
    result = fit("dense_joint_known_slope", q, c)
    check("dense_joint_computed", result["status"] == "computed")
    check(
        "independent_eliminated_offset",
        abs(result["values"]["parameters"]["offset"] - reference_a) < 2e-9,
        {"native": result["values"]["parameters"]["offset"], "reference": reference_a},
    )
    check(
        "independent_absolute_variance",
        math.isclose(result["values"]["parameter_standard_covariance"][0][0], reference_var, rel_tol=2e-9),
        {"native": result["values"]["parameter_standard_covariance"][0][0], "reference": reference_var},
    )
    check(
        "independent_joint_GLS_nuisance_x",
        np.allclose(result["values"]["latent_x"], gls[1:], rtol=0, atol=2e-9),
        float(np.max(abs(np.array(result["values"]["latent_x"]) - gls[1:]))),
    )
    check(
        "full_covariance_receipt_not_diagonalized",
        np.array_equal(result["native_calls"][0]["fit_covariance"], C),
    )
    check(
        "independent_references_agree",
        abs(gls[0] - reference_a) < 1e-11
        and math.isclose(np.linalg.inv(information)[0, 0], reference_var, rel_tol=2e-12),
    )
    check(
        "absolute_not_residual_scaled",
        result["audits"]["covariance_residual_scaled"] is False
        and abs(result["values"]["weighted_sum_squares"] - result["audits"]["degrees_of_freedom"]) > 0.1,
    )

    # A common error parallel to y=b*x cancels exactly in y-b*x.  Diagonalization would destroy this.
    along_line = np.r_[np.full(n, 0.7), np.full(n, b * 0.7)]
    same = covariance(q, C + np.outer(along_line, along_line))
    r_same = fit("shared_along_line_error", same, recontext(same, c))
    check(
        "cross_axis_common_error_cancels_in_offset",
        abs(r_same["values"]["parameters"]["offset"] - reference_a) < 5e-9,
    )
    check(
        "cross_axis_common_error_cancels_in_variance",
        math.isclose(r_same["values"]["parameter_standard_covariance"][0][0], reference_var, rel_tol=2e-9),
    )

    exact_C = np.zeros_like(C)
    exact_C[n:, n:] = C[n:, n:]
    exact = covariance(
        q.model_copy(
            update={"exact_x_ids": tuple(o.id for o in q.observations), "exact_x_evidence_id": "source"}
        ),
        exact_C,
    )
    rexact = fit("exact_x_dense_y_errors", exact, recontext(exact, c))
    weights = np.linalg.solve(C[n:, n:], ones)
    exact_a = float(weights @ (y - b * x) / (ones @ weights))
    exact_var = float(1 / (ones @ weights))
    check(
        "exact_x_known_slope_GLS",
        abs(rexact["values"]["parameters"]["offset"] - exact_a) < 2e-9
        and math.isclose(rexact["values"]["parameter_standard_covariance"][0][0], exact_var, rel_tol=2e-9),
    )
    check(
        "exact_x_no_nuisance_or_variance_invention",
        rexact["native_calls"][0]["latent_x_ids"] == [] and rexact["values"]["latent_x"] == x.tolist(),
    )

    hold = q.model_copy(
        update={
            "observations": tuple(
                o.model_copy(update={"role": "validation", "run_id": "new-validation-run"}) if i >= 4 else o
                for i, o in enumerate(q.observations)
            ),
            "split_policy": "separate_runs",
            "require_independent_validation": False,
        }
    )
    rh = fit("shared_error_heldout", hold, recontext(hold, c))
    poisoned = hold.model_copy(
        update={
            "observations": tuple(
                o.model_copy(update={"x": 7.0 + i / 10, "y": float(1e6 + i)}) if o.role == "validation" else o
                for i, o in enumerate(hold.observations)
            )
        }
    )
    rp = fit("heldout_value_poison", poisoned, recontext(poisoned, c))
    check("holdout_native_fit_bitwise_unchanged", rh["native_calls"] == rp["native_calls"])
    check(
        "holdout_covariance_and_parameters_unchanged",
        rh["values"]["parameters"] == rp["values"]["parameters"]
        and rh["values"]["parameter_standard_covariance"] == rp["values"]["parameter_standard_covariance"],
    )
    check(
        "shared_error_validation_honestly_dependent",
        rh["values"]["validation"]["statistically_independent_errors"] is False,
    )
    valonly = np.zeros(2 * n)
    valonly[[4, 5, n + 4, n + 5]] = [0.2, 0.3, -0.4, 0.5]
    hc = covariance(hold, C + np.outer(valonly, valonly))
    rc = fit("heldout_covariance_poison", hc, recontext(hc, c))
    check("heldout_covariance_never_used_for_fit", rh["native_calls"] == rc["native_calls"])
    independent = hold.model_copy(update={"require_independent_validation": True})
    ri = fit("claimed_independence_refused", independent, recontext(independent, c))
    check(
        "actual_cross_covariance_closes_independence_gate",
        ri["status"] == "out_of_scope" and ri["native_called"] is False,
    )

    unknown_values = [list(row) for row in q.covariance.values]
    unknown_values[0][n + 1] = unknown_values[n + 1][0] = None
    unknown = q.model_copy(
        update={"covariance": q.covariance.model_copy(update={"values": tuple(map(tuple, unknown_values))})}
    )
    ru = fit("unknown_cross_axis_covariance", unknown, recontext(unknown, c))
    check(
        "unknown_cross_correlation_never_zero_filled",
        ru["status"] == "needs_data" and ru["native_called"] is False,
    )
    bounded = q.model_copy(update={"parameters": (q.parameters[0].model_copy(update={"upper": 0.5}),)})
    rb = fit("active_offset_bound", bounded, recontext(bounded, c))
    check("active_bound_no_unconstrained_covariance", rb["status"] == "out_of_scope" and rb["values"] is None)
    bad_provider = CalibrationProvider(
        p.model_id, p.version, p.x_unit, p.y_unit, p.parameter_units, unidentified, p.configuration
    )
    qr = q.model_copy(update=model_identity(bad_provider))
    rr = fit("rank_deficient_offset", qr, recontext(qr, c), bad_provider)
    check(
        "rank_deficient_model_no_covariance",
        rr["status"] == "out_of_scope" and rr["values"] is None and rr["audits"]["full_rank"] is False,
    )

    prediction = predict_calibration(result, [2.5], c, p)
    check(
        "prediction_does_not_assume_newinput_covariance_zero",
        prediction["uncertainty"] is None and abs(prediction["y"][0] - (reference_a + b * 2.5)) < 2e-9,
    )
    refused = False
    try:
        predict_calibration(result, [0.5], c, p)
    except ValueError:
        refused = True
    check("observed_support_extrapolation_refused", refused)

    for target in (
        "latent_x",
        "absolute_covariance",
        "original_covariance",
        "backend",
        "validation_independence",
    ):
        bad = deepcopy(rh if target == "validation_independence" else result)
        if target == "latent_x":
            bad["values"]["latent_x"][0] += 0.1
        elif target == "absolute_covariance":
            bad["values"]["parameter_standard_covariance"][0][0] *= 2
        elif target == "original_covariance":
            bad["request"]["covariance"]["values"][0][n + 1] = bad["request"]["covariance"]["values"][n + 1][
                0
            ] = 0.0
            bad["input_digest"] = digest({"request": bad["request"], "context": bad["context"]})
        elif target == "backend":
            bad["backend"]["scipy_selected_source_and_binary_sha256"] = "0" * 64
        else:
            bad["values"]["validation"]["statistically_independent_errors"] = True
        seal(bad)
        check("rehashed:" + target, not verify_calibration_record(bad, p)["valid"])

    check(
        "production_source_unchanged", file_digest(ROOT / "src/pipeline_integrity/calibration.py") == producer
    )
    output = {
        "status": "passed_bounded_independent_review",
        "producer_sha256": producer,
        "private_probe_sha256": file_digest(Path(__file__)),
        "checks": checks,
        "count": len(checks),
        "reference_offset": reference_a,
        "reference_absolute_variance": reference_var,
        "cases": {
            name: {
                "status": record["status"],
                "notes": record["notes"],
                "record_digest": record["record_digest"],
            }
            for name, record in cases.items()
        },
    }
    private = ROOT / "artifacts/verification/eiv_independent_review"
    private.mkdir(parents=True, exist_ok=True)
    (ROOT / "research/eiv_independent_review.json").write_text(
        json.dumps(output, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    for name, record in cases.items():
        (private / (name + ".json")).write_text(
            json.dumps(record, indent=2, allow_nan=False) + "\n", encoding="utf-8"
        )
    print(
        json.dumps(
            {
                "status": output["status"],
                "checks": len(checks),
                "cases": len(cases),
                "reference_offset": reference_a,
                "reference_absolute_variance": reference_var,
            }
        )
    )


if __name__ == "__main__":
    main()
