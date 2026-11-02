"""Actual owner thermodynamics, independent map references and fail-closed coupling."""

import builtins
import json
import math
import runpy
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from pipeline_integrity.compressor_train import (
    CompressorMap,
    CompressorTrainRequest,
    boundary_metadata,
    evaluate_compressor_train,
    to_signal_frame,
    verify_compressor_train_record,
)
from pipeline_integrity.contracts import digest

ROOT = Path(__file__).resolve().parents[1]
MAKE = runpy.run_path(str(ROOT / "examples/compressor_train.py"))["make_case"]


def recontext(q, c):
    return c.model_copy(
        update={"metadata": {**c.metadata, "compressor_train_boundary": boundary_metadata(q)}}
    )


def stage_change(q, c, index=0, **changes):
    stages = list(q.stages)
    stages[index] = stages[index].model_copy(update=changes)
    q = q.model_copy(update={"stages": tuple(stages)})
    return q, recontext(q, c)


@pytest.fixture(scope="module")
def baseline():
    source = ROOT.parent / "tmp/analysis/repos/lh2dt_models/src"
    if source.is_dir():
        sys.path.insert(0, str(source))
    pytest.importorskip("lh2dt.compressor")
    q, c = MAKE(reviewed=True)
    return q, c, evaluate_compressor_train(q, c)


def test_default_no_native_import_and_valid_withheld_receipt(monkeypatch):
    original = builtins.__import__

    def guarded(name, *args, **kwargs):
        if name.startswith(("CoolProp", "lh2dt", "scipy")):
            pytest.fail("unreviewed train imported a native model")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    q, c = MAKE()
    r = evaluate_compressor_train(q, c)
    assert r["status"] == "needs_data" and not r["native_called"] and r["backend"] is None
    assert verify_compressor_train_record(r, request=q, context=c)["valid"]


def test_actual_owner_stage_and_train_conservation(baseline):
    q, c, r = baseline
    assert r["status"] == "computed_diagnostic"
    assert r["audits"]["within_energy_budget"]
    for row, stage in zip(r["values"]["stages"], q.stages, strict=True):
        inlet, hot, outlet = (
            row[k] for k in ("inlet_total_state", "compressor_outlet_total_state", "outlet_total_state")
        )
        assert row["gas_power_W"] == pytest.approx(
            q.mass_flow_kg_s * (hot["specific_enthalpy_J_kg"] - inlet["specific_enthalpy_J_kg"])
        )
        assert row["heat_rejected_W"] == pytest.approx(
            q.mass_flow_kg_s * (hot["specific_enthalpy_J_kg"] - outlet["specific_enthalpy_J_kg"]), abs=1e-6
        )
        assert row["shaft_input_power_W"] * stage.mechanical_efficiency == pytest.approx(row["gas_power_W"])
        assert row["electrical_input_power_W"] * stage.drive_efficiency == pytest.approx(
            row["shaft_input_power_W"]
        )
        assert row["mass_in_kg_s"] == row["mass_out_kg_s"] == q.mass_flow_kg_s
        assert row["shaft_torque_N_m"] * stage.speed_rad_s == pytest.approx(row["shaft_input_power_W"])
    assert r["values"]["stages"][0]["outlet_total_state"] == r["values"]["stages"][1]["inlet_total_state"]
    assert abs(r["audits"]["overall_energy_residual_W"]) < r["audits"]["energy_tolerance_W"]
    assert not r["surge_dynamics_executed"] and not r["physical_validation_claim"]
    assert verify_compressor_train_record(r, request=q, context=c)["valid"]


def test_bilinear_map_independent_affine_surface_and_owner_receipt_link(baseline):
    q, _, r = baseline
    for stage, row in zip(q.stages, r["values"]["stages"], strict=True):
        point = row["map_point"]
        n, w = point["speed_coordinate_rad_s"], point["flow_coordinate_kg_s"]
        assert point["pressure_ratio"] == pytest.approx(1.2 + 0.0005 * n - 2 * w, abs=1e-13)
        assert point["isentropic_efficiency"] == pytest.approx(
            0.78 + 0.00001 * (n - 1500) - 0.2 * (w - 0.06), abs=1e-13
        )
        native = next(
            v
            for v in r["native_calls"]
            if v.get("stage_id") == stage.id and v["call"] == "LH2EquipmentClient.build/compressor.evaluate"
        )
        assert native["result"]["pressure_ratio"] == point["pressure_ratio"]
        assert native["result"]["efficiency"] == point["isentropic_efficiency"]
        assert native["inputs"]["inlet_total_state"] == row["inlet_total_state"]


def test_explicit_reference_correction_preserves_map_point_not_real_gas_work(baseline):
    q, c, r = baseline
    q = q.model_copy(
        update={
            "inlet_total_pressure_pa_abs": 200000.0,
            "inlet_total_temperature_K": 360.0,
            "mass_flow_kg_s": 0.04 * 2 / math.sqrt(1.2),
        }
    )
    q, c = stage_change(q, c, speed_rad_s=1500 * math.sqrt(1.2))
    changed = evaluate_compressor_train(q, c)
    assert changed["status"] == "computed_diagnostic"
    old = r["values"]["stages"][0]["map_point"]
    new = changed["values"]["stages"][0]["map_point"]
    for key in ("flow_coordinate_kg_s", "speed_coordinate_rad_s", "pressure_ratio", "isentropic_efficiency"):
        assert new[key] == pytest.approx(old[key], rel=1e-12)
    assert changed["values"]["stages"][0]["gas_power_W"] != pytest.approx(
        r["values"]["stages"][0]["gas_power_W"]
    )


@pytest.mark.parametrize("fluid", ["Hydrogen", "ParaHydrogen"])
def test_both_explicit_owner_fluids_and_actual_coordinates(baseline, fluid):
    q, c = MAKE(reviewed=True, fluid=fluid, stage_count=1, convention="actual")
    r = evaluate_compressor_train(q, c)
    assert r["status"] == "computed_diagnostic"
    point = r["values"]["stages"][0]["map_point"]
    assert (point["theta"], point["delta"]) == (1.0, 1.0)
    assert point["flow_coordinate_kg_s"] == q.mass_flow_kg_s
    assert point["speed_coordinate_rad_s"] == q.stages[0].speed_rad_s


def test_target_and_duty_cooling_boundaries_are_equivalent(baseline):
    q, c, old = baseline
    duty = old["values"]["stages"][0]["heat_rejected_W"]
    cooler = q.stages[0].intercooler.model_copy(
        update={"mode": "heat_removal", "outlet_total_temperature_K": None, "heat_removal_W": duty}
    )
    q, c = stage_change(q, c, intercooler=cooler)
    r = evaluate_compressor_train(q, c)
    assert r["status"] == "computed_diagnostic"
    # Compare the imposed physical P/H station and downstream energy budget.
    # A native P/S flash can choose a slightly different inversion path after
    # a 310 K P/T -> P/H roundtrip; no tighter temperature contract is invented.
    assert (
        r["values"]["stages"][0]["outlet_total_state"]["specific_enthalpy_J_kg"]
        == old["values"]["stages"][0]["outlet_total_state"]["specific_enthalpy_J_kg"]
    )
    downstream_energy_difference = q.mass_flow_kg_s * abs(
        r["values"]["outlet_total_state"]["specific_enthalpy_J_kg"]
        - old["values"]["outlet_total_state"]["specific_enthalpy_J_kg"]
    )
    assert downstream_energy_difference <= min(
        r["audits"]["energy_tolerance_W"], old["audits"]["energy_tolerance_W"]
    )
    assert r["values"]["heat_rejected_W"] == pytest.approx(duty, rel=1e-10)


def test_zero_duty_pressure_drop_is_isenthalpic_not_isothermal(baseline):
    q, c, _ = baseline
    cooler = q.stages[0].intercooler.model_copy(
        update={"mode": "heat_removal", "outlet_total_temperature_K": None, "heat_removal_W": 0.0}
    )
    q, c = stage_change(q, c, intercooler=cooler)
    r = evaluate_compressor_train(q, c)
    assert r["status"] == "computed_diagnostic"
    row = r["values"]["stages"][0]
    assert row["outlet_total_state"]["specific_enthalpy_J_kg"] == pytest.approx(
        row["compressor_outlet_total_state"]["specific_enthalpy_J_kg"], abs=1e-6
    )
    assert row["cooler_pressure_drop_pa"] == pytest.approx(3000.0)
    assert row["heat_rejected_W"] == pytest.approx(0.0, abs=1e-6)


@pytest.mark.parametrize(
    "change", ["capacity", "approach", "sink", "heating", "liquid", "pressure", "excess_duty"]
)
def test_physically_infeasible_cooler_is_refused_with_original_attempts(baseline, change):
    q, c, _ = baseline
    updates = {
        "capacity": {"available_heat_rejection_W": 1.0},
        "approach": {"minimum_approach_K": 50.0},
        "sink": {"sink_temperature_K": 400.0},
        "heating": {"outlet_total_temperature_K": 700.0},
        "liquid": {"outlet_total_temperature_K": 20.0},
        "pressure": {"pressure_drop_pa": 1e6},
        "excess_duty": {"mode": "heat_removal", "outlet_total_temperature_K": None, "heat_removal_W": 1e7},
    }[change]
    q, c = stage_change(q, c, intercooler=q.stages[0].intercooler.model_copy(update=updates))
    r = evaluate_compressor_train(q, c)
    assert r["status"] == "out_of_scope" and r["owner_called"] and r["values"] is None
    assert any(v.get("result") for v in r["native_calls"])
    with pytest.raises(ValueError):
        to_signal_frame(r, c)


def test_liquid_compressor_inlet_never_calls_owner_compressor(baseline):
    q, c, _ = baseline
    q = q.model_copy(update={"inlet_total_temperature_K": 20.0})
    r = evaluate_compressor_train(q, c)
    assert r["status"] == "out_of_scope" and r["property_called"] and not r["owner_called"]
    assert r["native_calls"][0]["result"]["phase"] == "liquid"


@pytest.mark.parametrize(
    "change",
    ["speed", "flow", "surge", "choke", "required_margin", "pressure_support", "temperature_support"],
)
def test_map_extrapolation_and_static_boundaries_refused(baseline, change):
    q, c, _ = baseline
    if change == "speed":
        q, c = stage_change(q, c, speed_rad_s=900.0)
    elif change == "flow":
        q = q.model_copy(update={"mass_flow_kg_s": 0.2})
    elif change == "surge":
        q = q.model_copy(update={"mass_flow_kg_s": 0.006})
    elif change == "choke":
        q = q.model_copy(update={"mass_flow_kg_s": 0.115})
    elif change == "required_margin":
        q, c = stage_change(q, c, minimum_surge_flow_margin_fraction=20.0)
    elif change == "pressure_support":
        q = q.model_copy(update={"inlet_total_pressure_pa_abs": 2.1e7})
    else:
        q = q.model_copy(update={"inlet_total_temperature_K": 1100.0})
    r = evaluate_compressor_train(q, c)
    assert r["status"] == "out_of_scope" and not r["owner_called"]


@pytest.mark.parametrize(
    "field",
    [
        "speed_rad_s",
        "performance_map",
        "mechanical_efficiency",
        "drive_efficiency",
        "intercooler",
        "inlet_port_id",
    ],
)
def test_stage_unknowns_before_native(baseline, field):
    q, c, _ = baseline
    q, c = stage_change(q, c, **{field: None})
    r = evaluate_compressor_train(q, c)
    assert r["status"] == "needs_data" and r["backend"] is None and not r["native_called"]


@pytest.mark.parametrize(
    "field",
    [
        "time_s",
        "fluid",
        "energy_reference",
        "inlet_total_pressure_pa_abs",
        "mass_flow_kg_s",
        "source_port_id",
    ],
)
def test_train_unknowns_before_native(baseline, field):
    q, c, _ = baseline
    q = q.model_copy(update={field: None})
    c = recontext(q, c)
    r = evaluate_compressor_train(q, c)
    assert r["status"] == "needs_data" and r["backend"] is None


@pytest.mark.parametrize(
    "field",
    ["reference_temperature_K", "reference_pressure_pa_abs", "fluid", "inlet_pressure_range_pa_abs", "knot"],
)
def test_missing_map_metadata_and_unknown_knot_are_not_filled(baseline, field):
    q, c, _ = baseline
    mp = q.stages[0].performance_map
    update = (
        {field: None}
        if field != "knot"
        else {"pressure_ratio": ((None, *mp.pressure_ratio[0][1:]), *mp.pressure_ratio[1:])}
    )
    q, c = stage_change(q, c, performance_map=mp.model_copy(update=update))
    r = evaluate_compressor_train(q, c)
    assert r["status"] == "needs_data" and r["backend"] is None


@pytest.mark.parametrize(
    "change",
    [
        "different_fluid",
        "energy_reference",
        "disconnected",
        "recycle",
        "same_shaft",
        "clock",
        "context_asset",
        "evidence",
        "boundary",
    ],
)
def test_physical_binding_and_reviewed_route(baseline, change):
    q, c, _ = baseline
    if change == "different_fluid":
        q, c = stage_change(
            q, c, performance_map=q.stages[0].performance_map.model_copy(update={"fluid": "ParaHydrogen"})
        )
    elif change == "energy_reference":
        q = q.model_copy(update={"energy_reference": "unknown-reference"})
        c = recontext(q, c)
    elif change == "disconnected":
        q, c = stage_change(q, c, index=1, inlet_port_id="other")
    elif change == "recycle":
        q, c = stage_change(
            q, c, index=1, compressor_outlet_port_id=q.source_port_id, outlet_port_id=q.source_port_id
        )
        q = q.model_copy(update={"target_port_id": q.source_port_id})
        c = recontext(q, c)
    elif change == "same_shaft":
        q, c = stage_change(q, c, index=1, shaft_id=q.stages[0].shaft_id, speed_rad_s=1600.0)
    else:
        updates = {
            "clock": {"clock_id": "other"},
            "context_asset": {"asset_id": "other"},
            "evidence": {"evidence": ()},
            "boundary": {"metadata": {}},
        }[change]
        with pytest.raises(ValueError):
            evaluate_compressor_train(q, c.model_copy(update=updates))
        return
    r = evaluate_compressor_train(q, c)
    assert r["status"] == "out_of_scope" and r["backend"] is None


def test_partial_prefix_cannot_be_exported_as_a_complete_train(baseline):
    q, c, _ = baseline
    q, c = stage_change(q, c, index=1, speed_rad_s=2500.0)
    r = evaluate_compressor_train(q, c)
    assert r["status"] == "out_of_scope" and len(r["stage_prefix"]) == 1 and r["values"] is None
    assert verify_compressor_train_record(r, request=q, context=c)["valid"]
    with pytest.raises(ValueError):
        to_signal_frame(r, c)


@pytest.mark.parametrize(
    "kind",
    ["power", "outlet", "map", "cooling", "native", "budget", "source", "status", "qualification", "clock"],
)
def test_rehashed_semantic_result_changes_fail_replay_and_export(baseline, kind):
    q, c, original = baseline
    r = deepcopy(original)
    if kind == "power":
        r["values"]["gas_power_W"] *= 1.1
    elif kind == "outlet":
        r["values"]["outlet_total_state"]["specific_enthalpy_J_kg"] += 1000.0
    elif kind == "map":
        r["values"]["stages"][0]["map_point"]["isentropic_efficiency"] *= 0.9
    elif kind == "cooling":
        r["values"]["heat_rejected_W"] += 1000.0
    elif kind == "native":
        next(v for v in r["native_calls"] if v["call"] == "LH2EquipmentClient.build/compressor.evaluate")[
            "result"
        ]["shaft_power_W"] *= 0.9
    elif kind == "budget":
        r["audits"]["within_energy_budget"] = False
    elif kind == "source":
        r["backend"]["owner_python_tree_sha256"] = "a" * 64
    elif kind == "status":
        r["status"] = "computed"
    elif kind == "qualification":
        r["surge_dynamics_executed"] = True
    else:
        r["context"]["clock_id"] = "another-clock"
    r["result_digest"] = digest({k: v for k, v in r.items() if k != "result_digest"})
    assert not verify_compressor_train_record(r)["valid"]
    with pytest.raises(ValueError):
        to_signal_frame(r, c)


def test_signal_frame_units_clock_total_state_and_origin(baseline):
    q, c, r = baseline
    f = to_signal_frame(json.loads(json.dumps(r)), c)
    assert f.time_s == 12.0 and f.clock_id == q.clock_id
    assert f.units["gas_power_W"] == "W" and f.units["outlet_total_pressure_pa_abs"] == "Pa"
    assert f.operating_mode == "steady-total-state-compressor-train"
    assert f.origin == "synthetic" and all(f.valid.values())


@pytest.mark.parametrize("field", ["temperature", "speed", "map", "context"])
def test_nonfinite_clones_invalid_not_unknown(baseline, field):
    q, c, _ = baseline
    if field == "temperature":
        q = q.model_copy(update={"inlet_total_temperature_K": float("nan")})
    elif field == "speed":
        q = q.model_copy(
            update={"stages": (q.stages[0].model_copy(update={"speed_rad_s": float("inf")}), *q.stages[1:])}
        )
    elif field == "map":
        mp = q.stages[0].performance_map.model_copy(
            update={
                "pressure_ratio": (
                    (float("nan"), *q.stages[0].performance_map.pressure_ratio[0][1:]),
                    *q.stages[0].performance_map.pressure_ratio[1:],
                )
            }
        )
        q = q.model_copy(
            update={"stages": (q.stages[0].model_copy(update={"performance_map": mp}), *q.stages[1:])}
        )
    else:
        c = c.model_copy(update={"metadata": {"bad": float("nan")}})
    with pytest.raises(ValueError):
        evaluate_compressor_train(q, c)


def test_map_shape_unit_and_clock_contracts(baseline):
    q, _, _ = baseline
    mp = q.stages[0].performance_map
    with pytest.raises(ValueError):
        CompressorMap.model_validate(
            mp.model_copy(update={"speed_rad_s": tuple(reversed(mp.speed_rad_s))}).model_dump(mode="python")
        )
    with pytest.raises(ValueError):
        CompressorTrainRequest.model_validate(
            q.model_copy(update={"time_s": 1e308, "clock_offset_s": 1e308}).model_dump(mode="python")
        )
