"""Actual original radial heat solver, analytic references and coupling semantics."""

import argparse
import json
import math
import re
import runpy
import sys
from copy import deepcopy
from pathlib import Path

import numpy as np
from scipy.optimize import brentq
from scipy.special import j0, j1, y0, y1

from pipeline_integrity.adapters import MaterialClient
from pipeline_integrity.contracts import digest
from pipeline_integrity.datasets import file_digest
from pipeline_integrity.material_source import material_source_fingerprint
from pipeline_integrity.pipe_soil_transient import (
    MODEL,
    PipeSoilBoundary,
    evaluate_pipe_soil_transient,
    pipe_soil_boundary,
    pipe_soil_signal_frames,
    verify_pipe_soil_transient_record,
)


def save(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--materials", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path, default=Path("artifacts/pipe_soil_transient"))
    args = parser.parse_args()
    args.artifacts.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str((args.materials / "src").resolve()))
    before = material_source_fingerprint(args.materials)
    client = MaterialClient()
    description = client.describe(MODEL)
    make = runpy.run_path("examples/pipe_soil_transient.py")["make_case"]
    records, checks = {}, {}

    def case(count=4):
        return make(
            source_sha256=before["source_tree_sha256"],
            model_version=description["model_version"],
            schema_digest=digest(description["input_schema"]),
            count=count,
        )

    def run(name, request, context):
        context = context.model_copy(update={"metadata": {"pipe_soil_boundary": pipe_soil_boundary(request)}})
        answer = evaluate_pipe_soil_transient(
            request, context, client=client, owner_source_root=args.materials
        )
        verify_pipe_soil_transient_record(answer)
        records[name] = answer
        if answer["status"] == "computed":
            frames = pipe_soil_signal_frames(answer, context)
            checks[name + "_clock_and_origin"] = [f.time_s for f in frames] == [
                request.clock_offset_s + t for t in request.time_s
            ] and all(f.origin == "synthetic" for f in frames)
            checks[name + "_energy"] = answer["audit"]["energy_within_tolerance"]
        return answer

    request, context = case()
    run("varying_fluid_and_outer_temperature", request, context)
    for sign in (1, -1):
        boundary = PipeSoilBoundary(
            id="imposed-inner-flux",
            kind="prescribed_heat_flux",
            heat_flux_into_solid_W_m2=(sign * 10.0,) * 4,
            evidence_id="authored",
        )
        outside = PipeSoilBoundary(id="outer-adiabatic", kind="adiabatic", evidence_id="authored")
        result = run(
            "constant_flux_" + str(sign),
            request.model_copy(update={"inner_boundary": boundary, "outer_boundary": outside}),
            context,
        )
        expected = sign * 10 * 2 * math.pi * 0.1 * request.cylinder_length_m * request.time_s[-1]
        checks[f"flux_{sign}_known_integral"] = (
            abs(result["audit"]["inner_boundary_energy_into_solid_J"] - expected) < 1e-9
        )
        checks[f"flux_{sign}_known_storage"] = (
            abs(result["audit"]["total_solid_energy_change_J"] - expected) < 1e-4
        )

    # A multi-material steady log profile is an independent cylindrical reference.
    Rwall = math.log(0.12 / 0.1) / (2 * math.pi * 10)
    Rsoil = math.log(0.2 / 0.12) / (2 * math.pi)
    q = 20 / (Rwall + Rsoil)
    profiles = []
    for s in request.shells:
        temperatures = []
        for a, b in zip(s.radial_edges_m, s.radial_edges_m[1:], strict=False):
            radius = math.sqrt((a * a + b * b) / 2)
            temperature = (
                320 - q * math.log(radius / 0.1) / (2 * math.pi * 10)
                if s.role == "pipe_wall"
                else 320 - q * Rwall - q * math.log(radius / 0.12) / (2 * math.pi)
            )
            temperatures.append(temperature)
        profiles.append(s.model_copy(update={"initial_cell_temperature_K": tuple(temperatures)}))
    steady = request.model_copy(
        update={
            "shells": tuple(profiles),
            "inner_boundary": PipeSoilBoundary(
                id="inner-isotherm",
                kind="prescribed_temperature",
                temperature_K=(320.0,) * 4,
                evidence_id="authored",
            ),
            "outer_boundary": PipeSoilBoundary(
                id="outer-isotherm",
                kind="prescribed_temperature",
                temperature_K=(300.0,) * 4,
                evidence_id="authored",
            ),
        }
    )
    result = run("steady_log_multimaterial", steady, context)
    values = result["native_receipt"]["native"]["payload"]["values"]
    maximum_steady_change = max(
        abs(a - b)
        for a, b in zip(values["temperature_history_K"][0], values["temperature_history_K"][-1], strict=True)
    )
    checks["steady_log_profile"] = maximum_steady_change < 1e-9
    checks["steady_log_boundary_heat"] = all(
        abs(pair[0] - q) < 1e-8 and abs(pair[1] + q) < 1e-8
        for pair in values["boundary_heat_rate_into_solid_W"]
    )
    # Preserve the owner's native relative zero-net-heat outcome; do not override it.
    checks["steady_native_status_preserved"] = (
        result["status"] == result["native_receipt"]["native"]["status"]
    )

    # Separable radial eigenmode with volume-averaged initial/reference temperatures.
    a, b = 0.1, 0.2
    lam = brentq(lambda z: j0(z * a) * y0(z * b) - y0(z * a) * j0(z * b), 20, 40, xtol=1e-13)

    def basis(radius):
        return j0(lam * radius) * y0(lam * a) - y0(lam * radius) * j0(lam * a)

    peak = max(abs(basis(a + (b - a) * i / 1000)) for i in range(1001))

    def cell_mean(left, right):
        def primitive(radius):
            return radius * (j1(lam * radius) * y0(lam * a) - y1(lam * radius) * j0(lam * a)) / lam

        return 2 * (primitive(right) - primitive(left)) / (right * right - left * left) / peak

    errors = {}
    for count in (4, 8, 16):
        r, ctx = case(count)
        materials = tuple(
            m.model_copy(
                update={"conductivity_W_m_K": (1.0, 1.0), "volumetric_heat_capacity_J_m3_K": (1e5, 1e5)}
            )
            for m in r.materials
        )
        shells = tuple(
            s.model_copy(
                update={
                    "initial_cell_temperature_K": tuple(
                        300 + 10 * cell_mean(left, right)
                        for left, right in zip(s.radial_edges_m, s.radial_edges_m[1:], strict=False)
                    )
                }
            )
            for s in r.shells
        )
        r = r.model_copy(
            update={
                "materials": materials,
                "shells": shells,
                "maximum_step_s": 0.05,
                "inner_boundary": PipeSoilBoundary(
                    id="inner-isotherm",
                    kind="prescribed_temperature",
                    temperature_K=(300.0,) * 4,
                    evidence_id="authored",
                ),
                "outer_boundary": PipeSoilBoundary(
                    id="outer-isotherm",
                    kind="prescribed_temperature",
                    temperature_K=(300.0,) * 4,
                    evidence_id="authored",
                ),
            }
        )
        answer = run(f"eigenmode_{count}", r, ctx)
        final = answer["native_receipt"]["native"]["payload"]["values"]["temperature_history_K"][-1]
        reference = [
            300 + 10 * cell_mean(left, right) * math.exp(-1e-5 * lam * lam * r.time_s[-1])
            for s in shells
            for left, right in zip(s.radial_edges_m, s.radial_edges_m[1:], strict=False)
        ]
        errors[str(count)] = max(
            abs(actual - expected) for actual, expected in zip(final, reference, strict=True)
        )
    checks["radial_eigenmode_spatial_refinement"] = errors["16"] < errors["8"] < errors["4"]
    checks["radial_eigenmode_fine_error"] = errors["16"] < 0.03
    coarse = run("eigenmode_time_coarse", r.model_copy(update={"maximum_step_s": 4.0}), ctx)
    coarse_final = coarse["native_receipt"]["native"]["payload"]["values"]["temperature_history_K"][-1]
    coarse_error = max(
        abs(actual - expected) for actual, expected in zip(coarse_final, reference, strict=True)
    )
    checks["radial_eigenmode_time_refinement"] = errors["16"] < coarse_error

    # Variable capacity is checked as enthalpy, not final Cv times delta T.
    variable = tuple(
        m.model_copy(
            update={
                "temperature_K": (280.0, 300.0, 330.0),
                "conductivity_W_m_K": (1.0, 2.0, 3.0),
                "volumetric_heat_capacity_J_m3_K": (1e6, 2e6, 3e6),
            }
        )
        for m in request.materials
    )
    nonlinear = run(
        "variable_conductivity_capacity", request.model_copy(update={"materials": variable}), context
    )
    checks["variable_capacity_energy"] = nonlinear["audit"]["energy_within_tolerance"]
    outside = request.model_copy(
        update={
            "inner_boundary": request.inner_boundary.model_copy(
                update={"ambient_temperature_K": (1000.0,) * 4, "convection_W_m2_K": (1e6,) * 4}
            )
        }
    )
    overrun = run("native_property_support_overrun", outside, context)
    checks["native_support_overrun_retained"] = (
        overrun["status"] == "out_of_scope"
        and overrun["native_receipt"]["native"]["status"] == "out_of_scope"
    )
    for name, changes in (
        ("finite_contact", {"perfect_contacts": False}),
        (
            "missing_initial",
            {
                "shells": (
                    request.shells[0].model_copy(update={"initial_cell_temperature_K": (None,) * 4}),
                    request.shells[1],
                )
            },
        ),
        (
            "missing_properties",
            {
                "materials": (
                    request.materials[0],
                    request.materials[1].model_copy(update={"conductivity_W_m_K": (None, None)}),
                )
            },
        ),
    ):
        answer = run(name, request.model_copy(update=changes), context)
        checks[name + "_before_owner"] = answer["native_receipt"] is None

    corruptions = []
    for fault in (
        "clock",
        "qualification",
        "unit",
        "boundary_energy",
        "source_pin",
        "schema_version",
        "hidden_generation",
        "native_temperature",
        "boundary_point_heat",
        "prescribed_surface_temperature",
        "reported_solver_error",
        "extra_native_input",
    ):
        broken = deepcopy(records["varying_fluid_and_outer_temperature"])
        native = broken["native_receipt"]
        payload = native["native"]["payload"]
        if fault == "clock":
            broken["context"]["clock_id"] = "other"
        elif fault == "qualification":
            broken["qualification"]["fluid_energy_feedback_modelled"] = True
        elif fault == "unit":
            payload["units"]["boundary_heat_rate_into_solid_W"] = "J"
        elif fault == "boundary_energy":
            payload["values"]["cumulative_boundary_energy_into_solid_J"][0] += 1000
        elif fault == "source_pin":
            broken["source_pin_report"]["after"]["source_tree_sha256"] = "f" * 64
        elif fault == "schema_version":
            payload["schema_version"] = "unreviewed"
        elif fault == "hidden_generation":
            payload["values"]["cumulative_generated_energy_J"] = 1.0
        elif fault == "boundary_point_heat":
            payload["values"]["boundary_heat_rate_into_solid_W"][-1][1] = 123.0
        elif fault == "prescribed_surface_temperature":
            payload["values"]["boundary_surface_temperature_K"][-1][1] = 100.0
        elif fault == "reported_solver_error":
            payload["values"]["maximum_relative_discrete_energy_balance_error"] = 0.9
        elif fault == "extra_native_input":
            payload["inputs"]["unrequested_future_physics"] = True
        else:
            payload["values"]["temperature_history_K"][-1][0] = 500.0
        payload["input_digest"] = digest(
            {k: payload[k] for k in ("model_id", "model_version", "inputs", "context")}
        )
        native["native"]["payload_digest"] = digest(payload)
        native["result_digest"] = digest({k: v for k, v in native.items() if k != "result_digest"})
        broken["record_digest"] = digest({k: v for k, v in broken.items() if k != "record_digest"})
        try:
            verify_pipe_soil_transient_record(broken)
        except (ValueError, TypeError):
            corruptions.append(fault)
        else:
            raise AssertionError("semantic corruption accepted: " + fault)
    after = material_source_fingerprint(args.materials)
    checks["source_unchanged"] = before == after
    doc = Path("docs/PIPE_SOIL_TRANSIENT_KO.md")
    blocks = re.findall(r"```python\n(.*?)```", doc.read_text(encoding="utf-8"), re.S) if doc.exists() else []
    for block in blocks:
        exec(compile(block, str(doc), "exec"), {})
    if any(not isinstance(v, (bool, np.bool_)) for v in checks.values()):
        raise TypeError("reference criteria must be boolean comparisons")
    checks = {k: bool(v) for k, v in checks.items()}
    errors = {k: float(v) for k, v in errors.items()}
    save(args.artifacts / "records.json", records)
    save(args.artifacts / "reference_checks.json", checks)
    own = [
        "src/pipeline_integrity/pipe_soil_transient.py",
        "examples/pipe_soil_transient.py",
        "tests/test_pipe_soil_transient.py",
        "scripts/check_pipe_soil_transient.py",
        "docs/PIPE_SOIL_TRANSIENT_KO.md",
    ]
    summary = dict(
        schema="pipeline_integrity.pipe_soil_transient_summary.v1",
        scope_state_recommendation="partial",
        original_model=MODEL,
        original_model_version=description["model_version"],
        original_schema_digest=digest(description["input_schema"]),
        source_before=before,
        source_after=after,
        case_count=len(records),
        native_count=sum(r["native_receipt"] is not None for r in records.values()),
        status_counts={
            s: sum(r["status"] == s for r in records.values())
            for s in ("computed", "needs_data", "out_of_scope")
        },
        checks=checks,
        checks_passed=all(checks.values()),
        reference_and_contract_check_count=len(checks),
        rehashed_corruptions_refused=len(corruptions),
        documentation_python_blocks_executed=len(blocks),
        radial_eigenmode_maximum_absolute_errors_K=errors,
        radial_eigenmode_coarse_time_error_K=float(coarse_error),
        maximum_steady_temperature_change_K=maximum_steady_change,
        maximum_independent_energy_residual_J=max(
            abs(r["audit"]["final_energy_residual_J"])
            for r in records.values()
            if r["audit"] and r["audit"].get("available")
        ),
        output_heat_meaning="point boundary W versus native substep-integrated final J",
        qualification="authored_software_references_not_field_calibration",
        limitations=[
            "Concentric cylindrical outer boundary is not a horizontal ground plane.",
            "Perfect interfaces; no finite contact, moisture flow, multiple pipes or two-way fluid energy feedback.",
            "Owner zero-net-heat status is preserved; no override of its numerical acceptance.",
        ],
        code_sha256={p: file_digest(Path(p)) for p in own if Path(p).exists()},
    )
    save(Path("research/pipe_soil_transient_results.json"), summary)
    print(
        json.dumps(
            {
                k: summary[k]
                for k in (
                    "case_count",
                    "native_count",
                    "status_counts",
                    "checks_passed",
                    "reference_and_contract_check_count",
                    "rehashed_corruptions_refused",
                    "radial_eigenmode_maximum_absolute_errors_K",
                    "maximum_independent_energy_residual_J",
                )
            }
        )
    )
    if not all(checks.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
