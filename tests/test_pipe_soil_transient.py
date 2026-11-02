"""Radial assembly meaning and receipt faults; actual owner references run separately."""

import json
import math
import runpy
from contextlib import contextmanager
from copy import deepcopy

import pytest

from pipeline_integrity.adapters import ExternalResult
from pipeline_integrity.contracts import digest
from pipeline_integrity.pipe_soil_transient import (
    MODEL,
    OUTPUT_UNITS,
    PipeSoilBoundary,
    evaluate_pipe_soil_transient,
    pipe_soil_boundary,
    pipe_soil_signal_frames,
    verify_pipe_soil_transient_record,
)

OwnerSpy = runpy.run_path("tests/test_characterization.py")["OwnerSpy"]


class ThermalSpy(OwnerSpy):
    """A uniform, unforced authored response tests contracts, not an engine implementation."""

    def describe(self, method):
        description = super().describe(method)
        description["output_units"] = OUTPUT_UNITS
        return description

    def evaluate(self, method, parameters, context, **kwargs):
        old = super().evaluate(method, parameters, context, **kwargs)
        p = old.payload
        n = len(parameters["initial_temperature_K"])
        nt = len(parameters["time_s"])
        e = parameters["cell_edges_m"]
        p["units"] = OUTPUT_UNITS
        p["values"] = {
            "cell_centers_m": [math.sqrt((a * a + b * b) / 2) for a, b in zip(e, e[1:], strict=False)],
            "temperature_history_K": [parameters["initial_temperature_K"].copy() for _ in range(nt)],
            "boundary_surface_temperature_K": [[300.0, 300.0] for _ in range(nt)],
            "boundary_heat_rate_into_solid_W": [[0.0, 0.0] for _ in range(nt)],
            "distributed_heat_rate_into_solid_W": [0.0] * nt,
            "volumetric_generation_rate_W": [0.0] * nt,
            "cumulative_boundary_energy_into_solid_J": [0.0, 0.0],
            "cumulative_distributed_energy_into_solid_J": 0.0,
            "cumulative_generated_energy_J": 0.0,
            "maximum_absolute_discrete_energy_balance_error_J": 0.0,
            "maximum_relative_discrete_energy_balance_error": 0.0,
            "internal_steps": nt - 1,
            "maximum_nonlinear_iterations_used": 1,
        }
        assert all(len(row) == n for row in p["values"]["temperature_history_K"])
        return ExternalResult(**{**old.model_dump(), "payload": p, "payload_digest": digest(p)})


def case(*, pinned=False):
    request, context = runpy.run_path("examples/pipe_soil_transient.py")["make_case"](
        source_sha256="0" * 64 if pinned else None,
        model_version="spy-v1" if pinned else None,
        schema_digest=digest({"type": "object"}) if pinned else None,
    )
    return request, context


@pytest.fixture
def mocked_source(monkeypatch):
    @contextmanager
    def guard(*args):
        fingerprint = dict(source_tree_sha256="0" * 64, source_tree_files=76)
        yield dict(before=fingerprint, after=fingerprint, source_unchanged=True)

    monkeypatch.setattr("pipeline_integrity.pipe_soil_transient.material_source_guard", guard)


@pytest.mark.parametrize(
    "mode", ["pins", "reviews", "contact", "initial", "properties", "boundary", "phase_review"]
)
def test_unknowns_are_withheld_before_owner_import(mode, monkeypatch):
    request, context = case(pinned=mode != "pins")
    if mode == "reviews":
        request = request.model_copy(update={"applicability_reviews": {}})
    elif mode == "phase_review":
        request = request.model_copy(
            update={
                "applicability_reviews": {
                    k: v
                    for k, v in request.applicability_reviews.items()
                    if k != "phase_change_absent_or_represented_in_supplied_capacity"
                }
            }
        )
    elif mode == "contact":
        request = request.model_copy(update={"perfect_contacts": None})
    elif mode == "initial":
        shell = request.shells[0].model_copy(update={"initial_cell_temperature_K": (None,) * 4})
        request = request.model_copy(update={"shells": (shell, request.shells[1])})
    elif mode == "properties":
        m = request.materials[1].model_copy(update={"conductivity_W_m_K": (None, None)})
        request = request.model_copy(update={"materials": (request.materials[0], m)})
    elif mode == "boundary":
        b = request.outer_boundary.model_copy(update={"temperature_K": (300.0, None, 300.0, 300.0)})
        request = request.model_copy(update={"outer_boundary": b})
    monkeypatch.setattr(
        "pipeline_integrity.pipe_soil_transient.prepare_material_job",
        lambda *a, **k: pytest.fail("owner imported"),
    )
    record = evaluate_pipe_soil_transient(request, context)
    assert record["status"] == "needs_data" and record["native_receipt"] is None
    verify_pipe_soil_transient_record(record)


@pytest.mark.parametrize("mode", ["finite_contact", "initial_support"])
def test_unsupported_physics_is_not_missing_or_silently_repaired(mode):
    request, context = case()
    if mode == "finite_contact":
        request = request.model_copy(update={"perfect_contacts": False})
    else:
        s = request.shells[0].model_copy(update={"initial_cell_temperature_K": (500.0,) * 4})
        request = request.model_copy(update={"shells": (s, request.shells[1])})
    record = evaluate_pipe_soil_transient(request, context)
    assert record["status"] == "out_of_scope"
    verify_pipe_soil_transient_record(record)


@pytest.mark.parametrize(
    "mode",
    [
        "nan",
        "boolean_radius",
        "gap",
        "clock",
        "time_order",
        "inactive_boundary",
        "array_clock",
        "role",
        "budget",
        "unknown_material",
        "boundary_event",
        "evidence",
    ],
)
def test_invalid_layout_clock_evidence_and_budgets_rejected_before_import(mode):
    request, context = case()
    if mode == "nan":
        request = request.model_copy(update={"maximum_step_s": float("nan")})
    elif mode == "boolean_radius":
        s = request.shells[0].model_copy(
            update={"radial_edges_m": (False, *request.shells[0].radial_edges_m[1:])}
        )
        request = request.model_copy(update={"shells": (s, request.shells[1])})
    elif mode == "gap":
        s = request.shells[1].model_copy(
            update={"radial_edges_m": (0.121, *request.shells[1].radial_edges_m[1:])}
        )
        request = request.model_copy(update={"shells": (request.shells[0], s)})
    elif mode == "clock":
        context = context.model_copy(update={"clock_id": "other"})
    elif mode == "time_order":
        request = request.model_copy(update={"time_s": (0.0, 10.0, 10.0, 40.0)})
    elif mode == "inactive_boundary":
        request = request.model_copy(
            update={"outer_boundary": request.outer_boundary.model_copy(update={"emissivity": 0.0})}
        )
    elif mode == "array_clock":
        request = request.model_copy(
            update={"outer_boundary": request.outer_boundary.model_copy(update={"temperature_K": (300.0,)})}
        )
    elif mode == "role":
        s = request.shells[0].model_copy(update={"role": "soil"})
        request = request.model_copy(update={"shells": (s, request.shells[1])})
    elif mode == "budget":
        request = request.model_copy(update={"maximum_work_units": 1})
    elif mode == "unknown_material":
        s = request.shells[0].model_copy(update={"material_id": "unknown"})
        request = request.model_copy(update={"shells": (s, request.shells[1])})
    elif mode == "boundary_event":
        context = context.model_copy(update={"metadata": {}})
    else:
        request = request.model_copy(update={"evidence_ids": ("absent",)})
    with pytest.raises((ValueError, TypeError)):
        evaluate_pipe_soil_transient(request, context)


def stored(mocked_source):
    request, context = case(pinned=True)
    request = request.model_copy(
        update={
            "inner_boundary": PipeSoilBoundary(id="inside", kind="adiabatic", evidence_id="authored"),
            "outer_boundary": PipeSoilBoundary(id="outside", kind="adiabatic", evidence_id="authored"),
        }
    )
    context = context.model_copy(update={"metadata": {"pipe_soil_boundary": pipe_soil_boundary(request)}})
    record = evaluate_pipe_soil_transient(request, context, client=ThermalSpy(), owner_source_root="test-spy")
    return record, context


def test_native_assembly_context_units_energy_and_signal_offset(mocked_source):
    record, context = stored(mocked_source)
    verify_pipe_soil_transient_record(record)
    assert record["status"] == "computed" and record["audit"]["final_energy_residual_J"] == 0
    assert record["native_receipt"]["job"]["model_id"] == MODEL
    frames = pipe_soil_signal_frames(record, context)
    assert [f.time_s for f in frames] == [100.0, 110.0, 120.0, 140.0]
    assert all(f.origin == "synthetic" and f.values["inner_heat_into_solid_W"] == 0 for f in frames)
    assert frames[0].units["cell.soil.0.temperature_K"] == "K"
    assert all(v is False for v in record["qualification"].values())
    json.dumps(record, allow_nan=False)


def rehash(record):
    native = record["native_receipt"]
    payload = native["native"]["payload"]
    payload["input_digest"] = digest(
        {k: payload[k] for k in ("model_id", "model_version", "inputs", "context")}
    )
    native["native"]["payload_digest"] = digest(native["native"]["payload"])
    native["result_digest"] = digest({k: v for k, v in native.items() if k != "result_digest"})
    record["record_digest"] = digest({k: v for k, v in record.items() if k != "record_digest"})


@pytest.mark.parametrize(
    "mode",
    [
        "native_unit",
        "native_context",
        "native_input",
        "initial",
        "heat_null",
        "heat_boolean",
        "cell_support",
        "boundary_energy",
        "parent_audit",
        "qualification",
        "version",
        "schema_pin",
        "source_pin",
        "hidden_generation",
        "boundary_point_heat",
        "boundary_surface",
        "solver_error",
        "extra_input",
    ],
)
def test_rehashed_record_semantics_still_refuse_corruption(mocked_source, mode):
    record, context = stored(mocked_source)
    record = deepcopy(record)
    receipt = record["native_receipt"]
    p = receipt["native"]["payload"]
    if mode == "native_unit":
        p["units"]["temperature_history_K"] = "degC"
    elif mode == "native_context":
        p["context"]["time_basis"] = "other"
    elif mode == "native_input":
        p["inputs"]["left_boundary"]["kind"] = "prescribed_heat_flux"
    elif mode == "initial":
        p["values"]["temperature_history_K"][0][0] = 301.0
    elif mode in {"heat_null", "heat_boolean"}:
        p["values"]["boundary_heat_rate_into_solid_W"][-1][0] = None if mode == "heat_null" else False
    elif mode == "cell_support":
        p["values"]["temperature_history_K"][-1][0] = 500.0
    elif mode == "boundary_energy":
        p["values"]["cumulative_boundary_energy_into_solid_J"] = [1000.0, 0.0]
    elif mode == "parent_audit":
        record["audit"]["total_solid_energy_change_J"] = 1000.0
    elif mode == "qualification":
        record["qualification"]["ground_surface_plane_modelled"] = True
    elif mode == "version":
        receipt["job"]["model_version"] = "different"
    elif mode == "schema_pin":
        receipt["job"]["input_schema_digest"] = "f" * 64
    elif mode == "source_pin":
        record["source_pin_report"]["after"]["source_tree_sha256"] = "a" * 64
    elif mode == "boundary_point_heat":
        p["values"]["boundary_heat_rate_into_solid_W"][-1] = [100.0, -100.0]
    elif mode == "boundary_surface":
        p["values"]["boundary_surface_temperature_K"][-1][0] = 100.0
    elif mode == "solver_error":
        p["values"]["maximum_relative_discrete_energy_balance_error"] = 0.9
    elif mode == "extra_input":
        p["inputs"]["unrequested_future_physics"] = True
    else:
        p["values"]["cumulative_generated_energy_J"] = 1.0
    rehash(record)
    with pytest.raises((ValueError, TypeError)):
        verify_pipe_soil_transient_record(record)
    with pytest.raises((ValueError, TypeError)):
        pipe_soil_signal_frames(record, context)
