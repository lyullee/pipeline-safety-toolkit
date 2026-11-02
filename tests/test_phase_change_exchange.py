"""Owner thermal routes: independent limits, budgets and unsigned replay attacks."""

import builtins
import json
import math
import runpy
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from pipeline_integrity.contracts import digest
from pipeline_integrity.phase_change_exchange import (
    PhaseChangeRequest,
    boundary_metadata,
    evaluate_phase_change,
    to_flow_boundaries,
    to_signal_frames,
    verify_phase_change_record,
)
from pipeline_integrity.thermo import StateRequest

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = runpy.run_path(str(ROOT / "examples/phase_change_exchange.py"))
MAKE = EXAMPLE["make_case"]


def changed(q, c, **changes):
    case = q.case.model_copy(update=changes)
    q = PhaseChangeRequest.model_validate(q.model_copy(update={"case": case}).model_dump(mode="python"))
    return q, c.model_copy(update={"metadata": {"phase_change_boundary": boundary_metadata(q)}})


@pytest.fixture(scope="module")
def cases():
    source = ROOT.parent / "tmp/analysis/repos/lh2dt_models/src"
    if source.is_dir():
        sys.path.insert(0, str(source))
    pytest.importorskip("lh2dt.phase_change")
    values = {}
    for name in EXAMPLE["KINDS"]:
        q, c = MAKE(name, reviewed=True)
        values[name] = (q, c, evaluate_phase_change(q, c))
    return values


@pytest.mark.parametrize("kind", EXAMPLE["KINDS"])
def test_default_never_imports_optional_owner_or_properties(kind, monkeypatch):
    original = builtins.__import__

    def guarded(name, *args, **kwargs):
        if name.startswith(("CoolProp", "lh2dt", "scipy")):
            raise AssertionError("unreviewed route imported a native model")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    q, c = MAKE(kind)
    r = evaluate_phase_change(q, c)
    assert r["status"] == "needs_data" and not r["native_called"]
    assert r["native"] is r["provider"] is r["values"] is None
    assert verify_phase_change_record(r, request=q, context=c)["valid"]


@pytest.mark.parametrize("kind", EXAMPLE["KINDS"][:6])
def test_six_native_routes_replay_and_preserve_qualification(cases, kind):
    q, c, r = cases[kind]
    assert r["status"] == "computed_diagnostic", r["notes"]
    assert r["native_called"] and r["native"] and r["provider"]
    assert not r["physical_validation_claim"] and not r["broad_phase_change_complete"]
    assert verify_phase_change_record(json.loads(json.dumps(r)), request=q, context=c)["valid"]


@pytest.mark.parametrize("kind", ["bulk_freezing", "ambient_frost"])
def test_unsupported_physics_is_not_a_successful_zero(cases, kind):
    q, c, r = cases[kind]
    assert r["status"] == "out_of_scope" and not r["native_called"]
    assert r["values"] is None and verify_phase_change_record(r)["valid"]
    with pytest.raises(ValueError):
        to_signal_frames(r, c)


def test_independent_kinetic_scalar_reference_and_heat_meaning(cases):
    q, c, r = cases["kinetic_interface"]
    args = r["native"]["request"]
    expected = (
        2
        * args["accommodation_coefficient"]
        / (2 - args["accommodation_coefficient"])
        / math.sqrt(2 * math.pi * args["specific_gas_constant_J_kgK"])
        * (
            args["interface_pressure_Pa"] / math.sqrt(args["interface_temperature_K"])
            - args["vapor_pressure_Pa"] / math.sqrt(args["vapor_temperature_K"])
        )
    )
    assert r["values"]["mass_flux_liquid_to_vapor_kg_m2_s"] == pytest.approx(expected, rel=1e-13)
    assert r["values"]["mass_rate_liquid_to_vapor_kg_s"] == pytest.approx(expected * q.case.area_m2)
    assert r["values"]["latent_power_demand_W"] == pytest.approx(
        expected * q.case.area_m2 * r["values"]["latent_heat_J_kg"]
    )
    assert r["audits"]["interface_energy_residual_W"] is None
    assert r["values"]["cumulative_mass_transferred_kg"] is None
    with pytest.raises(ValueError):
        to_flow_boundaries(r, c)


def test_condensation_sign_and_area_scaling(cases):
    q, c, r = cases["kinetic_interface"]
    saturation_pressure = r["native"]["request"]["interface_pressure_Pa"]
    q, c = changed(
        q,
        c,
        vapor_state=StateRequest(
            fluid=q.property_basis.fluid, inputs={"P": saturation_pressure * 1.06, "T": 21.0}
        ),
        area_m2=q.case.area_m2 * 2,
    )
    result = evaluate_phase_change(q, c)
    assert result["status"] == "computed_diagnostic", result["notes"]
    assert result["values"]["mass_rate_liquid_to_vapor_kg_s"] < 0
    assert result["values"]["latent_power_demand_W"] < 0


def test_kinetic_interface_heat_supply_is_checked_not_assumed(cases):
    q, c, r = cases["kinetic_interface"]
    demand = r["values"]["latent_power_demand_W"] - r["values"]["gas_sensible_heat_into_interface_W"]
    q, c = changed(q, c, liquid_side_heat_into_interface_W=demand, external_heat_into_interface_W=0.0)
    matched = evaluate_phase_change(q, c)
    assert matched["status"] == "computed_diagnostic"
    q, c = changed(q, c, liquid_side_heat_into_interface_W=demand + 100.0)
    failed = evaluate_phase_change(q, c)
    assert failed["status"] == "diagnostic_rejected"
    assert failed["audits"]["interface_energy_residual_W"] == pytest.approx(100.0)


@pytest.mark.parametrize(
    "changes",
    [
        {"interface_temperature_K": 10.0},
        {"mass_accommodation": 0.0},
        {"energy_accommodation": 0.0},
        {"maximum_relative_pressure_imbalance": 1e-6},
    ],
)
def test_kinetic_unsupported_domains_do_not_clip(cases, changes):
    q, c, _ = cases["kinetic_interface"]
    q, c = changed(q, c, **changes)
    r = evaluate_phase_change(q, c)
    assert r["status"] == "out_of_scope" and r["values"] is None


@pytest.mark.parametrize("kind", ["steady_vaporizer", "steady_reliquefier"])
def test_steady_native_enthalpy_balance_and_real_ports(cases, kind):
    q, c, r = cases[kind]
    h_in = r["native"]["inlet"]["specific_enthalpy_J_kg"]
    h_out = r["native"]["result"]["outlet"]["specific_enthalpy_J_kg"]
    assert q.case.mass_flow_kg_s * (h_out - h_in) == pytest.approx(r["values"]["heat_into_fluid_W"], abs=1e-7)
    assert r["values"]["cumulative_heat_J"] is None
    inlet, outlet = to_flow_boundaries(r, c)
    assert (inlet.port_id, outlet.port_id) == (q.case.inlet_port_id, q.case.outlet_port_id)
    assert inlet.direction == "into_component" and outlet.direction == "out_of_component"
    assert inlet.mass_flow_kg_s == outlet.mass_flow_kg_s == q.case.mass_flow_kg_s
    assert inlet.property_basis == outlet.property_basis and "runtime-anchor:" in inlet.property_basis
    assert (r["values"]["heat_into_fluid_W"] > 0) == (kind == "steady_vaporizer")


def test_insulation_parallel_paths_equal_temperature_and_vacuum_domain(cases):
    q, c, r = cases["vacuum_insulation"]
    native = r["native"]["result"]
    assert native["total_heat_into_cold_W"] == pytest.approx(
        math.fsum(
            native[k]
            for k in (
                "radiation_heat_W",
                "mli_solid_heat_W",
                "support_heat_W",
                "residual_gas_heat_W",
                "localized_heat_W",
            )
        ),
        rel=1e-13,
    )
    q, c = changed(q, c, warm_temperature_K=q.case.cold_temperature_K)
    equal = evaluate_phase_change(q, c)
    assert equal["status"] == "computed_diagnostic" and equal["values"]["heat_into_cold_W"] == 0.0
    q, c, _ = cases["vacuum_insulation"]
    q, c = changed(q, c, vacuum_pressure_Pa_abs=q.case.maximum_valid_vacuum_pressure_Pa_abs * 2)
    rejected = evaluate_phase_change(q, c)
    assert rejected["status"] == "out_of_scope" and rejected["values"] is None


@pytest.mark.parametrize("kind", ["dynamic_vaporizer", "dynamic_reliquefier"])
def test_dynamic_closed_energy_is_not_endpoint_heat_times_duration(cases, kind):
    q, c, r = cases[kind]
    assert abs(r["audits"]["closed_adiabatic_total_energy_residual_J"]) < 1e-7
    assert r["values"]["cumulative_external_heat_J"] is None
    assert r["values"]["cumulative_inlet_enthalpy_J"] is None
    assert r["values"]["cumulative_outlet_enthalpy_J"] is None
    assert r["values"]["fluid_energy_change_J"] == pytest.approx(
        -r["values"]["wall_energy_change_J"], abs=1e-7
    )
    frame = to_signal_frames(r, c)[0]
    assert frame.time_s == q.time_s + q.case.duration_s
    assert frame.valid["cumulative_external_heat_J"] is False
    assert frame.units["endpoint_heat_from_wall_W"] == "W" and frame.units["fluid_energy_change_J"] == "J"


def test_dynamic_open_flow_keeps_missing_integrated_energy_and_known_mass(cases):
    q, c, _ = cases["dynamic_vaporizer"]
    q, c = changed(q, c, mass_flow_in_kg_s=1e-5, mass_flow_out_kg_s=2e-5, reservoir_UA_W_K=0.01)
    r = evaluate_phase_change(q, c)
    assert r["status"] == "computed_diagnostic", r["notes"]
    assert r["audits"]["closed_adiabatic_total_energy_residual_J"] is None
    assert r["values"]["cumulative_external_heat_J"] is None
    assert abs(r["audits"]["mass_increment_residual_kg"]) < 1e-12


def test_dynamic_reviewed_duration_limit_and_missing_parameter(cases):
    q, c, _ = cases["dynamic_vaporizer"]
    q, c = changed(q, c, duration_s=1.0)
    assert evaluate_phase_change(q, c)["status"] == "out_of_scope"
    q, c = changed(q, c, duration_s=None)
    r = evaluate_phase_change(q, c)
    assert r["status"] == "needs_data" and not r["native_called"]


@pytest.mark.parametrize(
    "kind", ["kinetic_interface", "steady_vaporizer", "dynamic_vaporizer", "vacuum_insulation"]
)
def test_unknown_source_time_does_not_invent_signal_or_port_time(cases, kind):
    q, c, _ = cases[kind]
    q = q.model_copy(update={"time_s": None})
    c = c.model_copy(update={"metadata": {"phase_change_boundary": boundary_metadata(q)}})
    r = evaluate_phase_change(q, c)
    # None time is explicitly allowed for steady material/closure diagnostics.
    assert r["status"] in {"needs_data", "computed_diagnostic"}
    with pytest.raises(ValueError):
        to_signal_frames(r, c)
    with pytest.raises(ValueError):
        to_flow_boundaries(r, c)


@pytest.mark.parametrize("which", ["asset", "clock", "boundary", "evidence", "fluid", "nonfinite"])
def test_context_and_cloned_invalid_inputs_are_revalidated(cases, which):
    q, c, _ = cases["steady_vaporizer"]
    if which == "asset":
        c = c.model_copy(update={"asset_id": "other"})
    elif which == "clock":
        c = c.model_copy(update={"clock_id": "other"})
    elif which == "boundary":
        c = c.model_copy(update={"metadata": {}})
    elif which == "evidence":
        c = c.model_copy(update={"evidence": ()})
    elif which == "fluid":
        q = q.model_copy(
            update={
                "case": q.case.model_copy(
                    update={"inlet_state": StateRequest(fluid="Water", inputs={"P": 1e5, "T": 300.0})}
                )
            }
        )
    else:
        q = q.model_copy(update={"case": q.case.model_copy(update={"capacity_W": float("inf")})})
    with pytest.raises(ValueError):
        evaluate_phase_change(q, c)


@pytest.mark.parametrize(
    "what", ["output", "native", "audit", "status", "source", "reference", "qualification", "context"]
)
def test_rehashed_thermal_record_mutations_refused(cases, what):
    q, c, original = cases["steady_vaporizer"]
    r = deepcopy(original)
    if what == "output":
        r["values"]["heat_into_fluid_W"] *= 2
    elif what == "native":
        r["native"]["result"]["outlet"]["pressure_Pa"] *= 2
    elif what == "audit":
        r["audits"]["steady_energy_residual_W"] = 100.0
    elif what == "status":
        r["status"] = "computed"
    elif what == "source":
        r["implementation_sha256"] = "0" * 64
    elif what == "reference":
        r["provider"]["reference_anchors"][q.property_basis.fluid]["specific_enthalpy_J_kg"] += 100.0
    elif what == "qualification":
        r["physical_validation_claim"] = True
    else:
        r["context"]["scenario_id"] = "changed"
    r["record_digest"] = digest({k: v for k, v in r.items() if k != "record_digest"})
    assert not verify_phase_change_record(r, request=q, context=c)["valid"]
