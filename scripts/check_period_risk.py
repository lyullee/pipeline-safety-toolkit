"""Execute the existing owner Boolean engine with common-period scopes and primary fitted marginals."""

import argparse
import json
import math
import runpy
import sys
from pathlib import Path

from check_integrations import revision

from pipeline_integrity.contracts import EvidenceRef, RunContext, digest
from pipeline_integrity.creep_exchange import (
    CreepRequest,
    CreepRiskMapping,
    creep_risk_probability,
    evaluate_creep,
    load_cmsx6_specimens,
)
from pipeline_integrity.datasets import file_digest
from pipeline_integrity.period_risk import (
    PeriodLogicRequest,
    RiskPeriod,
    bind_creep_period_event,
    evaluate_period_logic,
)
from pipeline_integrity.risk_exchange import (
    DemandExposure,
    FaultNode,
    HRARiskClient,
    JointState,
    RiskProbability,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--hra", type=Path, required=True)
    parser.add_argument("--materials", type=Path, required=True)
    args = parser.parse_args()
    roots = dict(hra=args.hra, materials=args.materials)
    before = {key: revision(path) for key, path in roots.items()}
    sys.path[:0] = [str((p / "src").resolve()) for p in roots.values()]
    owner = HRARiskClient()
    output = Path("artifacts/period_risk")
    output.mkdir(parents=True, exist_ok=True)
    example = runpy.run_path("examples/period_risk.py")
    cases, context = example["make_cases"]()
    expected = dict(
        independent_or=0.28, shared_identity=0.2, period_and=0.02, vote_one=0.28, neither=0.72, joint_or=0.25
    )
    references = {}
    for name, request in cases.items():
        record = evaluate_period_logic(request, context, client=owner)
        assert record["status"] == "exchange_checked", record["issues"]
        actual = record["probability"]["probability"]
        assert math.isclose(actual, expected[name], rel_tol=1e-12, abs_tol=0)
        references[name] = dict(
            actual=actual, expected=expected[name], absolute_error=abs(actual - expected[name])
        )
        (output / (name + ".json")).write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    # Recompute the existing primary fit from its declared job and the freshly checked workbook.
    fit_path = Path("artifacts/creep/primary_censored_fit.json")
    if not fit_path.exists():
        raise SystemExit("Run scripts/check_creep.py with these owner paths before this check.")
    previous = json.loads(fit_path.read_text(encoding="utf-8"))
    assert previous["record_digest"] == digest({k: v for k, v in previous.items() if k != "record_digest"})
    assert previous["implementation_sha256"] == file_digest(Path("src/pipeline_integrity/creep_exchange.py"))
    workbook = Path("data/raw/cmsx6_creep/2024-10_Creep-reference-data-CMSX-6_v1.0.xlsx")
    source = load_cmsx6_specimens(workbook)
    rows = source["records"]
    request = CreepRequest.model_validate_json(json.dumps(previous["request"]))
    for key in ("specimen_id", "initial_stress_Pa", "temperature_K", "endpoint_time_s", "right_censored"):
        assert request.parameters[key] == [r[key] for r in rows]
    for key in ("material_condition_id", "stress_basis", "temperature_basis"):
        assert {r[key] for r in rows} == {request.parameters[key]}
    assert request.material_condition_id == request.parameters["material_condition_id"]
    assert request.parameters["family"] == "stress_only"
    fit_context = RunContext.model_validate_json(json.dumps(previous["context"]))
    assert next(e for e in fit_context.evidence if e.id == "source").sha256 == source["source_sha256"]
    fit = evaluate_creep(request, fit_context)
    assert fit["status"] == "computed" and fit["values"]["model"]["failures"] == 9
    extra = tuple(
        EvidenceRef(
            id=key,
            citation="Original hypothetical system/age/common-survival mapping and declared feasible joint scenarios; no observed component dependence",
            origin="synthetic",
            license_id="MIT",
        )
        for key in ("period", "mapping", "logic", "joint_scenarios")
    )
    system = RunContext(
        asset_id="hypothetical_two_cmsx6_components",
        scenario_id="declared_dependence_scenarios",
        clock_id="elapsed_s",
        origin="synthetic",
        evidence=(*fit_context.evidence, *extra),
    )
    period = RiskPeriod(
        id="survival_400000_600000",
        clock_id=system.clock_id,
        start_s=400000.0,
        finish_s=600000.0,
        common_condition_id="both_survived_to_start",
        common_conditioning="Both hypothetical components survived to 400000 s under unchanged declared CMSX-6 conditions; fresh-material origin and joint scope are research assumptions",
        evidence_ids=("period",),
    )
    predict = runpy.run_path("examples/creep_methods.py")["prediction_request"]
    events = []
    for key, stress in (("A", 2e8), ("B", 2.01e8)):
        ctx = system.model_copy(update={"asset_id": f"hypothetical_component_{key}"})
        req = predict(
            fit, ctx, current_age_s=400000.0, horizon_s=200000.0, stress_pa=stress, temperature_k=1253.15
        )
        prediction = evaluate_creep(req, ctx, fit_record=fit)
        assert prediction["status"] == "computed"
        mapping = CreepRiskMapping(
            expected_prediction_digest=prediction["record_digest"],
            event_id=key,
            physical_scope_id=f"component_{key}",
            condition_id="individual_survival",
            period_start_s=period.start_s,
            period_finish_s=period.finish_s,
            fresh_material_origin_s=0.0,
            conditioning="Individual hypothetical component survived to period start under unchanged conditions",
            failure_definition="First creep rupture during the declared period",
            age_origin_evidence_id="mapping",
            evidence_ids=("source", "review", "mapping"),
            physical_event_and_age_mapping_reviewed=True,
        )
        bound = creep_risk_probability(prediction, mapping)
        event = bind_creep_period_event(
            bound,
            period=period,
            target_context=system,
            mapping_evidence_ids=("mapping",),
            common_conditioning_reviewed=True,
        )
        events.append(event)
        (output / ("primary_" + key + ".json")).write_text(
            json.dumps(bound, indent=2) + "\n", encoding="utf-8"
        )
    req = PeriodLogicRequest(
        root=FaultNode(
            kind="OR", children=tuple(FaultNode(kind="event", event_id=e.probability.id) for e in events)
        ),
        events=tuple(events),
        period=period,
        top_id="either_creep_rupture",
        top_endpoint_id="either_creep_rupture",
        top_definition="At least one of two hypothetical components ruptures during this period; temporal coincidence or containment is not inferred",
        logic_evidence_id="logic",
        event_logic_reviewed=True,
        dependence="declared_independent",
        dependence_evidence_id="joint_scenarios",
    )
    unmodeled = evaluate_period_logic(req, system, client=owner)
    assert unmodeled["status"] == "withheld" and unmodeled["provider"] is None
    a, b = (e.probability.probability for e in events)
    scenarios = {}
    for name, overlap, none, reference in (
        ("maximum_overlap", min(a, b), 1 - max(a, b), max(a, b)),
        ("minimum_overlap", max(0.0, a + b - 1), max(0.0, 1 - a - b), min(1.0, a + b)),
    ):
        states = tuple(
            JointState(states=dict(A=x, B=y), probability=p)
            for x, y, p in (
                (False, False, none),
                (True, False, a - overlap),
                (False, True, b - overlap),
                (True, True, overlap),
            )
        )
        result = evaluate_period_logic(
            req.model_copy(update={"dependence": "joint_states", "joint_states": states}),
            system,
            client=owner,
        )
        assert result["status"] == "exchange_checked", result["issues"]
        actual = result["probability"]["probability"]
        assert math.isclose(actual, reference, rel_tol=1e-12, abs_tol=1e-15)
        scenarios[name] = dict(
            probability=actual,
            algebraic_reference=reference,
            declared_not_observed_joint_distribution=True,
            result_digest=result["result_digest"],
        )
        (output / (name + ".json")).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    top = RiskProbability.model_validate_json(json.dumps(result["probability"]))
    exposure = DemandExposure(
        opportunities_per_year=1.0,
        opportunity_id=top.opportunity_id,
        opportunity_definition=top.opportunity_definition,
        condition_id=top.condition_id,
        evidence_id="period",
    )
    annualized = owner.annualize(top, exposure)
    assert annualized["status"] == "withheld"
    assert before == {key: revision(path) for key, path in roots.items()}
    summary = dict(
        schema="pipeline_integrity.period_logic_verification.v1",
        independent_references=references,
        primary_data=dict(
            dataset_id=source["dataset_id"],
            source=source["source"],
            citation=source["citation"],
            license_id=source["license_id"],
            source_sha256=source["source_sha256"],
            specimens=len(rows),
            failures=fit["values"]["model"]["failures"],
            right_censored=sum(r["right_censored"] for r in rows),
            source_fit_recomputed=True,
            hypothetical_components=2,
            common_survival_and_fresh_age_are_declared_assumptions=True,
            shared_model_independence_status=unmodeled["status"],
            fitted_period_marginals=dict(A=a, B=b),
            scenarios=scenarios,
            parameter_uncertainty_included=False,
            observed_joint_dependence_available=False,
            independent_field_validation=False,
        ),
        demand_annualization_status=annualized["status"],
        provider=owner.provider,
        external_sources=before,
        external_sources_unchanged=True,
        implementation_sha256=file_digest(Path("src/pipeline_integrity/period_risk.py")),
        script_sha256=file_digest(Path(__file__)),
        example_sha256=file_digest(Path("examples/period_risk.py")),
        temporal_coincidence_or_repair_modelled=False,
        source_or_transformed_specimen_rows_in_release=False,
        field_validation_claim=False,
    )
    Path("research/period_risk_results.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            dict(
                status="passed",
                authored_native_references=len(references),
                primary_specimens=len(rows),
                hypothetical_joint_scenarios=len(scenarios),
                shared_model_independence_withheld=True,
                annualization_withheld=True,
            )
        )
    )


if __name__ == "__main__":
    main()
