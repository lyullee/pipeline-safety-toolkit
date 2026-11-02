"""Training-only marginal/copula fits and declared ordered/group holdout diagnostics.

Source row order is not elapsed time. A fitted distribution and a successful
numerical score do not establish stationarity, independent trials or field use.
"""

from __future__ import annotations

import json
import math
from importlib.metadata import version
from pathlib import Path
from typing import Literal

import numpy as np
from pydantic import Field, model_validator

from .contracts import Contract, Fraction, Name, Positive, RunContext, digest
from .datasets import file_digest
from .inverse import ResidualScale
from .uncertainty import (
    EnsembleRequest,
    JointCase,
    Marginal,
    UncertainVariable,
    generate_ensemble,
)


class JointObservation(Contract):
    id: Name
    order: int = Field(ge=0)
    group_id: Name
    condition_id: Name
    values: dict[str, float | None]
    valid: dict[str, bool]
    evidence_id: Name

    @model_validator(mode="after")
    def quality(self):
        if not self.values or set(self.values) != set(self.valid):
            raise ValueError("paired values and explicit validity flags must align")
        if any(ok and self.values[k] is None for k, ok in self.valid.items()):
            raise ValueError("a missing observation cannot be valid")
        return self


class JointObservations(Contract):
    id: Name
    variables: tuple[UncertainVariable, ...] = Field(min_length=1, max_length=16)
    rows: tuple[JointObservation, ...] = Field(min_length=2, max_length=4096)
    order_basis: Literal["acquisition_order", "source_row_order", "user_declared_order"]
    source_digests: dict[str, str]
    context: RunContext

    @model_validator(mode="after")
    def alignment(self):
        ids = {v.id for v in self.variables}
        evidence = {e.id for e in self.context.evidence}
        if len(ids) != len(self.variables) or any(v.marginal is not None for v in self.variables):
            raise ValueError("observed variables need unique IDs without pre-fitted marginal laws")
        if any(set(r.values) != ids for r in self.rows):
            raise ValueError("each original row must retain all variables, including missing values")
        if len({r.id for r in self.rows}) != len(self.rows) or any(
            b.order <= a.order for a, b in zip(self.rows, self.rows[1:], strict=False)
        ):
            raise ValueError("source rows must have unique IDs and strictly increasing order; no sorting")
        if not ({v.evidence_id for v in self.variables} | {r.evidence_id for r in self.rows}) <= evidence:
            raise ValueError("observation and variable evidence must exist in the context")
        if not self.source_digests or any(
            not key.strip() or len(value) != 64 or any(c not in "0123456789abcdef" for c in value)
            for key, value in self.source_digests.items()
        ):
            raise ValueError("observations require named SHA-256 source bindings")
        return self


class DistributionFitRequest(Contract):
    id: Name
    condition_id: Name
    training_ids: tuple[Name, ...] = Field(min_length=3)
    validation_ids: tuple[Name, ...] = Field(min_length=1)
    families: dict[str, Literal["normal", "lognormal"]]
    scales: dict[str, ResidualScale]
    split_policy: Literal["group_disjoint", "ordered_gap"]
    minimum_order_gap: int = Field(ge=1)
    minimum_training_rows: int = Field(default=30, ge=3)
    minimum_validation_rows: int = Field(default=10, ge=1)
    definition_reviewed: bool = False
    evidence_id: Name

    @model_validator(mode="after")
    def split(self):
        train, test = set(self.training_ids), set(self.validation_ids)
        if len(train) != len(self.training_ids) or len(test) != len(self.validation_ids) or train & test:
            raise ValueError("training and validation row identifiers must be unique and disjoint")
        return self


class DistributionUse(Contract):
    mode: Literal["empirical_pairs", "normal_score_copula", "independent_counterfactual"]
    method: Literal["monte_carlo", "latin_hypercube", "sobol"] = "sobol"
    samples: int = Field(default=1024, ge=1, le=65536)
    seed: int = Field(default=145, ge=0, le=2**63 - 1)
    interpretation: Literal["scenario", "epistemic", "aleatory"]
    target_condition_id: Name
    applicability_reviewed: bool = False
    evidence_id: Name


class DistributionAssessment(Contract):
    lower_quantile: Fraction = 0.05
    upper_quantile: Fraction = 0.95
    minimum_marginal_coverage: Fraction | None = None
    maximum_mean_energy_score: Positive | None = None
    maximum_distance_pairs: int = Field(default=4000000, ge=1, le=50000000)
    evidence_id: Name

    @model_validator(mode="after")
    def intervals(self):
        if not self.lower_quantile < self.upper_quantile:
            raise ValueError("prediction interval quantiles must increase")
        if (self.minimum_marginal_coverage is None) != (self.maximum_mean_energy_score is None):
            raise ValueError("a declared diagnostic gate needs both coverage and energy-score criteria")
        return self


def _sealed(body):
    return {**body, "record_digest": digest(body)}


def _checked(record):
    if record.get("record_digest") != digest({k: v for k, v in record.items() if k != "record_digest"}):
        raise ValueError("distribution record changed after sealing")


def _selected(observations, request):
    by_id = {r.id: r for r in observations.rows}
    if (set(request.training_ids) | set(request.validation_ids)) - set(by_id):
        raise ValueError("split refers to absent original observation rows")
    train = tuple(by_id[k] for k in request.training_ids)
    test = tuple(by_id[k] for k in request.validation_ids)
    if any(b.order <= a.order for group in (train, test) for a, b in zip(group, group[1:], strict=False)):
        raise ValueError("selected rows must retain original order")
    if test[0].order - train[-1].order < request.minimum_order_gap:
        raise ValueError("validation must follow the training horizon and the declared order gap")
    if request.split_policy == "group_disjoint" and {r.group_id for r in train} & {r.group_id for r in test}:
        raise ValueError("group-disjoint validation cannot share a source group with training")
    return train, test


def fit_joint_distribution(observations: JointObservations, request: DistributionFitRequest):
    """Fit normal/lognormal marginals and normal-score rank correlation on training rows only."""
    observations = JointObservations.model_validate_json(observations.model_dump_json())
    request = DistributionFitRequest.model_validate_json(request.model_dump_json())
    train, test = _selected(observations, request)
    ids = [v.id for v in observations.variables]
    evidence = {e.id for e in observations.context.evidence}
    if set(request.families) != set(ids) or set(request.scales) != set(ids):
        raise ValueError("each variable needs its predeclared family and scoring scale")
    if request.evidence_id not in evidence or {s.source_id for s in request.scales.values()} - evidence:
        raise ValueError("fit definition and scoring scales require context evidence")
    if any(request.scales[v.id].unit != v.unit for v in observations.variables):
        raise ValueError("scoring scales must have the exact observed variable units")
    receipt = dict(
        schema="pipeline_integrity.distribution_fit.v1",
        request=request.model_dump(mode="json"),
        context=observations.context.model_dump(mode="json"),
        observations_digest=digest(observations),
        source_digests=observations.source_digests,
        order_basis=observations.order_basis,
        variables=[v.model_dump(mode="json") for v in observations.variables],
        training_rows=[r.model_dump(mode="json") for r in train],
        training_only_digest=digest(
            dict(variables=observations.variables, rows=train, condition_id=request.condition_id)
        ),
        validation_row_ids=list(request.validation_ids),
        ignored_row_ids=[
            r.id
            for r in observations.rows
            if r.id not in set(request.training_ids) | set(request.validation_ids)
        ],
        status="computed",
        issues=[],
        fitted_variables=None,
        latent_correlation=None,
        training_diagnostics={},
        runtime={"numpy": np.__version__},
        field_distribution_validated=False,
        independent_trials_established=False,
        row_order_converted_to_elapsed_time=False,
        implementation_sha256=file_digest(Path(__file__)),
    )
    incomplete = [r.id for r in train if any(not r.valid[k] or r.values[k] is None for k in ids)]
    receipt["training_diagnostics"] = dict(
        requested_rows=len(train),
        complete_rows=len(train) - len(incomplete),
        unresolved_row_ids=incomplete,
        validation_rows=len(test),
        overlapping_source_groups=sorted({r.group_id for r in train} & {r.group_id for r in test}),
    )
    if not request.definition_reviewed:
        receipt.update(
            status="needs_data",
            issues=["fit definition, source conditions, split and scoring scales need review"],
        )
    elif any(r.condition_id != request.condition_id for r in train):
        receipt.update(
            status="out_of_scope", issues=["training rows mix or differ from the declared process condition"]
        )
    elif len(train) < request.minimum_training_rows or len(test) < request.minimum_validation_rows:
        receipt.update(status="needs_data", issues=["the predeclared minimum split sizes are not met"])
    elif incomplete:
        receipt.update(
            status="needs_data",
            issues=["incomplete paired training rows; no deletion, imputation or conditional fit"],
        )
    if receipt["status"] != "computed":
        return _sealed(receipt)
    from scipy import stats

    receipt["runtime"]["scipy"] = version("scipy")
    matrix = np.asarray([[r.values[k] for k in ids] for r in train], dtype=float)
    fitted, normal_scores = [], []
    for j, variable in enumerate(observations.variables):
        x = matrix[:, j]
        if np.all(x == x[0]):
            receipt.update(
                status="needs_data",
                issues=[
                    f"{variable.id}: constant training channel cannot identify a continuous marginal/correlation"
                ],
            )
            return _sealed(receipt)
        family = request.families[variable.id]
        if family == "lognormal":
            if (x <= 0).any():
                receipt.update(
                    status="out_of_scope",
                    issues=[
                        f"{variable.id}: nonpositive observation is outside the predeclared lognormal support"
                    ],
                )
                return _sealed(receipt)
            with np.errstate(over="ignore", invalid="ignore", under="ignore"):
                shape, location, scale = stats.lognorm.fit(x, floc=0.0)
            if location != 0:
                raise ValueError("the lognormal fitting engine changed the fixed zero location")
            parameters = dict(log_mean=math.log(float(scale)), log_standard_deviation=float(shape))
        else:
            with np.errstate(over="ignore", invalid="ignore", under="ignore"):
                mean, sd = stats.norm.fit(x)
            parameters = dict(mean=float(mean), standard_deviation=float(sd))
        if not all(math.isfinite(v) for v in parameters.values()):
            receipt.update(
                status="out_of_scope", issues=[f"{variable.id}: fitted parameters exceeded finite arithmetic"]
            )
            return _sealed(receipt)
        positive_scale = parameters.get("standard_deviation", parameters.get("log_standard_deviation"))
        if positive_scale <= 0:
            receipt.update(status="needs_data", issues=[f"{variable.id}: marginal scale is not identified"])
            return _sealed(receipt)
        fitted.append(
            variable.model_copy(update={"marginal": Marginal(family=family, parameters=parameters)})
        )
        rank = stats.rankdata(x, method="average", nan_policy="raise")
        normal_scores.append(stats.norm.ppf((rank - 0.5) / len(x)))
        receipt["training_diagnostics"][variable.id] = dict(
            family=family,
            training_min=float(x.min()),
            training_max=float(x.max()),
            unique_values=int(len(np.unique(x))),
            ties=int(len(x) - len(np.unique(x))),
            scale_fitted_by="SciPy maximum likelihood",
            rank_plotting_positions="(average_rank - 0.5) / training_count",
        )
    correlation = np.ones((1, 1)) if len(ids) == 1 else np.corrcoef(normal_scores)
    # NumPy can differ by a final bit across symmetric entries. Report this
    # averaging explicitly, without changing eigenvalues or fitting a new matrix.
    asymmetry = float(np.max(np.abs(correlation - correlation.T)))
    correlation = (correlation + correlation.T) / 2
    np.fill_diagonal(correlation, 1.0)
    if not np.isfinite(correlation).all() or (np.abs(correlation) > 1).any():
        receipt.update(
            status="out_of_scope",
            issues=["normal-score correlation could not be represented as a valid matrix"],
        )
        return _sealed(receipt)
    checked = EnsembleRequest(
        id="fitted_matrix_check",
        variables=tuple(fitted),
        method="sobol",
        samples=2,
        seed=0,
        interpretation="scenario",
        dependence="gaussian_copula",
        latent_correlation=tuple(tuple(float(x) for x in row) for row in correlation),
        evidence_id=request.evidence_id,
    )
    receipt.update(
        fitted_variables=[v.model_dump(mode="json") for v in fitted],
        latent_correlation=[list(row) for row in checked.latent_correlation],
        correlation_estimator="Pearson correlation of finite training-only normal rank scores",
        pre_symmetrization_max_asymmetry=asymmetry,
        marginal_family_selected_from_validation=False,
    )
    return _sealed(receipt)


def distribution_design(fit: dict, use: DistributionUse, context: RunContext):
    """Materialize the training fit through the existing ensemble engine, with explicit applicability review."""
    _checked(fit)
    use = DistributionUse.model_validate_json(use.model_dump_json())
    context = RunContext.model_validate_json(context.model_dump_json())
    if context.asset_id != fit["context"]["asset_id"]:
        raise ValueError("a distribution fit cannot silently transfer to another physical asset")
    evidence = {e.id for e in context.evidence}
    if (
        use.evidence_id not in evidence
        or {v["evidence_id"] for v in fit["variables"]} - evidence
        or {r["evidence_id"] for r in fit["training_rows"]} - evidence
    ):
        raise ValueError("distribution use must retain training and applicability evidence")
    original_evidence = {e["id"]: e for e in fit["context"]["evidence"]}
    current_evidence = {e.id: e.model_dump(mode="json") for e in context.evidence}
    required = {
        fit["request"]["evidence_id"],
        *(v["evidence_id"] for v in fit["variables"]),
        *(r["evidence_id"] for r in fit["training_rows"]),
    }
    if any(current_evidence.get(key) != original_evidence[key] for key in required):
        raise ValueError("training evidence was changed or removed in the target context")
    record = dict(
        schema="pipeline_integrity.distribution_use.v1",
        fit_digest=fit["record_digest"],
        use=use.model_dump(mode="json"),
        context=context.model_dump(mode="json"),
        status=fit["status"],
        issues=list(fit["issues"]),
        design=None,
        field_distribution_validated=False,
        implementation_sha256=file_digest(Path(__file__)),
    )
    if fit["status"] != "computed":
        return _sealed(record)
    if use.target_condition_id != fit["request"]["condition_id"]:
        record.update(
            status="out_of_scope",
            issues=["target process condition differs from training; no automatic transfer"],
        )
        return _sealed(record)
    if not use.applicability_reviewed:
        record.update(
            status="needs_data",
            issues=["distribution applicability and interpretation require explicit review"],
        )
        return _sealed(record)
    empirical = use.mode == "empirical_pairs"
    variables = tuple(
        UncertainVariable.model_validate_json(json.dumps(v))
        for v in fit["variables" if empirical else "fitted_variables"]
    )
    cases = (
        tuple(
            JointCase(
                id=r["id"],
                weight=1.0 / len(fit["training_rows"]),
                values=r["values"],
                evidence_id=r["evidence_id"],
            )
            for r in fit["training_rows"]
        )
        if empirical
        else ()
    )
    latent = (
        None
        if empirical or use.mode == "independent_counterfactual"
        else tuple(tuple(row) for row in fit["latent_correlation"])
    )
    request = EnsembleRequest(
        id=f"{fit['request']['id']}:{use.mode}",
        variables=variables,
        method="joint_cases" if empirical else use.method,
        interpretation=use.interpretation,
        dependence="paired_rows"
        if empirical
        else "independent"
        if use.mode == "independent_counterfactual"
        else "gaussian_copula",
        evidence_id=use.evidence_id,
        definition_reviewed=True,
        samples=None if empirical else use.samples,
        seed=None if empirical else use.seed,
        cases=cases,
        latent_correlation=latent,
    )
    if "distribution_fit" in context.metadata:
        raise ValueError("distribution-fit metadata is reserved for this binding")
    bound_context = context.model_copy(
        update={
            "metadata": context.metadata
            | {
                "distribution_fit": dict(
                    fit_digest=fit["record_digest"],
                    training_only_digest=fit["training_only_digest"],
                    condition_id=use.target_condition_id,
                    mode=use.mode,
                    independence_is_counterfactual=use.mode == "independent_counterfactual",
                )
            }
        }
    )
    design = generate_ensemble(request, bound_context)
    record.update(status=design["status"], issues=design["issues"], design=design)
    return _sealed(record)


def assess_joint_distribution(
    fit: dict, observations: JointObservations, use_record: dict, specification: DistributionAssessment
):
    """Score finite predictive distributions on the untouched declared holdout.

    Energy score uses Euclidean distances after caller-declared unit scaling.
    Diagonal pairs are included because the supplied design is a finite weighted
    predictive distribution, not an unbiased independent ensemble estimator.
    """
    _checked(fit)
    _checked(use_record)
    observations = JointObservations.model_validate_json(observations.model_dump_json())
    spec = DistributionAssessment.model_validate_json(specification.model_dump_json())
    if fit["observations_digest"] != digest(observations) or use_record["fit_digest"] != fit["record_digest"]:
        raise ValueError("fit, predictive distribution and original evaluation data must match")
    if spec.evidence_id not in {e.id for e in observations.context.evidence}:
        raise ValueError("diagnostic gate and interval definition need evidence")
    request = DistributionFitRequest.model_validate_json(json.dumps(fit["request"]))
    _, test = _selected(observations, request)
    record = dict(
        schema="pipeline_integrity.distribution_assessment.v1",
        fit_digest=fit["record_digest"],
        use_digest=use_record["record_digest"],
        specification=spec.model_dump(mode="json"),
        status=use_record["status"],
        issues=list(use_record["issues"]),
        rows=[],
        marginal_intervals={},
        scores={},
        declared_gate="not_evaluated",
        independently_validated=False,
        independent_trial_confidence_interval_computed=False,
        field_qualified=False,
        automatically_selected_or_applied=False,
        implementation_sha256=file_digest(Path(__file__)),
    )
    if use_record["status"] != "computed":
        return _sealed(record)
    if any(r.condition_id != request.condition_id for r in test):
        record.update(
            status="out_of_scope", issues=["validation rows differ from the fitted process condition"]
        )
        return _sealed(record)
    design = use_record["design"]
    _checked(design)
    ids = [v.id for v in observations.variables]
    draws = [r for r in design["draws"] if r["weight"] > 0]
    if not draws or any(any(r["values"].get(k) is None for k in ids) for r in draws):
        record.update(
            status="needs_data",
            issues=["predictive joint distribution is incomplete; no conditional renormalization"],
        )
        return _sealed(record)
    required_pairs = len(draws) ** 2 + len(draws) * len(test)
    record["distance_pairs"] = required_pairs
    if required_pairs > spec.maximum_distance_pairs:
        record.update(status="needs_data", issues=["declared exact-distance budget exceeded; no subsampling"])
        return _sealed(record)
    from scipy.spatial.distance import cdist

    matrix = np.array([[r["values"][k] for k in ids] for r in draws])
    weights = np.array([r["weight"] for r in draws])
    total = math.fsum(weights)
    if not math.isclose(total, 1.0, abs_tol=1e-12, rel_tol=0):
        raise ValueError("predictive weights must retain their complete unit mass")
    normalized_weights = weights / total
    scales = np.array([request.scales[k].value for k in ids])
    with np.errstate(over="ignore", invalid="ignore"):
        standardized = matrix / scales
        pair_distance = cdist(standardized, standardized)
    if not np.isfinite(pair_distance).all():
        record.update(
            status="out_of_scope", issues=["scaled predictive distances exceeded finite arithmetic"]
        )
        return _sealed(record)
    pair_term = float(normalized_weights @ pair_distance @ normalized_weights / 2)
    coverage_counts = {}
    for j, key in enumerate(ids):
        interval = np.quantile(
            matrix[:, j], (spec.lower_quantile, spec.upper_quantile), weights=weights, method="inverted_cdf"
        ).tolist()
        valid = [r.values[key] for r in test if r.valid[key] and r.values[key] is not None]
        covered = sum(interval[0] <= x <= interval[1] for x in valid)
        coverage_counts[key] = dict(known=len(valid), missing=len(test) - len(valid), covered=covered)
        record["marginal_intervals"][key] = dict(
            unit=next(v.unit for v in observations.variables if v.id == key),
            interval=interval,
            nominal_mass=spec.upper_quantile - spec.lower_quantile,
            conditional_on_valid_coverage=None if not valid else covered / len(valid),
            full_holdout_coverage_bounds=[
                covered / len(test),
                (covered + len(test) - len(valid)) / len(test),
            ],
            **coverage_counts[key],
        )
    for row in test:
        complete = all(row.valid[k] and row.values[k] is not None for k in ids)
        score = None
        status = "needs_data"
        if complete:
            with np.errstate(over="ignore", invalid="ignore"):
                target = np.array([[row.values[k] / scales[j] for j, k in enumerate(ids)]])
                distance = cdist(standardized, target)[:, 0]
                score = float(normalized_weights @ distance - pair_term)
            status = "computed" if math.isfinite(score) and score >= -1e-12 else "out_of_scope"
            if status != "computed":
                score = None
        record["rows"].append(
            dict(
                id=row.id,
                order=row.order,
                group_id=row.group_id,
                status=status,
                energy_score=score,
                missing_variables=[k for k in ids if not row.valid[k] or row.values[k] is None],
            )
        )
    known_scores = [r["energy_score"] for r in record["rows"] if r["status"] == "computed"]
    count = len(known_scores)
    conditional = math.fsum(score / count for score in known_scores) if count else None
    record["scores"] = dict(
        known_rows=count,
        unresolved_rows=len(test) - count,
        known_fraction=count / len(test),
        unresolved_fraction=1 - count / len(test),
        conditional_on_complete_mean_energy_score=conditional,
        full_holdout_mean_energy_score=conditional if count == len(test) else None,
        predictive_pair_distance_half_term=pair_term,
        score_space="caller_scaled_joint_variables",
        score_weight_normalization_divisor=total,
        diagonal_predictive_pairs_included=True,
        energy_score_roundoff_tolerance=1e-12,
        negative_roundoff_scores_retained=sum(-1e-12 <= score < 0 for score in known_scores),
    )
    if count != len(test):
        record["status"] = (
            "out_of_scope" if any(r["status"] == "out_of_scope" for r in record["rows"]) else "needs_data"
        )
        record["issues"].append("holdout has incomplete or unrepresentable joint scores; full mean withheld")
    if spec.maximum_mean_energy_score is not None:
        if count != len(test) or any(c["missing"] for c in coverage_counts.values()):
            record["declared_gate"] = "needs_data"
        else:
            accepted = conditional <= spec.maximum_mean_energy_score and all(
                c["covered"] / c["known"] >= spec.minimum_marginal_coverage for c in coverage_counts.values()
            )
            record["declared_gate"] = "passed" if accepted else "failed"
    record["runtime"] = {"numpy": np.__version__, "scipy": version("scipy")}
    return _sealed(record)
