"""Authored joint-metric regressions prepared for deferred HPC execution only."""

from copy import deepcopy

import numpy as np
import pytest

from pipeline_integrity.contracts import EvidenceRef, RunContext, digest
from pipeline_integrity.inverse import (
    FitParameter,
    ObservedChannel,
    OperatingPoint,
    OperatingPointFitRequest,
    OperatingPointObservations,
    OperatingPointPrediction,
    OperatingPointResidualMetric,
    ResidualRow,
    ResidualScale,
    _point_metric,
    fit_operating_points,
)


def case(*, scale=1000.0, cross=0.2, masked=False, reviewed=True, kind="covariance"):
    data_sha = digest("authored independent-axis point observations; no field validation")
    ids = ("a", "b", "c", "d")
    points = tuple(
        OperatingPoint(
            id=key,
            state={"x": float(i)},
            state_units={"x": "m"},
            group_id="same_authored_apparatus",
            evidence_ids=("data",),
            source_digests={"data": data_sha},
        )
        for i, key in enumerate(ids)
    )
    obs = OperatingPointObservations(
        id="authored_joint",
        asset_id="asset",
        origin="synthetic",
        source_digests={"data": data_sha},
        points=points,
        channels=(
            ObservedChannel(
                id="y",
                scale=ResidualScale(value=scale, unit="m", source_id="scale"),
                values=(1.0, None if masked else 2.0, 3.0, 4.0),
                valid=(True, not masked, True, True),
            ),
        ),
    )
    order = ("b", "a", "d", "c")
    variance = {"a": 1.0, "b": 4.0, "c": 9.0, "d": 16.0}

    def entry(left, right):
        if left == right:
            return variance[left]
        if {left, right} == {"a", "b"}:
            return 0.5
        if {left, right} == {"c", "d"}:
            return 0.0
        return cross

    matrix = tuple(tuple(entry(a, b) for b in order) for a in order)
    metric_sha = digest((order, matrix, kind))
    metric = OperatingPointResidualMetric(
        kind=kind,
        rows=tuple(ResidualRow(point_id=key, channel_id="y", unit="m") for key in order),
        matrix=matrix,
        source_sha256=metric_sha,
        evidence_id="metric",
        definition_id="authored_joint_residual_basis",
        applicability_reviewed=reviewed,
    )
    request = OperatingPointFitRequest(
        parameters=(FitParameter(id="offset", unit="m", lower=-10.0, upper=10.0, initial=0.0),),
        provider_id="authored_constant",
        provider_version="1",
        training_point_ids=("a", "b"),
        heldout_point_ids=("c", "d"),
        residual_metric=metric,
    )
    context = RunContext(
        asset_id="asset",
        scenario_id="authored_joint_fit",
        clock_id="execution_only",
        origin="synthetic",
        evidence=(
            EvidenceRef(id="data", citation="Authored observations", origin="synthetic", sha256=data_sha),
            EvidenceRef(
                id="metric",
                citation="Authored matrix, not a Gaussian law or independent validation",
                origin="synthetic",
                sha256=metric_sha,
            ),
            EvidenceRef(
                id="scale",
                citation="Authored legacy scalar metadata; not applied in joint mode",
                origin="synthetic",
            ),
        ),
    )
    return obs, request, context


def predict(parameters, points, context):
    return OperatingPointPrediction(
        status="computed",
        asset_id=context.asset_id,
        point_ids=tuple(p.id for p in points),
        point_digests={p.id: digest(p) for p in points},
        values={"y": (parameters["offset"],) * len(points)},
        units={"y": "m"},
        provider_source_digest=digest("authored constant provider"),
        evaluation_digest=digest((parameters, tuple(p.id for p in points))),
    )


def metric_for(obs, request, context):
    return _point_metric(obs, request, context, np.asarray([0, 1]), np.asarray([2, 3]))


def test_declared_matrix_row_order_and_analytic_quadratic_form():
    obs, request, context = case()
    whiten, info, missing = metric_for(obs, request, context)
    assert missing == []
    value = whiten(np.asarray([1.0, 2.0]), np.asarray([0, 1]))
    assert float(value @ value) == pytest.approx(1.6)
    assert [r["point_id"] for r in info["training_rows"]] == ["b", "a"]
    assert info["scalar_residual_scale_values_applied"] is False


def test_holdout_uses_marginal_block_despite_nonzero_cross_split_covariance():
    obs, request, context = case(cross=0.2)
    whiten, info, _ = metric_for(obs, request, context)
    held = whiten(np.asarray([3.0, 4.0]), np.asarray([2, 3]))
    assert float(held @ held) == pytest.approx(2.0)
    assert info["cross_split_block_known"] is True and info["cross_split_block_all_zero"] is False
    assert info["cross_split_independence_assumed"] is False
    assert "not conditioned" in info["heldout_block"] and info["distribution_assumed"] is None


def test_unknown_cross_block_never_becomes_independence_or_global_spd_claim():
    obs, request, context = case(cross=None)
    whiten, info, missing = metric_for(obs, request, context)
    assert not missing and whiten is not None
    assert info["selected_principal_blocks_spd_confirmed"] is True
    assert info["full_matrix_spd_confirmed"] is None
    assert info["cross_split_block_known"] is False and info["cross_split_block_all_zero"] is None


def test_valid_mask_selects_covariance_submatrix_without_conditioning_on_missing_row():
    obs, request, context = case(masked=True)
    whiten, info, _ = metric_for(obs, request, context)
    value = whiten(np.asarray([1.0]), np.asarray([0, 1]))
    assert float(value @ value) == pytest.approx(1.0)
    assert [r["point_id"] for r in info["training_rows"]] == ["a"]
    assert [r["point_id"] for r in info["masked_rows"]] == ["b"]


@pytest.mark.parametrize("defect", ["unreviewed", "matrix_unknown", "training_unknown", "source_unknown"])
def test_unknown_metric_withholds_before_forward_or_native_factor(defect, monkeypatch):
    obs, request, context = case(reviewed=defect != "unreviewed")
    metric = request.residual_metric
    if defect == "matrix_unknown":
        metric = metric.model_copy(update={"matrix": None})
    elif defect == "training_unknown":
        matrix = [list(row) for row in metric.matrix]
        matrix[0][0] = None
        metric = metric.model_copy(update={"matrix": tuple(tuple(row) for row in matrix)})
    elif defect == "source_unknown":
        context = context.model_copy(
            update={
                "evidence": tuple(
                    e.model_copy(update={"sha256": None}) if e.id == "metric" else e for e in context.evidence
                )
            }
        )
    request = request.model_copy(update={"residual_metric": metric})
    monkeypatch.setattr(
        "pipeline_integrity.inverse._scipy_least_squares",
        lambda: pytest.fail("unknown metric imported solver"),
    )
    result = fit_operating_points(
        obs, request, context, lambda *args: pytest.fail("unknown metric called forward provider")
    )
    assert result["status"] == "withheld" and result["fitted_parameters"] is None
    assert result["model_evaluations"] == 0 and result["evaluations"] == []


@pytest.mark.parametrize(
    "defect", ["units", "row_identity", "source", "duplicate_evidence", "nonsymmetric", "not_spd"]
)
def test_invalid_metric_contract_is_not_an_inferred_covariance(defect):
    obs, request, context = case()
    metric = request.residual_metric
    if defect == "units":
        metric = metric.model_copy(
            update={"rows": (metric.rows[0].model_copy(update={"unit": "s"}), *metric.rows[1:])}
        )
    elif defect == "row_identity":
        metric = metric.model_copy(
            update={"rows": (metric.rows[0].model_copy(update={"point_id": "unknown"}), *metric.rows[1:])}
        )
    elif defect == "source":
        metric = metric.model_copy(update={"source_sha256": "f" * 64})
    elif defect == "duplicate_evidence":
        context = context.model_copy(update={"evidence": (*context.evidence, context.evidence[1])})
    else:
        matrix = [list(row) for row in metric.matrix]
        matrix[0][1] = 5.0
        if defect == "not_spd":
            matrix[1][0] = 5.0
        metric = metric.model_copy(update={"matrix": tuple(tuple(row) for row in matrix)})
    request = request.model_copy(update={"residual_metric": metric})
    with pytest.raises(ValueError):
        fit_operating_points(
            obs, request, context, lambda *args: pytest.fail("invalid metric called forward provider")
        )


def test_joint_gls_does_not_apply_scalar_normalization_twice():
    obs, request, context = case(scale=1000.0)
    result = fit_operating_points(obs, request, context, predict)
    assert result["fitted_parameters"]["offset"] == pytest.approx(1.125, abs=1e-5)
    assert result["training"]["baseline"]["normalized_rmse"] == pytest.approx(np.sqrt(0.8))
    assert result["validation"]["baseline"]["normalized_rmse"] == pytest.approx(1.0)
    changed = obs.model_copy(
        update={
            "channels": (
                obs.channels[0].model_copy(
                    update={"scale": obs.channels[0].scale.model_copy(update={"value": 0.001})}
                ),
            )
        }
    )
    other = fit_operating_points(changed, request, context, predict)
    assert other["fitted_parameters"] == result["fitted_parameters"]
    assert (
        other["training"]["baseline"]["normalized_rmse"] == result["training"]["baseline"]["normalized_rmse"]
    )


def test_robust_loss_is_not_silently_applied_after_order_dependent_whitening():
    _, request, _ = case()
    with pytest.raises(ValueError, match="linear loss only"):
        OperatingPointFitRequest.model_validate(
            request.model_copy(update={"loss": "huber"}).model_dump(mode="python")
        )


def test_raw_budgets_precede_numeric_coercion_and_shape_allocation():
    _, request, _ = case()
    raw = request.residual_metric.model_dump(mode="python")
    raw["maximum_rows"] = 2
    raw["matrix"] = tuple(tuple("not numeric" for _ in range(4)) for _ in range(4))
    with pytest.raises(ValueError, match="raw row/cell budget"):
        OperatingPointResidualMetric.model_validate(raw)


def test_extreme_valid_scales_preserve_tiny_representable_correlation():
    obs, request, context = case()
    rows = tuple(ResidualRow(point_id=key, channel_id="y", unit="m") for key in ("a", "b", "c", "d"))
    matrix = (
        (1e308, 1e-200, 0.0, 0.0),
        (1e-200, 1e-308, 0.0, 0.0),
        (0.0, 0.0, 9.0, 0.0),
        (0.0, 0.0, 0.0, 16.0),
    )
    metric = request.residual_metric.model_copy(update={"rows": rows, "matrix": matrix})
    request = request.model_copy(update={"residual_metric": metric})
    whiten, _, missing = metric_for(obs, request, context)
    assert not missing
    value = whiten(np.asarray([1e154, 0.0]), np.asarray([0, 1]))
    assert value[1] == pytest.approx(-1e-200, rel=1e-12, abs=0)


def test_default_scalar_path_and_deterministic_joint_scale_keep_meaning_separate():
    obs, request, context = case(kind="residual_scale_matrix")
    result = fit_operating_points(obs, request, context, predict)
    assert result["residual_metric"]["kind"] == "residual_scale_matrix"
    assert result["residual_metric"]["distribution_assumed"] is None
    assert (
        result["field_validation_claim"] is False
        and result["parameter_confidence_intervals_calculated"] is False
    )
    scalar_obs = obs.model_copy(
        update={
            "channels": (
                obs.channels[0].model_copy(
                    update={"scale": obs.channels[0].scale.model_copy(update={"value": 1.0})}
                ),
            )
        }
    )
    scalar = fit_operating_points(
        scalar_obs, request.model_copy(update={"residual_metric": None}), context, predict
    )
    assert "residual_metric" not in scalar
    assert scalar["fitted_parameters"]["offset"] == pytest.approx(1.5, abs=1e-5)


def test_mask_and_row_order_are_retained_in_request_digest():
    obs, request, context = case(masked=True)
    result = fit_operating_points(obs, request, context, predict)
    changed = deepcopy(result)
    changed["residual_metric"]["masked_rows"] = []
    assert digest({k: v for k, v in changed.items() if k != "result_digest"}) != result["result_digest"]
