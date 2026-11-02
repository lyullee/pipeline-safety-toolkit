"""Shared uncertainty draws, explicit dependence and unreduced unresolved weight.

Draw identifiers are ensemble coordinates, never observation times. Numerical
quadrature weights do not supply field occurrence rates or independent trials.
"""

from __future__ import annotations

import copy
import json
import math
import platform
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from typing import Callable, Literal

import numpy as np
from pydantic import Field, ValidationError, model_validator

from .contracts import Contract, Fraction, Name, RunContext, digest, json_value
from .datasets import file_digest

Status = Literal["computed", "needs_data", "out_of_scope"]


class Marginal(Contract):
    family: Literal["constant", "normal", "lognormal", "uniform", "triangular", "beta"]
    parameters: dict[str, float]

    @model_validator(mode="after")
    def valid_parameters(self):
        p = self.parameters
        keys = {
            "constant": {"value"},
            "normal": {"mean", "standard_deviation"},
            "lognormal": {"log_mean", "log_standard_deviation"},
            "uniform": {"lower", "upper"},
            "triangular": {"lower", "mode", "upper"},
            "beta": {"alpha", "beta", "lower", "upper"},
        }
        if set(p) != keys[self.family] or any(not math.isfinite(v) for v in p.values()):
            raise ValueError("exact finite distribution parameter keys are required")
        if self.family in ("normal", "lognormal"):
            key = "standard_deviation" if self.family == "normal" else "log_standard_deviation"
            if p[key] <= 0:
                raise ValueError("standard deviation must be positive")
        if "lower" in p and not (p["upper"] > p["lower"] and math.isfinite(p["upper"] - p["lower"])):
            raise ValueError("a finite positive support width is required")
        if self.family == "triangular" and not p["lower"] <= p["mode"] <= p["upper"]:
            raise ValueError("triangular mode must be within support")
        if self.family == "beta" and (p["alpha"] <= 0 or p["beta"] <= 0):
            raise ValueError("beta shape parameters must be positive")
        return self


class UncertainVariable(Contract):
    id: Name
    unit: Name
    layer: Literal["measurement", "input", "model"]
    evidence_id: Name
    marginal: Marginal | None = None


class JointCase(Contract):
    id: Name
    weight: Fraction
    values: dict[str, float | None]
    evidence_id: Name


class EnsembleRequest(Contract):
    id: Name
    variables: tuple[UncertainVariable, ...] = Field(min_length=1, max_length=64)
    method: Literal["joint_cases", "monte_carlo", "latin_hypercube", "sobol"]
    interpretation: Literal["scenario", "epistemic", "aleatory"]
    dependence: Literal["paired_rows", "independent", "gaussian_copula"]
    evidence_id: Name
    definition_reviewed: bool = False
    samples: int | None = Field(default=None, ge=1, le=65536)
    seed: int | None = Field(default=None, ge=0, le=2**63 - 1)
    cases: tuple[JointCase, ...] = Field(default=(), max_length=65536)
    latent_correlation: tuple[tuple[float, ...], ...] | None = None
    matrix_roundoff_tolerance: float = Field(default=1e-12, gt=0, le=1e-10)

    @model_validator(mode="after")
    def coherent(self):
        ids = [v.id for v in self.variables]
        if len(set(ids)) != len(ids):
            raise ValueError("variable identifiers must be unique")
        if self.method == "joint_cases":
            if self.dependence != "paired_rows" or self.samples is not None or self.seed is not None:
                raise ValueError("joint cases retain paired rows without a random sampling seed")
            if not self.cases or len({r.id for r in self.cases}) != len(self.cases):
                raise ValueError("nonempty unique joint cases are required")
            if any(v.marginal is not None for v in self.variables):
                raise ValueError("joint cases supply values rather than marginal distributions")
            if any(set(r.values) != set(ids) for r in self.cases):
                raise ValueError("joint cases must identify every variable, including missing values")
            if not math.isclose(math.fsum(r.weight for r in self.cases), 1.0, rel_tol=0, abs_tol=1e-12):
                raise ValueError("declared joint weights must sum to one; no automatic normalization")
        elif self.dependence == "paired_rows" or self.samples is None or self.seed is None or self.cases:
            raise ValueError("sampling requires a sample count, seed and explicit distribution dependence")
        if self.method == "sobol" and self.samples is not None and self.samples & (self.samples - 1):
            raise ValueError("Sobol balance requires a power-of-two sample count")
        if self.dependence == "gaussian_copula":
            if any(v.marginal and v.marginal.family == "constant" for v in self.variables):
                raise ValueError("constant inputs belong in model configuration, not a copula matrix")
            matrix, _ = _correlation_root(self.latent_correlation, len(ids), self.matrix_roundoff_tolerance)
            if matrix.shape != (len(ids), len(ids)):
                raise ValueError("correlation dimensions must follow variable order")
        elif self.latent_correlation is not None:
            raise ValueError("latent correlation belongs to the Gaussian copula definition")
        return self


def _correlation_root(values, count, tolerance):
    matrix = np.asarray(values, dtype=float)
    if matrix.shape != (count, count) or not np.isfinite(matrix).all():
        raise ValueError("a finite square latent correlation matrix is required")
    if not np.array_equal(matrix, matrix.T) or not np.array_equal(np.diag(matrix), np.ones(count)):
        raise ValueError("correlation must be symmetric with an exact unit diagonal")
    if (np.abs(matrix) > 1).any():
        raise ValueError("correlations must be between minus one and one")
    eigenvalues, vectors = np.linalg.eigh(matrix)
    if eigenvalues.min() < -tolerance:
        raise ValueError("correlation matrix is not positive semidefinite; no matrix repair is performed")
    # Only roundoff-sized negative eigenvalues are floored and explicitly reported.
    root = (vectors * np.sqrt(np.maximum(eigenvalues, 0))) @ vectors.T
    diagnostics = {
        "minimum_eigenvalue": float(eigenvalues.min()),
        "negative_roundoff_eigenvalues_floored": int((eigenvalues < 0).sum()),
        "declared_tolerance": tolerance,
        "root_reconstruction_max_absolute_error": float(np.max(np.abs(root @ root.T - matrix))),
        "root": root.tolist(),
    }
    return matrix, diagnostics


def _seal(record):
    record["record_digest"] = digest(record)
    return record


def _verify(record):
    if record.get("record_digest") != digest({k: v for k, v in record.items() if k != "record_digest"}):
        raise ValueError("ensemble record was modified after sealing")


def generate_ensemble(request: EnsembleRequest, context: RunContext):
    """Generate finite paired draws or preserve supplied joint rows without fitting distributions."""
    request = EnsembleRequest.model_validate_json(request.model_dump_json())
    context = RunContext.model_validate_json(context.model_dump_json())
    evidence = {e.id for e in context.evidence}
    required = {
        request.evidence_id,
        *(v.evidence_id for v in request.variables),
        *(c.evidence_id for c in request.cases),
    }
    if not required <= evidence:
        raise ValueError("every ensemble definition, variable and row must retain context evidence")
    record = {
        "schema": "pipeline_integrity.ensemble_design.v1",
        "request": request.model_dump(mode="json"),
        "context": context.model_dump(mode="json"),
        "status": "computed",
        "issues": [],
        "draws": [],
        "runtime": {"python": platform.python_version(), "numpy": np.__version__},
        "draw_index_is_time": False,
        "field_distribution_validated": False,
    }
    if not request.definition_reviewed:
        record.update(status="needs_data", issues=["ensemble definition and interpretation require review"])
        return _seal(record)
    if request.method != "joint_cases" and any(v.marginal is None for v in request.variables):
        record.update(status="needs_data", issues=["unresolved marginal distribution; no sampling performed"])
        return _seal(record)
    if request.method == "joint_cases":
        record["draws"] = [c.model_dump(mode="json") for c in request.cases]
    else:
        from scipy import stats
        from scipy.stats import qmc

        record["runtime"]["scipy"] = version("scipy")
        count, dimension = request.samples, len(request.variables)
        rng = np.random.default_rng(request.seed)
        record["runtime"]["bit_generator"] = type(rng.bit_generator).__name__
        if request.method == "sobol":
            uniform = qmc.Sobol(dimension, scramble=True, rng=rng).random_base2(count.bit_length() - 1)
        elif request.method == "latin_hypercube":
            uniform = qmc.LatinHypercube(dimension, scramble=True, rng=rng).random(count)
        else:
            uniform = rng.random((count, dimension))
        if request.dependence == "gaussian_copula":
            _, diagnostics = _correlation_root(
                request.latent_correlation, dimension, request.matrix_roundoff_tolerance
            )
            record["copula"] = diagnostics | {
                "correlation_space": "latent_standard_normal",
                "physical_Pearson_correlation_implied": False,
                "LHS_marginal_stratification_after_copula_guaranteed": False,
            }
            if (uniform <= 0).any() or (uniform >= 1).any():
                record.update(
                    status="out_of_scope",
                    issues=["normal transform endpoint encountered; no clipping or redraw"],
                )
                return _seal(record)
            uniform = stats.norm.cdf(stats.norm.ppf(uniform) @ np.asarray(diagnostics["root"]).T)
        columns = []
        for index, variable in enumerate(request.variables):
            p, family = variable.marginal.parameters, variable.marginal.family
            if family == "constant":
                values = np.full(count, p["value"])
            else:
                if family == "normal":
                    law = stats.norm(loc=p["mean"], scale=p["standard_deviation"])
                elif family == "lognormal":
                    # log_mean and log_standard_deviation are natural-log parameters.
                    try:
                        scale = math.exp(p["log_mean"])
                    except OverflowError:
                        scale = math.inf
                    if not math.isfinite(scale) or scale == 0:
                        record.update(
                            status="out_of_scope", issues=[f"{variable.id}: unrepresentable lognormal scale"]
                        )
                        return _seal(record)
                    law = stats.lognorm(s=p["log_standard_deviation"], scale=scale)
                elif family == "uniform":
                    law = stats.uniform(loc=p["lower"], scale=p["upper"] - p["lower"])
                elif family == "triangular":
                    law = stats.triang(
                        c=(p["mode"] - p["lower"]) / (p["upper"] - p["lower"]),
                        loc=p["lower"],
                        scale=p["upper"] - p["lower"],
                    )
                else:
                    law = stats.beta(p["alpha"], p["beta"], loc=p["lower"], scale=p["upper"] - p["lower"])
                with np.errstate(over="ignore", invalid="ignore"):
                    values = law.ppf(uniform[:, index])
            if not np.isfinite(values).all():
                record.update(
                    status="out_of_scope",
                    issues=[f"{variable.id}: nonfinite inverse distribution; no clipping or redraw"],
                )
                return _seal(record)
            columns.append(values)
        record["draws"] = [
            dict(
                id=f"draw_{i}",
                weight=1.0 / count,
                values={v.id: float(columns[j][i]) for j, v in enumerate(request.variables)},
                evidence_id=request.evidence_id,
            )
            for i in range(count)
        ]
    record["total_weight"] = math.fsum(r["weight"] for r in record["draws"])
    record["weight_normalized"] = False
    return _seal(record)


class Threshold(Contract):
    value: float
    comparison: Literal["gt", "ge", "lt", "le"]
    evidence_id: Name


class EnsembleMetric(Contract):
    id: Name
    unit: Name
    meaning: Literal["quantity", "conditional_probability", "expected_count_rate", "cost"]
    evidence_id: Name
    support_lower: float | None = None
    support_upper: float | None = None
    threshold: Threshold | None = None

    @model_validator(mode="after")
    def support(self):
        if (
            self.support_lower is not None
            and self.support_upper is not None
            and self.support_lower > self.support_upper
        ):
            raise ValueError("metric support must be ordered")
        if self.meaning == "conditional_probability":
            if self.unit != "1" or self.support_lower != 0.0 or self.support_upper != 1.0:
                raise ValueError("conditional probability requires unit 1 and explicit [0,1] support")
        return self


class EnsembleValues(Contract):
    context: RunContext
    values: dict[str, float | None]
    units: dict[str, str]
    statuses: dict[str, Status]
    native_payload: dict

    @model_validator(mode="after")
    def aligned(self):
        if not self.values or set(self.values) != set(self.units) or set(self.values) != set(self.statuses):
            raise ValueError("every scalar result needs its unit and status")
        if any(self.statuses[k] == "computed" and value is None for k, value in self.values.items()):
            raise ValueError("a computed scalar cannot be missing")
        digest(self.native_payload)
        return self


@dataclass(frozen=True)
class EnsembleNode:
    id: str
    version: str
    source_digest: str
    configuration: dict
    metrics: tuple[EnsembleMetric, ...]
    evaluate: Callable[[dict[str, float | None], RunContext], EnsembleValues]


def evaluate_ensemble(design: dict, nodes: tuple[EnsembleNode, ...], *, quantiles=(0.05, 0.5, 0.95)):
    """Evaluate every positive-weight draw on each node, preserving common draw IDs.

    Unexpected callback exceptions propagate. A caller must explicitly classify
    physical/input failures; swallowing programming failures would bias a study.
    """
    _verify(design)
    request = EnsembleRequest.model_validate_json(json.dumps(design["request"]))
    context = RunContext.model_validate_json(json.dumps(design["context"]))
    if "ensemble" in context.metadata:
        raise ValueError("nested ensemble coordinates require an explicit outer/inner design API")
    if not nodes or len({n.id for n in nodes}) != len(nodes):
        raise ValueError("unique nonempty ensemble nodes are required")
    if not quantiles or any(type(q) not in (int, float) or not 0 <= q <= 1 for q in quantiles):
        raise ValueError("finite quantiles in [0,1] are required")
    evidence = {e.id for e in context.evidence}
    metadata, metrics = {}, {}
    for node in nodes:
        if (
            not node.id.strip()
            or not node.version.strip()
            or len(node.source_digest) != 64
            or any(c not in "0123456789abcdef" for c in node.source_digest)
        ):
            raise ValueError("each node needs an identifier, version and implementation SHA-256")
        if not node.metrics or len({m.id for m in node.metrics}) != len(node.metrics):
            raise ValueError("unique nonempty node metrics are required")
        checked = tuple(EnsembleMetric.model_validate_json(m.model_dump_json()) for m in node.metrics)
        for metric in checked:
            if (
                metric.evidence_id not in evidence
                or metric.threshold
                and metric.threshold.evidence_id not in evidence
            ):
                raise ValueError("metric meaning, support and threshold require context evidence")
            metrics[(node.id, metric.id)] = metric
        metadata[node.id] = dict(
            version=node.version,
            source_digest=node.source_digest,
            configuration=copy.deepcopy(node.configuration),
            metrics=[m.model_dump(mode="json") for m in checked],
        )
    result = dict(
        schema="pipeline_integrity.ensemble_result.v1",
        design_digest=design["record_digest"],
        context=copy.deepcopy(design["context"]),
        interpretation=request.interpretation,
        method=request.method,
        node_definitions=metadata,
        status=design["status"],
        issues=list(design["issues"]),
        rows=[],
        summaries={},
        paired_covariances=[],
        quantiles=list(quantiles),
        observed_failure_rate_inferred=False,
        independent_trial_confidence_interval_computed=False,
        field_qualified=False,
        implementation_sha256=file_digest(Path(__file__)),
    )
    if design["status"] != "computed":
        return _seal(result)
    # Revalidate rows even when a caller supplies a newly sealed hand-built record.
    rows = [JointCase.model_validate_json(json.dumps(row)) for row in design["draws"]]
    if request.method == "joint_cases" and tuple(rows) != request.cases:
        raise ValueError("joint rows differ from the declared paired cases")
    if request.method != "joint_cases" and len(rows) != request.samples:
        raise ValueError("generated sample count differs from the design request")
    if len({r.id for r in rows}) != len(rows) or any(
        set(r.values) != {v.id for v in request.variables} for r in rows
    ):
        raise ValueError("design rows must preserve unique IDs and declared variables")
    if not rows or not math.isclose(math.fsum(r.weight for r in rows), 1.0, rel_tol=0, abs_tol=1e-12):
        raise ValueError("design must retain the full declared weight")
    support_violations = set()
    for row in rows:
        draw_context = context.model_copy(
            deep=True,
            update={
                "metadata": context.metadata
                | {
                    "ensemble": {
                        "design_digest": design["record_digest"],
                        "draw_id": row.id,
                        "interpretation": request.interpretation,
                        "draw_index_is_time": False,
                    }
                }
            },
        )
        node_rows = {}
        for node in nodes:
            expected = {m.id for m in node.metrics}
            if row.weight == 0:
                node_rows[node.id] = dict(
                    values=dict.fromkeys(expected),
                    statuses=dict.fromkeys(expected, "needs_data"),
                    skipped_zero_weight=True,
                )
                continue
            outcome = node.evaluate(dict(row.values), draw_context.model_copy(deep=True))
            if not isinstance(outcome, EnsembleValues):
                raise TypeError("node callbacks must return EnsembleValues")
            outcome = EnsembleValues.model_validate_json(outcome.model_dump_json())
            if outcome.context != draw_context:
                raise ValueError("node returned a different asset, scenario, clock or draw context")
            if set(outcome.values) != expected or outcome.units != {m.id: m.unit for m in node.metrics}:
                raise ValueError("node result metrics or units differ from their declared definitions")
            node_row = outcome.model_dump(mode="json", exclude={"context"})
            node_row["native_digest"] = digest(outcome.native_payload)
            node_row["issues"] = []
            for key, value in outcome.values.items():
                metric = metrics[(node.id, key)]
                if outcome.statuses[key] == "computed" and (
                    (metric.support_lower is not None and value < metric.support_lower)
                    or (metric.support_upper is not None and value > metric.support_upper)
                ):
                    node_row["statuses"][key] = "out_of_scope"
                    node_row["issues"].append(
                        f"{key}: result violates declared support; original value retained"
                    )
                    support_violations.add((node.id, key))
            node_rows[node.id] = node_row
        result["rows"].append(
            dict(
                id=row.id,
                weight=row.weight,
                input_digest=digest(row.values),
                draw_context_digest=digest(draw_context),
                nodes=node_rows,
            )
        )

    def entries(node, key):
        return [
            (r["weight"], r["nodes"][node]["values"][key], r["nodes"][node]["statuses"][key])
            for r in result["rows"]
            if r["weight"] > 0
        ]

    for (node_id, key), metric in metrics.items():
        result["summaries"].setdefault(node_id, {})[key] = _summarize(
            entries(node_id, key),
            metric,
            quantiles,
            (node_id, key) not in support_violations,
            request.interpretation,
        )
    all_metrics = list(metrics)
    for i, left in enumerate(all_metrics):
        for right in all_metrics[i + 1 :]:
            paired = [
                (a[0], a[1], b[1])
                for a, b in zip(entries(*left), entries(*right), strict=True)
                if a[2] == b[2] == "computed"
            ]
            weight = math.fsum(r[0] for r in paired)
            covariance = None
            if weight:
                try:
                    mx = math.fsum(w * x for w, x, _ in paired) / weight
                    my = math.fsum(w * y for w, _, y in paired) / weight
                    value = math.fsum(w * (x - mx) * (y - my) for w, x, y in paired) / weight
                    covariance = value if math.isfinite(value) else None
                except OverflowError:
                    pass
            result["paired_covariances"].append(
                dict(
                    left=list(left),
                    right=list(right),
                    paired_known_weight=weight,
                    conditional_on_paired_valid_covariance=covariance,
                    unit_product=[metrics[left].unit, metrics[right].unit],
                    population_weighted=True,
                    independent_sample_estimator=False,
                )
            )
    for node in nodes:
        if digest(node.configuration) != digest(metadata[node.id]["configuration"]):
            raise ValueError("node configuration changed during ensemble evaluation")
    _verify(design)
    statuses = [m["status"] for group in result["summaries"].values() for m in group.values()]
    result["status"] = (
        "out_of_scope"
        if "out_of_scope" in statuses
        else "needs_data"
        if "needs_data" in statuses
        else "computed"
    )
    result["total_weight"] = math.fsum(r.weight for r in rows)
    result["zero_weight_draws_skipped"] = sum(r.weight == 0 for r in rows)
    return _seal(result)


def verify_ensemble_result(record: dict, design: dict, nodes: tuple[EnsembleNode, ...]):
    """Replay a sealed ensemble result against its exact design and node set.

    The result stores per-draw native payload digests rather than duplicating
    native responses. Reverification therefore requires the original design
    and the same deterministic node callbacks/providers. A changed owner,
    callback, node configuration, input design, or summary invalidates replay.
    """
    try:
        if not isinstance(record, dict):
            raise TypeError("ensemble result must be a JSON object")
        checked = json_value(record)
        _verify(checked)
        if checked.get("schema") != "pipeline_integrity.ensemble_result.v1":
            raise ValueError("unsupported ensemble result schema")
        if checked.get("design_digest") != design.get("record_digest"):
            raise ValueError("ensemble result is bound to a different design")
        if not isinstance(nodes, tuple) or not nodes:
            raise ValueError("the original nonempty immutable node tuple is required for replay")
        replayed = evaluate_ensemble(design, nodes, quantiles=tuple(checked["quantiles"]))
        if replayed != checked:
            raise ValueError("ensemble outputs or per-draw node receipts differ on replay")
        return {"valid": True, "design_digest": checked["design_digest"]}
    except (ValueError, TypeError, KeyError, AttributeError, ImportError) as error:
        return {"valid": False, "reason": str(error)}


def _summarize(rows, metric, quantiles, support_trusted, interpretation):
    known = [(w, x) for w, x, s in rows if s == "computed"]
    weight = math.fsum(w for w, _ in known)
    unknown = math.fsum(w for w, _, s in rows if s != "computed")
    status = (
        "out_of_scope"
        if any(s == "out_of_scope" for _, _, s in rows)
        else "needs_data"
        if unknown
        else "computed"
    )
    summary = dict(
        status=status,
        unit=metric.unit,
        meaning=metric.meaning,
        known_weight=weight,
        unresolved_weight=unknown,
        known_weighted_sum=None,
        conditional_on_valid_mean=None,
        full_design_mean=None,
        conditional_on_valid_quantiles=None,
        declared_support_trusted=support_trusted,
        full_design_mean_bounds=None,
        threshold_weight_bounds=None,
    )
    try:
        weighted_sum = math.fsum(w * x for w, x in known)
        if not math.isfinite(weighted_sum):
            raise OverflowError
        summary["known_weighted_sum"] = weighted_sum
        if weight:
            summary["conditional_on_valid_mean"] = weighted_sum / weight
            summary["conditional_on_valid_quantiles"] = np.quantile(
                [x for _, x in known], quantiles, method="inverted_cdf", weights=[w for w, _ in known]
            ).tolist()
        if unknown == 0:
            summary["full_design_mean"] = weighted_sum / weight
        elif support_trusted and metric.support_lower is not None and metric.support_upper is not None:
            total = weight + unknown
            bounds = [
                (weighted_sum + unknown * endpoint) / total
                for endpoint in (metric.support_lower, metric.support_upper)
            ]
            if all(math.isfinite(v) for v in bounds):
                summary["full_design_mean_bounds"] = bounds
    except OverflowError:
        summary.update(status="out_of_scope", arithmetic_issue="weighted summary exceeds finite arithmetic")
    if metric.threshold:
        t = metric.threshold
        predicate = {
            "gt": lambda x: x > t.value,
            "ge": lambda x: x >= t.value,
            "lt": lambda x: x < t.value,
            "le": lambda x: x <= t.value,
        }[t.comparison]
        exceed = math.fsum(w for w, x in known if predicate(x))
        summary["threshold_weight_bounds"] = dict(
            lower=exceed,
            upper=exceed + unknown,
            interpretation=interpretation,
            finite_design_weight=True,
            operational_failure_probability=False,
        )
    return summary


def registry_node(
    registry,
    *,
    id: str,
    model_id: str,
    base_inputs: dict,
    bindings: dict[str, str],
    variables: tuple[UncertainVariable, ...],
    metrics: tuple[EnsembleMetric, ...],
):
    """Bind declared draw variables to top-level registry scalar inputs; units must match exactly.

    Nested inputs and external engines use a reviewed Python callback. No expression
    strings, implicit unit conversion or pressure-reference substitution are used.
    """
    description = registry.describe(model_id)
    info = description["model"]
    input_contract_title = description["input_schema"]["title"]
    by_id = {v.id: v for v in variables}
    if not bindings or len(set(bindings.values())) != len(bindings):
        raise ValueError("unique target fields are required")
    for variable_id, target in bindings.items():
        if variable_id not in by_id or target not in base_inputs or target not in info["input_units"]:
            raise ValueError("binding needs a declared variable and existing scalar model field")
        if by_id[variable_id].unit != info["input_units"][target]:
            raise ValueError("variable and model field units must match exactly")
        if base_inputs[target] is not None and type(base_inputs[target]) not in (int, float):
            raise ValueError("registry binding supports top-level numeric scalar fields")
    if any(info["output_units"].get(m.id) != m.unit for m in metrics):
        raise ValueError("metric names and units must match registered scalar outputs")
    configuration = dict(model=info, base_inputs=copy.deepcopy(base_inputs), bindings=dict(bindings))
    frozen_configuration = copy.deepcopy(configuration)
    # Configuration construction validates input shape and fingerprints code;
    # the registered calculation runs only when an ensemble draw is evaluated.
    registry.validate_inputs(model_id,copy.deepcopy(base_inputs))
    implementation = registry.implementation_identity(model_id)

    def evaluate(values, context):
        if digest(configuration) != digest(frozen_configuration):
            raise ValueError("prepared registry node configuration was modified")
        if registry.implementation_identity(model_id)!=implementation:
            raise ValueError("registered implementation changed after ensemble preparation")
        parameters = copy.deepcopy(frozen_configuration["base_inputs"])
        for variable_id, target in frozen_configuration["bindings"].items():
            parameters[target] = values[variable_id]
        if any(values[k] is None for k in frozen_configuration["bindings"]):
            return EnsembleValues(
                context=context,
                values={m.id: None for m in metrics},
                units={m.id: m.unit for m in metrics},
                statuses={m.id: "needs_data" for m in metrics},
                native_payload={
                    "model_id": model_id,
                    "inputs": parameters,
                    "issue": "unresolved bound input",
                },
            )
        try:
            native = registry.evaluate(model_id, parameters, context)
        except ValidationError as error:
            if error.title != input_contract_title:
                raise
            return EnsembleValues(
                context=context,
                values={m.id: None for m in metrics},
                units={m.id: m.unit for m in metrics},
                statuses={m.id: "out_of_scope" for m in metrics},
                native_payload={
                    "model_id": model_id,
                    "inputs": parameters,
                    "validation_errors": error.errors(
                        include_url=False, include_context=False, include_input=False
                    ),
                },
            )
        if (
            native.model_version != implementation["model_version"]
            or native.model_source_digest != implementation["model_source_digest"]
        ):
            raise ValueError("registered implementation changed during ensemble evaluation")
        return EnsembleValues(
            context=context,
            values={m.id: native.values.get(m.id) for m in metrics},
            units={m.id: m.unit for m in metrics},
            statuses={
                m.id: native.status
                if native.values.get(m.id) is not None
                else "needs_data"
                if native.status == "computed"
                else native.status
                for m in metrics
            },
            native_payload=native.model_dump(mode="json"),
        )

    return EnsembleNode(
        id=id,
        version=info["version"],
        source_digest=implementation["model_source_digest"],
        configuration=configuration,
        metrics=metrics,
        evaluate=evaluate,
    )
