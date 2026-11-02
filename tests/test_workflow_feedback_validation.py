"""Independent workflow oracles; numerical execution belongs to Nurion.

The real constrained GLS adapter fits a common mean to the two observations
(x, 2) with identity covariance. Its solution is F(x)=(x+2)/2 and its residual
sum of squares is (x-2)**2/2. No dispatcher, campaign, optimizer or GLS routine
is replaced by a test callback. The analytic model is a workflow fixture and
does not qualify a physical process model or an industrial probability law.
"""

from copy import deepcopy

import pytest

from pipeline_integrity.adapter_campaign import (
    AdapterCampaign,
    CampaignBinding,
    CampaignNode,
    run_adapter_campaign,
    verify_adapter_campaign_record,
)
from pipeline_integrity.adapter_feedback import (
    AdapterFeedbackRequest,
    FeedbackLink,
    evaluate_adapter_feedback,
    verify_adapter_feedback_record,
)
from pipeline_integrity.adapter_jobs import AdapterJob, run_adapter_job, verify_adapter_job_record
from pipeline_integrity.constrained_metrology import ConstrainedGLSRequest, GLSParameter
from pipeline_integrity.continuous_optimization import (
    ContinuousDecision,
    ContinuousOptimizationRequest,
    OptimizationReadout,
    evaluate_continuous_optimization,
    verify_continuous_optimization_record,
)
from pipeline_integrity.contracts import EvidenceRef, RunContext, digest
from pipeline_integrity.metrology import MeasurementBasis, MeasurementInput, StandardCovariance


def mean_job(*, x=0.0, reviewed=True, job_id="mean-job"):
    evidence = EvidenceRef(
        id="linear-review", citation="Authored two-observation workflow oracle",
        origin="synthetic", sha256=digest("identity-covariance-common-mean-fixture"),
    )
    context = RunContext(
        asset_id="workflow-oracle", scenario_id="linear-map", clock_id="elapsed-s",
        origin="synthetic", evidence=(evidence,),
    )
    basis = MeasurementBasis(
        quantity="dimensionless_response", unit="1", definition_id="common-mean-response",
        evidence_id=evidence.id,
    )
    request = ConstrainedGLSRequest(
        id="two-observation-mean", asset_id=context.asset_id, clock_id=context.clock_id,
        event_id="linear-map", time_s=0.0,
        observations=(
            MeasurementInput(id="variable", estimate=x, basis=basis, evidence_id=evidence.id, valid=True),
            MeasurementInput(id="anchor", estimate=2.0, basis=basis, evidence_id=evidence.id, valid=True),
        ),
        parameters=(GLSParameter(id="mean", basis=basis, scale=1.0, evidence_id=evidence.id),),
        design_matrix=((1.0,), (1.0,)), design_coefficient_units=("1",),
        observation_scales=(1.0, 1.0),
        covariance=StandardCovariance(
            input_ids=("variable", "anchor"), values=((1.0, 0.0), (0.0, 1.0)),
            evidence_id=evidence.id, definition_reviewed=True,
        ),
        declared_covariance_rank=2,
        exact_linear_model_and_absolute_covariance_reviewed=reviewed,
        singular_rank_and_exact_directions_reviewed=True,
        model_evidence_id=evidence.id, evidence_ids=(evidence.id,),
    )
    return AdapterJob(
        id=job_id, adapter_id="instrument.constrained_gls",
        parameters=request.model_dump(mode="json"), context=context,
    )


def feedback_request(*, relaxation=1.0, maximum_iterations=100, reviewed=True):
    job = mean_job(reviewed=reviewed)
    campaign = AdapterCampaign(id="linear-feedback-child", nodes=(CampaignNode(id="mean", job=job),))
    link = FeedbackLink(
        id="mean-return", source_node_id="mean", source_result_pointer="/parameter_estimates/0",
        target_node_id="mean", target_pointer="/observations/0/estimate",
        source_unit="1", target_unit="1", lower_bound=-10.0, upper_bound=10.0,
        absolute_tolerance=1e-5, relaxation=relaxation, mapping_evidence_id="linear-review",
    )
    request = AdapterFeedbackRequest(
        id="linear-feedback", campaign=campaign, links=(link,), maximum_iterations=maximum_iterations,
        fixed_point_mapping_and_bounds_reviewed=True,
    )
    return request, job.context


def optimization_request(*, reviewed=True, constraint_lower=None):
    job = mean_job(x=0.0, reviewed=reviewed)
    constraints = () if constraint_lower is None else (
        OptimizationReadout(
            id="minimum-mean", result_pointer="/parameter_estimates/0", source_unit="1", unit="1",
            normalization_scale=1.0, evidence_id="linear-review", lower_bound=constraint_lower,
        ),
    )
    request = ContinuousOptimizationRequest(
        id="convex-common-mean-loss", job=job,
        decisions=(ContinuousDecision(
            id="variable-observation", parameter_pointer="/observations/0/estimate",
            lower_bound=-2.0, upper_bound=6.0, unit="1", target_unit="1", evidence_id="linear-review",
        ),),
        objective=OptimizationReadout(
            id="residual-sum-squares", result_pointer="/stochastic_sum_squares",
            source_unit="1", unit="1", normalization_scale=1.0, evidence_id="linear-review",
        ),
        constraints=constraints, seed=713, population_multiplier=5, maximum_generations=24,
        maximum_model_evaluations=200, maximum_result_bytes=16_000_000,
        relative_solver_tolerance=1e-6, domain_units_objective_and_constraints_reviewed=True,
    )
    return request, job.context


def test_campaign_actual_dispatch_feedforward_units_and_child_seals():
    first, second = mean_job(x=6.0, job_id="upstream"), mean_job(x=-4.0, job_id="downstream")
    campaign = AdapterCampaign(id="two-real-gls-solves", nodes=(
        CampaignNode(id="source", job=first),
        CampaignNode(id="destination", job=second, input_bindings=(CampaignBinding(
            source_node_id="source", source_pointer="/parameter_estimates/0",
            target_parameter_pointer="/observations/0/estimate", mapping_evidence_id="linear-review",
            source_unit="1", target_unit="1",
        ),)),
    ))
    result = run_adapter_campaign(campaign, providers={})
    assert result["status"] == "computed" and result["execution_order"] == ["source", "destination"]
    assert result["converted_values"] == 1
    assert result["nodes"]["source"]["receipt"]["result"]["parameter_estimates"] == pytest.approx([4.0])
    destination = result["nodes"]["destination"]
    assert destination["receipt"]["job"]["parameters"]["observations"][0]["estimate"] == pytest.approx(4.0)
    assert destination["receipt"]["result"]["parameter_estimates"] == pytest.approx([3.0])
    assert destination["bindings"][0]["source_receipt_digest"] == result["nodes"]["source"]["receipt"]["record_digest"]
    assert verify_adapter_campaign_record(result, request=campaign, providers={})
    assert result["engineering_qualification_conferred"] is False


@pytest.mark.parametrize("relaxation", [1.0, 0.4])
def test_actual_gls_fixed_point_matches_independent_linear_iterates_and_unrelaxed_residual(relaxation):
    request, context = feedback_request(relaxation=relaxation)
    record = evaluate_adapter_feedback(request, context, providers={})
    assert record["status"] == "computed_diagnostic"
    assert record["reason"] == "all_unrelaxed_mapping_residuals_within_tolerance"
    ratio = 1.0 - 0.5 * relaxation
    for iteration, item in enumerate(record["history"]):
        link = item["links"][0]
        independent_input = 2.0 - 2.0 * ratio**iteration
        assert link["current_input"] == pytest.approx(independent_input, abs=2e-13)
        assert link["mapped_value"] == pytest.approx((independent_input + 2.0) / 2.0, abs=2e-13)
        assert link["residual"] == pytest.approx((2.0 - independent_input) / 2.0, abs=2e-13)
        assert link["next_input"] == pytest.approx(independent_input + relaxation * link["residual"], abs=2e-13)
        assert item["campaign"]["nodes"]["mean"]["receipt"]["result"]["status"] == "computed"
    last = record["history"][-1]["links"][0]
    assert abs(last["residual"]) <= last["tolerance"]
    assert abs(last["current_input"] - 2.0) <= 2.0 * last["tolerance"] + 2e-13
    assert record["solution_uniqueness_proven"] is False and record["transient_stability_proven"] is False
    assert verify_adapter_feedback_record(record, request=request, context=context, providers={})


def test_tiny_relaxation_cannot_promote_a_large_unrelaxed_residual_to_convergence():
    request, context = feedback_request(relaxation=1e-9, maximum_iterations=2)
    record = evaluate_adapter_feedback(request, context, providers={})
    assert record["status"] == "withheld" and record["reason"] == "iteration_limit"
    for item in record["history"]:
        link = item["links"][0]
        assert abs(link["next_input"] - link["current_input"]) < link["tolerance"]
        assert abs(link["residual"]) > 0.9 and not link["converged"]


def test_outer_allowlisted_feedback_dispatch_injects_an_explicit_nested_provider_registry():
    request, context = feedback_request(maximum_iterations=30)
    outer = AdapterJob(
        id="outer-feedback", adapter_id="workflow.fixed_point", context=context,
        parameters=request.model_dump(mode="json"), bindings={"providers": "nested-registry"},
    )
    registry = {"nested-registry": {}}
    receipt = run_adapter_job(outer, providers=registry)
    assert receipt["result"]["status"] == "computed_diagnostic"
    assert receipt["identity"]["adapter"]["id"] == "workflow.fixed_point"
    assert verify_adapter_job_record(receipt, job=outer, providers=registry)["valid"]
    assert registry == {"nested-registry": {}}
    with pytest.raises(ValueError, match="alias is not registered"):
        run_adapter_job(outer, providers={})


def test_seeded_continuous_search_matches_independent_convex_loss_and_replays_without_global_claim():
    request, context = optimization_request()
    record = evaluate_continuous_optimization(request, context, providers={})
    assert record["status"] == "computed_diagnostic" and record["best_sample_index"] is not None
    assert not record["budget_exhausted"]
    assert record["model_evaluations"] == record["retained_model_evaluations"] == len(record["samples"])
    for row in record["samples"]:
        x = row["decisions"]["variable-observation"]["value"]
        assert -2.0 <= x <= 6.0
        assert row["accepted_model_status"] and row["native_status"] == "computed"
        assert row["objective"] == pytest.approx((x - 2.0) ** 2 / 2.0, abs=1e-12)
        assert row["receipt"]["result"]["parameter_estimates"][0] == pytest.approx((x + 2.0) / 2.0, abs=1e-12)
    best = record["samples"][record["best_sample_index"]]
    assert best["decisions"]["variable-observation"]["value"] == pytest.approx(2.0, abs=0.005)
    assert best["objective"] < 2e-5
    assert record["global_optimum_proven"] is False and record["numerical_validation_completed"] is False
    assert verify_continuous_optimization_record(record, request=request, context=context, providers={})


def test_withheld_native_status_blocks_campaign_feedback_and_optimization_without_readout_fabrication():
    child = mean_job(reviewed=False)
    campaign = AdapterCampaign(id="withheld-dag", nodes=(
        CampaignNode(id="unreviewed", job=child),
        CampaignNode(id="dependent", job=mean_job(job_id="dependent"), depends_on=("unreviewed",)),
    ))
    result = run_adapter_campaign(campaign, providers={})
    assert result["status"] == "partial"
    assert result["nodes"]["unreviewed"]["native_status"] == "needs_data"
    assert result["nodes"]["unreviewed"]["status"] == "withheld"
    assert result["nodes"]["dependent"]["status"] == "blocked"
    assert result["nodes"]["dependent"]["executed"] is False
    feedback, context = feedback_request(reviewed=False)
    trace = evaluate_adapter_feedback(feedback, context, providers={})
    assert trace["status"] == "withheld" and trace["reason"] == "child_model_not_accepted"
    assert len(trace["history"]) == 1 and trace["history"][0]["links"] == []
    optimization, context = optimization_request(reviewed=False)
    optimization = optimization.model_copy(update={"maximum_generations": 1, "maximum_model_evaluations": 100})
    search = evaluate_continuous_optimization(optimization, context, providers={})
    assert search["status"] == "withheld" and search["best_sample_index"] is None
    assert search["samples"]
    assert all(row["native_status"] == "needs_data" and not row["feasible"] for row in search["samples"])
    assert all(row["objective"] is None and row["constraint_values"] is None for row in search["samples"])


def test_outer_search_dispatch_preserves_the_independent_linear_feasibility_bound():
    request, context = optimization_request(constraint_lower=3.0)
    outer = AdapterJob(
        id="outer-constrained-search", adapter_id="economics.continuous_optimization",
        parameters=request.model_dump(mode="json"), context=context,
        bindings={"providers": "nested-registry"},
    )
    record = run_adapter_job(outer, providers={"nested-registry": {}})["result"]
    assert record["status"] == "computed_diagnostic"
    best = record["samples"][record["best_sample_index"]]
    x = best["decisions"]["variable-observation"]["value"]
    # (x+2)/2 >= 3 is exactly x >= 4. The convex loss has its
    # constrained minimum at x=4 with value 2, independently of the search.
    assert 4.0 - 1e-12 <= x <= 4.005
    assert best["objective"] == pytest.approx(2.0, abs=0.011)
    assert best["constraint_values"][0] >= 3.0
    assert record["global_optimum_proven"] is False
    for row in record["samples"]:
        if row["feasible"]:
            assert row["decisions"]["variable-observation"]["value"] >= 4.0 - 1e-12


def test_feedback_and_search_seals_reject_changed_results_before_reexecution():
    request, context = feedback_request()
    feedback = evaluate_adapter_feedback(request, context, providers={})
    changed = deepcopy(feedback)
    changed["history"][0]["links"][0]["residual"] = 0.0
    with pytest.raises(ValueError, match="receipt changed"):
        verify_adapter_feedback_record(changed, request=request, context=context, providers={})
    request, context = optimization_request()
    record = evaluate_continuous_optimization(request, context, providers={})
    changed = deepcopy(record)
    changed["samples"][changed["best_sample_index"]]["objective"] = -1.0
    with pytest.raises(ValueError, match="receipt changed"):
        verify_continuous_optimization_record(changed, request=request, context=context, providers={})
