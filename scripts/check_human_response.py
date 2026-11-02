"""Exercise the actual HRA time/recovery models and source-preserving risk export."""

import argparse
import json
import math
import sys
from pathlib import Path

from check_integrations import revision

from pipeline_integrity.contracts import EvidenceRef, RunContext, digest
from pipeline_integrity.datasets import file_digest
from pipeline_integrity.response_exchange import (
    CognitiveResponse,
    CognitiveStage,
    DeadlineEffect,
    EmpiricalResponse,
    HumanResponseClient,
    HumanResponseRequest,
    LognormalResponse,
    RecoveryEffect,
    ResponseObservation,
    ResponseWindow,
    WeibullResponse,
)
from pipeline_integrity.risk_exchange import DemandExposure, FaultNode, FaultTreeRequest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--hra", type=Path, required=True)
    args = parser.parse_args()
    before = revision(args.hra)
    sys.path.insert(0, str((args.hra / "src").resolve()))
    from human_reliability import ApplicabilityReview, BetaPrior, builtin_evidence_catalog
    from human_reliability.contracts import Estimate, HumanFailureEvent

    context = RunContext(
        asset_id="authored_response_reference",
        scenario_id="conditional_response_only",
        clock_id="scenario_elapsed",
        origin="synthetic",
        evidence=tuple(
            EvidenceRef(
                id=key,
                citation=text,
                origin="synthetic",
                license_id="MIT",
            )
            for key, text in [
                ("reference", "Original synthetic probability, response times and process limits"),
                (
                    "review",
                    "Authored conditional-population and clock interpretation for interface verification",
                ),
            ]
        ),
    )
    owner = HumanResponseClient()
    model = LognormalResponse(median_s=10.0, log_sigma=0.5, evidence_id="reference")
    root = Path("artifacts/human_response")
    root.mkdir(parents=True, exist_ok=True)
    cases = {}

    def evaluate(
        key, *, model=model, available=10.0, cue=5.0, bins=(), recovery=None, initial_q=0.2, cognitive=False
    ):
        event = HumanFailureEvent(
            id="response_failure",
            task_id="reviewed_hfe",
            step_ids=("objective",),
            role="response",
            failure_definition=(
                "Correct response not achieved before the reviewed limit"
                if recovery is None
                else "Initial response error remains unrecovered in an untimed assessment"
            ),
            opportunity_definition="One response opportunity in the authored conditional case",
            conditioning="The synthetic process has issued one usable response cue",
            deadline_s=available if recovery is None else None,
            includes_timing=recovery is None,
            includes_recovery=recovery is not None,
        )
        initial = Estimate(
            event_id=event.id,
            method="authored_initial_error",
            probability=initial_q,
            conditioning=event.conditioning,
            evidence_ids=("reference",),
            status="assumed",
            trace={"basis": "Original illustrative assumption, not field-calibrated HEP"},
        )
        if cognitive:
            initial = owner.cognitive_estimate(
                event,
                CognitiveResponse(
                    stages=(
                        CognitiveStage(
                            id="detect", conditional_failure_probability=0.1, evidence_id="reference"
                        ),
                        CognitiveStage(
                            id="act", conditional_failure_probability=0.2, evidence_id="reference"
                        ),
                    ),
                    applicability_evidence_id="review",
                    applicability_rationale="Authored conditional stages only",
                ),
                context,
            )
            assert math.isclose(initial.probability, 0.28, abs_tol=1e-15)
        effect = (
            DeadlineEffect(
                window=ResponseWindow(
                    clock_id=context.clock_id,
                    cue_time_s=cue,
                    limit_time_s=cue + available,
                    evidence_id="reference",
                ),
                time_model=model,
                population_definition="Authored completion durations given no initial error",
                bin_boundaries_s=bins,
            )
            if recovery is None
            else RecoveryEffect(
                failure_to_recover_given_error=recovery,
                evidence_id="reference",
                population_definition="Authored recovery failure conditional on an initial error; no time limit",
            )
        )
        request = HumanResponseRequest(
            condition_id="usable_cue",
            opportunity_id="one_response_demand",
            protection_ids=("manual_response",),
            applicability_evidence_id="review",
            applicability_rationale="Synthetic conditional reference only",
            effect=effect,
        )
        source_before = digest(initial), digest(event)
        result = owner.evaluate(initial, event, request, context)
        assert source_before == (digest(initial), digest(event))
        path = root / f"{key}.json"
        path.write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")
        cases[key] = {
            "status": result.status,
            "probability": None if result.probability is None else result.probability.probability,
            "includes": None if result.probability is None else list(result.probability.includes),
            "native_bin_sum": sum(v["probability"] for v in result.native_completion_bins) or None,
            "issues": result.issues,
            "record_sha256": file_digest(path),
        }
        return result

    reference = evaluate("lognormal_median", bins=(5.0, 10.0, 20.0))
    assert math.isclose(reference.risk_probability().probability, 0.6, abs_tol=1e-15)
    assert math.isclose(sum(v["probability"] for v in reference.native_completion_bins), 1.0, abs_tol=1e-15)
    shifted = evaluate("shifted_clock", cue=105.0)
    assert shifted.risk_probability().probability == reference.probability.probability
    short = evaluate("shorter_window", available=5.0)
    long = evaluate("longer_window", available=20.0)
    assert short.probability.probability > 0.6 > long.probability.probability >= 0.2
    weibull = evaluate(
        "weibull_reference", model=WeibullResponse(scale_s=10.0, shape=1.0, evidence_id="reference")
    )
    assert math.isclose(weibull.probability.probability, 0.2 + 0.8 / math.e, abs_tol=1e-15)
    empirical = EmpiricalResponse(
        observations=tuple(
            ResponseObservation(id=f"trial_{i}", duration_s=t, outcome=outcome, evidence_id="reference")
            for i, (t, outcome) in enumerate(
                [
                    (2.0, "correct_completion"),
                    (4.0, "right_censored"),
                    (4.0, "correct_completion"),
                    (6.0, "correct_completion"),
                    (8.0, "right_censored"),
                ]
            )
        ),
        censoring_review_evidence_id="review",
    )
    tied = evaluate("km_tied_censor_and_completion", model=empirical, available=4.0)
    assert math.isclose(tied.probability.probability, 0.68, abs_tol=1e-15)
    later = evaluate("km_within_support", model=empirical, available=6.0, bins=(2.0, 4.0, 6.0))
    assert math.isclose(later.probability.probability, 0.44, abs_tol=1e-15)
    assert evaluate("km_unidentified_tail", model=empirical, available=9.0).status == "needs_data"
    assert evaluate("km_unidentified_bin", model=empirical, available=6.0, bins=(9.0,)).status == "needs_data"
    for outcome in ("wrong_response", "unknown"):
        unsupported = EmpiricalResponse(
            observations=(
                ResponseObservation(
                    id="competing_trial", duration_s=3.0, outcome=outcome, evidence_id="reference"
                ),
            ),
            censoring_review_evidence_id="review",
        )
        assert evaluate(outcome, model=unsupported).status == "needs_data"
    for key, q in [("recovery_quarter", 0.25), ("recovery_always", 0.0), ("recovery_never", 1.0)]:
        result = evaluate(key, recovery=q)
        assert math.isclose(result.probability.probability, 0.2 * q, abs_tol=1e-15)
    assert evaluate("no_initial_errors", initial_q=0.0).probability.probability == 0.5
    assert evaluate("all_initial_errors", initial_q=1.0).probability.probability == 1.0
    assert math.isclose(
        evaluate("cognitive_then_deadline", cognitive=True).probability.probability, 0.64, abs_tol=1e-15
    )

    risk = reference.risk_probability()
    fault_tree = owner.risk_client.fault_tree(
        FaultTreeRequest(
            root=FaultNode(kind="event", event_id=risk.id),
            events=(risk,),
            condition_id=risk.condition_id,
            opportunity_id=risk.opportunity_id,
            dependence="declared_independent",
            dependence_evidence_id="review",
        ),
        context,
    )
    assert math.isclose(fault_tree["probability"]["probability"], 0.6, abs_tol=1e-15)
    annualized = owner.risk_client.annualize(
        risk,
        DemandExposure(
            opportunities_per_year=2.0,
            opportunity_id=risk.opportunity_id,
            opportunity_definition=risk.opportunity_definition,
            condition_id=risk.condition_id,
            evidence_id="reference",
        ),
    )
    assert math.isclose(annualized["frequencies"][0]["annual_frequency_per_year"], 1.2, abs_tol=1e-15)
    assert annualized["frequencies"][0]["endpoint"] == "event_occurrence"
    (root / "annualized.json").write_text(json.dumps(annualized, indent=2) + "\n", encoding="utf-8")

    catalog = builtin_evidence_catalog()
    source_row = catalog.get("sacada.action.r0003")
    source = catalog.sources[source_row.source_id]
    terminal_event = HumanFailureEvent(
        id="retained_simulator_outcome",
        task_id="cross_domain_review",
        step_ids=("response",),
        role="response",
        failure_definition="Training terminal outcome retains its inseparable timing and recovery scope",
        opportunity_definition="One reviewed simulator training opportunity",
        conditioning="Cross-domain transfer is for interface research only",
        deadline_s=10.0,
        includes_timing=True,
        includes_recovery=True,
    )
    binding = catalog.bind(
        source_row.id,
        terminal_event,
        prior=BetaPrior(alpha=0.5, beta=0.5, evidence_id="reference"),
        review=ApplicabilityReview(
            analyst="original_reference_runner",
            target_domain="pipeline_research_only",
            rationale="Released simulator counts do not calibrate pipeline response probabilities",
            scope_rationale="The terminal outcome does not separate timing and recovery",
            included_effects=("timing", "recovery"),
        ),
    )
    try:
        owner.evaluate(binding.estimate, terminal_event, reference.request, context)
    except ValueError as error:
        assert "already included" in str(error)
        double_count = str(error)
    else:
        raise AssertionError("public terminal outcomes must not receive timing/recovery effects twice")
    assert before == revision(args.hra), "the source HRA project was modified"
    summary = {
        "schema": "pipeline_integrity.human_response_verification.v1",
        "hra": before | {"provider": owner.provider},
        "cases": cases,
        "sacada_scope_guard": {
            "source_record": source_row.id,
            "source_id": source_row.source_id,
            "numeric_reuse": source.rights.numeric_reuse,
            "rejection": double_count,
            "pipeline_field_calibration": False,
        },
        "risk_exchange": {
            "fault_tree_probability": fault_tree["probability"]["probability"],
            "expected_annual_count": 1.2,
            "endpoint": "event_occurrence",
            "physical_loss_of_containment_frequency": False,
        },
        "independent_references": [
            "lognormal CDF at its median",
            "exponential CDF at one scale",
            "hand-counted KM risk sets with tied censor/completion",
            "conditional recovery limits",
        ],
        "module_sha256": file_digest(Path("src/pipeline_integrity/response_exchange.py")),
        "script_sha256": file_digest(Path(__file__)),
        "field_calibrated": False,
        "work_authorized": False,
    }
    Path("research/human_response_results.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {"cases": len(cases), "scope_guard": "passed", "risk_export": "passed", "field_calibrated": False}
        )
    )


if __name__ == "__main__":
    main()
