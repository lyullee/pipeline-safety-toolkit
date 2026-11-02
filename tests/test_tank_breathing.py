"""Tests authored for NURION execution; local pytest execution is deferred."""

import importlib.util
from pathlib import Path

import pytest
from pydantic import ValidationError

from pipeline_integrity import tank_breathing as tb
from pipeline_integrity.contracts import digest

_spec = importlib.util.spec_from_file_location(
    "tank_breathing_example", Path(__file__).parents[1] / "examples" / "tank_breathing.py"
)
_example = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_example)


def test_unreviewed_boundary_never_loads_owner(monkeypatch):
    q, c = _example.make_case()
    monkeypatch.setattr(tb, "native_backend_metadata", lambda: pytest.fail("unreviewed owner access"))
    r = tb.evaluate_tank_breathing(q, c)
    assert r["status"] == "needs_data"
    assert r["native_called"] is False
    assert r["history"] is None
    assert tb.verify_tank_breathing_record(r, request=q, context=c)["valid"]


def test_missing_optional_native_backend_is_reported_without_solver_call(monkeypatch):
    monkeypatch.setattr(_example, "native_backend_metadata", lambda: {"fixture": "reviewed-pin"})
    q, c = _example.make_case(reviewed=True)

    def unavailable():
        raise ImportError("optional tank owner is not installed")

    monkeypatch.setattr(tb, "native_backend_metadata", unavailable)
    monkeypatch.setattr(tb, "_native", lambda *args: pytest.fail("solver must not be called"))
    result = tb.evaluate_tank_breathing(q, c)
    assert result["status"] == "out_of_scope"
    assert result["native_called"] is False
    assert result["provider"] is None
    assert result["notes"] == ["ImportError: optional tank owner is not installed"]
    assert tb.verify_tank_breathing_record(result, request=q, context=c)["valid"] is True


def test_owner_import_race_returns_diagnostic_without_second_backend_discovery(monkeypatch):
    monkeypatch.setattr(_example, "native_backend_metadata", lambda: {"owner": "pinned"})
    request, context = _example.make_case(reviewed=True)
    calls = []

    def metadata():
        calls.append("metadata")
        return {"owner": "pinned"}

    def owner_import(_request, _context, _body):
        raise ImportError("optional tank solver module disappeared")

    monkeypatch.setattr(tb, "native_backend_metadata", metadata)
    monkeypatch.setattr(tb, "_native", owner_import)
    result = tb.evaluate_tank_breathing(request, context)

    assert calls == ["metadata"]
    assert result["status"] == "out_of_scope"
    assert result["native_called"] is False
    assert result["provider"] is None
    assert result["record_digest"] == digest({k: v for k, v in result.items() if k != "record_digest"})


def test_context_clock_and_port_binding():
    q, c = _example.make_case()
    with pytest.raises(ValueError):
        tb.evaluate_tank_breathing(q, c.model_copy(update={"clock_id": "different"}))
    altered = q.model_copy(update={"ports": q.ports.model_copy(update={"vapor_outlet_id": "other"})})
    with pytest.raises(ValueError):
        tb.evaluate_tank_breathing(altered, c)


def test_event_and_initial_closure_changes_require_actual_context_rebinding():
    q, c = _example.make_case()
    with pytest.raises(ValueError):
        tb.evaluate_tank_breathing(q, c.model_copy(update={"scenario_id": "unrelated_event"}))
    changed = q.model_copy(update={"initial_pressure_pa_abs": q.initial_pressure_pa_abs * 1.01})
    with pytest.raises(ValueError):
        tb.evaluate_tank_breathing(changed, c)
    changed = q.model_copy(update={"closures": q.closures.model_copy(update={"wall_axial_UA_W_K": 1.23})})
    with pytest.raises(ValueError):
        tb.evaluate_tank_breathing(changed, c)


@pytest.mark.parametrize("mode", ["pressure_relief_hysteresis", "atmospheric_mixture"])
def test_unsupported_physics_never_calls_native(mode, monkeypatch):
    q, c = _example.make_case(reviewed=True, mode=mode)
    monkeypatch.setattr(tb, "native_backend_metadata", lambda: pytest.fail("unsupported owner access"))
    r = tb.evaluate_tank_breathing(q, c)
    assert r["status"] == "out_of_scope"
    assert r["native_called"] is False


def test_tamper_and_no_signal_for_unreviewed_record():
    q, c = _example.make_case()
    r = tb.evaluate_tank_breathing(q, c)
    r["notes"] = []
    assert not tb.verify_tank_breathing_record(r)["valid"]
    with pytest.raises(ValueError):
        tb.to_signal_frames(r, c)


def test_copied_nonfinite_payload_is_revalidated():
    q, c = _example.make_case()
    with pytest.raises((ValueError, ValidationError)):
        tb.evaluate_tank_breathing(q.model_copy(update={"start_time_s": float("nan")}), c)


def test_duplicate_phase_ports_rejected():
    with pytest.raises(ValidationError):
        tb.TankBreathingPorts(
            liquid_inlet_id="p", liquid_outlet_id="p", vapor_inlet_id="v", vapor_outlet_id="o"
        )


def test_native_wet_phase_interior_is_withheld_before_claiming_closed_tank_balances():
    pytest.importorskip("lh2dt")
    q, c = _example.make_case(reviewed=True)
    r = tb.evaluate_tank_breathing(q, c)
    assert r["status"] == "out_of_scope", r["notes"]
    assert any("wet interior" in note for note in r["notes"])
    assert r["native_called"] is True
    assert tb.verify_tank_breathing_record(r, request=q, context=c)["valid"]
    with pytest.raises(ValueError, match="complete verified tank history"):
        tb.to_signal_frames(r, c)
