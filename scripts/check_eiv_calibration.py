"""Execute independent numeric references and preserve complete private native receipts."""

from __future__ import annotations

import argparse
import json
import math
import runpy
import sys
from copy import deepcopy
from importlib.metadata import distribution, version
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from pipeline_integrity.calibration import (  # noqa: E402
    CalibrationProvider,
    boundary_metadata,
    fit_calibration,
    model_identity,
    predict_calibration,
    verify_calibration_record,
)
from pipeline_integrity.contracts import digest  # noqa: E402
from pipeline_integrity.datasets import DatasetSpec, file_digest  # noqa: E402

MAKE_CASE = runpy.run_path(str(ROOT / "examples/calibration.py"))["make_case"]


def native_json(value):
    if isinstance(value, np.ndarray):
        return native_json(value.tolist())
    if isinstance(value, np.generic):
        return native_json(value.item())
    if isinstance(value, dict):
        return {k: native_json(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [native_json(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return {"unusable_native_nonfinite": repr(value)}
    return value


def exponential(x, parameters, configuration):
    return parameters["offset"] * np.exp(parameters["slope"] * x)


def recontext(q, c):
    return c.model_copy(update={"metadata": {**c.metadata, "calibration_boundary": boundary_metadata(q)}})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts", type=Path, default=ROOT / "artifacts/calibration")
    parser.add_argument(
        "--source-inputs", type=Path, default=ROOT / "artifacts/calibration_data/calibration_inputs.json"
    )
    parser.add_argument("--catalog-file", type=Path, help="optional reviewed source specification catalog")
    parser.add_argument(
        "--odr-reference",
        action="store_true",
        help="optional separate binary/library reference, not production",
    )
    args = parser.parse_args()
    args.artifacts.mkdir(parents=True, exist_ok=True)
    checks, cases, records = {}, {}, {}

    def run(name, q, c, p):
        r = fit_calibration(q, c, p)
        records[name] = r
        path = args.artifacts / (name + ".json")
        path.write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")
        checks[name + ":stored_replay"] = verify_calibration_record(
            json.loads(path.read_text(encoding="utf-8")), p, request=q, context=c
        )["valid"]
        checks[name + ":input_binding"] = r["input_digest"] == digest({"request": q, "context": c})
        checks[name + ":source_binding"] = r["adapter_sha256"] == file_digest(
            ROOT / "src/pipeline_integrity/calibration.py"
        )
        cases[name] = {
            "status": r["status"],
            "native_called": r["native_called"],
            "model_calls": r["model_calls"],
            "fit_count": len(r["values"]["fit_ids"]) if r["values"] else None,
            "record_digest": r["record_digest"],
            "audits": r["audits"],
            "notes": r["notes"],
        }
        return r

    q, c, p = MAKE_CASE(reviewed=True)
    baseline = run("authored_deming", q, c, p)
    checks["deming:computed"] = baseline["status"] == "computed"
    x, y = np.array([v.x for v in q.observations]), np.array([v.y for v in q.observations])
    xx, yy = x - x.mean(), y - y.mean()
    sxx, syy, sxy = xx @ xx, yy @ yy, xx @ yy
    slope = (syy - 4 * sxx + np.sqrt((syy - 4 * sxx) ** 2 + 16 * sxy**2)) / (2 * sxy)
    beta = baseline["values"]["parameters"]
    checks["deming:independent_closed_form_slope"] = abs(beta["slope"] - slope) < 1e-8
    checks["deming:independent_closed_form_intercept"] = (
        abs(beta["offset"] - (y.mean() - slope * x.mean())) < 1e-7
    )
    checks["deming:degree_count"] = baseline["audits"]["degrees_of_freedom"] == 4
    checks["deming:absolute_not_residual_scaled"] = baseline["audits"]["covariance_residual_scaled"] is False
    n = len(x)
    u = np.array(q.covariance.values)
    scaled = q.model_copy(
        update={"covariance": q.covariance.model_copy(update={"values": tuple(map(tuple, 9 * u))})}
    )
    scaled_r = run("uncertainty_scaled", scaled, recontext(scaled, c), p)
    checks["scale:parameters_invariant"] = np.allclose(
        list(beta.values()), list(scaled_r["values"]["parameters"].values()), rtol=1e-7, atol=1e-8
    )
    checks["scale:covariance_times_nine"] = np.allclose(
        scaled_r["values"]["parameter_standard_covariance"],
        9 * np.array(baseline["values"]["parameter_standard_covariance"]),
        rtol=1e-6,
        atol=1e-10,
    )
    checks["scale:objective_divided_nine"] = (
        abs(scaled_r["values"]["weighted_sum_squares"] * 9 - baseline["values"]["weighted_sum_squares"])
        < 1e-10
    )

    exact_u = np.zeros_like(u)
    exact_u[n:, n:] = u[n:, n:]
    exact = q.model_copy(
        update={
            "covariance": q.covariance.model_copy(update={"values": tuple(map(tuple, exact_u))}),
            "exact_x_ids": tuple(v.id for v in q.observations),
            "exact_x_evidence_id": "source",
        }
    )
    exact_r = run("explicit_exact_input", exact, recontext(exact, c), p)
    design = np.column_stack((np.ones(n), x))
    checks["exact:linear_solution"] = np.allclose(
        list(exact_r["values"]["parameters"].values()), np.linalg.lstsq(design, y, rcond=None)[0], rtol=1e-8
    )
    checks["exact:absolute_covariance"] = np.allclose(
        exact_r["values"]["parameter_standard_covariance"], 0.04 * np.linalg.inv(design.T @ design), rtol=1e-8
    )
    checks["exact:no_latent_x"] = exact_r["native_calls"][0]["latent_x_ids"] == []
    nonlinear = CalibrationProvider("exponential", "1", "1", "1", {"offset": "1", "slope": "1"}, exponential)
    eq = exact.model_copy(
        update={
            "model_id": "exponential",
            **model_identity(nonlinear),
            "observations": tuple(
                v.model_copy(update={"y": float(2 * np.exp(0.3 * v.x))}) for v in q.observations
            ),
        }
    )
    er = run("nonlinear_exponential", eq, recontext(eq, c), nonlinear)
    jj = np.column_stack((np.exp(0.3 * x), 2 * x * np.exp(0.3 * x)))
    checks["nonlinear:known_parameters"] = np.allclose(
        list(er["values"]["parameters"].values()), [2, 0.3], rtol=1e-7
    )
    checks["nonlinear:independent_information"] = np.allclose(
        er["values"]["parameter_standard_covariance"], 0.04 * np.linalg.inv(jj.T @ jj), rtol=1e-5
    )

    shared_vector = np.r_[np.full(n, 0.04), np.full(n, 0.08)]
    shared = q.model_copy(
        update={
            "covariance": q.covariance.model_copy(
                update={"values": tuple(map(tuple, u + np.outer(shared_vector, shared_vector)))}
            )
        }
    )
    shared_r = run("shared_cross_axis_covariance", shared, recontext(shared, c), p)
    checks["shared:retained_off_diagonal"] = (
        abs(shared_r["native_calls"][0]["fit_covariance"][0][-1] - 0.0032) < 1e-12
    )
    rows = tuple(
        v.model_copy(update={"role": "validation", "run_id": "heldout-run"}) if i >= 4 else v
        for i, v in enumerate(q.observations)
    )
    hold = q.model_copy(
        update={"observations": rows, "split_policy": "separate_runs", "require_independent_validation": True}
    )
    hr = run("heldout_original", hold, recontext(hold, c), p)
    changed = hold.model_copy(
        update={
            "observations": tuple(
                v.model_copy(update={"y": v.y + 1000.0}) if v.role == "validation" else v
                for v in hold.observations
            )
        }
    )
    cr = run("heldout_changed_only", changed, recontext(changed, c), p)
    checks["holdout:parameters_unchanged"] = hr["values"]["parameters"] == cr["values"]["parameters"]
    checks["holdout:native_fit_inputs_and_outputs_identical"] = hr["native_calls"] == cr["native_calls"]
    checks["holdout:large_residual_observable"] = (
        min(abs(v) for v in cr["values"]["validation"]["residual_y"]) > 999
    )
    checks["holdout:separate_error_declaration"] = (
        hr["values"]["validation"]["statistically_independent_errors"] is True
    )
    same = hold.model_copy(
        update={
            "observations": tuple(v.model_copy(update={"run_id": "authored-run"}) for v in hold.observations)
        }
    )
    checks["holdout:same_run_refused"] = (
        run("withheld_same_run", same, recontext(same, c), p)["status"] == "out_of_scope"
    )
    singular = u.copy()
    singular[0, 1] = singular[1, 0] = 0.01
    sq = q.model_copy(
        update={"covariance": q.covariance.model_copy(update={"values": tuple(map(tuple, singular))})}
    )
    checks["singular:no_repair"] = (
        run("withheld_singular", sq, recontext(sq, c), p)["status"] == "out_of_scope"
    )
    dq, dc, dp = MAKE_CASE()
    withheld = run("unreviewed", dq, dc, dp)
    checks["unreviewed:no_backend"] = (
        withheld["backend"] is None and not withheld["native_called"] and withheld["model_calls"] == 0
    )
    predicted = predict_calibration(baseline, [2.0, 4.0], c, p)
    checks["application:no_hidden_input_uncertainty"] = predicted["uncertainty"] is None
    try:
        predict_calibration(baseline, [7.0], c, p)
        checks["application:no_extrapolation"] = False
    except ValueError:
        checks["application:no_extrapolation"] = True
    for key in ("parameters", "covariance", "native_result", "backend", "split", "context", "status"):
        damaged = deepcopy(baseline)
        if key == "parameters":
            damaged["values"]["parameters"]["slope"] += 0.1
        elif key == "covariance":
            damaged["values"]["parameter_standard_covariance"][0][0] *= 2
        elif key == "native_result":
            damaged["native_calls"][0]["result"]["cost"] += 1
        elif key == "backend":
            damaged["backend"]["shared_inverse_sha256"] = "a" * 64
        elif key == "split":
            damaged["request"]["observations"][-1]["role"] = "validation"
        elif key == "context":
            damaged["context"]["clock_id"] = "other"
        else:
            damaged["status"] = "needs_data"
        damaged["record_digest"] = digest({k: v for k, v in damaged.items() if k != "record_digest"})
        checks["tamper:" + key] = not verify_calibration_record(damaged, p)["valid"]

    source_summary = None
    if args.source_inputs.exists():
        from pipeline_integrity.calibration_data import (
            ARCHIVE_NAME,
            EMUE_ID,
            emue_calibration_inputs,
            read_emue_calibration,
        )

        spec = None
        if args.catalog_file is not None:
            specs = [
                DatasetSpec.model_validate_json(json.dumps(row))
                for row in json.loads(args.catalog_file.read_text(encoding="utf-8"))["datasets"]
                if row["id"] == EMUE_ID
            ]
            if len(specs) != 1:
                raise ValueError("one reviewed calibration source specification required")
            spec = specs[0]
        raw_root = ROOT / "data/raw"
        source_record = read_emue_calibration(raw_root / EMUE_ID / ARCHIVE_NAME, source_spec=spec)
        source = emue_calibration_inputs(source_record, source_root=raw_root, source_spec=spec)
        cached = json.loads(args.source_inputs.read_text(encoding="utf-8"))
        checks["source:cached_inputs_match_pinned_archive"] = digest(cached) == digest(source)
        if not checks["source:cached_inputs_match_pinned_archive"]:
            raise ValueError("cached source inputs differ from the freshly re-read supplier-pinned archive")
        source_q, source_c, source_p = MAKE_CASE(reviewed=True, source_inputs=source)
        sr = run("emue_published_full_covariance", source_q, source_c, source_p)
        sv = sr["values"]
        checks["source:thirty_paired_points"] = len(sv["fit_ids"]) == 30
        checks["source:all_cross_correlations_preserved"] = (
            np.count_nonzero(
                np.array(source_q.covariance.values) - np.diag(np.diag(source_q.covariance.values))
            )
            == 3540
        )
        checks["source:published_rounded_offset"] = abs(sv["parameters"]["offset"] - 0.99663) < 5e-6
        checks["source:published_rounded_slope"] = abs(sv["parameters"]["slope"] + 3.5267) < 5e-5
        checks["source:published_rounded_u_offset"] = (
            abs(sv["parameter_standard_uncertainty"][0] - 0.000961) < 5e-7
        )
        checks["source:published_rounded_u_slope"] = (
            abs(sv["parameter_standard_uncertainty"][1] - 0.04827) < 5e-6
        )
        checks["source:published_rounded_covariance"] = (
            abs(sv["parameter_standard_covariance"][0][1] + 2.53e-5) < 5e-8
        )
        checks["source:one_campaign_no_validation"] = (
            sv["validation"] is None and len({v.run_id for v in source_q.observations}) == 1
        )
        source_summary = {
            "asset_sha256": source["source_asset_sha256"],
            "source_record_digest": source["source_record_digest"],
            "model_source_sha256": source_q.provider_source_sha256,
            "fit_count": 30,
            "comparison": "published rounded numerical reference, not independent experimental validation",
            "parameters": sv["parameters"],
            "standard_uncertainty": sv["parameter_standard_uncertainty"],
            "parameter_covariance": sv["parameter_standard_covariance"],
            "weighted_sum_squares": sv["weighted_sum_squares"],
            "independent_validation": False,
        }

    odr_summary = None
    if args.odr_reference:
        from odrpack import odr_fit

        fit = odr_fit(
            lambda xx, b: b[0] + b[1] * xx,
            x,
            y,
            np.array([1.0, 2.0]),
            weight_x=100.0,
            weight_y=25.0,
            report="none",
            maxit=200,
        )
        fields = native_json(vars(fit))
        (args.artifacts / "optional_odr_deming_reference.json").write_text(
            json.dumps(fields, indent=2), encoding="utf-8"
        )
        checks["odr:actual_native_success"] = bool(fit.success)
        checks["odr:deming_parameter_agreement"] = np.allclose(
            fit.beta, list(beta.values()), rtol=1e-6, atol=1e-7
        )
        checks["odr:raw_irank_is_zero_for_full_rank"] = fit.irank == 0
        checks["odr:sd_is_residual_scaled"] = np.allclose(
            fit.sd_beta**2, np.diag(fit.cov_beta) * fit.res_var, rtol=1e-9
        )
        # This failed probe is retained rather than manufacturing repeated observations.
        z = np.r_[x, y].reshape(2 * n, 1)

        def implicit(zdata, b):
            return zdata[n:] - b[0] - b[1] * zdata[:n]

        failed = odr_fit(
            implicit,
            z,
            np.zeros((n, 1)),
            np.array([1.0, 2.0]),
            weight_x=np.linalg.inv(u),
            task="implicit-ODR",
            report="none",
            maxit=100,
        )
        failure = native_json(vars(failed))
        (args.artifacts / "optional_odr_full_vector_failed.json").write_text(
            json.dumps(failure, indent=2), encoding="utf-8"
        )
        checks["odr:full_vector_failure_not_accepted"] = not failed.success and failed.info == 10010
        dist = distribution("odrpack")
        binaries = {
            str(v).replace("\\", "/"): file_digest(Path(dist.locate_file(v)))
            for v in dist.files
            if str(v).endswith((".pyd", ".dll"))
        }
        odr_summary = {
            "version": version("odrpack"),
            "native_binary_sha256": binaries,
            "full_vector_failure_info": int(failed.info),
            "irank_meaning": "preserved native rank deficiency; zero does not mean zero fitted rank",
            "license": "Python/ODRPACK95 MIT; compiler runtime DLLs retain separate supplier licenses and exceptions; optional installed reference only",
        }

    source_files = [
        "src/pipeline_integrity/calibration.py",
        "tests/test_calibration.py",
        "examples/calibration.py",
        "scripts/check_eiv_calibration.py",
        "docs/EIV_CALIBRATION_KO.md",
    ]
    aggregate = {
        "schema": "pipeline_integrity.calibration.verification.v1",
        "checks": checks,
        "passed": sum(bool(v) for v in checks.values()),
        "total": len(checks),
        "requests": len(cases),
        "computed": sum(v["status"] == "computed" for v in cases.values()),
        "cases": cases,
        "source_application": source_summary,
        "optional_odr_reference": odr_summary,
        "backend": baseline["backend"],
        "files": {v: file_digest(ROOT / v) for v in source_files if (ROOT / v).exists()},
        "limitations": [
            "No independent field/custody certification; public source is one fit-only campaign.",
            "Positive-definite stochastic covariance; only explicitly exact abscissae can be removed from stochastic coordinates.",
            "Parameter covariance is local Gauss-Newton nuisance marginalization, unscaled by residuals; no automatic coverage probability.",
            "Source callbacks must be deterministic; hashes/replay are not signatures or execution attestation.",
        ],
    }
    aggregate = native_json(aggregate)
    (ROOT / "research/eiv_calibration_results.json").write_text(
        json.dumps(aggregate, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "passed": aggregate["passed"],
                "total": aggregate["total"],
                "requests": aggregate["requests"],
                "computed": aggregate["computed"],
            }
        )
    )
    if aggregate["passed"] != aggregate["total"]:
        raise SystemExit("calibration verification failed")


if __name__ == "__main__":
    main()
