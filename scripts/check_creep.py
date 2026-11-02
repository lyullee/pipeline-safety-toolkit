"""Native creep/ageing references, primary censored specimens and explicit period-risk semantics."""

import argparse
import json
import math
import runpy
import sys
from pathlib import Path

import numpy as np
from check_integrations import revision

from pipeline_integrity.contracts import EvidenceRef, RunContext, digest
from pipeline_integrity.creep_exchange import (
    REVIEWS,
    CreepRequest,
    CreepRiskMapping,
    creep_risk_probability,
    evaluate_creep,
    evaluate_creep_sequence,
    load_cmsx6_specimens,
)
from pipeline_integrity.datasets import catalog, file_digest
from pipeline_integrity.risk_exchange import (
    DemandExposure,
    FaultNode,
    FaultTreeRequest,
    HRARiskClient,
    RiskProbability,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--materials", type=Path, required=True)
    parser.add_argument("--hra", type=Path, required=True)
    args = parser.parse_args()
    roots = dict(materials=args.materials, hra=args.hra)
    before = {key: revision(path) for key, path in roots.items()}
    sys.path[:0] = [str((path / "src").resolve()) for path in roots.values()]
    from matintegrity.creep_data import read_cmsx6_trace

    private = Path("artifacts/creep")
    private.mkdir(parents=True, exist_ok=True)
    example = runpy.run_path("examples/creep_methods.py")
    cases, sequence, context = example["make_cases"]()
    records = {}

    def save(name, request, ctx=context, fit=None):
        result = evaluate_creep(request, ctx, fit_record=fit)
        assert result["record_digest"] == digest({k: v for k, v in result.items() if k != "record_digest"})
        (private / (name + ".json")).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        records[name] = result
        return result

    for method, request in cases.items():
        assert save(method, request)["status"] == "computed"
        assert (
            save(method + "_unreviewed", request.model_copy(update={"applicability_reviews": {}}))["status"]
            == "needs_data"
        )
    fit = records["creep.lognormal_life_fit"]
    prediction_request = example["prediction_request"](
        fit, context, current_age_s=500000.0, horizon_s=500000.0
    )
    prediction = save("creep.conditional_life", prediction_request, fit=fit)
    assert prediction["status"] == "computed"
    sequence_result = evaluate_creep_sequence(sequence, context)
    assert sequence_result["status"] == "computed"
    (private / "sequence.json").write_text(json.dumps(sequence_result, indent=2) + "\n", encoding="utf-8")
    references = {}

    def check(name, actual, expected, rtol=1e-11, atol=1e-12):
        assert math.isclose(actual, expected, rel_tol=rtol, abs_tol=atol), (name, actual, expected)
        references[name] = dict(actual=actual, expected=expected, absolute_error=abs(actual - expected))

    check("norton_rate", records["creep.norton"]["values"]["steady_creep_strain_rate_per_s"], 1e-8)
    check("norton_strain", records["creep.norton"]["values"]["steady_creep_strain"], 1e-4)
    check("larson_miller_hours", records["creep.larson_miller"]["values"]["parameter_K"], 923.15 * 21)
    check("time_fraction", records["creep.time_fraction"]["values"]["time_fraction_damage"], 0.15)
    check("sequence", sequence_result["damage"], 0.15)
    check(
        "arrhenius_factor",
        records["kinetics.arrhenius"]["values"]["temperature_factor"],
        math.exp(100000 / 8.31446261815324 * (1 / 900 - 1 / 1000)),
    )
    check(
        "oxide_thickness",
        records["degradation.parabolic_oxidation"]["values"]["oxide_thickness_m"],
        math.sqrt(2) * 1e-6,
    )
    check("lognormal_stress_exponent", fit["values"]["model"]["stress_exponent"], 3.0, rtol=1e-6)
    check("lognormal_sigma", fit["values"]["model"]["log_life_sigma"], math.sqrt(0.02), rtol=1e-6)
    check("conditional_median", prediction["values"]["median_life_s"], 1000000.0, rtol=1e-6)
    mu = prediction["values"]["log_median_life_s"]
    sigma = prediction["values"]["log_life_sigma"]

    def cdf(t):
        return 0.5 * math.erfc(-(math.log(t) - mu) / (sigma * math.sqrt(2)))

    expected = (cdf(1000000.0) - cdf(500000.0)) / (1 - cdf(500000.0))
    check("conditional_survival_probability", prediction["values"]["probability_given_survival"], expected)
    assert (
        save(
            "outside_stress",
            prediction_request.model_copy(
                update={"parameters": prediction_request.parameters | {"initial_stress_Pa": 4e8}}
            ),
            fit=fit,
        )["status"]
        == "out_of_scope"
    )
    assert (
        save(
            "unknown_full_history",
            prediction_request.model_copy(
                update={
                    "parameters": prediction_request.parameters
                    | {"constant_conditions_since_fresh_material_confirmed": False}
                }
            ),
            fit=fit,
        )["status"]
        == "needs_data"
    )
    # Bind age and the physical endpoint before using the existing generic Boolean primitive.
    mapping = CreepRiskMapping(
        expected_prediction_digest=prediction["record_digest"],
        event_id="authored_creep_rupture",
        physical_scope_id="declared_span_period",
        condition_id="authored_constant_condition",
        period_start_s=500000.0,
        period_finish_s=1000000.0,
        fresh_material_origin_s=0.0,
        conditioning="Survived to 500000s under the unchanged authored material/stress/temperature condition",
        failure_definition="First creep rupture of the declared reference component",
        age_origin_evidence_id="reference",
        evidence_ids=("reference",),
        physical_event_and_age_mapping_reviewed=True,
    )
    binding = creep_risk_probability(prediction, mapping)
    assert binding["status"] == "computed"
    event = RiskProbability.model_validate_json(json.dumps(binding["probability"]))
    hra = HRARiskClient()
    native_period_probability = hra.api.fault_probability(
        hra.api.Event(event.id), {event.id: event.probability}
    )
    check("native_single_event_period_gate", native_period_probability, event.probability)
    exposure = DemandExposure(
        opportunities_per_year=20.0,
        opportunity_id=event.opportunity_id,
        opportunity_definition=event.opportunity_definition,
        condition_id=event.condition_id,
        evidence_id="reference",
    )
    rejected_rate = hra.annualize(event, exposure)
    assert rejected_rate["status"] == "withheld"
    demand_request = FaultTreeRequest(
        root=FaultNode(kind="event", event_id=event.id),
        events=(event,),
        condition_id=event.condition_id,
        opportunity_id=event.opportunity_id,
        dependence="declared_independent",
        dependence_evidence_id="reference",
    )
    rejected_demand = hra.fault_tree(demand_request, context)
    assert rejected_demand["status"] == "withheld"
    (private / "risk_binding.json").write_text(json.dumps(binding, indent=2) + "\n", encoding="utf-8")
    source = next(d for d in catalog() if d.id == "cmsx6_creep")
    folder = Path("data/raw/cmsx6_creep")
    assets = []
    for asset in source.assets:
        algorithm, expected_checksum = asset.checksum.split(":", 1)
        assert file_digest(folder / asset.filename, algorithm) == expected_checksum
        assets.append(
            dict(
                filename=asset.filename,
                bytes=(folder / asset.filename).stat().st_size,
                sha256=file_digest(folder / asset.filename),
                supplier_checksum=asset.checksum,
            )
        )
    specimens = load_cmsx6_specimens(folder / "2024-10_Creep-reference-data-CMSX-6_v1.0.xlsx")
    rows = specimens["records"]
    assert len(rows) == 12 and sum(r["right_censored"] for r in rows) == 3
    assert {r["temperature_K"] for r in rows} == {1253.15}
    (private / "source_specimens.json").write_text(json.dumps(specimens, indent=2) + "\n", encoding="utf-8")
    data_context = RunContext(
        asset_id="cmsx6_laboratory_cohort",
        scenario_id="conditional_constant_nominal_stress",
        clock_id="elapsed_s",
        origin="laboratory",
        evidence=(
            EvidenceRef(
                id="source",
                citation=source.citation,
                origin="laboratory",
                sha256=specimens["source_sha256"],
                license_id=source.license_id,
            ),
            EvidenceRef(
                id="review",
                citation="Research assumption of independent tests/common reference condition; not field or orientation/heat qualification",
                origin="synthetic",
                license_id="MIT",
            ),
        ),
    )
    material = rows[0]["material_condition_id"]
    parameters = dict(
        material_condition_id=material,
        stress_basis=rows[0]["stress_basis"],
        temperature_basis=rows[0]["temperature_basis"],
        family="stress_only",
        specimen_id=[r["specimen_id"] for r in rows],
        initial_stress_Pa=[r["initial_stress_Pa"] for r in rows],
        temperature_K=[r["temperature_K"] for r in rows],
        endpoint_time_s=[r["endpoint_time_s"] for r in rows],
        right_censored=[r["right_censored"] for r in rows],
        temperature_tolerance_K=0.0,
        independent_specimens_confirmed=True,
        common_mechanism_and_condition_confirmed=True,
        evidence=[
            dict(
                source_id="source",
                origin="open_dataset",
                citation=source.citation,
                license_id=source.license_id,
                sha256=specimens["source_sha256"],
            )
        ],
    )
    request = CreepRequest(
        method="creep.lognormal_life_fit",
        asset_id=data_context.asset_id,
        location_id="single_material_reference_cohort",
        clock_id=data_context.clock_id,
        material_condition_id=material,
        parameters=parameters,
        applicability_reviews=dict.fromkeys(REVIEWS["creep.lognormal_life_fit"], True),
        evidence_ids=("source", "review"),
    )
    actual_fit = save("primary_censored_fit", request, data_context)
    assert actual_fit["status"] == "computed" and actual_fit["values"]["model"]["failures"] == 9
    assert (
        actual_fit["native_receipt"]["native"]["payload"]["inputs"]["right_censored"]
        == parameters["right_censored"]
    )
    unidentifiable = save(
        "one_temperature_cannot_fit_activation",
        request.model_copy(update={"parameters": parameters | {"family": "arrhenius_stress"}}),
        data_context,
    )
    assert unidentifiable["status"] == "needs_data"
    actual_request = example["prediction_request"](
        actual_fit,
        data_context,
        current_age_s=100000.0,
        horizon_s=200000.0,
        stress_pa=2e8,
        temperature_k=1253.15,
    )
    actual_prediction = save("primary_conditional_prediction", actual_request, data_context, actual_fit)
    assert actual_prediction["status"] == "computed"
    assert (
        save(
            "wrong_temperature_basis",
            actual_request.model_copy(
                update={
                    "parameters": actual_request.parameters | {"temperature_basis": "measured_representative"}
                }
            ),
            data_context,
            actual_fit,
        )["status"]
        == "needs_data"
    )
    assert (
        save(
            "outside_primary_temperature",
            actual_request.model_copy(
                update={"parameters": actual_request.parameters | {"temperature_K": 1300.0}}
            ),
            data_context,
            actual_fit,
        )["status"]
        == "out_of_scope"
    )
    traces = []
    for asset in source.assets:
        if not asset.filename.endswith(".LIS"):
            continue
        path = folder / asset.filename
        trace = read_cmsx6_trace(path, duplicate_policy="preserve")
        lines = path.read_text(encoding="latin1").splitlines()
        marker = next(i for i, line in enumerate(lines) if line.split("\t")[0] == "[data]")
        raw = np.asarray(
            [
                [float(s.strip().replace(",", ".")) for s in line.split("\t")[:5]]
                for line in lines[marker + 4 :]
                if line.strip()
            ]
        )
        converted = raw * np.array([3600, 0.01, 1, 1, 1]) + np.array([0, 0, 273.15, 273.15, 273.15])
        # Division versus multiplication can differ by one bit; audit physical SI values explicitly.
        np.testing.assert_allclose(trace.iloc[:, :5].to_numpy(), converted, rtol=1e-15, atol=1e-12)
        assert len(raw) == len(trace)
        endpoint = next(r for r in rows if r["specimen_id"] == path.stem)
        traces.append(
            dict(
                specimen_id=path.stem,
                source_sha256=file_digest(path),
                rows=len(trace),
                repeated_clock_intervals=trace.attrs["repeated_clock_intervals"],
                source_clock_origin=trace.attrs["clock_origin"],
                source_time_unit="h",
                source_extension_unit="percent",
                endpoint_kind=endpoint["endpoint_kind"],
                rupture_inferred_from_trace_endpoint=False,
            )
        )
    assert len(traces) == 12
    assert before == {key: revision(path) for key, path in roots.items()}
    summary = dict(
        schema="pipeline_integrity.creep_verification.v1",
        authored_cases={
            name: dict(status=r["status"], values=r["values"], result_digest=r["record_digest"])
            for name, r in records.items()
            if not name.startswith(("primary", "one_temperature", "wrong_temperature", "outside_primary"))
        },
        independent_references=references,
        sequence=dict(
            status=sequence_result["status"],
            damage=sequence_result["damage"],
            result_digest=sequence_result["record_digest"],
            damage_is_probability=False,
        ),
        period_risk=dict(
            status=binding["status"],
            measure=event.measure,
            endpoint="creep_rupture",
            probability=event.probability,
            native_single_event_period_probability=native_period_probability,
            hra_provider=hra.provider,
            demand_annualization_status=rejected_rate["status"],
            demand_fta_adapter_status=rejected_demand["status"],
            generic_multievent_period_adapter_completed=False,
        ),
        primary_data=dict(
            dataset_id=source.id,
            source=source.source,
            citation=source.citation,
            creators=list(source.creators),
            license_id=source.license_id,
            assets=assets,
            specimens=12,
            ruptures=9,
            administrative_right_censored=3,
            temperature_levels_K=[1253.15],
            stress_levels_Pa=sorted({r["initial_stress_Pa"] for r in rows}),
            fit_status=actual_fit["status"],
            fitted_population_model=actual_fit["values"]["model"],
            parameter_uncertainty_included=False,
            independent_field_validation=False,
            activation_energy_fit_status=unidentifiable["status"],
            conditional_prediction_status=actual_prediction["status"],
            conditional_prediction=actual_prediction["values"],
            traces=traces,
            total_trace_rows=sum(t["rows"] for t in traces),
            repeated_clock_intervals=sum(t["repeated_clock_intervals"] for t in traces),
            raw_or_derived_specimen_and_trace_rows_in_code_release=False,
        ),
        external_sources=before,
        external_sources_unchanged=True,
        implementation_sha256=file_digest(Path("src/pipeline_integrity/creep_exchange.py")),
        script_sha256=file_digest(Path(__file__)),
        example_sha256=file_digest(Path("examples/creep_methods.py")),
        field_or_standard_qualification=False,
    )
    Path("research/creep_results.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            dict(
                status="passed",
                native_models=7,
                independent_references=len(references),
                primary_specimens=12,
                ruptures=9,
                right_censored=3,
                trace_rows=summary["primary_data"]["total_trace_rows"],
                period_probability_measure=event.measure,
                annualization_rejected=True,
            )
        )
    )


if __name__ == "__main__":
    main()
