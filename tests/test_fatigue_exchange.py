"""Exchange policy tests; actual owner mathematics is exercised by the separate research runner."""

import copy
import json
import runpy
from pathlib import Path

import pytest

from pipeline_integrity.adapters import ExternalResult
from pipeline_integrity.contracts import RunContext, digest
from pipeline_integrity.fatigue_exchange import (
    PressureHistory,
    assess_pressure_fatigue,
    pressure_history_from_transient,
)
from pipeline_integrity.transient import LiquidTransient, simulate_liquid_transient


def case():
    return runpy.run_path("examples/pressure_fatigue.py")["make_case"]()


def history_with(history, **changes):
    return PressureHistory.model_validate_json(
        json.dumps(history.model_dump(mode="json") | {"binding_digest": None} | changes)
    )


class SpyClient:
    def __init__(self, *, status="computed", change=None):
        self.calls = []
        self.status = status
        self.change = change

    def evaluate(self, model_id, parameters, context, *, location_id):
        self.calls.append(copy.deepcopy(parameters))
        values = (
            dict(
                initial_damage=0.1,
                damage_increment=0.002,
                total_damage=0.102,
                damage_margin=0.898,
                history_source_digest=parameters["history"]["source_digest"],
            )
            if self.status == "computed"
            else {"rejected_stress_ratio": 0.95}
        )
        payload = dict(
            schema_version="matintegrity.assessment.v1",
            model_id=model_id,
            status=self.status,
            inputs=copy.deepcopy(parameters),
            values=values,
            context=dict(asset_id=context.asset_id, location_id=location_id, time_basis=context.clock_id),
        )
        if self.change:
            self.change(payload)
        return ExternalResult(
            owner="matintegrity",
            owner_schema=payload["schema_version"],
            status=payload["status"],
            asset_id=context.asset_id,
            payload=payload,
            payload_digest=digest(payload),
        )


def test_gauge_conversion_keeps_half_cycle_policy_origin_and_original_receipts():
    request, context = case()
    client = SpyClient()
    result = assess_pressure_fatigue(request, context, client=client)
    native = client.calls[0]["history"]
    assert native["pressure_absolute_Pa"] == [2101325.0, 4101325.0, 2101325.0, 4101325.0, 2101325.0]
    assert native["time_s"] == list(request.history.time_s)
    assert native["source_digest"] == request.history.binding_digest
    assert native["origin"] == result.native_history_origin == "model_prediction"
    assert result.pressure_origin == "synthetic" and result.temperature_origin == "user_declared"
    assert not result.failure_probability_calculated and not result.field_validation
    result.check_integrity()
    result.native_result.payload["values"]["total_damage"] = 0.0
    with pytest.raises(ValueError, match="changed"):
        result.check_integrity()


@pytest.mark.parametrize("missing", ["reference", "ambient", "pressure", "temperature", "invalid"])
def test_unresolved_observations_do_not_call_the_optional_engine(missing):
    request, context = case()
    changes = {
        "reference": {"pressure_reference": "unspecified", "ambient_pressure_pa_abs": None},
        "ambient": {"ambient_pressure_pa_abs": None},
        "pressure": {"pressure_pa": [2e6, None, 2e6, 4e6, 2e6]},
        "temperature": {"temperature_k": [293.15, None, 293.15, 293.15, 293.15]},
        "invalid": {"valid": [True, False, True, True, True]},
    }[missing]
    request = request.model_copy(update={"history": history_with(request.history, **changes)})
    client = SpyClient()
    result = assess_pressure_fatigue(request, context, client=client)
    assert result.status == "needs_data" and not client.calls
    assert result.native_result is None and result.values == {}
    result.check_integrity()


def test_observed_pressure_and_assumed_temperature_are_not_all_measured():
    request, context = case()
    h = history_with(request.history, pressure_origin="laboratory")
    result = assess_pressure_fatigue(request.model_copy(update={"history": h}), context, client=SpyClient())
    assert result.native_history_origin == "model_prediction"
    h = history_with(h, temperature_origin="measured")
    result = assess_pressure_fatigue(request.model_copy(update={"history": h}), context, client=SpyClient())
    assert result.native_history_origin == "measured" and result.result_origin == "calculated_screening"


def test_mutated_inputs_identity_budgets_and_evidence_are_rejected():
    request, context = case()
    altered = request.history.model_copy(update={"pressure_pa": (1e6,) * 5})
    with pytest.raises(ValueError, match="receipt"):
        assess_pressure_fatigue(request.model_copy(update={"history": altered}), context, client=SpyClient())
    with pytest.raises(ValueError, match="clocks"):
        assess_pressure_fatigue(
            request, context.model_copy(update={"clock_id": "another"}), client=SpyClient()
        )
    with pytest.raises(ValueError, match="budget"):
        assess_pressure_fatigue(
            request.model_copy(update={"maximum_samples": 3}), context, client=SpyClient()
        )
    with pytest.raises(ValueError, match="evidence"):
        assess_pressure_fatigue(request, context.model_copy(update={"evidence": ()}), client=SpyClient())
    row = context.evidence[0].model_copy(update={"sha256": "f" * 64})
    with pytest.raises(ValueError, match="source hash"):
        assess_pressure_fatigue(request, context.model_copy(update={"evidence": (row,)}), client=SpyClient())


@pytest.mark.parametrize("target", ["model", "clock", "input", "ledger", "history_digest"])
def test_even_rehashed_native_changes_are_rejected(target):
    request, context = case()

    def change(p):
        if target == "model":
            p["model_id"] = "fatigue.miner"
        elif target == "clock":
            p["context"]["time_basis"] = "another"
        elif target == "input":
            p["inputs"]["history"]["pressure_absolute_Pa"][0] += 1.0
        elif target == "ledger":
            p["values"]["damage_increment"] = 0.8
        else:
            p["values"]["history_source_digest"] = "e" * 64

    with pytest.raises(ValueError):
        assess_pressure_fatigue(request, context, client=SpyClient(change=change))


def test_original_out_of_scope_rejection_is_not_promoted_or_filled():
    request, context = case()
    result = assess_pressure_fatigue(request, context, client=SpyClient(status="out_of_scope"))
    assert result.status == "out_of_scope"
    assert result.values == {"rejected_stress_ratio": 0.95}
    assert "total_damage" not in result.values
    result.check_integrity()


def test_damage_above_one_is_retained_as_damage_with_a_negative_margin():
    request, context = case()

    def change(payload):
        payload["values"].update(damage_increment=1.5, total_damage=1.6, damage_margin=-0.6)

    result = assess_pressure_fatigue(request, context, client=SpyClient(change=change))
    assert result.status == "computed" and result.values["total_damage"] == 1.6
    assert result.values["damage_margin"] == -0.6 and not result.failure_probability_calculated


def test_transient_mapping_subtracts_elevation_and_keeps_declared_temperature_separate():
    job = json.loads(Path("examples/liquid_transient.json").read_text(encoding="utf-8"))
    spec = LiquidTransient.model_validate_json(json.dumps(job["specification"]))
    context = RunContext.model_validate_json(json.dumps(job["context"]))
    result = simulate_liquid_transient(spec, context)
    pipe = spec.pipes[0]
    h = pressure_history_from_transient(
        result,
        spec,
        pipe_id=pipe.id,
        endpoint="end",
        temperature_k=293.15,
        temperature_evidence_id="declared_temperature",
        source_id="transient",
    )
    node = next(n for n in spec.nodes if n.id == pipe.end_node)
    expected = spec.ambient_pressure_pa_abs + spec.density_kg_m3 * spec.gravity_m_s2 * (
        result["pipes"][pipe.id]["end_head_m"][0] - node.elevation_m
    )
    assert h.pressure_pa[0] == expected and h.source_sha256 == digest(result)
    assert h.pressure_origin == "simulation" and h.temperature_origin == "user_declared"
    assert h.location_id == f"{pipe.id}:end"
    for changed in (
        {"status": "withheld"},
        {"all_volume_balances_within_tolerance": False},
        {"specification_digest": "changed"},
    ):
        with pytest.raises(ValueError, match="computed, balanced"):
            pressure_history_from_transient(
                result | changed,
                spec,
                pipe_id=pipe.id,
                endpoint="end",
                temperature_k=293.15,
                temperature_evidence_id="declared_temperature",
                source_id="transient",
            )
