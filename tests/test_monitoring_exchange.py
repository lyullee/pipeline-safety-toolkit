"""Split leakage, raw clocks/quality, pre-import review gates and sealed owner exchange."""

import json
import runpy
import sys
from copy import deepcopy
from datetime import datetime, timezone
from types import ModuleType, SimpleNamespace

import pytest

import pipeline_integrity.monitoring_exchange as exchange
from pipeline_integrity.contracts import digest


def cases(family="statistical", method="zscore"):
    module = runpy.run_path("examples/monitoring_exchange.py")
    spec = module["authored_native_spec"](method, family)
    parameters = {}
    if family in {"neural", "temporal_neural"}:
        parameters.update(hidden=4, epochs=2, batch_size=16, seed=73)
    if family in {"dynamic_subspace", "temporal_neural"}:
        parameters["window"] = 4
    request, phases, context = module["make_case"](
        method,
        family,
        owner_version="spy-1",
        native_spec_digest=digest(spec),
        source_sha256="1" * 64,
        parameters=parameters,
    )
    return request, phases, context, spec


class OwnerSpy:
    def __init__(self, spec):
        self.spec, self.calls = spec, []

    def run(self, recipe, splits, context):
        self.calls.append((deepcopy(recipe), deepcopy(splits), deepcopy(context)))
        window = (
            recipe["parameters"].get("window", 12) if self.spec["temporal"] else recipe["features"]["window"]
        )

        def result(split):
            frames = split["frames"]
            valid = [
                i >= window - 1
                and all(all(f["valid"].values()) for f in frames[max(0, i + 1 - window) : i + 1])
                for i in range(len(frames))
            ]
            scores = [float(i % 4) if ok else None for i, ok in enumerate(valid)]
            return dict(
                schema="process-monitoring.detection.v1",
                time_s=[f["time_s"] for f in frames],
                scores=scores,
                valid=valid,
                alarms=[v and s > 1.5 for s, v in zip(scores, valid, strict=True)],
                threshold=1.5,
                detector="OwnerSpy",
                metadata=dict(
                    channels=list(frames[0]["values"]),
                    calibration=dict(
                        rows=len(frames) - window + 1, false_alarm_fraction=recipe["false_alarm_fraction"]
                    ),
                ),
            )

        calibration, evaluation = result(splits[1]), result(splits[2])
        evaluation["metadata"]["calibration"] = deepcopy(calibration["metadata"]["calibration"])
        state = dict(
            source_tree_sha256="1" * 64,
            source_tree_files=2,
            support_sha256={"LICENSE": "2" * 64, "THIRD_PARTY_NOTICES.md": "3" * 64},
        )
        native = dict(
            schema="pipeline_integrity.monitoring_native.v1",
            owner="process-monitoring-toolkit",
            owner_version="spy-1",
            native_spec=self.spec,
            native_spec_digest=digest(self.spec),
            recipe_digest=digest(recipe),
            context_digest=digest(context),
            split_digests=[digest(s) for s in splits],
            calibration=calibration,
            evaluation=evaluation,
            native_window_rows=window,
            feature_transform_applied=not self.spec["temporal"],
            pipeline_scaler_applied=recipe["normalize"] and not self.spec["temporal"],
            detector_config=dict(registry_name=recipe["method"], parameters=recipe["parameters"]),
            source_pin=dict(before=state, after=deepcopy(state), source_unchanged=True),
        )
        if recipe["family"] in {"neural", "temporal_neural"}:
            arrays = {
                name: dict(shape=[3], dtype="float32", sha256="4" * 64)
                for name in ("center", "scale", "model.weight")
            }
            native.update(
                runtime_versions={"torch": "contract-fixture"},
                neural_training=dict(
                    seed=recipe["parameters"]["seed"],
                    epochs=recipe["parameters"]["epochs"],
                    loss_history=[1.0] * recipe["parameters"]["epochs"],
                    before_queries=arrays,
                    after_queries=deepcopy(arrays),
                    unchanged=True,
                    execution=dict(
                        torch_version="contract-fixture",
                        device="cpu",
                        cpu_threads=recipe["cpu_threads"],
                        deterministic_algorithms=True,
                        pretrained_weights_loaded=False,
                    ),
                ),
            )
        return dict(native, result_digest=digest(native))


@pytest.mark.parametrize(
    "family,method",
    [
        ("statistical", "zscore"),
        ("classical", "pca"),
        ("forecasting", "lag_ridge"),
        ("modern", "ecod"),
        ("dynamic_subspace", "dynamic_pca"),
        ("neural", "diffusion"),
        ("temporal_neural", "deep_graph_infomax"),
    ],
)
def test_replaceable_client_keeps_native_recipe_split_and_context(family, method):
    request, phases, context, spec = cases(family, method)
    spy = OwnerSpy(spec)
    record = exchange.evaluate_monitoring(request, phases, context, client=spy)
    assert record["status"] == "computed" and len(spy.calls) == 1
    assert spy.calls[0][0] == request.model_dump(mode="json")
    assert spy.calls[0][1] == [s.model_dump(mode="json") for s in phases]
    exchange.verify_monitoring_record(record)


@pytest.mark.parametrize(
    "update",
    [
        {"parameters": None},
        {"applicability_reviews": {}},
        {"review_evidence_ids": {}},
        {"expected_owner_version": None},
        {"expected_native_spec_digest": None},
        {"owner_source_tree_sha256": None},
    ],
)
def test_unresolved_reviews_or_pins_stop_before_owner_import(monkeypatch, update):
    request, phases, context, spec = cases()

    def unexpected(*_, **__):
        raise AssertionError("native import reached")

    monkeypatch.setattr(exchange, "_native_owner", unexpected)
    spy = OwnerSpy(spec)
    record = exchange.evaluate_monitoring(request.model_copy(update=update), phases, context, client=spy)
    assert record["status"] == "needs_data" and record["native_receipt"] is None and not spy.calls


@pytest.mark.parametrize(
    "mutation", ["row_id", "reverse_time", "duplicate_time", "units", "channel_order", "stream"]
)
def test_source_split_contamination_or_schema_mismatch_is_rejected(mutation):
    request, phases, context, spec = cases()
    phase = phases[2]
    if mutation == "row_id":
        phase = phase.model_copy(
            update={"observation_ids": (phases[0].observation_ids[0], *phase.observation_ids[1:])}
        )
    else:
        frames = list(phase.frames)
        first = frames[0]
        if mutation in ("reverse_time", "duplicate_time"):
            frames[1] = frames[1].model_copy(
                update={"time_s": first.time_s if mutation == "duplicate_time" else first.time_s - 1}
            )
        elif mutation == "units":
            frames = [f.model_copy(update={"units": dict.fromkeys(f.units, "wrong")}) for f in frames]
        elif mutation == "channel_order":
            frames[0] = first.model_copy(update={"values": dict(reversed(list(first.values.items())))})
        else:
            frames[1] = frames[1].model_copy(update={"stream_id": "other"})
        phase = phase.model_copy(update={"frames": tuple(frames)})
    with pytest.raises(ValueError):
        exchange.evaluate_monitoring(request, (*phases[:2], phase), context, client=OwnerSpy(spec))


def test_separate_run_policy_requires_distinct_physical_groups_and_preserves_run_clocks():
    request, phases, context, spec = cases()
    request = request.model_copy(update={"split_policy": "separate_physical_runs"})
    with pytest.raises(ValueError, match="distinct"):
        exchange.evaluate_monitoring(request, phases, context, client=OwnerSpy(spec))
    phases = tuple(
        s.model_copy(
            update={
                "physical_run_id": "run-" + str(i),
                "source_group_id": "group-" + str(i),
                "frames": tuple(
                    f.model_copy(update={"clock_id": "source-clock-" + str(i), "time_s": float(j)})
                    for j, f in enumerate(s.frames)
                ),
            }
        )
        for i, s in enumerate(phases)
    )
    record = exchange.evaluate_monitoring(request, phases, context, client=OwnerSpy(spec))
    assert [s["frames"][0]["clock_id"] for s in record["splits"]] == [
        "source-clock-0",
        "source-clock-1",
        "source-clock-2",
    ]


def test_nonfinite_source_is_not_json_null_but_valid_utc_timestamp_survives():
    request, phases, context, spec = cases()
    first = phases[2].frames[0]
    utc = datetime(2026, 10, 27, tzinfo=timezone.utc)
    phase = phases[2].model_copy(
        update={"frames": (first.model_copy(update={"timestamp_utc": utc}), *phases[2].frames[1:])}
    )
    result = exchange.evaluate_monitoring(request, (*phases[:2], phase), context, client=OwnerSpy(spec))
    assert result["splits"][2]["frames"][0]["timestamp_utc"].startswith("2026-10-27")
    bad = first.model_copy(
        update={
            "values": dict(first.values, sensor_a=float("nan")),
            "valid": dict(first.valid, sensor_a=False),
        }
    )
    phase = phase.model_copy(update={"frames": (bad, *phase.frames[1:])})
    with pytest.raises(ValueError, match="nonfinite"):
        exchange.evaluate_monitoring(request, (*phases[:2], phase), context, client=OwnerSpy(spec))


@pytest.mark.parametrize(
    "field,value", [("maximum_rows_per_split", 20), ("maximum_channels", 2), ("maximum_feature_cells", 10)]
)
def test_data_budget_precedes_serialization(monkeypatch, field, value):
    request, phases, context, spec = cases()
    monkeypatch.setattr(
        exchange, "_strict_dump", lambda _: (_ for _ in ()).throw(AssertionError("serialized too soon"))
    )
    with pytest.raises(ValueError, match="budget"):
        exchange.evaluate_monitoring(
            request.model_copy(update={field: value}), phases, context, client=OwnerSpy(spec)
        )


def test_parameter_budget_is_checked_during_direct_request_construction():
    request, _, _, _ = cases()
    with pytest.raises(ValueError, match="budget"):
        exchange.MonitoringRequest.model_validate(
            request.model_dump() | {"parameters": {"too_many": [0] * 101}, "maximum_parameter_nodes": 100}
        )


@pytest.mark.parametrize(
    "mutation", ["values", "time", "calibration", "pin", "meaning", "recipe", "spec", "warmup"]
)
def test_resealed_outer_record_does_not_change_original_native_receipt(mutation):
    request, phases, context, spec = cases()
    record = exchange.evaluate_monitoring(request, phases, context, client=OwnerSpy(spec))
    native = record["native_receipt"]
    if mutation == "values":
        native["evaluation"]["scores"][0] = 100.0
    elif mutation == "time":
        native["evaluation"]["time_s"][0] = 0.0
    elif mutation == "calibration":
        native["evaluation"]["threshold"] = 9.0
    elif mutation == "pin":
        native["source_pin"]["source_unchanged"] = False
    elif mutation == "meaning":
        record["score_is_probability_or_HEP"] = True
    elif mutation == "recipe":
        record["request"]["normalize"] = True
    elif mutation == "spec":
        native["native_spec"]["family"] = "classification"
    else:
        native["native_window_rows"] = 4
        native["result_digest"] = digest({k: v for k, v in native.items() if k != "result_digest"})
    record["record_digest"] = digest({k: v for k, v in record.items() if k != "record_digest"})
    with pytest.raises(ValueError):
        exchange.verify_monitoring_record(record)


def test_withheld_request_cannot_claim_computed_result():
    request, phases, context, _ = cases()
    record = exchange.evaluate_monitoring(request.model_copy(update={"parameters": None}), phases, context)
    record["status"] = "computed"
    record["record_digest"] = digest({k: v for k, v in record.items() if k != "record_digest"})
    with pytest.raises(ValueError):
        exchange.verify_monitoring_record(record)


def test_source_pin_rejects_wrong_bytes_and_loaded_package_path(tmp_path, monkeypatch):
    root = tmp_path / "owner"
    (root / "src").mkdir(parents=True)
    (root / "src" / "owner.py").write_text("value = 1\n", encoding="utf-8")
    (root / "LICENSE").write_text("Synthetic license fixture", encoding="utf-8")
    (root / "THIRD_PARTY_NOTICES.md").write_text("Synthetic notices fixture", encoding="utf-8")
    client = exchange.NativeMonitoringClient(root)
    with pytest.raises(ValueError, match="pin"):
        client.describe("zscore", expected_source_sha256="0" * 64)
    fake = ModuleType("process_monitoring")
    fake.__file__ = str(tmp_path / "wrong.py")
    fake.Monitor = fake.create_detector = fake.list_detectors = fake.SignalBatch = fake.WindowFeatures = (
        lambda: None
    )
    monkeypatch.setitem(sys.modules, "process_monitoring", fake)
    pin = exchange.monitoring_source_fingerprint(root)["source_tree_sha256"]
    with pytest.raises(ValueError, match="pinned source"):
        client.describe("zscore", expected_source_sha256=pin)


def test_json_worker_transport_uses_selected_python_without_shell_or_public_path(monkeypatch, tmp_path):
    calls = []

    def run(command, **kwargs):
        calls.append((command, json.loads(kwargs["input"]), kwargs))
        return SimpleNamespace(returncode=0, stdout=json.dumps(dict(status="ok", result={"fixture": True})))

    monkeypatch.setattr(exchange.subprocess, "run", run)
    client = exchange.PythonMonitoringClient(tmp_path / "python.exe", tmp_path / "source")
    assert client.describe("zscore", expected_source_sha256="1" * 64) == {"fixture": True}
    assert (
        calls[0][0][0] == str(tmp_path / "python.exe")
        and calls[0][0][-1] == "pipeline_integrity.monitoring_worker"
    )
    assert "shell" not in calls[0][2] and calls[0][2]["timeout"] == 180
    assert "source_root" in calls[0][1]


@pytest.mark.parametrize("field,value", [("output_origin", "measured"), ("score_meaning", "probability")])
def test_resealing_cannot_relabel_calculated_score(field, value):
    request, phases, context, spec = cases()
    record = exchange.evaluate_monitoring(request, phases, context, client=OwnerSpy(spec))
    record[field] = value
    record["record_digest"] = digest({k: v for k, v in record.items() if k != "record_digest"})
    with pytest.raises(ValueError):
        exchange.verify_monitoring_record(record)


def test_even_resealed_native_result_cannot_claim_incomplete_window_is_available():
    request, phases, context, spec = cases()
    request = request.model_copy(update={"features": exchange.MonitoringFeatures(window=4)})
    record = exchange.evaluate_monitoring(request, phases, context, client=OwnerSpy(spec))
    native = record["native_receipt"]
    native["evaluation"]["valid"][0] = True
    native["evaluation"]["scores"][0] = 0.0
    native["result_digest"] = digest({k: v for k, v in native.items() if k != "result_digest"})
    record["record_digest"] = digest({k: v for k, v in record.items() if k != "record_digest"})
    with pytest.raises(ValueError, match="history window"):
        exchange.verify_monitoring_record(record)


def test_same_physical_run_does_not_mix_source_origins():
    request, phases, context, spec = cases()
    phase = phases[2].model_copy(
        update={"frames": tuple(f.model_copy(update={"origin": "simulation"}) for f in phases[2].frames)}
    )
    with pytest.raises(ValueError, match="same physical run"):
        exchange.evaluate_monitoring(request, (*phases[:2], phase), context, client=OwnerSpy(spec))


def test_direct_split_requires_explicit_source_units():
    _, phases, _, _ = cases()
    raw = phases[0].model_dump(mode="json")
    for frame in raw["frames"]:
        frame["units"] = {key: "" for key in frame["units"]}
    with pytest.raises(ValueError, match="source units"):
        exchange.MonitoringSplit.model_validate_json(json.dumps(raw))


@pytest.mark.parametrize("missing", ("epochs", "batch_size", "hidden", "seed", "window"))
def test_neural_recipe_requires_explicit_training_and_window_before_optional_import(missing):
    request, stages, context, spec = cases("temporal_neural", "lstm")
    parameters = {k: v for k, v in request.parameters.items() if k != missing}
    spy = OwnerSpy(spec)
    record = exchange.evaluate_monitoring(
        request.model_copy(update={"parameters": parameters}), stages, context, client=spy
    )
    assert record["status"] == "needs_data" and not spy.calls


@pytest.mark.parametrize(
    "change",
    (
        {"maximum_training_epochs": 1},
        {"maximum_hidden_units": 2},
        {"maximum_training_work": 10},
        {"maximum_feature_cells": 300},
    ),
)
def test_neural_and_actual_sequence_budgets_precede_serialization(monkeypatch, change):
    request, stages, context, spec = cases("temporal_neural", "lstm")
    monkeypatch.setattr(exchange, "_strict_dump", lambda _: pytest.fail("serialized before checking work"))
    with pytest.raises(ValueError, match="budget"):
        exchange.evaluate_monitoring(
            request.model_copy(update=change), stages, context, client=OwnerSpy(spec)
        )


@pytest.mark.parametrize(
    "field,value", (("epochs", False), ("hidden", 2.5), ("seed", None), ("window", True))
)
def test_neural_integer_settings_are_not_coerced(field, value):
    request, stages, context, spec = cases("temporal_neural", "lstm")
    with pytest.raises(ValueError):
        exchange.evaluate_monitoring(
            request.model_copy(update={"parameters": dict(request.parameters, **{field: value})}),
            stages,
            context,
            client=OwnerSpy(spec),
        )


@pytest.mark.parametrize(
    "mutation",
    (
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
    ),
)
def test_rehashed_neural_receipt_cannot_change_training_or_execution_meaning(mutation):
    request, stages, context, spec = cases("neural", "diffusion")
    record = exchange.evaluate_monitoring(request, stages, context, client=OwnerSpy(spec))
    native = record["native_receipt"]
    training = native["neural_training"]
    if mutation == "weights":
        training["after_queries"]["model.weight"]["sha256"] = "5" * 64
    elif mutation in {"seed", "epochs"}:
        training[mutation] += 1
    elif mutation == "loss_trace":
        training["loss_history"].pop()
    elif mutation == "loss_bool":
        training["loss_history"][0] = True
    elif mutation == "missing_weights":
        del training["before_queries"]["model.weight"]
        del training["after_queries"]["model.weight"]
    else:
        key, value = {
            "device": ("device", "cuda"),
            "threads": ("cpu_threads", True),
            "version": ("torch_version", "changed"),
            "pretrained": ("pretrained_weights_loaded", True),
        }[mutation]
        training["execution"][key] = value
    native["result_digest"] = digest({k: v for k, v in native.items() if k != "result_digest"})
    record["record_digest"] = digest({k: v for k, v in record.items() if k != "record_digest"})
    with pytest.raises(ValueError):
        exchange.verify_monitoring_record(record)
