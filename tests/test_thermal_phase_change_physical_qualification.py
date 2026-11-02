"""Bounded physical checks for the local fixed-mass enthalpy phase-change route."""

import builtins
import math

import pytest

from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.lumped_phase_change import PhaseStorageRequest, PhaseStorageWindow
from pipeline_integrity.phase_change_exchange import (
    REVIEWS,
    LumpedEnthalpyStorageCase,
    PhaseChangeRequest,
    boundary_metadata,
    evaluate_phase_change,
    local_phase_storage_metadata,
)


MASS = 2.0
TRANSITION_K = 273.15
SOLID_CP = 4000.0
LIQUID_CP = 5000.0
LATENT = 100_000.0
LATENT_TOTAL = MASS * LATENT
EVIDENCE = (
    EvidenceRef(id="material-properties", citation="Authored synthetic constant-property inputs", origin="synthetic", license_id="MIT"),
    EvidenceRef(id="boundary-history", citation="Authored synthetic heat-boundary history", origin="synthetic", license_id="MIT"),
)
BASE_CONTEXT = RunContext(
    asset_id="synthetic-fixed-mass-body",
    scenario_id="lumped_enthalpy_phase_transition",
    clock_id="elapsed_seconds",
    origin="synthetic",
    evidence=EVIDENCE,
)


def evaluate_case(*, initial_enthalpy_J=0.0, windows, mass_kg=MASS, reviewed=True):
    storage = PhaseStorageRequest(
        id="synthetic-storage-run",
        asset_id=BASE_CONTEXT.asset_id,
        clock_id=BASE_CONTEXT.clock_id,
        start_time_s=10.0,
        mass_kg=mass_kg,
        transition_temperature_K=TRANSITION_K,
        solid_heat_capacity_J_kg_K=SOLID_CP,
        liquid_heat_capacity_J_kg_K=LIQUID_CP,
        latent_heat_J_kg=LATENT,
        initial_enthalpy_J=initial_enthalpy_J,
        windows=tuple(windows),
        lumped_temperature_and_constant_properties_reviewed=reviewed,
        evidence_ids=("material-properties", "boundary-history"),
    )
    case = LumpedEnthalpyStorageCase(
        storage=storage,
        model_source_sha256=local_phase_storage_metadata()["model_source_sha256"],
        absolute_energy_tolerance_J=1e-8,
    )
    request = PhaseChangeRequest(
        id="synthetic-phase-change",
        asset_id=BASE_CONTEXT.asset_id,
        event_id="synthetic-heating-or-cooling-event",
        clock_id=BASE_CONTEXT.clock_id,
        time_s=storage.start_time_s,
        case=case,
        evidence_ids=("material-properties", "boundary-history"),
        reviews={name: True for name in REVIEWS},
        review_evidence_ids={name: "material-properties" for name in REVIEWS},
        owner_pins={},
    )
    context = BASE_CONTEXT.model_copy(
        update={"metadata": {"phase_change_boundary": boundary_metadata(request)}}
    )
    return request, context, evaluate_phase_change(request, context)


def window(name, duration, *, direct_heat_W=0.0, conductance_W_K=0.0, ambient_K=TRANSITION_K):
    return PhaseStorageWindow(
        id=name,
        duration_s=duration,
        ambient_temperature_K=ambient_K,
        conductance_W_K=conductance_W_K,
        direct_heat_W=direct_heat_W,
        evidence_id="boundary-history",
    )


def test_latent_plateau_event_and_liquid_sensible_heat_match_closed_form():
    _, _, result = evaluate_case(
        windows=(window("melt", 200.0, direct_heat_W=1000.0), window("liquid-warm", 50.0, direct_heat_W=1000.0))
    )
    values = result["values"]
    assert result["status"] == "computed_diagnostic"
    assert result["local_model_called"] and not result["native_called"]
    assert values["duration_s"] == pytest.approx(250.0)
    assert values["enthalpy_J"] == pytest.approx(LATENT_TOTAL + 50_000.0, abs=1e-8)
    assert values["temperature_K"] == pytest.approx(TRANSITION_K + 5.0, abs=1e-12)
    assert values["liquid_mass_fraction"] == pytest.approx(1.0, abs=0.0)
    events = result["local_model"]["phase_events"]
    assert len(events) == 1
    assert events[0]["time_s"] == pytest.approx(210.0)
    assert events[0]["boundary_enthalpy_J"] == pytest.approx(LATENT_TOTAL)
    assert values["direct_heat_J"] == pytest.approx(250_000.0, abs=1e-8)
    assert values["ambient_heat_J"] == pytest.approx(0.0, abs=1e-8)
    assert result["audits"]["energy_residual_J"] == pytest.approx(0.0, abs=1e-8)


def test_cooling_crosses_liquid_and_latent_regions_at_analytic_event_times():
    _, _, result = evaluate_case(
        initial_enthalpy_J=LATENT_TOTAL + 50_000.0,
        windows=(window("cool", 250.0, direct_heat_W=-1000.0),),
    )
    values = result["values"]
    events = result["local_model"]["phase_events"]
    assert result["status"] == "computed_diagnostic"
    assert [event["time_s"] - 10.0 for event in events] == pytest.approx([50.0, 250.0])
    assert [event["boundary_enthalpy_J"] for event in events] == pytest.approx([LATENT_TOTAL, 0.0])
    assert values["enthalpy_J"] == pytest.approx(0.0, abs=1e-8)
    assert values["temperature_K"] == pytest.approx(TRANSITION_K, abs=1e-12)
    assert values["liquid_mass_fraction"] == pytest.approx(0.0, abs=0.0)
    assert values["direct_heat_J"] == pytest.approx(-250_000.0, abs=1e-8)
    assert result["audits"]["energy_residual_J"] == pytest.approx(0.0, abs=1e-8)


def test_sensible_region_relaxation_matches_independent_newton_exponential():
    initial = -40_000.0
    capacity = MASS * SOLID_CP
    conductance = 100.0
    duration = math.log(2.0) * capacity / conductance
    _, _, result = evaluate_case(
        initial_enthalpy_J=initial,
        windows=(window("half-relaxation", duration, conductance_W_K=conductance),),
    )
    values = result["values"]
    expected_enthalpy = initial * math.exp(-conductance * duration / capacity)
    expected_temperature = TRANSITION_K + expected_enthalpy / capacity
    assert values["enthalpy_J"] == pytest.approx(expected_enthalpy, abs=1e-8)
    assert values["enthalpy_J"] == pytest.approx(-20_000.0, abs=1e-8)
    assert values["temperature_K"] == pytest.approx(expected_temperature, abs=1e-12)
    assert values["ambient_heat_J"] == pytest.approx(20_000.0, abs=1e-8)
    assert result["audits"]["energy_residual_J"] == pytest.approx(0.0, abs=1e-8)


def test_equal_transition_ambient_is_stationary_with_no_phase_event():
    _, _, result = evaluate_case(
        initial_enthalpy_J=0.4 * LATENT_TOTAL,
        windows=(window("stationary", 1234.0, conductance_W_K=150.0, ambient_K=TRANSITION_K),),
    )
    values = result["values"]
    assert result["status"] == "computed_diagnostic"
    assert values["enthalpy_J"] == pytest.approx(0.4 * LATENT_TOTAL, abs=1e-8)
    assert values["temperature_K"] == pytest.approx(TRANSITION_K, abs=1e-12)
    assert values["liquid_mass_fraction"] == pytest.approx(0.4, abs=1e-15)
    assert result["local_model"]["phase_events"] == []
    assert values["direct_heat_J"] == pytest.approx(0.0, abs=1e-8)
    assert values["ambient_heat_J"] == pytest.approx(0.0, abs=1e-8)
    assert values["mass_kg"] == pytest.approx(MASS)


def test_local_lumped_route_does_not_import_native_lh2dt_or_coolprop(monkeypatch):
    original_import = builtins.__import__

    def guarded(name, *args, **kwargs):
        if name.startswith(("lh2dt", "CoolProp")):
            raise AssertionError("local lumped enthalpy route must not import external property/phase solvers")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    _, _, result = evaluate_case(windows=(window("local-only", 10.0, direct_heat_W=50.0),))
    assert result["status"] == "computed_diagnostic"
    assert result["local_model_called"] and not result["native_called"]
    assert result["local_model"]["numerical_validation_completed"] is False
    assert result["physical_validation_claim"] is False
    assert result["broad_phase_change_complete"] is False
