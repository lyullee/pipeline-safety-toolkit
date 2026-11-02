"""Run native pressure-test methods, complete gas histories and the checked PE100RC source."""

import argparse
import json
import math
import runpy
import sys
from pathlib import Path

from check_integrations import revision

from pipeline_integrity.contracts import EvidenceRef, RunContext, SignalFrame
from pipeline_integrity.datasets import file_digest
from pipeline_integrity.pressure_tests import (
    R_NATIVE,
    GasDecaySpec,
    evaluate_gas_decay_series,
    evaluate_pressure_test,
    load_pe100_pressure_decay,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--materials", type=Path, required=True)
    args = parser.parse_args()
    before = revision(args.materials)
    sys.path.insert(0, str((args.materials / "src").resolve()))
    output = Path("artifacts/pressure_tests")
    output.mkdir(parents=True, exist_ok=True)
    requests, frames, spec, context = runpy.run_path("examples/pressure_tests.py")["make_cases"]()
    references = {}
    native = {}

    def save(name, record):
        (output / (name + ".json")).write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        return record

    def check(name, actual, expected, atol=1e-12):
        assert math.isclose(actual, expected, rel_tol=1e-10, abs_tol=atol), (name, actual, expected)
        references[name] = dict(actual=actual, expected=expected, absolute_error=abs(actual - expected))

    for method, request in requests.items():
        r = save(method, evaluate_pressure_test(request, context))
        assert r["status"] == "computed"
        native[method] = r
    check(
        "test_reference_load",
        native["inspection.pressure_test_load"]["values"]["reference_test_pressure_Pa"],
        2250000.0,
    )
    check(
        "test_local_elevation",
        native["inspection.pressure_test_load"]["values"]["local_test_pressure_Pa"],
        2348066.5,
    )
    final_volume = 0.1 * 2 ** (1 / 1.4)
    check(
        "adiabatic_final_volume",
        native["inspection.gas_expansion_energy"]["values"]["final_volume_m3"],
        final_volume,
    )
    check(
        "reversible_work_not_blast",
        native["inspection.gas_expansion_energy"]["values"]["ideal_expansion_work_J"],
        (20000 - 1e5 * final_volume) / 0.4,
        atol=1e-8,
    )
    check(
        "ideal_molar_outflow",
        native["inspection.gas_pressure_decay"]["values"]["net_molar_outflow_mol_s"],
        100 / (R_NATIVE * 300),
    )
    check(
        "reference_volume_outflow",
        native["inspection.gas_pressure_decay"]["values"]["reference_volume_outflow_m3_s"],
        0.001,
    )
    check("vacuum_total_load", native["inspection.vacuum_rise"]["values"]["total_gas_load_Pa_m3_s"], 0.0002)
    check(
        "negative_background_corrected_retained",
        native["inspection.vacuum_rise"]["values"]["background_corrected_throughput_Pa_m3_s"],
        -0.0001,
    )
    series = save("authored_series", evaluate_gas_decay_series(frames, spec, context))
    assert series["status"] == "computed"
    check(
        "complete_history_total_net_moles", series["values"]["total_net_moles_out"], 1000 / (R_NATIVE * 300)
    )
    check("interval_endpoint_residual", series["values"]["interval_endpoint_mole_residual"], 0.0)
    original = requests["inspection.gas_pressure_decay"]
    thermal = evaluate_pressure_test(
        original.model_copy(
            update={
                "parameters": original.parameters
                | dict(final_absolute_pressure_Pa=99000.0, final_temperature_K=297.0)
            }
        ),
        context,
    )
    check("cooling_pressure_drop_not_mole_loss", thermal["values"]["net_molar_outflow_mol_s"], 0.0)
    incoming = evaluate_pressure_test(
        original.model_copy(
            update={"parameters": original.parameters | dict(final_absolute_pressure_Pa=110000.0)}
        ),
        context,
    )
    check(
        "negative_net_outflow_retained",
        incoming["values"]["net_molar_outflow_mol_s"],
        -100 / (R_NATIVE * 300),
    )
    source = load_pe100_pressure_decay("data/raw/pe100_pressure_decay/DataSet Temperatue-Pressure.xlsx")
    assert len(source["records"]) == 10 and all(len(r["samples"]) == 31 for r in source["records"])
    assert all(
        r["samples"][0]["time_s"] == 0 and r["samples"][-1]["time_s"] == 900 for r in source["records"]
    )
    save("source_profiles", source)
    records = []
    residuals = []
    density_errors = []
    negative_intervals = 0
    for profile in source["records"]:
        evid = (
            EvidenceRef(
                id="source",
                citation=source["citation"],
                origin="laboratory",
                license_id=source["license_id"],
                sha256=source["source_sha256"],
            ),
            EvidenceRef(
                id="assumption",
                citation="Hypothetical 0.01 m3 rigid isolated volume, constant 101325 Pa ambient, reported T representative of gas and specified fluid model (Air uses the declared predefined reference model); no PE compliance or field qualification",
                origin="synthetic",
                license_id="MIT",
            ),
        )
        ctx = RunContext(
            asset_id=profile["id"],
            scenario_id="source_profile_with_declared_inventory_assumptions",
            clock_id="test_elapsed_s",
            origin="synthetic",
            evidence=evid,
        )
        observations = tuple(
            SignalFrame(
                stream_id=profile["id"],
                clock_id=ctx.clock_id,
                time_s=s["time_s"],
                values=dict(p=s["gauge_pressure_pa"], T=s["reported_temperature_k"]),
                units=dict(p="Pa", T="K"),
                valid=dict(p=s["gauge_pressure_pa"] is not None, T=s["reported_temperature_k"] is not None),
                origin="laboratory",
            )
            for s in profile["samples"]
        )
        unresolved = GasDecaySpec(
            asset_id=ctx.asset_id,
            location_id="pe100rc_capillary_test",
            clock_id=ctx.clock_id,
            pressure_channel="p",
            temperature_channel="T",
            pressure_reference="gauge",
            rigid_volume_m3=None,
            reference_pressure_pa_abs=None,
            reference_temperature_k=None,
            compressibility_source="provider",
            fluid=profile["fluid"],
            maximum_interval_s=30.0,
            evidence_ids=("source", "assumption"),
        )
        held = save(profile["id"] + "_unresolved", evaluate_gas_decay_series(observations, unresolved, ctx))
        assert held["status"] == "needs_data"
        scenario = unresolved.model_copy(
            update=dict(
                rigid_volume_m3=0.01,
                ambient_pa_abs=101325.0,
                reference_pressure_pa_abs=1e5,
                reference_temperature_k=293.15,
                gas_temperature_representative_reviewed=True,
                isolated_rigid_fixed_composition_reviewed=True,
            )
        )
        r = save(profile["id"] + "_scenario", evaluate_gas_decay_series(observations, scenario, ctx))
        records.append(r)
        if profile["fluid"] is None:
            assert r["status"] == "needs_data"
            continue
        assert r["status"] == "computed" and len(r["intervals"]) == 30
        residuals.append(abs(r["values"]["interval_endpoint_mole_residual"]))
        expected_moles = (
            r["states"][0]["molar_density_mol_m3"] - r["states"][-2]["molar_density_mol_m3"]
        ) * 0.01
        density_errors.append(abs(r["values"]["total_net_moles_out"] - expected_moles))
        assert math.isclose(r["values"]["total_net_moles_out"], expected_moles, rel_tol=1e-10, abs_tol=1e-12)
        negative_intervals += sum(x["values"]["net_molar_outflow_mol_s"] < 0 for x in r["intervals"])
    assert sum(r["status"] == "computed" for r in records) == 8 and before == revision(args.materials)
    summary = dict(
        schema="pipeline_integrity.pressure_tests_verification.v1",
        independent_references=references,
        native_models={k: dict(status=v["status"], values=v["values"]) for k, v in native.items()},
        primary_data=dict(
            dataset_id=source["dataset_id"],
            source=source["source"],
            citation=source["citation"],
            license_id=source["license_id"],
            source_sha256=source["source_sha256"],
            supplier_checksum=source["supplier_checksum"],
            physical_capillary_profiles=10,
            time_rows=310,
            samples_per_profile=31,
            metadata_only_no_defect_profiles_not_manufactured=True,
            ambient_temperature_channel_identified=False,
            gas_temperature_role_resolved=False,
            measured_volume_and_atmospheric_pressure_available=False,
            direct_inventory_profiles_withheld=10,
            hypothetical_named_fluid_profiles_computed=8,
            predefined_Air_model_is_declared_assumption=True,
            unresolved_mixture_profiles_withheld=2,
            hypothetical_rigid_volume_m3=0.01,
            hypothetical_ambient_pa_abs=101325.0,
            explicit_reference_pressure_pa_abs=1e5,
            explicit_reference_temperature_k=293.15,
            conditional_scenario_only=True,
            negative_interval_net_outflows_preserved=negative_intervals,
            maximum_interval_endpoint_mole_residual=max(residuals),
            maximum_independent_density_mole_error=max(density_errors),
            field_or_leak_path_validation=False,
        ),
        external_material_source=before,
        external_source_unchanged=True,
        implementation_sha256=file_digest(Path("src/pipeline_integrity/pressure_tests.py")),
        script_sha256=file_digest(Path(__file__)),
        example_sha256=file_digest(Path("examples/pressure_tests.py")),
        raw_or_transformed_observations_in_release=False,
        inspection_acceptance_or_field_validation=False,
    )
    Path("research/pressure_tests_results.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            dict(
                status="passed",
                native_models=4,
                independent_references=len(references),
                source_profiles=10,
                source_rows=310,
                conditional_scenarios=8,
                unresolved_mixtures=2,
                negative_intervals_preserved=negative_intervals,
            )
        )
    )


if __name__ == "__main__":
    main()
