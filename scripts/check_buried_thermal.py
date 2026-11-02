"""Independent authored steady thermal references and explicit support boundaries."""

import argparse
import builtins
import inspect
import json
import math
import re
import runpy
from copy import deepcopy
from importlib.metadata import metadata, version
from pathlib import Path
from unittest.mock import patch

from pipeline_integrity.buried_thermal import evaluate_buried_thermal, verify_buried_thermal_record
from pipeline_integrity.contracts import digest
from pipeline_integrity.datasets import file_digest


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts", type=Path, default=Path("artifacts/buried_thermal"))
    args = parser.parse_args()
    producer = Path("src/pipeline_integrity/buried_thermal.py")
    source_before = file_digest(producer)
    requests, context = runpy.run_path("examples/buried_thermal.py")["make_cases"]()
    records, references, rejected = {}, {}, []
    uniform, flow = requests["uniform"], requests["flow"]

    def evaluate(name, request, *, run_context=context):
        result = evaluate_buried_thermal(request, run_context)
        verify_buried_thermal_record(result)
        records[name] = result
        return result

    def reference(name, actual, expected, *, tolerance=1e-9):
        if type(expected) in (int, float):
            assert type(actual) in (int, float) and math.isclose(
                actual, expected, rel_tol=1e-11, abs_tol=tolerance
            ), (name, actual, expected)
            error = abs(actual - expected)
        else:
            assert actual == expected, (name, actual, expected)
            error = None
        references[name] = {"actual": actual, "expected": expected, "absolute_error": error}

    def expect_rejection(name, request, *, run_context=context):
        try:
            evaluate_buried_thermal(request, run_context)
        except (ValueError, TypeError):
            rejected.append(name)
        else:
            raise AssertionError("invalid buried thermal input accepted: " + name)

    # A dependency-free core must be able to hold unresolved requests. The guard
    # observes an import attempt, without replacing any implementation or module.
    original_import = builtins.__import__

    def no_ht(name, *positional, **keyword):
        if name == "ht" or name.startswith("ht."):
            raise AssertionError("unresolved request attempted ht import")
        return original_import(name, *positional, **keyword)

    with patch("builtins.__import__", side_effect=no_ht):
        for name, request in (
            ("unreviewed", uniform.model_copy(update={"applicability_reviews": {}})),
            ("unknown_soil_conductivity", uniform.model_copy(update={"soil_conductivity_W_m_K": None})),
            ("unknown_soil_contact", uniform.model_copy(update={"soil_contact_resistance_K_m_W": None})),
            (
                "unknown_layer_contact",
                uniform.model_copy(
                    update={
                        "layers": (
                            uniform.layers[0].model_copy(update={"upstream_contact_resistance_K_m_W": None}),
                        )
                    }
                ),
            ),
            ("unknown_film", uniform.model_copy(update={"inner_film_coefficient_W_m2_K": None})),
            ("unknown_flow", flow.model_copy(update={"mass_flow_kg_s": None})),
            ("unknown_temperature_support", uniform.model_copy(update={"soil_temperature_support_K": None})),
        ):
            held = evaluate(name, request)
            reference(name + "_pre_import_status", held["status"], "needs_data")
            reference(name + "_has_no_calculation", held["calculation"], None)
        for name, request in (
            ("zero_flow", flow.model_copy(update={"mass_flow_kg_s": 0.0})),
            ("touching_surface", uniform.model_copy(update={"depth_to_pipe_center_m": 0.2})),
            ("unsupported_slenderness", uniform.model_copy(update={"minimum_supported_slenderness": 30.0})),
        ):
            held = evaluate(name, request)
            reference(name + "_pre_import_status", held["status"], "out_of_scope")
            reference(name + "_has_no_calculation", held["calculation"], None)

    from ht import R_cylinder, S_isothermal_pipe_to_plane

    from pipeline_integrity.models.thermal import cooling

    library_functions = (R_cylinder, S_isothermal_pipe_to_plane, cooling)
    function_pins = {f.__name__: digest(inspect.getsource(f)) for f in library_functions}
    ht_source = Path(inspect.getfile(S_isothermal_pipe_to_plane))
    ht_file_before = file_digest(ht_source)
    r0 = 1 / (20 * math.pi)
    q_uniform = 400 * math.pi / 3
    native = evaluate("uniform", uniform)
    reference("uniform_computed", native["status"], "computed")
    values = native["values"]
    for key, expected in (
        ("inner_film_resistance_K_W", r0),
        ("soil_resistance_K_W", r0),
        ("total_resistance_K_W", 3 * r0),
        ("conductance_W_K", 20 * math.pi / 3),
        ("soil_shape_factor_m", 20 * math.pi),
        ("heat_from_fluid_to_soil_W", q_uniform),
        ("heat_per_length_W_m", q_uniform / 10),
        ("fluid_mean_temperature_K", 320.0),
        ("soil_inner_temperature_K", 300 + 20 / 3),
        ("temperature_drop_residual_K", 0.0),
        ("fluid_outlet_temperature_K", None),
        ("axial_NTU", None),
        ("fluid_energy_balance_residual_W", None),
    ):
        reference("uniform_" + key, values[key], expected)
    layer = values["layer_results"][0]
    reference("uniform_layer_resistance", layer["conduction_resistance_K_W"], r0)
    reference("uniform_layer_inner", layer["inner_temperature_K"], 320 - 20 / 3)
    reference("uniform_layer_outer", layer["outer_temperature_K"], 320 - 40 / 3)
    values = evaluate("flow", flow)["values"]
    outlet = 300 + 20 * math.exp(-math.pi / 15)
    flow_heat = 100 * (320 - outlet)
    for key, expected in (
        ("axial_NTU", math.pi / 15),
        ("fluid_outlet_temperature_K", outlet),
        ("heat_from_fluid_to_soil_W", flow_heat),
        ("fluid_mean_temperature_K", 300 + flow_heat * 3 * r0),
        ("fluid_energy_balance_residual_W", 0.0),
        ("temperature_drop_residual_K", 0.0),
    ):
        reference("flow_" + key, values[key], expected)
    endpoints = values["axial_endpoint_temperatures"]
    reference("flow_endpoint_order", [p["endpoint"] for p in endpoints], ["inlet", "outlet"])
    for endpoint, temperature in zip(endpoints, (320.0, outlet), strict=True):
        label = "flow_" + endpoint["endpoint"]
        reference(
            label + "_layer_inner",
            endpoint["layer_results"][0]["inner_temperature_K"],
            300 + (temperature - 300) * 2 / 3,
        )
        reference(
            label + "_layer_outer",
            endpoint["layer_results"][0]["outer_temperature_K"],
            300 + (temperature - 300) / 3,
        )
        reference(label + "_soil_inner", endpoint["soil_inner_temperature_K"], 300 + (temperature - 300) / 3)

    for name, request in (("uniform_zero_delta", uniform), ("flow_zero_delta", flow)):
        values = evaluate(name, request.model_copy(update={"fluid_inlet_temperature_K": 300.0}))["values"]
        reference(name + "_heat", values["heat_from_fluid_to_soil_W"], 0.0)
    for name, request, expected_heat in (
        ("uniform_heat_gain", uniform, -q_uniform),
        ("flow_heat_gain", flow, -flow_heat),
    ):
        values = evaluate(
            name,
            request.model_copy(
                update={"fluid_inlet_temperature_K": 300.0, "surface_plane_temperature_K": 320.0}
            ),
        )["values"]
        reference(name + "_signed_heat", values["heat_from_fluid_to_soil_W"], expected_heat)
        if name.startswith("flow"):
            reference(
                name + "_outlet", values["fluid_outlet_temperature_K"], 320 - 20 * math.exp(-math.pi / 15)
            )

    contact_layer = uniform.layers[0].model_copy(update={"upstream_contact_resistance_K_m_W": 10 * r0})
    values = evaluate(
        "two_declared_contacts",
        uniform.model_copy(update={"layers": (contact_layer,), "soil_contact_resistance_K_m_W": 10 * r0}),
    )["values"]
    reference("contacts_resistance", values["total_resistance_K_W"], 5 * r0)
    reference("contacts_heat", values["heat_from_fluid_to_soil_W"], 80 * math.pi)
    reference("upstream_contact_K_m_W_to_K_W", values["layer_results"][0]["contact_resistance_K_W"], r0)
    reference("soil_contact_K_m_W_to_K_W", values["soil_contact_resistance_K_W"], r0)
    reference("contact_layer_inner_K", values["layer_results"][0]["inner_temperature_K"], 312.0)
    reference("contact_layer_outer_K", values["layer_results"][0]["outer_temperature_K"], 308.0)
    reference("contact_soil_inner_K", values["soil_inner_temperature_K"], 304.0)
    layer2 = uniform.layers[0].model_copy(update={"id": "second_authored_layer", "outer_radius_m": 0.4})
    values = evaluate(
        "two_layers",
        uniform.model_copy(
            update={
                "layers": (uniform.layers[0], layer2),
                "depth_to_pipe_center_m": 0.4 * math.cosh(1.0),
                "minimum_supported_slenderness": 10.0,
            }
        ),
    )["values"]
    reference("two_layers_total_R", values["total_resistance_K_W"], 4 * r0)
    reference("two_layers_heat", values["heat_from_fluid_to_soil_W"], 100 * math.pi)
    reference("first_outer_K", values["layer_results"][0]["outer_temperature_K"], 310.0)
    reference("second_outer_K", values["layer_results"][1]["outer_temperature_K"], 305.0)

    values = evaluate(
        "double_depth_parameter", uniform.model_copy(update={"depth_to_pipe_center_m": 0.2 * math.cosh(2.0)})
    )["values"]
    reference("deeper_soil_R", values["soil_resistance_K_W"], 2 * r0)
    reference("deeper_heat", values["heat_from_fluid_to_soil_W"], 100 * math.pi)
    reference("deeper_heat_monotonic", values["heat_from_fluid_to_soil_W"] < q_uniform, True)
    values = evaluate("double_length", uniform.model_copy(update={"length_m": 20.0}))["values"]
    reference("double_length_heat", values["heat_from_fluid_to_soil_W"], 2 * q_uniform)
    reference("double_length_heat_per_m", values["heat_per_length_W_m"], q_uniform / 10)
    values = evaluate(
        "double_soil_conductivity", uniform.model_copy(update={"soil_conductivity_W_m_K": 2.0})
    )["values"]
    reference("more_conductive_soil_heat", values["heat_from_fluid_to_soil_W"], 160 * math.pi)

    unsupported = (
        (
            "uniform_layer_support",
            uniform.model_copy(
                update={
                    "layers": (
                        uniform.layers[0].model_copy(update={"temperature_support_K": (250.0, 312.0)}),
                    )
                }
            ),
        ),
        (
            "flow_layer_inlet_support",
            flow.model_copy(
                update={
                    "layers": (flow.layers[0].model_copy(update={"temperature_support_K": (250.0, 313.0)}),)
                }
            ),
        ),
        ("flow_soil_inlet_support", flow.model_copy(update={"soil_temperature_support_K": (250.0, 306.3)})),
        (
            "flow_layer_heat_gain_support",
            flow.model_copy(
                update={
                    "fluid_inlet_temperature_K": 280.0,
                    "layers": (flow.layers[0].model_copy(update={"temperature_support_K": (287.0, 400.0)}),),
                }
            ),
        ),
        ("flow_fluid_support", flow.model_copy(update={"fluid_temperature_support_K": (319.0, 400.0)})),
        ("unsupported_axial_NTU", flow.model_copy(update={"maximum_supported_axial_NTU": 0.1})),
    )
    for name, request in unsupported:
        result = evaluate(name, request)
        reference(name + "_status", result["status"], "out_of_scope")
        reference(name + "_calculation_retained", result["calculation"] is not None, True)
    for name, request in (
        ("wrong_ht_version", uniform.model_copy(update={"expected_ht_version": "unreviewed"})),
        ("missing_evidence", uniform.model_copy(update={"evidence_ids": ("absent",)})),
        ("false_as_radius", uniform.model_copy(update={"inner_radius_m": False})),
        ("negative_soil_contact", uniform.model_copy(update={"soil_contact_resistance_K_m_W": -1.0})),
        ("nan_conductivity", uniform.model_copy(update={"soil_conductivity_W_m_K": float("nan")})),
        ("inf_conductivity", uniform.model_copy(update={"soil_conductivity_W_m_K": float("inf")})),
        (
            "overlapping_layer",
            uniform.model_copy(
                update={"layers": (uniform.layers[0].model_copy(update={"outer_radius_m": 0.1}),)}
            ),
        ),
        ("unresolved_flow_numerical_scale", flow.model_copy(update={"soil_conductivity_W_m_K": 1e-300})),
    ):
        expect_rejection(name, request)
    expect_rejection("asset_binding", uniform, run_context=context.model_copy(update={"asset_id": "other"}))
    expect_rejection("clock_binding", uniform, run_context=context.model_copy(update={"clock_id": "other"}))

    corruptions = []
    for name in (
        "unhashed_heat",
        "schema",
        "qualification",
        "input_binding",
        "backend_version",
        "backend_function",
        "backend_source_hash",
    ):
        damaged = deepcopy(records["uniform"])
        if name == "unhashed_heat":
            damaged["values"]["heat_from_fluid_to_soil_W"] += 1
        elif name == "schema":
            damaged["schema"] = "unknown"
        elif name == "qualification":
            damaged["field_or_standard_qualification"] = True
        elif name == "input_binding":
            damaged["request"]["location_id"] = "other"
        else:
            field = {
                "backend_version": "version",
                "backend_function": "shape_function",
                "backend_source_hash": "shape_implementation_sha256",
            }[name]
            damaged["calculation"]["backend"][field] = (
                "0" * 64 if name == "backend_source_hash" else "unreviewed"
            )
            damaged["calculation"]["calculation_digest"] = digest(
                {k: v for k, v in damaged["calculation"].items() if k != "calculation_digest"}
            )
        if name != "unhashed_heat":
            damaged["record_digest"] = digest({k: v for k, v in damaged.items() if k != "record_digest"})
        try:
            verify_buried_thermal_record(damaged)
        except ValueError:
            corruptions.append(name)
        else:
            raise AssertionError("corrupt buried thermal record accepted: " + name)
    for name, record in records.items():
        if record["calculation"] is not None:
            reference(name + "_temperature_balance", record["values"]["temperature_drop_residual_K"], 0.0)
            if record["values"]["fluid_energy_balance_residual_W"] is not None:
                reference(name + "_energy_balance", record["values"]["fluid_energy_balance_residual_W"], 0.0)
            reference(name + "_ht_pin", record["calculation"]["backend"]["version"], "1.2.0")
    assert source_before == file_digest(producer), "producer changed during verification"
    assert ht_file_before == file_digest(ht_source), "ht source changed during verification"
    doc = Path("docs/BURIED_THERMAL_KO.md").read_text(encoding="utf-8")
    python_blocks = re.findall(r"```python\n(.*?)```", doc, flags=re.S)
    for block in python_blocks:
        exec(compile(block, "BURIED_THERMAL_KO.md", "exec"), {})
    save(args.artifacts / "records.json", records)
    save(args.artifacts / "reference_checks.json", references)
    save(args.artifacts / "rejections.json", {"inputs": rejected, "corruptions": corruptions})
    own_files = (
        "src/pipeline_integrity/buried_thermal.py",
        "src/pipeline_integrity/models/thermal.py",
        "examples/buried_thermal.py",
        "tests/test_buried_thermal.py",
        "scripts/check_buried_thermal.py",
        "docs/BURIED_THERMAL_KO.md",
    )
    summary = {
        "schema": "pipeline_integrity.buried_thermal_summary.v1",
        "scope_state_recommendation": "partial",
        "qualification": "authored_steady_software_references_only",
        "case_count": len(records),
        "status_counts": {
            s: sum(r["status"] == s for r in records.values())
            for s in ("computed", "needs_data", "out_of_scope")
        },
        "calculation_count": sum(r["calculation"] is not None for r in records.values()),
        "reference_and_contract_check_count": len(references),
        "numeric_or_balance_check_count": sum(
            type(r["expected"]) in (int, float) for r in references.values()
        ),
        "status_metadata_or_unknown_check_count": sum(
            type(r["expected"]) not in (int, float) for r in references.values()
        ),
        "reference_checks_passed": True,
        "invalid_inputs_rejected": len(rejected),
        "corrupt_receipts_rejected": len(corruptions),
        "documentation_python_blocks_executed": len(python_blocks),
        "dependency": {
            "name": "ht",
            "version": version("ht"),
            "license_metadata": metadata("ht").get("License"),
            "source_file_sha256": ht_file_before,
            "function_source_digests": function_pins,
            "source_unchanged": ht_file_before == file_digest(ht_source),
        },
        "producer_unchanged": source_before == file_digest(producer),
        "sources": ["https://ht.readthedocs.io/en/latest/ht.conduction.html"],
        "units": {
            "temperature": "K",
            "resistance": "K/W",
            "contact_resistance_per_length": "K*m/W",
            "heat_from_fluid_to_soil": "W; signed",
            "axial_NTU": "1",
        },
        "limitations": [
            "Steady single long straight pipe; local radial approximation and isothermal plane.",
            "No soil moisture, transient storage, multiple-pipe interaction, material calibration or field/standard qualification.",
            "No measured soil dataset was used; every numerical condition was independently authored.",
        ],
        "code_sha256": {p: file_digest(Path(p)) for p in own_files if Path(p).exists()},
    }
    save(Path("research/buried_thermal_results.json"), summary)
    print(
        json.dumps(
            {
                k: summary[k]
                for k in (
                    "case_count",
                    "status_counts",
                    "calculation_count",
                    "reference_and_contract_check_count",
                    "numeric_or_balance_check_count",
                    "status_metadata_or_unknown_check_count",
                    "invalid_inputs_rejected",
                    "corrupt_receipts_rejected",
                )
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
