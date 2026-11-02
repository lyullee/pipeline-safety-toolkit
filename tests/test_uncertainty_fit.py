"""Training-only identification, exact predictive scores and explicit transfer refusals."""

import copy
import math

import numpy as np
import pytest
from pydantic import ValidationError

from pipeline_integrity.contracts import EvidenceRef, RunContext, digest
from pipeline_integrity.inverse import ResidualScale
from pipeline_integrity.uncertainty import EnsembleMetric, EnsembleNode, EnsembleValues, evaluate_ensemble
from pipeline_integrity.uncertainty_fit import (
    DistributionAssessment,
    DistributionFitRequest,
    DistributionUse,
    JointObservation,
    JointObservations,
    UncertainVariable,
    assess_joint_distribution,
    distribution_design,
    fit_joint_distribution,
)


def observations(values=None, *, two=False):
    if values is None:
        values = [float(math.exp(1 + 0.2 * math.sin(i * 0.8) + i / 400)) for i in range(64)]
    context = RunContext(
        asset_id="rig",
        scenario_id="ordered_data",
        clock_id="source_order_coordinate",
        origin="synthetic",
        evidence=(
            EvidenceRef(
                id="reference", citation="Original mathematical test assumptions", origin="synthetic"
            ),
        ),
    )
    keys = ("x", "y") if two else ("x",)
    return JointObservations(
        id="original",
        variables=tuple(
            UncertainVariable(id=k, unit="m", layer="measurement", evidence_id="reference") for k in keys
        ),
        rows=tuple(
            JointObservation(
                id=f"row_{i}",
                order=i,
                group_id="training_group" if i < 40 else "later_group",
                condition_id="condition",
                values={k: x for k in keys},
                valid=dict.fromkeys(keys, x is not None),
                evidence_id="reference",
            )
            for i, x in enumerate(values)
        ),
        order_basis="source_row_order",
        source_digests={"original": "a" * 64},
        context=context,
    )


def request(data, *, small=False, family="lognormal"):
    train = tuple(r.id for r in data.rows[: 4 if small else 40])
    test = tuple(r.id for r in data.rows[4 if small else 48 :])
    return DistributionFitRequest(
        id="fixed_protocol",
        condition_id="condition",
        training_ids=train,
        validation_ids=test,
        families={v.id: family for v in data.variables},
        scales={v.id: ResidualScale(value=1.0, unit=v.unit, source_id="reference") for v in data.variables},
        split_policy="ordered_gap",
        minimum_order_gap=1 if small else 9,
        minimum_training_rows=3 if small else 30,
        minimum_validation_rows=1 if small else 10,
        definition_reviewed=True,
        evidence_id="reference",
    )


def use(mode="normal_score_copula", **updates):
    return DistributionUse(
        mode=mode,
        samples=256,
        seed=42,
        interpretation="scenario",
        target_condition_id="condition",
        applicability_reviewed=True,
        evidence_id="reference",
        **updates,
    )


def assessment(**updates):
    return DistributionAssessment(evidence_id="reference", **updates)


@pytest.mark.parametrize("family", ["normal", "lognormal"])
def test_mle_moments_match_training_only_closed_form_and_do_not_read_holdout(family):
    data = observations(two=True)
    spec = request(data, family=family)
    fit = fit_joint_distribution(data, spec)
    assert fit["status"] == "computed"
    x = np.array([r.values["x"] for r in data.rows[:40]])
    expected = x if family == "normal" else np.log(x)
    p = fit["fitted_variables"][0]["marginal"]["parameters"]
    assert list(p.values()) == pytest.approx([expected.mean(), expected.std(ddof=0)])
    modified = data.model_copy(
        update={
            "rows": (
                *data.rows[:48],
                *(r.model_copy(update={"values": {"x": 1000.0, "y": 0.01}}) for r in data.rows[48:]),
            )
        }
    )
    changed = fit_joint_distribution(modified, spec)
    assert changed["fitted_variables"] == fit["fitted_variables"]
    assert changed["latent_correlation"] == fit["latent_correlation"]
    assert changed["training_only_digest"] == fit["training_only_digest"]
    assert changed["observations_digest"] != fit["observations_digest"]
    np.testing.assert_allclose(fit["latent_correlation"], [[1.0, 1.0], [1.0, 1.0]], atol=1e-15)
    assert not fit["independent_trials_established"]


@pytest.mark.parametrize("two", [False, True])
def test_exact_empirical_energy_score_against_hand_calculation(two):
    data = observations([0.0, 0.0, 2.0, 2.0, 1.0], two=two)
    fit = fit_joint_distribution(data, request(data, small=True, family="normal"))
    design = distribution_design(fit, use("empirical_pairs"), data.context)
    score = assess_joint_distribution(
        fit, data, design, assessment(minimum_marginal_coverage=1.0, maximum_mean_energy_score=1.0)
    )
    assert score["status"] == "computed" and score["declared_gate"] == "passed"
    assert score["scores"]["full_holdout_mean_energy_score"] == pytest.approx(
        math.sqrt(2) / 2 if two else 0.5
    )
    assert score["scores"]["diagonal_predictive_pairs_included"]
    assert all(m["conditional_on_valid_coverage"] == 1 for m in score["marginal_intervals"].values())
    assert not score["field_qualified"] and not score["automatically_selected_or_applied"]


@pytest.mark.parametrize("mode", ["empirical_pairs", "normal_score_copula", "independent_counterfactual"])
def test_fitted_design_reuses_ensemble_and_preserves_source_condition_and_context(mode):
    data = observations(two=True)
    fit = fit_joint_distribution(data, request(data))
    record = distribution_design(fit, use(mode), data.context)
    assert record["status"] == "computed"
    design = record["design"]
    assert design["context"]["clock_id"] == data.context.clock_id
    assert design["context"]["metadata"]["distribution_fit"]["fit_digest"] == fit["record_digest"]
    if mode == "empirical_pairs":
        assert len(design["draws"]) == 40
        assert [r["values"] for r in design["draws"]] == [r.values for r in data.rows[:40]]
    else:
        assert len(design["draws"]) == 256
        if mode == "normal_score_copula":
            assert all(r["values"]["x"] == pytest.approx(r["values"]["y"]) for r in design["draws"])
    metric = EnsembleMetric(id="sum", unit="m", meaning="quantity", evidence_id="reference")

    def evaluate(values, context):
        return EnsembleValues(
            context=context,
            values={"sum": values["x"] + values["y"]},
            units={"sum": "m"},
            statuses={"sum": "computed"},
            native_payload={"definition": "original sum"},
        )

    result = evaluate_ensemble(
        design,
        (
            EnsembleNode(
                id="sum",
                version="1",
                source_digest="b" * 64,
                configuration={},
                metrics=(metric,),
                evaluate=evaluate,
            ),
        ),
    )
    assert result["status"] == "computed"
    assert result["context"]["metadata"]["distribution_fit"]["independence_is_counterfactual"] == (
        mode == "independent_counterfactual"
    )


def test_missing_training_row_is_not_deleted_or_conditionally_fit():
    data = observations()
    row = data.rows[1].model_copy(update={"values": {"x": None}, "valid": {"x": False}})
    data = data.model_copy(update={"rows": (data.rows[0], row, *data.rows[2:])})
    fit = fit_joint_distribution(data, request(data))
    assert fit["status"] == "needs_data" and fit["fitted_variables"] is None
    assert fit["training_diagnostics"]["unresolved_row_ids"] == [row.id]
    assert len(fit["training_rows"]) == 40
    assert distribution_design(fit, use(), data.context)["design"] is None


def test_missing_holdout_preserves_unknown_fraction_and_withholds_gate():
    data = observations(two=True)
    row = data.rows[-1].model_copy(update={"values": {"x": None, "y": 2.0}, "valid": {"x": False, "y": True}})
    data = data.model_copy(update={"rows": (*data.rows[:-1], row)})
    fit = fit_joint_distribution(data, request(data))
    score = assess_joint_distribution(
        fit,
        data,
        distribution_design(fit, use("empirical_pairs"), data.context),
        assessment(minimum_marginal_coverage=0.0, maximum_mean_energy_score=100.0),
    )
    assert score["status"] == "needs_data" and score["declared_gate"] == "needs_data"
    assert score["scores"]["full_holdout_mean_energy_score"] is None
    assert score["scores"]["unresolved_fraction"] == 1 / 16
    assert score["marginal_intervals"]["x"]["missing"] == 1
    assert score["marginal_intervals"]["y"]["missing"] == 0


def test_large_holdout_shift_fails_declared_gate_without_refitting_or_automatic_application():
    data = observations()
    data = data.model_copy(
        update={
            "rows": (
                *data.rows[:48],
                *(r.model_copy(update={"values": {"x": 100.0}}) for r in data.rows[48:]),
            )
        }
    )
    fit = fit_joint_distribution(data, request(data))
    score = assess_joint_distribution(
        fit,
        data,
        distribution_design(fit, use(), data.context),
        assessment(minimum_marginal_coverage=0.8, maximum_mean_energy_score=2.0),
    )
    assert score["status"] == "computed" and score["declared_gate"] == "failed"
    assert not score["automatically_selected_or_applied"]


def test_process_condition_transfer_and_foreign_asset_are_rejected():
    data = observations()
    fit = fit_joint_distribution(data, request(data))
    assert (
        distribution_design(fit, use().model_copy(update={"target_condition_id": "other"}), data.context)[
            "status"
        ]
        == "out_of_scope"
    )
    with pytest.raises(ValueError, match="another physical asset"):
        distribution_design(fit, use(), data.context.model_copy(update={"asset_id": "other"}))
    changed = data.context.evidence[0].model_copy(update={"citation": "changed evidence"})
    with pytest.raises(ValueError, match="evidence was changed"):
        distribution_design(fit, use(), data.context.model_copy(update={"evidence": (changed,)}))


def test_no_review_and_insufficient_rows_do_not_fit():
    data = observations()
    spec = request(data)
    for change in (
        {"definition_reviewed": False},
        {"minimum_training_rows": 50},
        {"minimum_validation_rows": 20},
    ):
        fit = fit_joint_distribution(data, spec.model_copy(update=change))
        assert fit["status"] == "needs_data" and "scipy" not in fit["runtime"]
    fit = fit_joint_distribution(data, spec)
    assert (
        distribution_design(fit, use().model_copy(update={"applicability_reviewed": False}), data.context)[
            "status"
        ]
        == "needs_data"
    )


@pytest.mark.parametrize(
    "change",
    [
        {"training_ids": ("missing", "row_1", "row_2")},
        {"training_ids": ("row_1", "row_0", "row_2")},
        {"minimum_order_gap": 10},
    ],
)
def test_split_cannot_silently_reorder_missing_or_close_rows(change):
    data = observations()
    with pytest.raises(ValueError):
        fit_joint_distribution(data, request(data).model_copy(update=change))


def test_group_disjoint_split_checks_actual_source_groups():
    data = observations()
    spec = request(data).model_copy(update={"split_policy": "group_disjoint"})
    assert fit_joint_distribution(data, spec)["status"] == "computed"
    data = data.model_copy(
        update={"rows": tuple(r.model_copy(update={"group_id": "same_run"}) for r in data.rows)}
    )
    with pytest.raises(ValueError, match="share a source group"):
        fit_joint_distribution(data, spec)


@pytest.mark.parametrize(
    "values,family,status",
    [([1.0] * 64, "normal", "needs_data"), ([-1.0, 1.0] * 32, "lognormal", "out_of_scope")],
)
def test_constant_channel_and_wrong_support_are_not_given_arbitrary_laws(values, family, status):
    data = observations(values)
    fit = fit_joint_distribution(data, request(data, family=family))
    assert fit["status"] == status and fit["fitted_variables"] is None


def test_training_conditions_and_validation_conditions_are_checked_separately():
    data = observations()
    mixed_train = data.model_copy(
        update={"rows": (data.rows[0].model_copy(update={"condition_id": "other"}), *data.rows[1:])}
    )
    assert fit_joint_distribution(mixed_train, request(data))["status"] == "out_of_scope"
    mixed_test = data.model_copy(
        update={"rows": (*data.rows[:-1], data.rows[-1].model_copy(update={"condition_id": "other"}))}
    )
    fit = fit_joint_distribution(mixed_test, request(mixed_test))
    assert fit["status"] == "computed"
    assert (
        assess_joint_distribution(
            fit, mixed_test, distribution_design(fit, use(), data.context), assessment()
        )["status"]
        == "out_of_scope"
    )


def test_score_budget_stale_data_and_seal_modification_are_not_hidden():
    data = observations()
    fit = fit_joint_distribution(data, request(data))
    prediction = distribution_design(fit, use(), data.context)
    assert (
        assess_joint_distribution(fit, data, prediction, assessment(maximum_distance_pairs=1))["status"]
        == "needs_data"
    )
    different = data.model_copy(update={"id": "different"})
    with pytest.raises(ValueError, match="must match"):
        assess_joint_distribution(fit, different, prediction, assessment())
    modified = copy.deepcopy(fit)
    modified["latent_correlation"][0][0] = 0.5
    with pytest.raises(ValueError, match="changed after sealing"):
        distribution_design(modified, use(), data.context)


def test_bad_units_invalid_order_nan_and_gate_definition_are_rejected():
    data = observations()
    bad = request(data).model_copy(
        update={"scales": {"x": ResidualScale(value=1.0, unit="s", source_id="reference")}}
    )
    with pytest.raises(ValueError, match="exact observed"):
        fit_joint_distribution(data, bad)
    with pytest.raises(ValidationError, match="strictly increasing"):
        JointObservations.model_validate(
            data.model_copy(update={"rows": tuple(reversed(data.rows))}).model_dump()
        )
    with pytest.raises(ValidationError):
        JointObservation(
            id="bad",
            order=0,
            group_id="g",
            condition_id="c",
            values={"x": math.nan},
            valid={"x": True},
            evidence_id="reference",
        )
    with pytest.raises(ValidationError, match="both coverage"):
        assessment(maximum_mean_energy_score=1.0)
    assert digest(fit_joint_distribution(data, request(data)))


def test_joint_energy_score_is_invariant_when_values_and_explicit_unit_scales_convert_together():
    original = observations(two=True)
    spec = request(original)
    fit = fit_joint_distribution(original, spec)
    a = assess_joint_distribution(
        fit, original, distribution_design(fit, use("empirical_pairs"), original.context), assessment()
    )
    converted = original.model_copy(
        update={
            "variables": tuple(v.model_copy(update={"unit": "cm"}) for v in original.variables),
            "rows": tuple(
                r.model_copy(update={"values": {k: x * 100 for k, x in r.values.items()}})
                for r in original.rows
            ),
            "source_digests": {"explicit_conversion_record": "b" * 64},
        }
    )
    converted_spec = spec.model_copy(
        update={
            "scales": {k: ResidualScale(value=100.0, unit="cm", source_id="reference") for k in spec.scales}
        }
    )
    converted_fit = fit_joint_distribution(converted, converted_spec)
    b = assess_joint_distribution(
        converted_fit,
        converted,
        distribution_design(converted_fit, use("empirical_pairs"), converted.context),
        assessment(),
    )
    assert a["scores"]["full_holdout_mean_energy_score"] == pytest.approx(
        b["scores"]["full_holdout_mean_energy_score"], abs=1e-14
    )


def test_all_holdout_missing_is_not_a_zero_energy_score_or_successful_gate():
    data = observations()
    data = data.model_copy(
        update={
            "rows": (
                *data.rows[:48],
                *(
                    r.model_copy(update={"values": {"x": None}, "valid": {"x": False}})
                    for r in data.rows[48:]
                ),
            )
        }
    )
    fit = fit_joint_distribution(data, request(data))
    report = assess_joint_distribution(
        fit,
        data,
        distribution_design(fit, use(), data.context),
        assessment(minimum_marginal_coverage=0.0, maximum_mean_energy_score=100.0),
    )
    assert report["status"] == "needs_data" and report["declared_gate"] == "needs_data"
    assert report["scores"]["conditional_on_complete_mean_energy_score"] is None
    assert report["scores"]["unresolved_fraction"] == 1.0
    assert report["marginal_intervals"]["x"]["full_holdout_coverage_bounds"] == [0.0, 1.0]


def test_nonrepresentable_training_scale_and_scoring_distance_are_withheld():
    data = observations([-1e308, 1e308] * 32)
    fit = fit_joint_distribution(data, request(data, family="normal"))
    assert fit["status"] == "out_of_scope" and fit["fitted_variables"] is None
    data = observations()
    spec = request(data).model_copy(
        update={"scales": {"x": ResidualScale(value=1e-300, unit="m", source_id="reference")}}
    )
    fit = fit_joint_distribution(data, spec)
    report = assess_joint_distribution(fit, data, distribution_design(fit, use(), data.context), assessment())
    assert report["status"] == "out_of_scope" and report["scores"] == {}
