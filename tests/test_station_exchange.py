"""Independent boundary checks; native numeric checks live in the executable guide."""

import json
import subprocess
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.station_exchange import (
    PROPERTY_BASIS,
    RESULT_SCHEMA,
    StationRequest,
    digest,
    evaluate,
    reference_boundary,
    reference_config,
    to_signal_frames,
    validate_config,
    validate_stored_result,
)


def reviewed_request():
    return StationRequest(
        "missing",
        "missing",
        reference_config(),
        RunContext(
            asset_id="fixture",
            scenario_id="contract-only",
            clock_id="clock",
            origin="synthetic",
            evidence=(
                EvidenceRef(
                    id="review-fixture",
                    citation="Authored boundary fixture, not a numerical oracle",
                    origin="synthetic",
                ),
            ),
            metadata={"station_boundary": reference_boundary("fixture", "clock")},
        ),
        evidence=("review-fixture",),
        source_pin=digest({"source": "hash"}),
        source_reviewed=True,
        model_reviewed=True,
        conditions_reviewed=True,
    )


def test_unreviewed_does_not_touch_source_or_executable():
    result = evaluate(StationRequest("missing", "missing", {}, {}))
    assert result["status"] == "needs_data"
    assert result["native_called"] is False


def test_review_requires_boolean_true():
    result = evaluate(replace(reviewed_request(), source_reviewed="true"))
    assert result["status"] == "needs_data"


def test_units_copy_does_not_mutate_boundary():
    changed = reference_config()
    changed["units"]["pressure"] = "bar"
    with pytest.raises(ValueError):
        validate_config(changed)
    validate_config(reference_config())


@pytest.mark.parametrize(
    "key,value",
    [
        ("duration_s", float("nan")),
        ("duration_s", -1),
        ("sample_count", 2002),
        ("maximum_rhs_calls", True),
        ("maximum_step_s", 1e-12),
    ],
)
def test_invalid_budget(key, value):
    config = reference_config()
    config[key] = value
    with pytest.raises(ValueError):
        validate_config(config)


@pytest.mark.parametrize("mode", ["units", "port", "sign", "clock", "unknown", "missing"])
def test_semantic_rejections(mode):
    config = reference_config()
    if mode == "units":
        config["units"] = {"pressure": "bar_g"}
    elif mode == "port":
        config["connections"][0]["target"] = "unknown"
    elif mode == "sign":
        config["commands"][0]["openings"]["transfer"] = -0.1
    elif mode == "clock":
        config["commands"].append(deepcopy(config["commands"][0]))
    elif mode == "unknown":
        config["tanks"]["bank"]["pressure_pa_abs"] = None
    else:
        config["commands"][0]["openings"] = {}
    with pytest.raises((ValueError, TypeError)):
        validate_config(config)


def test_reference_has_no_hidden_pressure_conversion():
    config = reference_config()
    validate_config(config)
    assert config["tanks"]["bank"]["pressure_pa_abs"] == 20_000_000


def test_pin_mismatch_prevents_process(monkeypatch):
    monkeypatch.setattr("pipeline_integrity.station_exchange.source_manifest", lambda _: {})
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("must not spawn"))
    assert evaluate(reviewed_request())["status"] == "invalid"


@pytest.mark.parametrize("mode", ["timeout", "missing", "nonfinite", "oversize", "source_mutation"])
def test_process_failures_are_not_success(monkeypatch, mode):
    manifests = iter([{"source": "hash"}, {"source": "changed"}])
    monkeypatch.setattr(
        "pipeline_integrity.station_exchange.source_manifest",
        lambda _: next(manifests) if mode == "source_mutation" else {"source": "hash"},
    )

    def process(args, **kwargs):
        if mode == "timeout":
            raise subprocess.TimeoutExpired(args, 1)
        if mode != "missing":
            response = (
                '{"status":"computed", "value":NaN}'
                if mode == "nonfinite"
                else json.dumps({"status": "computed", "native_called": True, "trajectory": [1]})
            )
            if mode == "oversize":
                response = "x" * 2048
            elif mode == "source_mutation":
                response = json.dumps(
                    {
                        "schema": RESULT_SCHEMA,
                        "request_pin": digest(json.loads(kwargs["input"])),
                        "native": {"status": "computed", "native_called": True, "trajectory": [1]},
                    }
                )
            Path(args[3]).write_text(response, encoding="utf-8")
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(subprocess, "run", process)
    result = evaluate(replace(reviewed_request(), output_budget_bytes=1024))
    assert result["status"] == {"timeout": "timeout", "source_mutation": "error"}.get(mode, "invalid")
    if mode == "source_mutation":
        assert result["trajectory"] is None
        assert result["native"]["trajectory"] == [1]


@pytest.mark.parametrize(
    "mode",
    [
        "dict",
        "clock",
        "asset",
        "property",
        "pressure",
        "negative_offset",
        "missing_boundary",
        "unknown_evidence",
        "duplicate_evidence",
        "duplicate_context_evidence",
    ],
)
def test_shared_context_semantics_refuse_before_process(monkeypatch, mode):
    request = reviewed_request()
    context = request.context.model_copy(deep=True)
    evidence = request.evidence
    if mode == "dict":
        context = context.model_dump(mode="json")
    elif mode in {"clock", "asset"}:
        context = context.model_copy(update={f"{mode}_id": "changed"})
    elif mode == "property":
        context.metadata["station_boundary"]["property_basis"] = "HEOS:Hydrogen"
    elif mode == "pressure":
        context.metadata["station_boundary"]["pressure_reference"] = "gauge"
    elif mode == "negative_offset":
        context.metadata["station_boundary"]["clock_offset_s"] = -1
    elif mode == "missing_boundary":
        context.metadata.clear()
    elif mode == "unknown_evidence":
        evidence = ("unknown",)
    elif mode == "duplicate_evidence":
        evidence = request.evidence * 2
    else:
        context = context.model_copy(update={"evidence": context.evidence * 2})
    monkeypatch.setattr(
        "pipeline_integrity.station_exchange.source_manifest", lambda _: pytest.fail("must not inspect owner")
    )
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("must not spawn"))
    assert evaluate(replace(request, context=context, evidence=evidence))["status"] == "invalid"


def _native_fixture(request):
    """Authored protocol response; does not test or replace the owner solver."""
    config = request.config
    records = {
        name: {
            "mass_kg": [1.0, 1.0],
            "gas_energy_j": [100.0, 100.0],
            "wall_temperature_k": [298.15, 298.15],
            "pressure_pa_abs": [tank["pressure_pa_abs"]] * 2,
            "gas_temperature_k": [tank["temperature_k"]] * 2,
        }
        for name, tank in config["tanks"].items()
    }
    energy = sum(100.0 + t["parameters"]["wall_heat_capacity"] * 298.15 for t in config["tanks"].values())
    return {
        "status": "computed",
        "native_called": True,
        "loaded_owner": str((Path(request.source_root) / "src/h2station/__init__.py").resolve()),
        "owner_version": "0.1.2",
        "property_basis": PROPERTY_BASIS,
        "runtime": {"scipy": "1.15.3"},
        "trajectory": {"time_s": [0.0, 1.0], "tanks": records, "flows": [[], []]},
        "balance": {
            "mass_residual_kg": 0.0,
            "energy_residual_j": 0.0,
            "ambient_heat_j": 0.0,
            "initial_total_energy_j": energy,
        },
    }


def _install_fixture(monkeypatch, change=None):
    request = reviewed_request()
    config = deepcopy(request.config)
    config["sample_count"] = 2
    request = replace(request, config=config)
    monkeypatch.setattr("pipeline_integrity.station_exchange.source_manifest", lambda _: {"source": "hash"})

    def process(args, **kwargs):
        response = {
            "schema": RESULT_SCHEMA,
            "request_pin": digest(json.loads(kwargs["input"])),
            "native": _native_fixture(request),
        }
        if change:
            change(response)
        Path(args[3]).write_text(json.dumps(response), encoding="utf-8")
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(subprocess, "run", process)
    return request


def test_receipt_and_signal_frames_bind_context_source_and_clock(monkeypatch):
    request = _install_fixture(monkeypatch)
    request.context.metadata["station_boundary"]["clock_offset_s"] = 42.0
    result = evaluate(request)
    assert result["status"] == "computed"
    assert validate_stored_result(result, request.source_root, request.context)["valid"]
    frames = to_signal_frames(result, request.context, source_root=request.source_root)
    assert [f.time_s for f in frames] == [42.0, 43.0]
    assert frames[0].clock_id == "clock"
    assert frames[0].origin == "synthetic"
    assert frames[0].units["tank.bank.pressure_pa_abs"] == "Pa_abs"
    assert frames[0].values["flow.transfer.signed_nominal_mass_flow_kg_s"] == 0
    assert frames[0].values["flow.transfer.stream_enthalpy_j_kg"] is None
    assert not frames[0].valid["flow.transfer.stream_enthalpy_j_kg"]
    changed = request.context.model_copy(update={"clock_id": "other-clock"})
    assert not validate_stored_result(result, request.source_root, changed)["valid"]
    with pytest.raises(ValueError):
        to_signal_frames(result, changed, source_root=request.source_root)


@pytest.mark.parametrize(
    "mode", ["clock", "balance", "ports", "source", "evidence", "mirror", "producer", "property"]
)
def test_rehashed_corruption_does_not_pass_semantic_receipt(monkeypatch, mode):
    request = _install_fixture(monkeypatch)
    result = evaluate(request)
    native = result["native"]
    if mode == "clock":
        native["trajectory"]["time_s"][1] = 0.9
    elif mode == "balance":
        native["balance"]["mass_residual_kg"] = 1
    elif mode == "ports":
        native["trajectory"]["flows"][0] = [
            {
                "name": "transfer",
                "source": "receiver",
                "target": "bank",
                "mass_flow_kg_s": 0.1,
                "stream_enthalpy_j_kg": 100,
            }
        ]
    elif mode == "source":
        native["loaded_owner"] = "other"
    elif mode == "evidence":
        result["evidence"] = ["unlisted"]
        result["selected_evidence_pin"] = digest(result["evidence"])
    elif mode == "mirror":
        result["status"] = "partial"
    elif mode == "producer":
        result["worker_pin"] = "0" * 64
    else:
        native["property_basis"] = "changed"
    result["native_pin"] = digest(native)
    if mode != "mirror":
        result.update(native)
    result["receipt_pin"] = digest({k: v for k, v in result.items() if k != "receipt_pin"})
    assert not validate_stored_result(result, request.source_root, request.context)["valid"]


@pytest.mark.parametrize(
    "mode",
    [
        "binding",
        "schema",
        "reserved",
        "channel_length",
        "duplicate_flow",
        "negative_flow",
        "unsupported_scipy",
    ],
)
def test_worker_response_semantic_rejections(monkeypatch, mode):
    def change(response):
        if mode == "binding":
            response["request_pin"] = "0" * 64
        elif mode == "schema":
            response["schema"] = "old"
        elif mode == "reserved":
            response["native"]["source_pin"] = "0" * 64
        elif mode == "channel_length":
            response["native"]["trajectory"]["tanks"]["bank"]["mass_kg"].pop()
        elif mode == "unsupported_scipy":
            response["native"]["runtime"]["scipy"] = "1.18.1"
        else:
            flow = {
                "name": "transfer",
                "source": "bank",
                "target": "receiver",
                "mass_flow_kg_s": -1.0 if mode == "negative_flow" else 1.0,
                "stream_enthalpy_j_kg": 100,
            }
            response["native"]["trajectory"]["flows"][0] = [flow] if mode == "negative_flow" else [flow, flow]

    request = _install_fixture(monkeypatch, change)
    assert evaluate(request)["status"] == "invalid"


def test_partial_prefix_requires_export_opt_in(monkeypatch):
    def change(response):
        native = response["native"]
        native["status"] = "partial"
        native["trajectory"]["time_s"].pop()
        native["trajectory"]["flows"].pop()
        for record in native["trajectory"]["tanks"].values():
            for values in record.values():
                values.pop()

    request = _install_fixture(monkeypatch, change)
    result = evaluate(request)
    assert result["status"] == "partial"
    assert validate_stored_result(result, request.source_root, request.context)["valid"]
    with pytest.raises(ValueError):
        to_signal_frames(result, request.context, source_root=request.source_root)
    frames = to_signal_frames(result, request.context, source_root=request.source_root, allow_partial=True)
    assert len(frames) == 1
    assert frames[0].operating_mode == "station-native-partial-prefix"


def test_withheld_receipt_requires_no_owner_or_optional_dependencies(monkeypatch):
    monkeypatch.setattr(
        "pipeline_integrity.station_exchange.source_manifest", lambda _: pytest.fail("must not inspect owner")
    )
    request = replace(reviewed_request(), source_reviewed=False)
    result = evaluate(request)
    assert validate_stored_result(result)["valid"]


def test_input_budget_refuses_before_process(monkeypatch):
    request = _install_fixture(monkeypatch)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("must not spawn"))
    assert evaluate(replace(request, input_budget_bytes=1024))["status"] == "invalid"


@pytest.mark.parametrize("path", ["tank", "flow"])
def test_partial_nonfinite_keeps_unknown_channel_and_quality(monkeypatch, path):
    def change(response):
        native = response["native"]
        native["status"] = "partial"
        if path == "tank":
            native["trajectory"]["tanks"]["bank"]["gas_temperature_k"][1] = None
            pointer = "/trajectory/tanks/bank/gas_temperature_k/1"
        else:
            native["trajectory"]["flows"][1] = [
                {
                    "name": "transfer",
                    "source": "bank",
                    "target": "receiver",
                    "mass_flow_kg_s": None,
                    "stream_enthalpy_j_kg": None,
                }
            ]
            pointer = "/trajectory/flows/1/0/mass_flow_kg_s"
        native["nonfinite_observations"] = [{"path": pointer, "original": "nan"}]
        if path == "flow":
            native["nonfinite_observations"].append(
                {"path": "/trajectory/flows/1/0/stream_enthalpy_j_kg", "original": "inf"}
            )

    request = _install_fixture(monkeypatch, change)
    result = evaluate(request)
    assert result["status"] == "partial"
    frames = to_signal_frames(result, request.context, source_root=request.source_root, allow_partial=True)
    key = "tank.bank.gas_temperature_k" if path == "tank" else "flow.transfer.signed_nominal_mass_flow_kg_s"
    assert frames[1].values[key] is None and not frames[1].valid[key]
    result["native"]["nonfinite_observations"][0]["path"] = "/unrelated"
    result.update(result["native"])
    result["native_pin"] = digest(result["native"])
    result["receipt_pin"] = digest({k: v for k, v in result.items() if k != "receipt_pin"})
    assert not validate_stored_result(result, request.source_root, request.context)["valid"]


@pytest.mark.parametrize("budget", ["mass", "energy"])
def test_consistent_but_nonconserved_native_record_cannot_be_exported(monkeypatch, budget):
    def change(response):
        native = response["native"]
        if budget == "mass":
            native["trajectory"]["tanks"]["bank"]["mass_kg"][1] += 1.0
            native["balance"]["mass_residual_kg"] = 1.0
        else:
            native["trajectory"]["tanks"]["bank"]["gas_energy_j"][1] += 10.0
            native["balance"]["energy_residual_j"] = 10.0

    request = _install_fixture(monkeypatch, change)
    result = evaluate(request)
    assert result["status"] == "computed"  # Native completion is retained as an observation.
    assert validate_stored_result(result, request.source_root, request.context)["valid"]
    assert result["audits"]["mass_within_1e_8_kg" if budget == "mass" else "energy_within_0_5_J"] is False
    with pytest.raises(ValueError, match="diagnostic failed"):
        to_signal_frames(result, request.context, source_root=request.source_root)
