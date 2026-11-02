"""Call existing HRA and PRISM engines with explicit research-only evidence transfers."""

import argparse
import json
import math
import os
import subprocess
import sys
from pathlib import Path

from check_integrations import revision

from pipeline_integrity.contracts import RunContext, digest
from pipeline_integrity.datasets import file_digest
from pipeline_integrity.risk_exchange import (
    DemandExposure,
    EventBranch,
    EventTreeRequest,
    FaultNode,
    FaultTreeRequest,
    HRARiskClient,
    JointState,
    ProtectionLayer,
    RiskFrequency,
    RiskProbability,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--hra", type=Path, required=True)
    parser.add_argument("--prism-source", type=Path, required=True)
    args = parser.parse_args()
    sys.path[:0] = [str((args.hra / "src").resolve()), str(args.prism_source.resolve())]
    from human_reliability import (
        ApplicabilityReview,
        BetaPrior,
        HumanFailureEvent,
        annual_frequency_from_evidence,
        builtin_evidence_catalog,
    )
    from human_reliability.dependency import apply_recovery

    context = RunContext(
        asset_id="synthetic_pipe",
        scenario_id="risk_boundary_reference",
        clock_id="declared_annual_exposure",
        origin="synthetic",
    )
    client = HRARiskClient()

    def assumed(key, value, *, condition="demand", protections=None, groups=(), role="equipment"):
        source = {
            "probability": value,
            "basis": "authored assumption for an API reference, not plant evidence",
        }
        return RiskProbability(
            id=key,
            probability=value,
            measure="demand_failure_probability",
            condition_id=condition,
            conditioning=f"Declared synthetic case: {condition}",
            opportunity_id="one_declared_demand",
            opportunity_definition="One demand in the declared conditional case",
            failure_definition=f"{key} fails",
            role=role,
            protection_ids=protections or (key,),
            dependency_groups=groups,
            evidence_ids=(f"assumed:{key}",),
            qualification="assumed",
            context=context,
            source_kind="declared",
            source_payload=source,
            source_digest=digest(source),
        )

    rate_source = {"annual_frequency_per_year": 0.01, "basis": "authored synthetic annual demand assumption"}
    rate = RiskFrequency(
        id="declared_protection_demand",
        annual_frequency_per_year=0.01,
        condition_id="demand",
        endpoint="protection_demand",
        evidence_ids=("assumed:annual_demand",),
        context=context,
        source_payload=rate_source,
        source_digest=digest(rate_source),
    )
    catalog = builtin_evidence_catalog()
    row = catalog.get("sacada.action.r0003")
    source = catalog.sources[row.source_id]
    assert row.kind == "observed_count" and source.rights.numeric_reuse == "government_public_use"
    event = HumanFailureEvent(
        id="manual_isolation_failure",
        task_id="synthetic_response",
        step_ids=("manual_command",),
        role="response",
        failure_definition="The declared manual isolation objective is not achieved within the response window",
        opportunity_definition="One reviewed manual isolation demand after automatic isolation has failed",
        conditioning="An automatic isolation failure has occurred in the synthetic reference scenario",
        deadline_s=30.0,
        includes_recovery=True,
        includes_timing=True,
        dependency_group="manual_response_context",
    )
    prior = BetaPrior(alpha=0.5, beta=0.5, evidence_id="assumed:beta_half_half")
    binding = catalog.bind(
        row.id,
        event,
        prior=prior,
        review=ApplicabilityReview(
            analyst="reproducible_interface_reference",
            target_domain="pipeline_response_demo",
            rationale="Cross-domain simulator counts exercise transfer mechanics; they do not calibrate pipeline operations",
            scope_rationale="The source cannot separate recovery and response timing; retain both within the terminal event",
            included_effects=("timing", "recovery"),
        ),
    )
    human = client.bind_human_estimate(
        binding.estimate,
        event,
        condition_id="auto_failed",
        opportunity_id="one_manual_demand",
        context=context,
        protection_ids=("manual_isolation", "manual_recovery", "manual_deadline"),
    )
    qh = (row.failures + prior.alpha) / (row.opportunities + prior.alpha + prior.beta)
    assert math.isclose(human.probability, qh, abs_tol=1e-15)
    assert human.qualification == "research" and human.includes == ("timing", "recovery")

    def leaf(key):
        return FaultNode(kind="event", event_id=key)

    other = assumed("independent_auxiliary", 0.3, condition="auto_failed")
    other_fields = other.model_dump(exclude={"binding_digest"})
    other_fields.update(
        opportunity_id=human.opportunity_id, opportunity_definition=human.opportunity_definition
    )
    other = RiskProbability(**other_fields)
    shared = FaultTreeRequest(
        root=FaultNode(
            kind="OR",
            children=(leaf(human.id), FaultNode(kind="AND", children=(leaf(human.id), leaf(other.id)))),
        ),
        events=(human, other),
        condition_id="auto_failed",
        opportunity_id="one_manual_demand",
        dependence="declared_independent",
        dependence_evidence_id="assumed:auxiliary_independence_for_reference",
    )
    shared_result = client.fault_tree(shared, context)
    assert math.isclose(shared_result["probability"]["probability"], qh, abs_tol=1e-15)
    automatic = assumed("automatic_failure", 0.1, groups=("automatic_context",))
    eta = EventTreeRequest(
        events=(automatic, human),
        partition_evidence_id="authored:exhaustive_three_outcome_reference",
        branches=(
            EventBranch(
                id="automatic_success",
                probability_id=automatic.id,
                complement=True,
                next_condition_id="auto_isolated",
                consequence_id="automatic_isolation",
            ),
            EventBranch(
                id="automatic_failure",
                probability_id=automatic.id,
                next_condition_id="auto_failed",
                children=(
                    EventBranch(
                        id="manual_success",
                        probability_id=human.id,
                        complement=True,
                        next_condition_id="manual_isolated",
                        consequence_id="manual_isolation",
                    ),
                    EventBranch(
                        id="manual_failure",
                        probability_id=human.id,
                        next_condition_id="not_isolated",
                        consequence_id="no_isolation",
                    ),
                ),
            ),
        ),
    )
    eta_result = client.event_tree(rate, eta)
    failure = eta_result["frequencies"][-1]
    assert math.isclose(failure["annual_frequency_per_year"], 0.01 * 0.1 * qh, rel_tol=1e-12)
    assert failure["dependency_groups"] == ["automatic_context", "manual_response_context"]
    exposure = DemandExposure(
        opportunities_per_year=0.001,
        opportunity_id=human.opportunity_id,
        opportunity_definition=human.opportunity_definition,
        condition_id="auto_failed",
        evidence_id="assumed:annual_demand_times_automatic_failure",
    )
    annualized = client.annualize(human, exposure)
    owner_rate = annual_frequency_from_evidence(
        binding.estimate,
        exposure.opportunities_per_year,
        opportunity_definition=event.opportunity_definition,
        exposure_evidence=exposure.evidence_id,
    )
    handoff = client.prism_demand(human, exposure)
    receiving_rate = handoff.native.annual_failure_rate()
    expected = failure["annual_frequency_per_year"]
    for value in (
        annualized["frequencies"][0]["annual_frequency_per_year"],
        owner_rate["annual_frequency_per_year"],
        receiving_rate["annual_frequency_per_year"],
    ):
        assert math.isclose(value, expected, rel_tol=1e-12)
    guards = {"missing_exposure": client.annualize(human, None)["status"]}
    try:
        apply_recovery(binding.estimate, 0.5, evidence_id="duplicate_recovery")
    except ValueError as error:
        guards["native_duplicate_recovery"] = str(error)
    else:
        raise AssertionError("native recovery must not be applied twice")
    a, b = (assumed(key, 0.1, groups=("common_power",)) for key in ("A", "B"))
    joint_base = dict(
        root=FaultNode(kind="AND", children=(leaf("A"), leaf("B"))),
        events=(a, b),
        condition_id="demand",
        opportunity_id=a.opportunity_id,
        dependence_evidence_id="assumed:perfectly_shared_supply_reference",
    )
    guards["unmodeled_shared_dependency"] = client.fault_tree(
        FaultTreeRequest(**joint_base, dependence="declared_independent"), context
    )["status"]
    joint_result = client.fault_tree(
        FaultTreeRequest(
            **joint_base,
            dependence="joint_states",
            joint_states=(
                JointState(states={"A": False, "B": False}, probability=0.9),
                JointState(states={"A": True, "B": True}, probability=0.1),
            ),
        ),
        context,
    )
    assert math.isclose(joint_result["probability"]["probability"], 0.1, abs_tol=1e-15)
    wrong_human = RiskProbability(
        **(human.model_dump(exclude={"binding_digest"}) | {"condition_id": "demand"})
    )
    guards["wrong_eta_condition"] = client.event_tree(
        rate, eta.model_copy(update={"events": (automatic, wrong_human)})
    )["status"]
    repeated = assumed("repeated_recovery", 0.2, protections=("manual_recovery",))
    repeated_fields = repeated.model_dump(exclude={"binding_digest"}) | {
        "opportunity_id": human.opportunity_id,
        "opportunity_definition": human.opportunity_definition,
    }
    repeated = RiskProbability(**repeated_fields)
    duplicate = shared.model_copy(
        update={
            "events": (wrong_human, repeated),
            "root": FaultNode(kind="AND", children=(leaf(human.id), leaf(repeated.id))),
            "condition_id": "demand",
        }
    )
    guards["duplicate_protection_credit"] = client.fault_tree(duplicate, context)["status"]
    events = (assumed("independent_shutdown", 0.1), assumed("passive_containment", 0.05))
    layers = tuple(
        ProtectionLayer(
            id=e.id, probability_id=e.id, kind=kind, independence_evidence_id=f"assumed:independence:{e.id}"
        )
        for e, kind in zip(events, ("automatic", "passive"), strict=True)
    )
    lopa_result = client.lopa(rate, events, layers)
    assert math.isclose(lopa_result["frequencies"][0]["annual_frequency_per_year"], 0.00005, abs_tol=1e-15)
    assert lopa_result["frequencies"][0]["endpoint"] == "protection_failure_combination"
    human_layer = assumed("manual_layer", 0.2, role="response")
    human_layer_result = client.lopa(
        rate,
        (human_layer,),
        (
            ProtectionLayer(
                id=human_layer.id,
                probability_id=human_layer.id,
                kind="human",
                independence_evidence_id="assumed:independence",
                response_feasible=None,
            ),
        ),
    )
    guards["unproven_human_response_feasibility"] = human_layer_result["status"]
    consumed_rate = RiskFrequency(
        **(
            rate.model_dump(exclude={"binding_digest"})
            | {
                "consumed_protection_ids": (events[0].id,),
            }
        )
    )
    guards["upstream_protection_reuse"] = client.lopa(consumed_rate, events, layers)["status"]
    assert all(value == "withheld" for key, value in guards.items() if key != "native_duplicate_recovery"), (
        guards
    )
    full = {
        "binding": human.model_dump(mode="json"),
        "shared_fault_tree": shared_result,
        "event_tree": eta_result,
        "joint_fault_tree": joint_result,
        "annualized": annualized,
        "native_annualized": owner_rate,
        "prism_handoff": handoff.record(),
        "prism_rate": receiving_rate,
        "lopa": lopa_result,
        "human_lopa_withheld": human_layer_result,
        "guard_checks": guards,
    }
    root = Path("artifacts/risk_exchange")
    root.mkdir(parents=True, exist_ok=True)
    local_file = root / "native_exchange.json"
    local_file.write_text(json.dumps(full, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    job_path, cli_path = root / "fault_tree_job.json", root / "cli_result.json"
    subprocess.run([sys.executable, "examples/risk_exchange.py", "--write-job", str(job_path)], check=True)
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(
        (str(Path("src").resolve()), str((args.hra / "src").resolve()), environment.get("PYTHONPATH", ""))
    )
    subprocess.run(
        [sys.executable, "-m", "pipeline_integrity.cli", "risk", str(job_path), "--output", str(cli_path)],
        env=environment,
        check=True,
    )
    cli_result = json.loads(cli_path.read_text(encoding="utf-8"))
    assert cli_result["status"] == "exchange_checked"
    assert math.isclose(cli_result["probability"]["probability"], 0.2, abs_tol=1e-15)
    catalog_root = args.hra / "src/human_reliability/data"
    result = {
        "schema": "pipeline_integrity.risk_exchange_checks.v1",
        "sources": {"HRA": revision(args.hra), "PRISM": revision(args.prism_source.parent)},
        "hra_provider": client.provider,
        "evidence": {
            "record_id": row.id,
            "source_id": source.id,
            "url": source.url,
            "license_id": source.rights.license_id,
            "numeric_reuse": source.rights.numeric_reuse,
            "rights_basis": "pinned owner metadata; source-server rights were not reverified by this run",
            "source_domain": row.domain,
            "target_domain": "pipeline_response_demo",
            "prior": prior.model_dump(mode="json"),
            "posterior_mean": qh,
            "qualified_status": human.qualification,
            "included_effects": human.includes,
            "catalog_sha256": {p.name: file_digest(p) for p in sorted(catalog_root.glob("*.json"))},
        },
        "shared_event_top_probability": shared_result["probability"]["probability"],
        "explicit_joint_and_probability": joint_result["probability"]["probability"],
        "eta_path_count": len(eta_result["frequencies"]),
        "eta_frequency_conservation_error_per_year": eta_result["native_result"][
            "frequency_conservation_error_per_year"
        ],
        "no_isolation_expected_annual_count": expected,
        "prism_demand_expected_annual_count": receiving_rate["annual_frequency_per_year"],
        "lopa_declared_independent_residual_per_year": lopa_result["frequencies"][0][
            "annual_frequency_per_year"
        ],
        "lopa_endpoint": lopa_result["frequencies"][0]["endpoint"],
        "guard_checks": guards,
        "json_cli": {
            "status": cli_result["status"],
            "top_probability": cli_result["probability"]["probability"],
            "input_digest": cli_result["input_digest"],
            "result_digest": cli_result["result_digest"],
        },
        "local_native_record_sha256": file_digest(local_file),
        "implementation_sha256": file_digest(Path("src/pipeline_integrity/risk_exchange.py")),
        "certified_ipl_claim": False,
        "physical_release_frequency_calculated": False,
        "independent_field_validation": False,
        "external_sources_modified": False,
    }
    Path("research/risk_exchange_results.json").write_text(
        json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                key: value
                for key, value in result.items()
                if key not in {"sources", "evidence", "hra_provider"}
            }
        )
    )


if __name__ == "__main__":
    main()
