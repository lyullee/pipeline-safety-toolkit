"""Exercise seven owner families, causal references and a shared source holdout."""

import argparse
import importlib.metadata
import json
import math
import re
import runpy
import sys
from copy import deepcopy
from pathlib import Path

# Also run under a separate monitoring Python without installing this source tree.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pipeline_integrity.contracts import EvidenceRef, RunContext, SignalFrame, digest  # noqa: E402
from pipeline_integrity.datasets import catalog, file_digest  # noqa: E402
from pipeline_integrity.monitoring_exchange import (  # noqa: E402
    REVIEWS,
    MonitoringSplit,
    NativeMonitoringClient,
    PythonMonitoringClient,
    _neural_runtime,
    evaluate_monitoring,
    monitoring_source_fingerprint,
    verify_monitoring_record,
)

OPTIONS = {
    "seasonal": {"period": 4},
    "pca": {"n_components": 2},
    "fuzzy_cmeans": {"n_clusters": 2, "max_iter": 20},
    "kmedoids": {"n_clusters": 2, "max_iter": 10},
    "isolation_forest": {"n_estimators": 16, "n_jobs": 1},
    "lof": {"n_neighbors": 5},
    "knn_distance": {"n_neighbors": 3},
    "kmeans": {"n_clusters": 2, "n_init": 2},
    "gmm": {"n_components": 2},
    "genetic_kmeans": {"n_clusters": 2, "population": 4, "generations": 2},
    "genetic_fuzzy_cmeans": {"n_clusters": 2, "population": 4, "generations": 2},
    "loda": {"n_random_cuts": 10, "random_state": 0},
    "sod": {"n_neighbors": 8, "ref_set": 4},
    "cblof": {"n_clusters": 2, "alpha": 0.5, "beta": 2, "random_state": 0},
    "feature_bagging": {"n_estimators": 3, "random_state": 0},
    "pca_pyod": {"n_components": 2, "n_selected_components": 1, "random_state": 0},
    "inne": {"n_estimators": 10, "max_samples": 16, "random_state": 0},
    "arima": {"maxiter": 100},
    "lag_ridge": {"window": 4},
    "lag_ard": {"window": 4},
    "lag_bayesian_ridge": {"window": 4},
    "dynamic_pca": {"window": 4, "n_components": 2},
}
NEURAL_METHODS = "autoencoder sparse_autoencoder denoising_autoencoder vae gan diffusion".split()
TEMPORAL_METHODS = "rnn lstm gru cnn tcn transformer cnn_lstm lstm_autoencoder graph_conv graph_attention graphsage conv_lstm deep_graph_infomax resnet".split()
for _method in NEURAL_METHODS + TEMPORAL_METHODS:
    OPTIONS[_method] = {"hidden": 4, "epochs": 2, "batch_size": 16, "seed": 73}
    if _method in TEMPORAL_METHODS:
        OPTIONS[_method].update(window=4, max_train_windows=48)
    elif _method != "diffusion":
        OPTIONS[_method]["latent"] = 2
    if _method == "gan":
        OPTIONS[_method]["latent_steps"] = 2
    if _method == "diffusion":
        OPTIONS[_method].update(steps=4, noise_levels=[0.25, 0.75])
    if _method in {"graph_conv", "graph_attention", "graphsage", "deep_graph_infomax"}:
        OPTIONS[_method]["adjacency"] = [[0.0, 1.0, 0.0], [1.0, 0.0, 1.0], [0.0, 1.0, 0.0]]
    if _method == "conv_lstm":
        OPTIONS[_method]["spatial_shape"] = [1, 3]


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n", encoding="utf-8")


def numerical_splits(values, context):
    result, start = [], 0
    for role, rows in zip(("training", "calibration", "evaluation"), values, strict=True):
        frames = tuple(
            SignalFrame(
                stream_id="analytic-reference",
                clock_id=context.clock_id,
                time_s=float(start + i),
                values={f"x{j}": value for j, value in enumerate(row)},
                units={f"x{j}": "1" for j in range(len(row))},
                valid={f"x{j}": value is not None for j, value in enumerate(row)},
                origin="synthetic",
            )
            for i, row in enumerate(rows)
        )
        result.append(
            MonitoringSplit(
                role=role,
                split_id="analytic-" + role,
                physical_run_id="analytic-run",
                source_group_id="analytic-source",
                source_evidence_id=context.evidence[0].id,
                observation_ids=tuple(f"analytic-{start + i}" for i in range(len(rows))),
                frames=frames,
                reference_kind="not_used" if role == "evaluation" else "caller_declared_reference",
            )
        )
        start += len(rows)
    return tuple(result)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--monitoring", type=Path, required=True)
    parser.add_argument("--raw-root", type=Path, default=Path("data/raw"))
    parser.add_argument("--worker-python", type=Path)
    args = parser.parse_args()
    sys.dont_write_bytecode = True
    source_before = monitoring_source_fingerprint(args.monitoring)
    pin = source_before["source_tree_sha256"]
    client = NativeMonitoringClient(args.monitoring)
    make = runpy.run_path("examples/monitoring_exchange.py")["make_case"]
    manifest, records, failures, refs, refusals = {}, {}, {}, {}, []
    names = {
        "statistical": "robust_z zscore ewma cusum gaussian seasonal distribution_tail".split(),
        "classical": "pca fuzzy_cmeans kmedoids isolation_forest one_class_svm lof knn_distance kmeans gmm ard bayesian_ridge ridge_residual genetic_kmeans genetic_fuzzy_cmeans".split(),
        "forecasting": "arima lag_ridge lag_ard lag_bayesian_ridge".split(),
        "modern": "ecod copod hbos abod loda sod sos loci cblof feature_bagging pca_pyod rod inne".split(),
        "dynamic_subspace": ["dynamic_pca"],
        "neural": NEURAL_METHODS,
        "temporal_neural": TEMPORAL_METHODS,
    }
    folder = Path("artifacts/monitoring_exchange")

    def ready(method, family, parameters):
        description = client.describe(method, expected_source_sha256=pin)
        manifest[method] = description
        return make(
            method,
            family,
            owner_version=description["owner_version"],
            native_spec_digest=description["native_spec_digest"],
            source_sha256=pin,
            parameters=parameters,
        )

    def evaluate(name, request, phases, context, selected_client=client):
        record = evaluate_monitoring(request, phases, context, client=selected_client)
        verify_monitoring_record(record)
        records[name] = record
        save(folder / "in_progress" / (name + ".json"), record)
        return record

    def reference(name, actual, expected, tolerance=1e-8, kind="independently_authored"):
        if expected is None or isinstance(expected, bool):
            assert actual is expected, (name, actual, expected)
            error = 0.0
        else:
            assert math.isclose(actual, expected, rel_tol=tolerance, abs_tol=tolerance), (
                name,
                actual,
                expected,
            )
            error = abs(actual - expected)
        refs[name] = dict(actual=actual, expected=expected, absolute_error=error, kind=kind)

    for family, methods in names.items():
        for method in methods:
            try:
                request, phases, context = ready(method, family, OPTIONS.get(method, {}))
                phases = tuple(
                    s.model_copy(
                        update={
                            "frames": s.frames[: 48 if s.role == "training" else 16],
                            "observation_ids": s.observation_ids[: 48 if s.role == "training" else 16],
                        }
                    )
                    for s in phases
                )
                assert evaluate("smoke_" + method, request, phases, context)["status"] == "computed"
                print(json.dumps(dict(method=method, status="executed")), flush=True)
            except Exception as error:
                failures[method] = dict(error_type=type(error).__name__, private_error=str(error))
                print(
                    json.dumps(dict(method=method, status="unverified", error_type=type(error).__name__)),
                    flush=True,
                )
    request, _, context = ready("zscore", "statistical", {})
    zphases = numerical_splits(
        (
            [[v] for v in (-2.0, -1.0, 0.0, 0.0, 1.0, 2.0)],
            [[-1.0], [0.0], [1.0]],
            [[0.0], [2.0], [None], [4.0]],
        ),
        context,
    )
    z = evaluate("analytic_zscore", request, zphases, context)
    scores = z["native_receipt"]["evaluation"]["scores"]
    for name, actual, expected in (
        ("z_zero", scores[0], 0.0),
        ("z_two", scores[1], math.sqrt(2)),
        ("z_missing", scores[2], None),
        ("z_four", scores[3], 2 * math.sqrt(2)),
        ("z_threshold", z["native_receipt"]["evaluation"]["threshold"], 1 / math.sqrt(2)),
    ):
        reference(name, actual, expected)
    request, _, context = ready("knn_distance", "classical", {"n_neighbors": 1})
    phases = numerical_splits(
        ([[v] for v in (-2.0, 0.0, 2.0)], [[-1.0], [0.0], [1.0]], [[2.5], [10.0], [-3.0]]), context
    )
    knn = evaluate("analytic_nearest_distance", request, phases, context)
    for i, expected in enumerate((0.5, 8.0, 1.0)):
        reference(f"nearest_distance_{i}", knn["native_receipt"]["evaluation"]["scores"][i], expected)
    request, _, context = ready("lag_ridge", "forecasting", {"window": 2, "alpha": 0.0})
    phases = numerical_splits(
        (
            [[v, 2 * v] for v in (-1.0, 1.0) * 8],
            [[-1.0, -2.0], [1.0, 2.0], [-1.0, -2.0], [1.0, 2.0]],
            [[-1.0, -2.0], [1.0, 2.0], [3.0, 6.0], [1.0, 2.0]],
        ),
        context,
    )
    lag = evaluate("analytic_lag_ridge", request, phases, context)
    lscores = lag["native_receipt"]["evaluation"]["scores"]
    reference("lag_warmup_is_unknown", lscores[0], None)
    reference("lag_first_complete_endpoint", lscores[1], 0.0)
    reference("lag_authored_departure", lscores[2], 4000.0)
    reference("lag_uses_past_observation", lscores[3], 4000.0)
    request, _, context = ready("hbos", "modern", {"n_bins": 4, "alpha": 0.1})
    phases = numerical_splits(
        ([[v] for v in (-1.0, -1 / 3, 1 / 3, 1.0) * 2], [[-0.5], [0.5], [0.2]], [[-0.25], [0.25], [0.75]]),
        context,
    )
    hbos = evaluate("analytic_histogram_density", request, phases, context)
    for i, value in enumerate(hbos["native_receipt"]["evaluation"]["scores"]):
        reference(f"four_equal_density_bins_{i}", value, -math.log2(0.6))
    # The lagged training covariance has eigenvectors (1,1) and (1,-1).
    # Its per-coordinate population variance is 3/2 and residual variance 1/3.
    dynamic_phases = numerical_splits(
        (
            [[v] for v in (-2.0, -1.0, 0.0, 1.0, 2.0, 1.0, 0.0, -1.0, -2.0)],
            [[v] for v in (-1.0, 0.0, 1.0, 0.0, -1.0)],
            [[v] for v in (1.0, 1.0, -1.0, 1.0, None, -1.0, 1.0)],
        ),
        context,
    )
    for statistic in ("spe", "t2"):
        request, _, context = ready(
            "dynamic_pca", "dynamic_subspace", {"window": 2, "n_components": 1, "statistic": statistic}
        )
        dynamic = evaluate("analytic_dynamic_pca_" + statistic, request, dynamic_phases, context)
        scores = dynamic["native_receipt"]["evaluation"]["scores"]
        expected = (
            (None, 0.0, 4.0, 4.0, None, None, 4.0)
            if statistic == "spe"
            else (None, 0.8, 0.0, 0.0, None, None, 0.0)
        )
        for i, value in enumerate(expected):
            reference(f"dynamic_pca_{statistic}_{i}", scores[i], value)

    import torch

    runtime_before = (
        torch.get_num_threads(),
        torch.are_deterministic_algorithms_enabled(),
        torch.is_deterministic_algorithms_warn_only_enabled(),
        str(torch.get_default_device()),
    )
    cpu_rng_before = torch.random.get_rng_state().clone()

    def compact(stages):
        return tuple(
            s.model_copy(
                update={
                    "frames": s.frames[: 48 if s.role == "training" else 16],
                    "observation_ids": s.observation_ids[: 48 if s.role == "training" else 16],
                }
            )
            for s in stages
        )

    def same_scores(first, second):
        return len(first) == len(second) and all(
            a is b if a is None or b is None else math.isclose(a, b, rel_tol=1e-6, abs_tol=1e-7)
            for a, b in zip(first, second, strict=True)
        )

    for family, methods in (("neural", NEURAL_METHODS), ("temporal_neural", TEMPORAL_METHODS)):
        for method in methods:
            if "smoke_" + method not in records:
                continue
            request, stages, context = ready(method, family, OPTIONS[method])
            stages = compact(stages)
            baseline = records["smoke_" + method]["native_receipt"]
            repeated = evaluate("seed_replay_" + method, request, stages, context)["native_receipt"]
            reference(
                method + "_seeded_fit_reproducible",
                baseline["neural_training"]["before_queries"] == repeated["neural_training"]["before_queries"]
                and baseline["neural_training"]["loss_history"] == repeated["neural_training"]["loss_history"]
                and same_scores(baseline["evaluation"]["scores"], repeated["evaluation"]["scores"]),
                True,
                kind="neural_seed_reproducibility",
            )
            tail = stages[2].frames[-1]
            changed_tail = tail.model_copy(update={"values": dict(tail.values, sensor_a=10000.0)})
            changed_stages = (
                *stages[:2],
                stages[2].model_copy(update={"frames": (*stages[2].frames[:-1], changed_tail)}),
            )
            changed = evaluate("neural_changed_future_" + method, request, changed_stages, context)[
                "native_receipt"
            ]
            reference(
                method + "_evaluation_cannot_fit_or_calibrate",
                baseline["neural_training"]["before_queries"] == changed["neural_training"]["before_queries"]
                and baseline["calibration"] == changed["calibration"],
                True,
                kind="causal_holdout_invariant",
            )
            reference(
                method + "_future_cannot_change_prefix",
                same_scores(baseline["evaluation"]["scores"][:-1], changed["evaluation"]["scores"][:-1]),
                True,
                kind="causal_holdout_invariant",
            )
    for family, methods in (("dynamic_subspace", ["dynamic_pca"]), ("temporal_neural", TEMPORAL_METHODS)):
        for method in methods:
            if "smoke_" + method not in records:
                continue
            request, stages, context = ready(method, family, dict(OPTIONS[method], max_gap_s=1.5))
            stages = compact(stages)
            altered = []
            for i, frame in enumerate(stages[2].frames):
                values, valid = dict(frame.values), dict(frame.valid)
                if i == 6:
                    values["sensor_a"], valid["sensor_a"] = None, False
                altered.append(
                    frame.model_copy(
                        update={
                            "values": values,
                            "valid": valid,
                            "time_s": frame.time_s + (100.0 if i >= 8 else 0.0),
                            "operating_mode": "changed-mode" if i >= 12 else "authored-initial-mode",
                        }
                    )
                )
            history = evaluate(
                "new_sequence_missing_gap_mode_" + method,
                request,
                (*stages[:2], stages[2].model_copy(update={"frames": tuple(altered)})),
                context,
            )
            reference(
                method + "_missing_gap_mode_endpoints",
                history["native_receipt"]["evaluation"]["valid"]
                == [False] * 3 + [True] * 3 + [False] * 5 + [True] + [False] * 3 + [True],
                True,
                kind="history_availability_contract",
            )
    runtime_after = (
        torch.get_num_threads(),
        torch.are_deterministic_algorithms_enabled(),
        torch.is_deterministic_algorithms_warn_only_enabled(),
        str(torch.get_default_device()),
    )
    reference(
        "neural_native_calls_restore_cpu_rng_threads_and_flags",
        bool(torch.equal(cpu_rng_before, torch.random.get_rng_state())) and runtime_before == runtime_after,
        True,
        kind="neural_runtime_restoration",
    )
    try:
        with _neural_runtime(1):
            torch.manual_seed(9876)
            torch.rand(3)
            raise RuntimeError("authored CPU context failure")
    except RuntimeError as error:
        assert str(error) == "authored CPU context failure"
    reference(
        "neural_exception_restores_cpu_rng_threads_and_flags",
        bool(torch.equal(cpu_rng_before, torch.random.get_rng_state()))
        and torch.get_num_threads() == runtime_before[0]
        and torch.are_deterministic_algorithms_enabled() == runtime_before[1]
        and torch.is_deterministic_algorithms_warn_only_enabled() == runtime_before[2]
        and str(torch.get_default_device()) == runtime_before[3],
        True,
        kind="neural_runtime_restoration",
    )
    request, phases, context = ready("ecod", "modern", {})
    baseline = evaluate("querywise_baseline", request, phases, context)
    tail = deepcopy(phases[2].frames[-1])
    tail = tail.model_copy(update={"values": dict.fromkeys(tail.values, 10000.0)})
    changed = (*phases[:2], phases[2].model_copy(update={"frames": (*phases[2].frames[:-1], tail)}))
    poisoned = evaluate("querywise_changed_future", request, changed, context)
    reference(
        "evaluation_cannot_change_calibration_threshold",
        poisoned["native_receipt"]["evaluation"]["threshold"],
        baseline["native_receipt"]["evaluation"]["threshold"],
        kind="causal_holdout_invariant",
    )
    for i, (a, b) in enumerate(
        zip(
            baseline["native_receipt"]["evaluation"]["scores"][:-1],
            poisoned["native_receipt"]["evaluation"]["scores"][:-1],
            strict=True,
        )
    ):
        reference(f"querywise_prefix_{i}", a, b, kind="causal_holdout_invariant")
    history_request, history_phases, history_context = ready(
        "lag_ridge", "forecasting", {"window": 4, "max_gap_s": 1.5}
    )
    history_frames = list(history_phases[2].frames[:16])
    missing_frame = history_frames[6]
    history_frames[6] = missing_frame.model_copy(
        update={
            "values": dict(missing_frame.values, sensor_a=None),
            "valid": dict(missing_frame.valid, sensor_a=False),
        }
    )
    missing_phases = (
        *history_phases[:2],
        history_phases[2].model_copy(
            update={
                "frames": tuple(history_frames),
                "observation_ids": history_phases[2].observation_ids[:16],
            }
        ),
    )
    missing_record = evaluate("causal_missing_window", history_request, missing_phases, history_context)
    reference(
        "missing_window_endpoints_preserved",
        missing_record["native_receipt"]["evaluation"]["valid"]
        == [False] * 3 + [True] * 3 + [False] * 4 + [True] * 6,
        True,
        kind="history_availability_contract",
    )
    gap_frames = tuple(
        f.model_copy(update={"time_s": f.time_s + (100.0 if i >= 8 else 0.0)})
        for i, f in enumerate(history_phases[2].frames[:16])
    )
    gap_phases = (
        *history_phases[:2],
        history_phases[2].model_copy(
            update={"frames": gap_frames, "observation_ids": history_phases[2].observation_ids[:16]}
        ),
    )
    gap_record = evaluate("causal_gap_window", history_request, gap_phases, history_context)
    reference(
        "gap_window_endpoints_preserved",
        gap_record["native_receipt"]["evaluation"]["valid"]
        == [False] * 3 + [True] * 5 + [False] * 3 + [True] * 5,
        True,
        kind="history_availability_contract",
    )
    for label, update in (
        ("missing_parameters", {"parameters": None}),
        ("unreviewed", {"applicability_reviews": {}}),
    ):
        assert evaluate(label, request.model_copy(update=update), phases, context)["native_receipt"] is None

    def refuse(name, candidate, stages):
        try:
            evaluate_monitoring(candidate, stages, context, client=client)
        except ValueError:
            refusals.append(name)
        else:
            raise AssertionError("invalid monitoring exchange accepted: " + name)

    overlap = phases[2].model_copy(
        update={"observation_ids": (*phases[2].observation_ids[:-1], phases[0].observation_ids[0])}
    )
    refuse("observation_leakage", request, (*phases[:2], overlap))
    refuse("wrong_owner_pin", request.model_copy(update={"owner_source_tree_sha256": "0" * 64}), phases)
    refuse("wrong_owner_version", request.model_copy(update={"expected_owner_version": "changed"}), phases)
    refuse("wrong_native_spec", request.model_copy(update={"expected_native_spec_digest": "0" * 64}), phases)
    refuse("row_budget", request.model_copy(update={"maximum_rows_per_split": 10}), phases)
    refuse("numeric_nan", request.model_copy(update={"parameters": {"bad": float("nan")}}), phases)
    one_request, _, _ = ready("lag_ridge", "forecasting", {"window": 2, "alpha": 0.0})
    one_phases = numerical_splits(
        ([[v] for v in (-1.0, 1.0) * 8], [[-1.0], [1.0], [-1.0], [1.0]], [[-1.0], [1.0], [3.0], [1.0]]),
        context,
    )
    refuse("native_lag_ridge_single_channel_shape", one_request, one_phases)
    cb_request, cb_phases, _ = ready(
        "cblof", "modern", {"n_clusters": 3, "alpha": 0.8, "beta": 2, "random_state": 0}
    )
    cb_phases = tuple(
        s.model_copy(
            update={
                "frames": s.frames[: 48 if s.role == "training" else 16],
                "observation_ids": s.observation_ids[: 48 if s.role == "training" else 16],
            }
        )
        for s in cb_phases
    )
    refuse("native_cblof_declared_partition_unsupported", cb_request, cb_phases)
    spec = next(s for s in catalog() if s.id == "battledim")
    source_assets = []
    for asset in spec.assets:
        if asset.filename in {"2018_SCADA_Pressures.csv", "2018_SCADA_Flows.csv", "2018_SCADA_Levels.csv"}:
            path = args.raw_root / "battledim" / asset.filename
            assert path.stat().st_size == asset.bytes
            algorithm, expected = asset.checksum.split(":", 1)
            assert file_digest(path, algorithm) == expected
            source_assets.append(
                dict(
                    filename=asset.filename,
                    sha256=file_digest(path),
                    supplier_checksum=asset.checksum,
                    bytes=asset.bytes,
                )
            )
    from pipeline_integrity.loaders import iter_battledim_frames

    first = next(iter_battledim_frames(args.raw_root / "battledim", 2018, maximum_rows=1))
    selected = tuple(
        next(c for c in first.values if c.startswith(prefix))
        for prefix in ("pressure_head:", "volume_flow:", "water_level:")
    )
    frames = tuple(
        iter_battledim_frames(args.raw_root / "battledim", 2018, selected_channels=selected, maximum_rows=864)
    )
    source_evidence = EvidenceRef(
        id="battledim-original",
        citation=spec.citation,
        origin="simulation",
        license_id=spec.license_id,
        sha256=digest(source_assets),
        limitations=(
            "One released network simulation; reference periods are caller-declared, not verified normal.",
        ),
    )
    reference_evidence = EvidenceRef(
        id="battledim-reference-hypothesis",
        citation="Caller-declared first-day training and second-day calibration; source normality is not established",
        origin="user_declared",
        license_id="MIT",
    )
    source_context = RunContext(
        asset_id="battledim-released-network",
        scenario_id="first-three-days-chronological-holdout",
        clock_id=frames[0].clock_id,
        origin="simulation",
        evidence=(source_evidence, reference_evidence),
        metadata=dict(
            normal_reference_is_verified=False,
            raw_row_order_preserved=True,
            independent_field_validation=False,
        ),
    )
    source_phases = tuple(
        MonitoringSplit(
            role=role,
            split_id="battledim-" + role,
            physical_run_id="one-released-2018-network-run",
            source_group_id="one-battledim-network",
            source_evidence_id=source_evidence.id,
            observation_ids=tuple(f"battledim-2018-row-{i}" for i in range(start, stop)),
            frames=frames[start:stop],
            reference_kind="not_used" if role == "evaluation" else "caller_declared_reference",
        )
        for role, start, stop in (("training", 0, 288), ("calibration", 288, 576), ("evaluation", 576, 864))
    )
    source_outputs = {}
    for family, method, parameters in (
        ("statistical", "zscore", {}),
        ("classical", "pca", {"n_components": 1}),
        ("forecasting", "lag_ridge", {"window": 4}),
        ("modern", "hbos", {"n_bins": 5}),
        ("dynamic_subspace", "dynamic_pca", {"window": 4, "n_components": 2}),
        ("neural", "diffusion", OPTIONS["diffusion"]),
        ("temporal_neural", "deep_graph_infomax", OPTIONS["deep_graph_infomax"]),
    ):
        request, _, _ = ready(method, family, parameters)
        request = request.model_copy(
            update=dict(
                asset_id=source_context.asset_id,
                clock_id=source_context.clock_id,
                population_id="one-released-simulation",
                normalize=True,
                evidence_ids=(source_evidence.id, reference_evidence.id),
                review_evidence_ids=dict.fromkeys(REVIEWS, source_evidence.id)
                | {"reference_and_calibration_population": reference_evidence.id},
            )
        )
        record = evaluate("battledim_" + method, request, source_phases, source_context)
        source_outputs[family] = dict(
            method=method,
            status=record["status"],
            valid_evaluation_rows=sum(record["native_receipt"]["evaluation"]["valid"]),
            evaluation_rows=288,
            source_record_digest=record["record_digest"],
        )
    if args.worker_python:
        worker = PythonMonitoringClient(args.worker_python, args.monitoring)
        for method, family in (
            ("zscore", "statistical"),
            ("diffusion", "neural"),
            ("deep_graph_infomax", "temporal_neural"),
        ):
            request, phases, context = ready(method, family, OPTIONS.get(method, {}))
            transported = evaluate(
                "json_worker_roundtrip_" + method, request, phases, context, selected_client=worker
            )
            direct = evaluate("inprocess_roundtrip_reference_" + method, request, phases, context)
            assert transported["native_receipt"]["evaluation"] == direct["native_receipt"]["evaluation"]
    neural_corruptions = []
    for mutation in (
        "weights",
        "seed",
        "epochs",
        "loss_trace",
        "loss_bool",
        "device",
        "threads",
        "version",
        "pretrained",
        "missing_weights",
    ):
        damaged = deepcopy(records["smoke_diffusion"])
        native = damaged["native_receipt"]
        training = native["neural_training"]
        if mutation == "weights":
            training["after_queries"]["model.0.weight"]["sha256"] = "5" * 64
        elif mutation in {"seed", "epochs"}:
            training[mutation] += 1
        elif mutation == "loss_trace":
            training["loss_history"].pop()
        elif mutation == "loss_bool":
            training["loss_history"][0] = True
        elif mutation == "missing_weights":
            for state_name in ("before_queries", "after_queries"):
                training[state_name] = {
                    k: v for k, v in training[state_name].items() if k in {"center", "scale"}
                }
        else:
            key, value = {
                "device": ("device", "cuda"),
                "threads": ("cpu_threads", True),
                "version": ("torch_version", "changed"),
                "pretrained": ("pretrained_weights_loaded", True),
            }[mutation]
            training["execution"][key] = value
        native["result_digest"] = digest({k: v for k, v in native.items() if k != "result_digest"})
        damaged["record_digest"] = digest({k: v for k, v in damaged.items() if k != "record_digest"})
        try:
            verify_monitoring_record(damaged)
        except ValueError:
            neural_corruptions.append(mutation)
        else:
            raise AssertionError("rehashed native neural corruption accepted: " + mutation)
    doc = Path("docs/MONITORING_EXCHANGE_KO.md").read_text(encoding="utf-8")
    python_blocks = re.findall(r"```python\n(.*?)```", doc, flags=re.S)
    for block in python_blocks:
        exec(compile(block, "MONITORING_EXCHANGE_KO.md", "exec"), {})
    source_after = monitoring_source_fingerprint(args.monitoring)
    assert source_before == source_after
    save(folder / "native_records.json", records)
    save(folder / "independent_references.json", refs)
    save(folder / "unverified_methods.json", failures)
    save(folder / "neural_corruptions_rejected.json", neural_corruptions)
    dependency_names = (
        "torch",
        "fsspec",
        "networkx",
        "setuptools",
        "sympy",
        "filelock",
        "Jinja2",
        "MarkupSafe",
        "mpmath",
        "typing_extensions",
    )
    neural_dependency = {
        "distribution": "torch",
        "version": importlib.metadata.version("torch"),
        "runtime_version": torch.__version__,
        "cuda_build": torch.version.cuda,
        "cuda_available": torch.cuda.is_available(),
        "license_expression": importlib.metadata.metadata("torch").get("License-Expression"),
        "dependency_versions": {name: importlib.metadata.version(name) for name in dependency_names},
        "distribution_sources": [
            "https://pypi.org/project/torch/",
            "https://pytorch.org/get-started/locally/",
        ],
        "binaries_or_weights_redistributed": False,
    }
    metadata_path = folder / "torch_index_metadata.json"
    report_path = folder / "torch_installation_report.json"
    if metadata_path.is_file() and report_path.is_file():
        published = json.loads(metadata_path.read_text(encoding="utf-8"))
        installed = json.loads(report_path.read_text(encoding="utf-8"))
        wheel = published["pypi_windows_cp312"][0]
        item = next(p for p in installed["install"] if p["metadata"]["name"] == "torch")
        if item["metadata"]["version"] == neural_dependency["version"] == published["pypi_version"]:
            assert item["download_info"]["archive_info"]["hashes"]["sha256"] == wheel["digests"]["sha256"]
            neural_dependency["local_installation_observation"] = {
                "filename": wheel["filename"],
                "bytes": wheel["size"],
                "publisher_sha256": wheel["digests"]["sha256"],
                "pip_report_sha256": item["download_info"]["archive_info"]["hashes"]["sha256"],
            }
    save(folder / "neural_dependency_receipt.json", neural_dependency)
    save(
        folder / "runtime_environment.json",
        {
            name: record["native_receipt"]["runtime_versions"]
            for name, record in records.items()
            if record["native_receipt"] is not None
        },
    )
    family_coverage = {
        f: dict(
            registered_methods=methods,
            executed_methods=[m for m in methods if "smoke_" + m in records],
            unverified_methods=[m for m in methods if m in failures],
        )
        for f, methods in names.items()
    }
    summary = dict(
        schema="pipeline_integrity.monitoring_exchange_verification.v1",
        status="partial_with_unverified_methods" if failures else "passed_with_explicit_coverage",
        owner_version="0.1.0",
        family_coverage=family_coverage,
        selected_registered_methods=sum(map(len, names.values())),
        native_case_count=sum(r["native_receipt"] is not None for r in records.values()),
        independent_reference_count=sum(r["kind"] == "independently_authored" for r in refs.values()),
        causal_holdout_invariant_count=sum(r["kind"] == "causal_holdout_invariant" for r in refs.values()),
        history_availability_contract_count=sum(
            r["kind"] == "history_availability_contract" for r in refs.values()
        ),
        neural_seed_reproducibility_count=sum(
            r["kind"] == "neural_seed_reproducibility" for r in refs.values()
        ),
        neural_runtime_restoration_count=sum(
            r["kind"] == "neural_runtime_restoration" for r in refs.values()
        ),
        total_reference_and_invariant_checks=len(refs),
        maximum_reference_absolute_error=max(r["absolute_error"] for r in refs.values()),
        refusal_count=len(refusals),
        refusals=refusals,
        neural_receipt_corruption_count=len(neural_corruptions),
        documentation_python_blocks_executed=len(python_blocks),
        neural_dependency=neural_dependency,
        source_before=source_before,
        source_after=source_after,
        source_unchanged=True,
        native_spec_digests={method: row["native_spec_digest"] for method, row in manifest.items()},
        runtime_versions=records["smoke_zscore"]["native_receipt"]["runtime_versions"],
        source_dataset=dict(
            dataset_id=spec.id,
            citation=spec.citation,
            license_id=spec.license_id,
            version=spec.version,
            assets=source_assets,
            frames=864,
            physical_runs=1,
            split_rows=[288, 288, 288],
            selected_channels=list(selected),
            units=frames[0].units,
            family_applications=source_outputs,
            reference_periods_are_verified_normal=False,
            independent_field_validation=False,
        ),
        score_is_probability_or_HEP=False,
        claimed_family_algorithms_beyond_manifest=False,
        raw_observations_and_scores_in_public_summary=False,
        unsupported_condition_paths=[
            dict(
                method="lag_ridge",
                condition="single input channel with selected sklearn runtime",
                status="native shape error; no score produced",
                owner_source_modified=False,
            )
        ],
        json_worker_verified=args.worker_python is not None,
        implementation_sha256=file_digest(Path("src/pipeline_integrity/monitoring_exchange.py")),
        worker_sha256=file_digest(Path("src/pipeline_integrity/monitoring_worker.py")),
        script_sha256=file_digest(Path(__file__)),
        example_sha256=file_digest(Path("examples/monitoring_exchange.py")),
        code_sha256={
            name: file_digest(Path(name))
            for name in (
                "src/pipeline_integrity/monitoring_exchange.py",
                "src/pipeline_integrity/monitoring_worker.py",
                "tests/test_monitoring_exchange.py",
                "examples/monitoring_exchange.py",
                "scripts/check_monitoring_exchange.py",
                "docs/MONITORING_EXCHANGE_KO.md",
            )
        },
    )
    save(Path("research/monitoring_exchange_results.json"), summary)
    print(
        json.dumps(
            dict(
                selected_methods=summary["selected_registered_methods"],
                failed_methods=list(failures),
                native_cases=summary["native_case_count"],
                references=len(refs),
                refusals=len(refusals),
            )
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
