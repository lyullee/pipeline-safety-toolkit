"""Analytical coupled moments, native HRA/cost chain and paired public cycle quantities."""

import argparse
import json
import math
import runpy
import sys
import zipfile
from pathlib import Path

import numpy as np
from check_integrations import revision

from pipeline_integrity import create_registry
from pipeline_integrity.contracts import EvidenceRef, RunContext, SignalFrame, digest
from pipeline_integrity.datasets import catalog, file_digest
from pipeline_integrity.lifecycle_exchange import PowerIntegration, integrate_power_frames
from pipeline_integrity.loaders import hydraulic_cycle
from pipeline_integrity.risk_exchange import HRARiskClient
from pipeline_integrity.uncertainty import (
    EnsembleMetric,
    EnsembleNode,
    EnsembleRequest,
    EnsembleValues,
    JointCase,
    Marginal,
    Threshold,
    UncertainVariable,
    evaluate_ensemble,
    generate_ensemble,
    registry_node,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--hra", type=Path, required=True)
    parser.add_argument("--raw-root", type=Path, default=Path("data/raw/hydraulic"))
    args = parser.parse_args()
    source_before = revision(args.hra)
    sys.path.insert(0, str((args.hra / "src").resolve()))
    private = Path("artifacts/uncertainty")
    private.mkdir(parents=True, exist_ok=True)
    example = runpy.run_path("examples/coupled_uncertainty.py")
    chain, context = example["make_node"](risk_client=HRARiskClient())
    native_design = generate_ensemble(example["make_request"](samples=512, include_risk=True), context)
    native = evaluate_ensemble(native_design, (chain,))
    assert native["status"] == "computed"
    for row in native["rows"]:
        receipt = row["nodes"][chain.id]["native_payload"]
        risk = receipt["risk"]
        assert risk["status"] == "exchange_checked"
        probability = row["nodes"][chain.id]["values"]["conditional_error_probability"]
        assert math.isclose(
            risk["native_result"]["annual_frequency_per_year"], probability * 30, rel_tol=1e-13
        )
        costs = receipt["lifecycle"]["alternatives"]
        assert math.isclose(
            costs["replace"]["present_cost"] - costs["keep"]["present_cost"], -99.71854270988888, abs_tol=1e-8
        )
    (private / "native_design.json").write_text(json.dumps(native_design, indent=2) + "\n", encoding="utf-8")
    (private / "native_result.json").write_text(json.dumps(native, indent=2) + "\n", encoding="utf-8")
    print("Native shared pump / HRA / lifecycle chain: 512 draws passed", flush=True)
    reference = {}
    engine = create_registry()
    variables = tuple(
        UncertainVariable(
            id=id,
            unit=unit,
            layer="input",
            evidence_id="reference",
            marginal=Marginal(family="normal", parameters=dict(mean=mean, standard_deviation=sd)),
        )
        for id, unit, mean, sd in (("flow", "m3/s", 0.01, 0.001), ("head", "m", 40.0, 4.0))
    )
    metric = EnsembleMetric(id="electrical_power_w", unit="W", meaning="quantity", evidence_id="reference")
    pump = registry_node(
        engine,
        id="pump",
        model_id="equipment.pump_power",
        base_inputs=dict(
            volume_flow_m3_s=0.01,
            density_kg_m3=1000.0,
            head_m=40.0,
            pump_efficiency=0.75,
            motor_efficiency=0.9,
        ),
        bindings=dict(flow="volume_flow_m3_s", head="head_m"),
        variables=variables,
        metrics=(metric,),
    )
    for rho in (0.0, 0.8):
        request = EnsembleRequest(
            id=f"joint_normal_rho_{rho}",
            variables=variables,
            method="sobol",
            samples=4096,
            seed=412,
            interpretation="epistemic",
            dependence="gaussian_copula",
            latent_correlation=((1.0, rho), (rho, 1.0)),
            evidence_id="reference",
            definition_reviewed=True,
        )
        design = generate_ensemble(request, context)
        result = evaluate_ensemble(design, (pump,))
        expected = 1000 * 9.80665 / (0.75 * 0.9) * (0.01 * 40 + rho * 0.001 * 4)
        actual = result["summaries"]["pump"][metric.id]["full_design_mean"]
        assert result["status"] == "computed" and math.isclose(actual, expected, rel_tol=0.0002)
        reference[str(rho)] = dict(
            samples=4096,
            analytic_expected_power_w=expected,
            estimated_power_w=actual,
            absolute_error_w=abs(actual - expected),
            relative_tolerance=0.0002,
            design_digest=design["record_digest"],
            result_digest=result["record_digest"],
        )
        (private / f"reference_{rho}.json").write_text(json.dumps(result) + "\n", encoding="utf-8")

    archive = args.raw_root / "source.zip"
    dataset = next(d for d in catalog() if d.id == "hydraulic")
    assert file_digest(archive) == dataset.assets[0].checksum.split(":", 1)[1]
    indices = (0, 100, 1100, 2204)
    source_rows = {}
    with zipfile.ZipFile(archive) as source:
        for sensor in ("EPS1", "FS1"):
            with source.open(sensor + ".txt") as stream:
                for i, line in enumerate(stream):
                    if i in indices:
                        source_rows[(sensor, i)] = np.fromstring(line.decode("ascii"), sep="\t")
    data_context = RunContext(
        asset_id="uci_hydraulic_fixture",
        scenario_id="curated_paired_cycles",
        clock_id="ensemble_cases_not_chronology",
        origin="laboratory",
        evidence=(
            EvidenceRef(
                id="uci447",
                citation=dataset.citation,
                origin="laboratory",
                sha256=file_digest(archive),
                license_id=dataset.license_id,
            ),
            EvidenceRef(
                id="reference",
                citation="Four selected cycles with equal scenario weights; not a population fit",
                origin="synthetic",
                license_id="MIT",
            ),
        ),
        metadata={
            "selection": list(indices),
            "weights_are_scenarios": True,
            "source_sensor_clocks_merged": False,
        },
    )
    quantities, source_receipts = {}, {}
    for cycle in indices:
        loaded = hydraulic_cycle(archive, cycle, sensors=("EPS1", "FS1"))["sensors"]
        power, flow = loaded["EPS1"], loaded["FS1"]
        assert np.array_equal(power["values"], source_rows[("EPS1", cycle)])
        assert np.array_equal(flow["values"], source_rows[("FS1", cycle)] * (1 / 60000))
        end = float(flow["time_s"][-1])
        count = int(round(end * power["sample_rate_hz"])) + 1
        assert count == 5991 and end == 59.9
        ctx = data_context.model_copy(update={"clock_id": power["clock_id"]})
        frames = tuple(
            SignalFrame(
                stream_id=f"EPS1_cycle_{cycle}",
                clock_id=power["clock_id"],
                time_s=float(t),
                values={"power": float(p)},
                units={"power": "W"},
                valid={"power": True},
                origin="laboratory",
            )
            for t, p in zip(power["time_s"][:count], power["values"][:count], strict=True)
        )
        receipt = integrate_power_frames(
            frames,
            PowerIntegration(
                channel="power",
                maximum_sample_interval_s=0.01,
                sign_convention="nonnegative_consumption",
                evidence_id="uci447",
            ),
            ctx,
        )
        assert receipt["status"] == "computed" and receipt["last_time_s"] == end
        independent_energy = float(np.trapezoid(power["values"][:count], power["time_s"][:count]))
        energy = receipt["values"]["import_energy_j"]
        assert math.isclose(energy, independent_energy, rel_tol=1e-13)
        volume = float(np.trapezoid(flow["values"], flow["time_s"]))
        quantities[cycle] = dict(energy_j=energy, volume_m3=volume)
        source_receipts[str(cycle)] = dict(
            power_receipt_digest=receipt["record_digest"],
            source_clock_id=power["clock_id"],
            common_interval_s=[0.0, end],
            energy_absolute_error_j=abs(energy - independent_energy),
            volume_integration="piecewise_linear_trapezoid_on_native_10Hz",
            power_samples=count,
            flow_samples=len(flow["values"]),
        )
        (private / f"source_power_{cycle}.json").write_text(json.dumps(receipt) + "\n", encoding="utf-8")
    data_variables = tuple(
        UncertainVariable(id=id, unit=unit, layer="measurement", evidence_id="uci447")
        for id, unit in (("energy_j", "J"), ("volume_m3", "m3"))
    )
    cases = tuple(
        JointCase(id=f"cycle_{i}", weight=0.25, values=quantities[i], evidence_id="uci447") for i in indices
    )
    paired_request = EnsembleRequest(
        id="curated_native_pairs",
        variables=data_variables,
        method="joint_cases",
        dependence="paired_rows",
        interpretation="scenario",
        cases=cases,
        evidence_id="reference",
        definition_reviewed=True,
    )
    specific = EnsembleMetric(
        id="specific_energy",
        unit="J/m3",
        meaning="quantity",
        evidence_id="reference",
        threshold=Threshold(value=1e8, comparison="gt", evidence_id="reference"),
    )

    def evaluate_specific(values, ctx):
        energy, volume = values["energy_j"], values["volume_m3"]
        value = None if energy is None or volume is None or volume <= 0 else energy / volume
        status = (
            "needs_data"
            if energy is None or volume is None
            else "out_of_scope"
            if volume <= 0
            else "computed"
        )
        return EnsembleValues(
            context=ctx,
            values={specific.id: value},
            units={specific.id: specific.unit},
            statuses={specific.id: status},
            native_payload={
                "paired_quantity_digest": digest(values),
                "quantity_is_ratio_of_interval_energy_to_interval_volume": True,
            },
        )

    specific_node = EnsembleNode(
        id="paired_meter_quantity",
        version="1",
        source_digest=file_digest(Path(__file__)),
        configuration={
            "definition": "interval energy divided by interval volume; no conversion of hydraulic oil to water",
            "source_archive_sha256": file_digest(archive),
            "source_receipts": source_receipts,
        },
        metrics=(specific,),
        evaluate=evaluate_specific,
    )
    paired_result = evaluate_ensemble(generate_ensemble(paired_request, data_context), (specific_node,))
    expected_ratio = math.fsum(quantities[i]["energy_j"] / quantities[i]["volume_m3"] for i in indices) / 4
    assert math.isclose(
        paired_result["summaries"][specific_node.id][specific.id]["full_design_mean"],
        expected_ratio,
        rel_tol=1e-14,
    )
    cross_cases = tuple(
        JointCase(
            id=f"counterfactual_{i}_{j}",
            weight=1 / 16,
            values=dict(energy_j=quantities[i]["energy_j"], volume_m3=quantities[j]["volume_m3"]),
            evidence_id="reference",
        )
        for i in indices
        for j in indices
    )
    counterfactual = evaluate_ensemble(
        generate_ensemble(
            paired_request.model_copy(
                update={"id": "explicitly_unpaired_counterfactual", "cases": cross_cases}
            ),
            data_context,
        ),
        (specific_node,),
    )
    missing_case = cases[-1].model_copy(update={"values": cases[-1].values | {"volume_m3": None}})
    missing = evaluate_ensemble(
        generate_ensemble(
            paired_request.model_copy(update={"id": "missing_volume", "cases": (*cases[:-1], missing_case)}),
            data_context,
        ),
        (specific_node,),
    )
    assert (
        missing["status"] == "needs_data"
        and missing["summaries"][specific_node.id][specific.id]["unresolved_weight"] == 0.25
    )
    for name, record in (
        ("paired", paired_result),
        ("unpaired_counterfactual", counterfactual),
        ("missing_volume", missing),
    ):
        (private / f"{name}.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    assert source_before == revision(args.hra)
    summary = dict(
        schema="pipeline_integrity.uncertainty_verification.v1",
        analytic_joint_normal_reference=reference,
        native_chain=dict(
            draws=512,
            status=native["status"],
            metrics=native["summaries"],
            covariances=native["paired_covariances"],
            design_digest=native_design["record_digest"],
            result_digest=native["record_digest"],
            probability_parameters_assumed=True,
            expected_loss_REF_per_event=25.0,
            annual_demands=30.0,
            alternatives_receive_identical_additional_energy_and_risk_costs=True,
        ),
        data=dict(
            id=dataset.id,
            citation=dataset.citation,
            creators=list(dataset.creators),
            license_id=dataset.license_id,
            source=dataset.source,
            archive_sha256=file_digest(archive),
            original_numeric_rows_verified=8,
            cycles=list(indices),
            power_samples_used=4 * 5991,
            flow_samples_used=4 * 600,
            common_interval_s=[0.0, 59.9],
            source_receipts=source_receipts,
            paired=paired_result["summaries"],
            unpaired_counterfactual=counterfactual["summaries"],
            missing_volume=missing["summaries"],
            independent_ratio_reference=expected_ratio,
            population_sampling_claim=False,
            raw_or_derived_case_rows_in_release=False,
        ),
        external_hra=source_before,
        external_hra_unchanged=True,
        interpretation_limits=[
            "Distributions, correlations, model multiplier, HEP, tariffs, duty and losses are declared scenarios, not fitted or field validated.",
            "Gaussian copula matrix is latent normal correlation, not a general physical Pearson matrix.",
            "A single scrambled Sobol design supplies no independent-trial confidence interval.",
            "Curated paired hydraulic cycles do not establish a population distribution or human error rate.",
            "Variance decomposition, epistemic/aleatory nested inference, tail qualification and joint failure inference remain future work.",
        ],
        module_sha256=file_digest(Path("src/pipeline_integrity/uncertainty.py")),
        example_sha256=file_digest(Path("examples/coupled_uncertainty.py")),
        script_sha256=file_digest(Path(__file__)),
    )
    Path("research/uncertainty_results.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            dict(
                status="passed",
                native_chain_draws=512,
                reference_draws=8192,
                source_power_samples=23964,
                source_flow_samples=2400,
                external_hra_unchanged=True,
            )
        )
    )


if __name__ == "__main__":
    main()
