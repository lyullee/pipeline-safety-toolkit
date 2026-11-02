"""Actual owner calls and independent authored welding references, without standards acceptance claims."""

import argparse
import json
import math
import runpy
import sys
from pathlib import Path

from check_integrations import revision

from pipeline_integrity.datasets import file_digest
from pipeline_integrity.welding_exchange import (
    evaluate_weld_thermal,
    evaluate_weld_waveform,
    evaluate_welding,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--materials", type=Path, required=True)
    args = parser.parse_args()
    before = revision(args.materials)
    sys.path.insert(0, str((args.materials / "src").resolve()))
    cases, frames, spec, thermal, thermal_spec, context = runpy.run_path("examples/welding_methods.py")[
        "make_cases"
    ]()
    folder = Path("artifacts/welding")
    folder.mkdir(parents=True, exist_ok=True)
    records = {}
    for method, request in cases.items():
        record = evaluate_welding(request, context)
        assert record["status"] == "computed", (method, record["issues"])
        records[method] = record
        (folder / (method + ".json")).write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    refs = {}

    def check(name, actual, expected, atol=1e-12):
        assert math.isclose(actual, expected, rel_tol=1e-10, abs_tol=atol), (name, actual, expected)
        refs[name] = dict(actual=actual, expected=expected, absolute_error=abs(actual - expected))

    values = {name: r["values"] for name, r in records.items()}
    check("steady_arc_per_length", values["welding.arc_energy"]["arc_energy_J_m"], 400000.0)
    check("declared_net_heat_per_length", values["welding.arc_energy"]["net_heat_input_J_m"], 320000.0)
    check("synchronized_waveform_energy", values["welding.waveform_energy"]["arc_energy_J"], 600.0)
    check("waveform_declared_net_heat", values["welding.waveform_energy"]["net_heat_input_J_m"], 48000.0)
    check("mass_percent_CE", values["welding.carbon_equivalents"]["CE_IIW"], 0.4)
    check("mass_percent_Pcm", values["welding.carbon_equivalents"]["Pcm"], 0.20583333333333334)
    check("upper_crossing", values["welding.cooling_interval"]["upper_crossing_s"], 5.0)
    check("lower_crossing", values["welding.cooling_interval"]["lower_crossing_s"], 50 / 3)
    check("cooling_duration", values["welding.cooling_interval"]["cooling_interval_s"], 35 / 3)
    hold = values["welding.heat_treatment_history"]
    check("simultaneous_hold_union", hold["total_simultaneous_hold_s"], 70 / 3)
    check("longest_continuous_hold", hold["longest_continuous_hold_s"], 15.0)
    check("maximum_sensor_spread", hold["maximum_sensor_spread_K"], 50.0)
    check("maximum_heating_rate", hold["maximum_heating_rate_K_s"], 15.0)
    mix = values["welding.dilution"]
    check("melted_base_mass_fraction", mix["role_mass_fractions"]["base"], 0.5)
    check("mixed_carbon_mass_fraction", mix["mixed_composition_mass_fraction"]["C"], 0.0175)
    check("mixed_nickel_mass_fraction", mix["mixed_composition_mass_fraction"]["Ni"], 0.075)
    assert mix["mixed_composition_mass_fraction"]["Cr"] is None
    assert mix["constituents_missing_element_evidence"]["Cr"] == ["previous"]
    hardness = values["welding.hardness_traverse"]
    assert hardness["outcome"] == "not_satisfied"
    outcomes = {r["region_id"]: r["outcome"] for r in hardness["regions"]}
    assert outcomes == dict(base="satisfied", HAZ="unknown", weld="not_satisfied", unmeasured="unknown")
    profile = values["welding.joint_profile"]
    check("profile_signed_offset", profile["signed_surface_offset_at_joint_m"], 0.002)
    check("profile_signed_angle", profile["signed_angular_mismatch_rad"], math.atan(0.02) - math.atan(0.01))
    request = cases["welding.joint_profile"]
    y = list(request.parameters["measured_surface_height_m"])
    y[1] += 0.01
    y[4] -= 0.01
    robust = {}
    for method in ("ols", "theil_sen", "soft_l1"):
        p = request.parameters | dict(measured_surface_height_m=y, fit_method=method)
        if method == "soft_l1":
            p["robust_residual_scale_m"] = 1e-5
        result = evaluate_welding(request.model_copy(update={"parameters": p}), context)
        assert result["status"] == "computed"
        robust[method] = dict(
            signed_offset_m=result["values"]["signed_surface_offset_at_joint_m"],
            absolute_error_m=abs(result["values"]["signed_surface_offset_at_joint_m"] - 0.002),
        )
    check("theil_sen_contaminated_offset", robust["theil_sen"]["signed_offset_m"], 0.002)
    assert robust["soft_l1"]["absolute_error_m"] < robust["ols"]["absolute_error_m"]
    waveform = evaluate_weld_waveform(frames, spec, context)
    assert waveform["status"] == "computed"
    check(
        "common_power_integrator_same_samples",
        waveform["electrical_diagnostic"]["values"]["import_energy_j"],
        600.0,
    )
    unknown = evaluate_weld_waveform(frames, spec.model_copy(update={"efficiency": None}), context)
    assert unknown["status"] == "needs_data" and unknown["native_result"] is None
    signed = tuple(
        f.model_copy(update={"values": dict(U=u, I=30.0)})
        for f, u in zip(frames, (10.0, -10.0, 10.0), strict=True)
    )
    negative = evaluate_weld_waveform(signed, spec, context)
    assert negative["status"] == "out_of_scope"
    check(
        "negative_electrical_exchange_retained",
        negative["electrical_diagnostic"]["values"]["export_energy_j"],
        150.0,
    )
    temperature = evaluate_weld_thermal(thermal, thermal_spec, context)
    assert temperature["status"] == "computed"
    check(
        "recorded_temperature_adapter_crossing",
        temperature["native_result"]["values"]["cooling_interval_s"],
        35 / 3,
    )
    assert before == revision(args.materials)
    result = dict(
        schema="pipeline_integrity.welding_verification.v1",
        native_models={
            k: dict(
                status=r["status"],
                model_version=r["native_receipt"]["job"]["model_version"],
                values=r["values"],
            )
            for k, r in records.items()
        },
        independent_references=refs,
        hardness_region_outcomes=outcomes,
        unknown_dilution_element_preserved=True,
        profile_outlier_comparison=robust,
        incomplete_heat_input_status=unknown["status"],
        negative_power_native_heat_status=negative["status"],
        external_material_source=before,
        external_source_unchanged=True,
        implementation_sha256=file_digest(Path("src/pipeline_integrity/welding_exchange.py")),
        script_sha256=file_digest(Path(__file__)),
        example_sha256=file_digest(Path("examples/welding_methods.py")),
        original_authored_inputs_not_supplier_rules=True,
        procedure_or_field_qualification=False,
    )
    Path("research/welding_results.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            dict(
                status="passed",
                native_models=len(records),
                independent_references=len(refs),
                profile_fit_methods=len(robust),
                unknown_chemistry_preserved=True,
                unmeasured_hardness_region="unknown",
            )
        )
    )


if __name__ == "__main__":
    main()
