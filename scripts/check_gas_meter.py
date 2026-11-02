"""Actual native-family smoke, gas-state compensation and independent flow checks."""

from __future__ import annotations

import argparse
import copy
import json
import math
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from pipeline_integrity.contracts import digest  # noqa: E402
from pipeline_integrity.datasets import file_digest  # noqa: E402
from pipeline_integrity.gas_meter import (  # noqa: E402
    GasComponent,
    _backend,
    available_gas_meter_types,
    boundary_metadata,
    evaluate_gas_meter,
    to_signal_frame,
    verify_gas_meter_record,
)

MAKE_CASE = runpy.run_path(str(ROOT / "examples/gas_meter.py"))["make_case"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts", type=Path, default=ROOT / "artifacts/gas_meter")
    args = parser.parse_args()
    args.artifacts.mkdir(parents=True, exist_ok=True)
    before = _backend()[2]
    checks = {}
    records = {}
    requests = {}
    contexts = {}
    cases = {}
    references = {}

    def run(name, request, context):
        record = evaluate_gas_meter(request, context)
        records[name] = record
        requests[name] = request
        contexts[name] = context
        path = args.artifacts / (name + ".json")
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        checks[name + "_stored_replay"] = verify_gas_meter_record(
            json.loads(path.read_text(encoding="utf-8")), request, context
        )["valid"]
        if record["status"] == "computed":
            frame = to_signal_frame(record, context)
            checks[name + "_clock_origin"] = (
                frame.time_s == request.time_s + request.clock_offset_s
                and frame.clock_id == context.clock_id
                and frame.origin == "synthetic"
            )
            checks[name + "_actual_mass_closure"] = (
                abs(record["audits"]["actual_volume_mass_residual_kg_s"]) < 1e-10
            )
            checks[name + "_reference_mass_closure"] = (
                record["audits"]["reference_volume_mass_residual_kg_s"] is None
                or abs(record["audits"]["reference_volume_mass_residual_kg_s"]) < 1e-10
            )
        cases[name] = {
            "status": record["status"],
            "native_called": record["native_called"],
            "meter_called": record["meter_called"],
            "record_digest": record["record_digest"],
            "values": record["values"],
            "notes": record.get("notes"),
            "basis_digest": record["audits"]["basis_digest"] if record["audits"] else None,
        }
        return record

    discovery = available_gas_meter_types()
    for index, name in enumerate(discovery["meter_types"]):
        record = run(
            "family_" + str(index),
            *MAKE_CASE(
                reviewed=True,
                mode="specified_pair" if name == "unspecified meter" else "native_correlation",
                meter_type=name,
            ),
        )
        cases["family_" + str(index)]["native_meter_selector"] = name
        checks["family_" + str(index) + "_computed"] = record["status"] == "computed"
    pair = run("specified_pair", *MAKE_CASE(reviewed=True))
    value = pair["values"]
    independent = (
        0.7
        * 0.99
        * (math.pi * 0.05**2 / 4)
        * math.sqrt(2 * value["upstream_density_kg_m3"] * 5000 / (1 - 0.5**4))
    )
    references["independent_fixed_pair_mass_kg_s"] = independent
    checks["independent_fixed_pair_relation"] = math.isclose(
        value["signed_mass_flow_kg_s"], independent, rel_tol=1e-12
    )
    checks["reused_existing_liquid_meter"] = (
        pair["audits"]["existing_liquid_meter_check"]["existing_model"] == "instrument.liquid_dp_meter"
    )
    checks["gamma_not_silently_cp_cv"] = (
        abs(value["local_isentropic_exponent"] - value["cp_cv_ratio"]) > 1e-5
        and value["exponent_supplied_to_native"] == value["local_isentropic_exponent"]
    )
    for name, components in (
        ("methane", (GasComponent(fluid="Methane", mole_fraction=1.0),)),
        ("air", (GasComponent(fluid="Air", mole_fraction=1.0),)),
        ("hydrogen", (GasComponent(fluid="Hydrogen", mole_fraction=1.0),)),
        (
            "binary_mixture",
            (
                GasComponent(fluid="Methane", mole_fraction=0.9),
                GasComponent(fluid="Nitrogen", mole_fraction=0.1),
            ),
        ),
    ):
        request, context = MAKE_CASE(reviewed=True, components=components)
        if name in {"methane", "binary_mixture"}:
            request = request.model_copy(
                update={"upstream_pressure_pa_abs": 2000000.0, "downstream_pressure_pa_abs": 1995000.0}
            )
        record = run(name, request, context)
        checks[name + "_computed"] = record["status"] == "computed"
        if record["audits"]:
            checks[name + "_real_PTZ_relation"] = math.isclose(
                record["audits"]["density_compensation_factor"],
                record["audits"]["PTZ_compensation_factor"],
                rel_tol=1e-9,
            )
    request, context = MAKE_CASE(reviewed=True)
    run(
        "explicit_cp_cv",
        request.model_copy(update={"exponent_basis": "reviewed_cp_cv_approximation"}),
        context,
    )
    run(
        "zero_pair",
        request.model_copy(update={"downstream_pressure_pa_abs": request.upstream_pressure_pa_abs}),
        context,
    )
    reverse = request.model_copy(
        update={
            "upstream_port_id": "port-B",
            "downstream_port_id": "port-A",
            "reversed_geometry_reviewed": True,
            "reversed_geometry_evidence_id": "authored",
        }
    )
    reversed_record = run("reviewed_reverse_pair", reverse, context)
    checks["reverse_preserves_signed_mass"] = (
        reversed_record["values"]["signed_mass_flow_kg_s"] == -pair["values"]["signed_mass_flow_kg_s"]
    )
    checks["zero_pair_exact_limit"] = records["zero_pair"]["values"]["signed_mass_flow_kg_s"] == 0
    no_ref = request.model_copy(update={"require_reference_volume": False, "reference": None})
    no_ref_ctx = context.model_copy(deep=True)
    no_ref_ctx.metadata["gas_meter_boundary"] = boundary_metadata(no_ref)
    run("reference_not_requested", no_ref, no_ref_ctx)
    checks["unrequested_reference_not_invented"] = (
        records["reference_not_requested"]["values"]["signed_reference_volume_flow_m3_s"] is None
    )
    run("unreviewed", *MAKE_CASE())
    unknown = request.model_copy(
        update={
            "geometry": None,
            "components": None,
            "reference": None,
            "support": None,
            "volume_basis": None,
            "time_s": None,
            "downstream_pressure_pa_abs": None,
        }
    )
    unknown_ctx = context.model_copy(deep=True)
    unknown_ctx.metadata["gas_meter_boundary"] = boundary_metadata(unknown)
    run("source_unknown", unknown, unknown_ctx)
    checks["unknown_before_native"] = (
        records["source_unknown"]["native_called"] is False
        and records["source_unknown"]["status"] == "needs_data"
    )
    for name, updates in (
        ("large_dp", {"downstream_pressure_pa_abs": 100000.0}),
        ("backpressure", {"downstream_pressure_pa_abs": 510000.0}),
        ("unreviewed_reverse", {"upstream_port_id": "port-B", "downstream_port_id": "port-A"}),
    ):
        record = run(name, request.model_copy(update=updates), context)
        checks[name + "_withheld_before_native"] = (
            record["status"] == "out_of_scope" and record["native_called"] is False
        )
    wet_ref = request.model_copy(
        update={"reference": request.reference.model_copy(update={"volume_basis": "same_composition_wet"})}
    )
    wet_ctx = context.model_copy(deep=True)
    wet_ctx.metadata["gas_meter_boundary"] = boundary_metadata(wet_ref)
    record = run("wet_dry_conversion_refused", wet_ref, wet_ctx)
    checks["wet_dry_no_native"] = record["status"] == "out_of_scope" and record["native_called"] is False
    request, context = MAKE_CASE(reviewed=True, components=(GasComponent(fluid="R718", mole_fraction=1.0),))
    record = run("dry_water_alias_refused", request, context)
    checks["dry_water_alias_not_metered"] = (
        record["status"] == "out_of_scope" and record["meter_called"] is False
    )
    request, context = MAKE_CASE(reviewed=True, mode="native_correlation", meter_type="Hollingshead orifice")
    request = request.model_copy(
        update={"geometry": request.geometry.model_copy(update={"secondary_dimension_m": 0.04})}
    )
    record = run("native_fit_extrapolation_refused", request, context)
    checks["fit_axis_refusal"] = record["status"] == "out_of_scope" and "extrapolate" in str(record["notes"])
    corruptions = {}
    for name in ("mass", "reference_volume", "exponent", "native_state", "backend", "status"):
        forged = copy.deepcopy(pair)
        if name == "mass":
            forged["values"]["signed_mass_flow_kg_s"] *= 1.1
        elif name == "reference_volume":
            forged["values"]["signed_reference_volume_flow_m3_s"] *= 1.1
        elif name == "exponent":
            forged["values"]["exponent_supplied_to_native"] = 1.4
        elif name == "native_state":
            next(c for c in forged["native_calls"] if c["function"] == "CoolPropProvider.resolve")["output"][
                "phase"
            ] = "supercritical"
        elif name == "backend":
            forged["backend"]["CoolProp_revision"] = "changed"
        else:
            forged["status"] = "needs_data"
        forged["record_digest"] = digest({k: v for k, v in forged.items() if k != "record_digest"})
        corruptions[name] = not verify_gas_meter_record(
            forged, requests["specified_pair"], contexts["specified_pair"]
        )["valid"]
        checks[name + "_corruption_refused"] = corruptions[name]
    checks["backend_unchanged"] = _backend()[2] == before
    files = [
        ROOT / "src/pipeline_integrity/gas_meter.py",
        ROOT / "tests/test_gas_meter.py",
        ROOT / "examples/gas_meter.py",
        Path(__file__),
        ROOT / "docs/GAS_METER_KO.md",
    ]
    report = {
        "schema": "pipeline_integrity.gas_meter_verification.v1",
        "backend_before": before,
        "backend_after": _backend()[2],
        "passed": sum(checks.values()),
        "total": len(checks),
        "checks": checks,
        "native_family_selectors": discovery["meter_types"],
        "cases": cases,
        "authored_numerical_references": references,
        "rehashed_corruptions_refused": corruptions,
        "files": {p.relative_to(ROOT).as_posix(): file_digest(p) for p in files},
        "limits": [
            "family calls are native smoke/contract checks, not 28 independent calibration oracles",
            "artificial geometry/coefficient/reviewed ranges and explicit HEOS states; no field accuracy or custody qualification",
            "no standard coefficient tables copied; native library owns correlation and interpolation data",
            "no automatic wet/dry composition change, reference state, phase forcing, pressure-tap reinterpretation or reverse-correlation mapping",
            "coupled measurement/model/composition uncertainty requires explicit metrology model, covariance and source applicability",
        ],
    }
    (ROOT / "research/gas_meter_results.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "passed": report["passed"],
                "total": report["total"],
                "case_states": {
                    state: sum(row["status"] == state for row in cases.values())
                    for state in {r["status"] for r in cases.values()}
                },
                "families": len(discovery["meter_types"]),
            },
            indent=2,
        )
    )
    if not all(checks.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
