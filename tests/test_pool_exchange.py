"""Contract faults and mass-preserving signal semantics, independent of optional SDK installs."""

import hashlib
import json
import subprocess
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from pipeline_integrity import pool_exchange
from pipeline_integrity.contracts import EvidenceRef, RunContext, digest
from pipeline_integrity.pool_exchange import (
    PoolRequest,
    evaluate_pool_request,
    reference_config,
    to_signal_frames,
    validate_config,
    validate_stored_result,
    zoh_cumulative,
)


def context(config):
    return RunContext(
        asset_id="ground",
        scenario_id="synthetic",
        clock_id="clock",
        origin="synthetic",
        evidence=(EvidenceRef(id="synthetic", citation="test only", origin="synthetic"),),
        metadata={"pool_boundary": config["boundary"]},
    )


@pytest.mark.parametrize("route", ["spreading", "fixed_area"])
def test_unreviewed_before_native_source_inspection(route, monkeypatch):
    monkeypatch.setattr(
        "pipeline_integrity.pool_exchange.source_manifest", lambda _: pytest.fail("source touched")
    )
    c = reference_config(route)
    answer = evaluate_pool_request(PoolRequest("missing", c, context(c)))
    assert answer["status"] == "needs_data" and answer["native_called"] is False


@pytest.mark.parametrize(
    "mode",
    [
        "nan",
        "false",
        "nil",
        "negative",
        "clock",
        "units",
        "grid",
        "work",
        "source",
        "initial",
        "fluid",
        "water_time",
    ],
)
def test_reject_invalid_and_unsupported(mode):
    c = reference_config()
    if mode in {"nan", "false", "nil", "negative"}:
        c["inflow"]["rates_kg_s"][0] = {"nan": float("nan"), "false": False, "nil": None, "negative": -1}[
            mode
        ]
    elif mode == "clock":
        c["output_times_s"] = [0, 0.1, 0.1]
    elif mode == "units":
        c["ambient_pressure_barg"] = c.pop("ambient_pressure_pa_abs")
    elif mode == "grid":
        c["numerics"]["radial_step_m"] = 1e-6
    elif mode == "work":
        c["numerics"]["maximum_time_step_s"] = 1e-9
    elif mode == "source":
        c["boundary"]["meaning"] = "jet_release"
    elif mode == "initial":
        c["initial_liquid_mass_kg"] = 1
    elif mode == "fluid":
        c["fluid"] = "ParaHydrogen"
    else:
        c["surface"] = {"kind": "water", "kwargs": {"heat_flux_W_m2": 1000.0, "calibration_label": "test"}}
        c["output_times_s"] = [0.0, 61.0]
    with pytest.raises((ValueError, NotImplementedError)):
        validate_config(c)


def test_zoh_final_hold_and_zero_are_distinct_from_missing():
    assert zoh_cumulative([0, 2, 5], [0, 1, 2], 7) == 7
    assert zoh_cumulative([0], [0], 7) == 0
    c = reference_config("fixed_area")
    c["fixed_area"]["critical_heat_flux_W_m2"] = None
    validate_config(c)


def stored_reference():
    """Authored mass ledger; no optional SDK or claimed native execution proof."""
    c = reference_config("fixed_area")
    c["boundary"]["clock_offset_s"] = 10.0
    c["initial_liquid_mass_kg"] = 1.0
    c["output_times_s"] = [0.0, 0.1, 1.0]
    c["inflow"]["rates_kg_s"] = [0.0, 0.0]
    ctx = context(c)
    rows = [
        {"time_s": 0.0, "liquid_mass_kg": 1.0, "cumulative_evaporation_kg": 0.0},
        {"time_s": 0.1, "liquid_mass_kg": 0.8, "cumulative_evaporation_kg": 0.2},
        {"time_s": 1.0, "liquid_mass_kg": 0.5, "cumulative_evaporation_kg": 0.5},
    ]
    for row in rows:
        row.update(cumulative_inflow_kg=0.0, source_status="within_declared_scope")
    native = {"ledger": rows, "mass_balance_residual_kg": 0.0}
    audits, status = pool_exchange._audit(native, c)
    result = {
        "schema": pool_exchange.SCHEMA,
        "status": status,
        "native_called": True,
        "sdk_version": pool_exchange.SDK_VERSION,
        "reviews": dict(boundary=True, source=True, model=True),
        "context": ctx.model_dump(mode="json"),
        "context_pin": digest(ctx.model_dump(mode="json")),
        "config_pin": digest(c),
        "evidence_pin": digest(ctx.model_dump(mode="json")["evidence"]),
        "config": c,
        "native": native,
        "native_pin": digest(native),
        "audits": audits,
        "source_unchanged": True,
        "worker_pin": hashlib.sha256(
            Path(pool_exchange.__file__).with_name("pool_worker.py").read_bytes()
        ).hexdigest(),
        "adapter_pin": hashlib.sha256(Path(pool_exchange.__file__).read_bytes()).hexdigest(),
        "energy_balance_status": pool_exchange.ENERGY_STATUS,
        "rate_semantics": pool_exchange.RATE_SEMANTICS,
    }
    result["receipt_pin"] = digest(result)
    return result, ctx


def test_interval_signal_reconstructs_evaporation_mass_and_offset():
    result, ctx = stored_reference()
    frames = to_signal_frames(result, ctx)
    assert frames[0].time_s == 10 and frames[-1].time_s == 11
    assert frames[0].values["evaporation_interval_mean_kg_s"] is None
    assert not frames[0].valid["evaporation_interval_mean_kg_s"]
    assert sum(
        frames[i].values["evaporation_interval_mean_kg_s"] * (frames[i].time_s - frames[i - 1].time_s)
        for i in range(1, len(frames))
    ) == pytest.approx(0.5)
    with pytest.raises(ValueError):
        to_signal_frames({**result, "status": "partial"}, ctx)


@pytest.mark.parametrize(
    "mode",
    [
        "negative_mass",
        "negative_evaporation",
        "decreasing",
        "nonfinite",
        "clock",
        "mass_balance",
        "scope",
        "audits",
        "meaning",
        "review",
        "without_receipt",
    ],
)
def test_export_refuses_semantic_corruption_even_with_rehashed_receipt(mode):
    result, ctx = stored_reference()
    row = result["native"]["ledger"][-1]
    if mode == "negative_mass":
        row["liquid_mass_kg"] = -1000.0
    elif mode == "negative_evaporation":
        row["cumulative_evaporation_kg"] = -1.0
    elif mode == "decreasing":
        row.update(cumulative_evaporation_kg=0.1, liquid_mass_kg=0.9)
    elif mode == "nonfinite":
        row["liquid_mass_kg"] = None
    elif mode == "clock":
        row["time_s"] = 0.05
    elif mode == "mass_balance":
        row["liquid_mass_kg"] += 1.0
    elif mode == "scope":
        row["source_status"] = "out_of_scope"
    elif mode == "audits":
        result["audits"][-1]["mass_residual_kg"] = 99.0
    elif mode == "meaning":
        result["energy_balance_status"] = "verified"
    elif mode == "review":
        result["reviews"]["boundary"] = False
    result["native_pin"] = digest(result["native"])
    result["receipt_pin"] = digest({k: v for k, v in result.items() if k != "receipt_pin"})
    if mode == "without_receipt":
        del result["receipt_pin"]
    assert not validate_stored_result(result, "missing", ctx)["valid"]
    with pytest.raises((ValueError, KeyError, TypeError)):
        to_signal_frames(result, ctx)


def test_context_boundary_mismatch_prevents_source_inspection(monkeypatch):
    c = reference_config()
    ctx = context(c)
    changed = deepcopy(c)
    changed["boundary"]["enu_origin_m"][0] = 2
    monkeypatch.setattr(
        "pipeline_integrity.pool_exchange.source_manifest", lambda _: pytest.fail("source touched")
    )
    request = PoolRequest(
        "missing",
        changed,
        ctx,
        source_pin="x",
        boundary_reviewed=True,
        source_reviewed=True,
        model_reviewed=True,
    )
    assert evaluate_pool_request(request)["status"] == "invalid"
    assert evaluate_pool_request(replace(request, model_reviewed="true"))["status"] == "needs_data"


@pytest.mark.parametrize("mode", ["missing", "binding", "nan", "oversize", "source_mutation"])
def test_worker_faults_never_become_computed(monkeypatch, mode):
    source_calls = 0

    def manifest(_):
        nonlocal source_calls
        source_calls += 1
        return {"stub": "changed" if mode == "source_mutation" and source_calls > 1 else "hash"}

    monkeypatch.setattr("pipeline_integrity.pool_exchange.source_manifest", manifest)

    def process(args, **kwargs):
        payload = json.loads(kwargs["input"])
        response = {
            "schema": "pool-exchange/1",
            "request_pin": digest(payload),
            "status": "error",
            "native_called": True,
            "native": None,
            "reason": "native fixture failure",
        }
        if mode == "missing":
            return subprocess.CompletedProcess(args, 0)
        if mode == "binding":
            response["request_pin"] = "incorrect"
        text = json.dumps(response)
        if mode == "nan":
            text = text[:-1] + ', "invalid": NaN}'
        elif mode == "oversize":
            text = "x" * 4096
        Path(args[3]).write_text(text, encoding="utf-8")
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(subprocess, "run", process)
    c = reference_config()
    request = PoolRequest(
        "missing",
        c,
        context(c),
        source_pin=digest({"stub": "hash"}),
        boundary_reviewed=True,
        source_reviewed=True,
        model_reviewed=True,
        output_budget_bytes=2048,
    )
    result = evaluate_pool_request(request)
    assert result["status"] == ("error" if mode == "source_mutation" else "invalid")


def test_stored_receipt_does_not_accept_incomplete_record():
    c = reference_config()
    assert not validate_stored_result({"status": "computed"}, "missing", context(c))["valid"]


def test_input_budget_prevents_worker(monkeypatch):
    c = reference_config()
    ctx = context(c).model_copy(update={"metadata": {"pool_boundary": c["boundary"], "large": "x" * 5000}})
    monkeypatch.setattr("pipeline_integrity.pool_exchange.source_manifest", lambda _: {"stub": "hash"})
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("must not spawn"))
    request = PoolRequest(
        "missing",
        c,
        ctx,
        source_pin=digest({"stub": "hash"}),
        boundary_reviewed=True,
        source_reviewed=True,
        model_reviewed=True,
        input_budget_bytes=2048,
    )
    result = evaluate_pool_request(request)
    assert result["status"] == "invalid" and "input budget" in result["reason"]
