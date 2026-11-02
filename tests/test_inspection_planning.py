"""Campaign composition and policy with fixed owner replies, plus independent tiny-instance enumeration."""

import itertools

import pytest

from pipeline_integrity.adapters import ExternalResult
from pipeline_integrity.contracts import EvidenceRef, RunContext, digest
from pipeline_integrity.inspection_planning import (
    DeclaredInspectionPOD,
    InspectionCampaign,
    InspectionHypothesis,
    InspectionOption,
    InspectionTarget,
    LogisticInspectionPOD,
    plan_inspections,
)
from pipeline_integrity.scheduling import PlanningResource

CONTEXT = RunContext(
    asset_id="a",
    scenario_id="planning",
    clock_id="elapsed",
    origin="synthetic",
    evidence=(EvidenceRef(id="e", citation="Authored campaign policy reference", origin="synthetic"),),
)


class MaterialSpy:
    """Fixed known owner responses; no likelihood, Bayes or detection model implementation."""

    def __init__(self, *, unresolved=False):
        self.calls = []
        self.unresolved = unresolved

    def evaluate(self, model_id, parameters, context, *, location_id):
        self.calls.append((model_id, parameters))
        status = "computed"
        if model_id == "inspection.logistic_pod":
            values = {"probability_of_detection": {0.0: 0.0, 1.0: 0.5}[parameters["flaw_size_m"]]}
        else:
            pods, coverage = tuple(parameters["probabilities_of_detection"]), parameters["coverage_fraction"]
            if coverage == 0:
                values = {"posterior_weights": [0.5, 0.5], "probability_of_no_indication": 1.0}
            elif self.unresolved:
                values, status = {}, "needs_data"
            elif pods == (0.0, 0.8):
                values = {"posterior_weights": [5 / 6, 1 / 6], "probability_of_no_indication": 0.6}
            elif pods == (0.0, 0.5):
                values = {"posterior_weights": [2 / 3, 1 / 3], "probability_of_no_indication": 0.75}
            elif pods == (0.0, 1.0) and parameters["prior_weights"] == [0.0, 1.0]:
                values, status = {}, "out_of_scope"
            else:
                raise AssertionError("no fixed native fixture for these inputs")
        payload = {
            "model_id": model_id,
            "inputs": parameters,
            "values": values,
            "status": status,
            "context": {
                "asset_id": context.asset_id,
                "location_id": location_id,
                "time_basis": context.clock_id,
                "metadata": {"pipeline_context_digest": digest(context)},
            },
        }
        return ExternalResult(
            owner="matintegrity",
            owner_schema="matintegrity.assessment.v1",
            status=status,
            asset_id=context.asset_id,
            payload=payload,
            payload_digest=digest(payload),
        )


def target(key, *, weight=1.0, start=0.0, required=False):
    return InspectionTarget(
        id=key,
        location_id=key,
        required=required,
        hypotheses=(
            InspectionHypothesis(id="healthy", prior_mass=0.5, unrevealed_weight=0.0, evidence_id="e"),
            InspectionHypothesis(id="flaw", prior_mass=0.5, unrevealed_weight=weight, evidence_id="e"),
        ),
        options=(
            InspectionOption(
                id="inspect",
                method_id="declared_demo",
                start_s=start,
                duration_s=2.0,
                coverage_fraction=1.0,
                sizes_m={"healthy": 0.0, "flaw": 1.0},
                detection=DeclaredInspectionPOD(probabilities={"healthy": 0.0, "flaw": 0.8}, evidence_id="e"),
                resources={"crew": 1},
                budget_use={"money": 1},
                evidence_id="e",
            ),
        ),
    )


def campaign(targets=None, **changes):
    values = dict(
        horizon_s=10.0,
        targets=targets or (target("a"),),
        resources=(PlanningResource(id="crew", capacity=1, evidence_id="e"),),
        budgets={"money": 1},
        score_quantum=0.001,
        criterion_unit="authored_unrevealed_weight",
        evidence_id="e",
    )
    return InspectionCampaign(**(values | changes))


def test_budget_selects_higher_weight_and_preserves_conditional_and_unconditional_meanings():
    result = plan_inspections(
        campaign((target("a", weight=2.0), target("b"))), CONTEXT, materials=MaterialSpy()
    )
    assert result.status == "optimal" and result.rounded_score_proven_optimal
    assert {r["operation_id"] for r in result.selected if r["mode_id"] != "not_selected"} == {"a"}
    assert result.unrounded_model_score == pytest.approx(0.7)
    assert not result.unrounded_score_proven_optimal and not result.criterion_is_failure_probability
    row = next(r for r in result.option_scores if r["target_id"] == "a" and r["option_id"] == "inspect")
    assert row["probability_of_no_indication"] == 0.6
    assert row["posterior_given_no_indication"][1] == pytest.approx(1 / 6)
    assert row["score"] == pytest.approx(0.2)
    result.check_integrity()


@pytest.mark.parametrize("start,expected", [(0.0, 1), (2.0, 2)])
def test_shared_resources_and_half_open_fixed_appointments_use_the_shared_solver(start, expected):
    spec = campaign((target("a"), target("b", start=start)), budgets={"money": 2})
    result = plan_inspections(spec, CONTEXT, materials=MaterialSpy())
    assert sum(r["mode_id"] != "not_selected" for r in result.selected) == expected


def test_required_inspection_can_be_infeasible_and_cannot_be_silently_skipped():
    result = plan_inspections(
        campaign((target("a", required=True),), budgets={"money": 0}), CONTEXT, materials=MaterialSpy()
    )
    assert result.status == "infeasible" and not result.selected


def test_unresolved_option_prevents_optimizing_over_a_silently_pruned_menu():
    result = plan_inspections(campaign(), CONTEXT, materials=MaterialSpy(unresolved=True))
    assert result.status == "needs_data" and result.schedule is None and not result.selected


def test_logistic_curve_is_called_in_owner_and_outside_support_is_not_extrapolated():
    t = target("a")
    curve = LogisticInspectionPOD(
        midpoint_size_m=1.0,
        log_size_scale=0.5,
        supported_minimum_size_m=0.1,
        supported_maximum_size_m=2.0,
        evidence_id="e",
    )
    t = t.model_copy(update={"options": (t.options[0].model_copy(update={"detection": curve}),)})
    spy = MaterialSpy()
    result = plan_inspections(campaign((t,)), CONTEXT, materials=spy)
    assert sum(name == "inspection.logistic_pod" for name, _ in spy.calls) == 2
    assert result.unrounded_model_score == 0.25
    unsupported = t.options[0].model_copy(update={"sizes_m": {"healthy": 0.0, "flaw": 3.0}})
    withheld = plan_inspections(
        campaign((t.model_copy(update={"options": (unsupported,)}),)), CONTEXT, materials=MaterialSpy()
    )
    assert withheld.status == "needs_data" and withheld.schedule is None


def test_impossible_no_indication_retains_native_out_of_scope_and_no_posterior():
    t = target("a", required=True)
    t = t.model_copy(
        update={
            "hypotheses": (
                t.hypotheses[0].model_copy(update={"prior_mass": 0.0}),
                t.hypotheses[1].model_copy(update={"prior_mass": 1.0}),
            ),
            "options": (
                t.options[0].model_copy(
                    update={
                        "detection": DeclaredInspectionPOD(
                            probabilities={"healthy": 0.0, "flaw": 1.0}, evidence_id="e"
                        )
                    }
                ),
            ),
        }
    )
    result = plan_inspections(campaign((t,)), CONTEXT, materials=MaterialSpy())
    row = result.option_scores[0]
    assert result.unrounded_model_score == 0.0 and row["posterior_given_no_indication"] is None
    assert row["no_indication_update_status"] == "out_of_scope"


def test_rounding_reports_model_objective_bound_without_claiming_exact_score_optimality():
    result = plan_inspections(campaign(score_quantum=0.3), CONTEXT, materials=MaterialSpy())
    assert result.rounding_error_upper_bound == 0.3
    assert result.model_score_lower_bound <= result.unrounded_model_score
    assert not result.unrounded_score_proven_optimal


def test_tiny_campaign_matches_independent_feasible_menu_enumeration():
    targets = (target("a", weight=3.0), target("b", weight=2.0), target("c", start=2.0))
    result = plan_inspections(campaign(targets, budgets={"money": 2}), CONTEXT, materials=MaterialSpy())
    possibilities = []
    for selected in itertools.product([False, True], repeat=3):
        if sum(selected) > 2 or selected[0] and selected[1]:
            continue
        possibilities.append(
            sum(
                weight * (0.1 if chosen else 0.5)
                for weight, chosen in zip([3.0, 2.0, 1.0], selected, strict=True)
            )
        )
    assert result.unrounded_model_score == pytest.approx(min(possibilities))


def test_evidence_and_nested_python_types_are_checked_before_material_calls():
    spec = campaign()
    spy = MaterialSpy()
    with pytest.raises(ValueError):
        plan_inspections(spec, CONTEXT.model_copy(update={"evidence": ()}), materials=spy)
    bad = spec.targets[0].hypotheses[0].model_copy(update={"prior_mass": True})
    broken = spec.model_copy(
        update={
            "targets": (
                spec.targets[0].model_copy(update={"hypotheses": (bad, spec.targets[0].hypotheses[1])}),
            )
        }
    )
    with pytest.raises(ValueError):
        plan_inspections(broken, CONTEXT, materials=spy)
    assert not spy.calls


def test_mutated_result_or_native_evidence_cannot_be_reused_as_a_verified_plan():
    result = plan_inspections(campaign(), CONTEXT, materials=MaterialSpy())
    result.native_material_results[0].payload["values"]["probability_of_no_indication"] = 0.0
    with pytest.raises(ValueError):
        result.check_integrity()


@pytest.mark.parametrize(
    "field,value", [("probability_of_no_indication", True), ("posterior_weights", [False, True])]
)
def test_untyped_owner_probability_fields_cannot_hide_boolean_values(field, value):
    class BadOwner(MaterialSpy):
        def evaluate(self, *args, **kwargs):
            result = super().evaluate(*args, **kwargs)
            result.payload["values"][field] = value
            return result.model_copy(update={"payload_digest": digest(result.payload)})

    with pytest.raises(ValueError, match="invalid no-indication"):
        plan_inspections(campaign(), CONTEXT, materials=BadOwner())


@pytest.mark.parametrize("changes", [{"budgets": {}}, {"maximum_material_calls": 1}, {"time_quantum_s": 3.0}])
def test_invalid_budget_reference_call_budget_or_time_grid_is_not_repaired(changes):
    with pytest.raises(ValueError):
        campaign(**changes)
