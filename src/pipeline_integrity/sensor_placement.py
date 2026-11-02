"""Finite correlated-noise designs compiled to the existing resource scheduler."""

from __future__ import annotations

import json
import math
import platform
from importlib.metadata import version
from itertools import combinations
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from . import contracts, scheduling
from .contracts import Contract, Name, Nonnegative, Positive, RunContext, digest, json_value
from .datasets import file_digest
from .metrology import MeasurementBasis
from .scheduling import (
    Count,
    Operation,
    OperationMode,
    PlanningResource,
    ScheduleRequest,
    seconds,
    solve_schedule,
    ticks,
)

REVIEWS = (
    "local_linearization_and_shared_parameters",
    "joint_noise_law_and_order",
    "parameter_scales_and_units",
    "measurement_geometry_and_clock",
    "budget_resource_and_observation_window",
)
SHA = r"^[a-f0-9]{64}$"


class DesignParameter(Contract):
    id: Name
    basis: MeasurementBasis
    reference_scale: Positive | None


class DesignMeasurement(Contract):
    id: Name
    sensor_id: Name
    basis: MeasurementBasis


class CandidateSensor(Contract):
    id: Name
    location_id: Name
    coordinate_frame_id: Name
    position_m: tuple[float, float, float] | None = None
    physical_port_id: Name | None = None
    budget_use: dict[Name, Count | None] = Field(default_factory=dict)
    resource_use: dict[Name, Count | None] = Field(default_factory=dict)
    required: bool = False
    evidence_id: Name


class DesignLinearization(Contract):
    model_id: Name
    model_version: Name
    source_sha256: str = Field(pattern=SHA)
    configuration_sha256: str = Field(pattern=SHA)
    linearization_point_sha256: str = Field(pattern=SHA)
    measurement_ids: tuple[Name, ...]
    parameter_ids: tuple[Name, ...]
    jacobian: tuple[tuple[float | None, ...], ...] | None
    evidence_id: Name


class JointDesignNoise(Contract):
    measurement_ids: tuple[Name, ...]
    covariance: tuple[tuple[float | None, ...], ...] | None
    law: Literal["parameter_independent_gaussian_at_one_window"] | None = None
    evidence_id: Name


class SensorPlacementRequest(Contract):
    id: Name
    asset_id: Name
    clock_id: Name
    event_id: Name
    parameters: tuple[DesignParameter, ...] = Field(min_length=1, max_length=32)
    measurements: tuple[DesignMeasurement, ...] = Field(min_length=1, max_length=128)
    candidates: tuple[CandidateSensor, ...] = Field(min_length=1, max_length=20)
    linearization: DesignLinearization | None
    noise: JointDesignNoise | None
    criterion: Literal["D_opt", "A_opt", "E_opt"] = "D_opt"
    budgets: dict[Name, Count] = Field(default_factory=dict)
    budget_definition_ids: dict[Name, Name] = Field(default_factory=dict)
    resources: tuple[PlanningResource, ...] = ()
    observation_start_s: Nonnegative | None
    observation_duration_s: Positive | None
    time_quantum_s: Positive = 1.0
    menus: tuple[tuple[Name, ...], ...] | None = None
    maximum_subsets: int = Field(default=4096, ge=1, le=65536)
    rank_relative_cutoff: float = Field(default=1e-10, gt=0, lt=1)
    maximum_scaled_condition: Positive = Field(default=1e10, ge=1)
    maximum_solver_seconds: Positive = Field(default=30.0, le=3600)
    random_seed: int = Field(default=0, ge=0, le=2147483647)
    evidence_id: Name
    reviews: dict[str, bool] = Field(default_factory=dict)
    review_evidence_ids: dict[str, Name] = Field(default_factory=dict)

    @model_validator(mode="after")
    def ordered(self):
        parameters = tuple(p.id for p in self.parameters)
        measurements = tuple(m.id for m in self.measurements)
        sensors = {s.id for s in self.candidates}
        if (
            len(set(parameters)) != len(parameters)
            or len(set(measurements)) != len(measurements)
            or len(sensors) != len(self.candidates)
        ):
            raise ValueError("parameter, measurement and candidate IDs must be distinct")
        if {m.sensor_id for m in self.measurements} != sensors:
            raise ValueError("each candidate must own at least one distinct measurement row")
        if set(self.budget_definition_ids) != set(self.budgets):
            raise ValueError("each integer budget needs its actual quote/count definition")
        resources = {r.id for r in self.resources}
        if len(resources) != len(self.resources):
            raise ValueError("resource identities must be distinct")
        for sensor in self.candidates:
            if set(sensor.budget_use) != set(self.budgets) or set(sensor.resource_use) != resources:
                raise ValueError(
                    "candidate usage must explicitly include zero or unknown for every budget/resource"
                )
        if self.linearization is not None:
            model = self.linearization
            if model.measurement_ids != measurements or model.parameter_ids != parameters:
                raise ValueError(
                    "Jacobian row/column order differs from declared measurement/parameter identities"
                )
            if model.jacobian is not None and (
                len(model.jacobian) != len(measurements)
                or any(len(row) != len(parameters) for row in model.jacobian)
            ):
                raise ValueError("Jacobian shape differs from its explicit row/column order")
        if self.noise is not None:
            if self.noise.measurement_ids != measurements:
                raise ValueError("noise covariance order differs from the actual measurement order")
            matrix = self.noise.covariance
            if matrix is not None:
                if len(matrix) != len(measurements) or any(len(row) != len(measurements) for row in matrix):
                    raise ValueError("joint covariance must be a complete square row-ordered declaration")
                if any(
                    matrix[i][j] is not None and matrix[j][i] is not None and matrix[i][j] != matrix[j][i]
                    for i in range(len(matrix))
                    for j in range(i)
                ):
                    raise ValueError("known covariance entries must be exactly symmetric; no matrix repair")
        if self.menus is None:
            if 2 ** len(sensors) > self.maximum_subsets:
                raise ValueError(
                    "full finite subset enumeration exceeds its explicit work budget; provide a reviewed menu"
                )
        else:
            if not self.menus or len(self.menus) > self.maximum_subsets:
                raise ValueError("a finite nonempty menu must fit the subset work budget")
            keys = []
            for menu in self.menus:
                if len(set(menu)) != len(menu) or set(menu) - sensors:
                    raise ValueError("each menu needs unique existing sensor IDs")
                keys.append(frozenset(menu))
            if len(set(keys)) != len(keys):
                raise ValueError("the same physical subset cannot occur more than once")
        if self.observation_start_s is not None and self.observation_duration_s is not None:
            ticks(self.observation_start_s, self.time_quantum_s)
            ticks(self.observation_duration_s, self.time_quantum_s)
            finish_ticks = ticks(self.observation_start_s, self.time_quantum_s) + ticks(
                self.observation_duration_s, self.time_quantum_s
            )
            if finish_ticks > 1000000000:
                raise ValueError("joint observation window exceeds the shared tick budget")
        return self


def boundary_metadata(request):
    return {
        "request_id": request.id,
        "event_id": request.event_id,
        "candidate_geometry": [
            {
                "id": s.id,
                "location": s.location_id,
                "frame": s.coordinate_frame_id,
                "position_m": s.position_m,
                "port": s.physical_port_id,
            }
            for s in request.candidates
        ],
        "measurement_bases_digest": digest(request.measurements),
        "parameter_bases_digest": digest(request.parameters),
        "linearization_digest": digest(request.linearization),
        "noise_digest": digest(request.noise),
        "observation_start_s": request.observation_start_s,
        "observation_duration_s": request.observation_duration_s,
        "observation_scope": "one joint observation window; no independent repeat assumption",
    }


def _checked(request, context):
    q = SensorPlacementRequest.model_validate(request.model_dump(mode="python"))
    c = RunContext.model_validate(context.model_dump(mode="python"))
    if (q.asset_id, q.clock_id) != (c.asset_id, c.clock_id) or c.metadata.get(
        "sensor_placement_boundary"
    ) != json_value(boundary_metadata(q)):
        raise ValueError("placement asset/clock/geometry/model/noise/window differs from actual context")
    ids = [e.id for e in c.evidence]
    needed = {q.evidence_id, *q.review_evidence_ids.values()}
    needed.update(s.evidence_id for s in q.candidates)
    needed.update(m.basis.evidence_id for m in q.measurements)
    needed.update(p.basis.evidence_id for p in q.parameters)
    needed.update(r.evidence_id for r in q.resources)
    if q.linearization:
        needed.add(q.linearization.evidence_id)
    if q.noise:
        needed.add(q.noise.evidence_id)
    if len(set(ids)) != len(ids) or needed - set(ids):
        raise ValueError("placement definitions and reviews require unique actual evidence members")
    return q, c


def _menus(q):
    if q.menus is not None:
        return q.menus
    ids = tuple(s.id for s in q.candidates)
    return tuple(menu for count in range(len(ids) + 1) for menu in combinations(ids, count))


def _covariance_factor(matrix):
    import numpy as np
    from scipy.linalg import cholesky

    array = np.asarray(matrix, dtype=float)
    if not np.isfinite(array).all() or not np.array_equal(array, array.T) or np.any(np.diag(array) <= 0):
        raise ValueError("selected joint measurement covariance must be finite symmetric positive definite")
    scales = np.sqrt(np.diag(array))
    with np.errstate(over="raise", invalid="raise", divide="raise", under="raise"):
        correlation = array / scales[:, None] / scales[None, :]
    try:
        return scales, cholesky(correlation, lower=True, check_finite=True)
    except np.linalg.LinAlgError as error:
        raise ValueError("joint covariance is not positive definite; no pseudoinverse or repair") from error


def _analyse(q):
    import numpy as np
    import scipy.linalg

    rows = []
    scales = np.asarray([p.reference_scale for p in q.parameters], dtype=float)
    required = {s.id for s in q.candidates if s.required}
    candidates = {s.id: s for s in q.candidates}
    covariance = q.noise.covariance
    jacobian = q.linearization.jacobian
    joint_complete = not any(v is None for row in covariance for v in row)
    if joint_complete:
        _covariance_factor(covariance)
    for index, menu in enumerate(_menus(q)):
        row = {
            "id": f"subset_{index}",
            "sensor_ids": list(menu),
            "status": "needs_data",
            "issues": [],
            "loss": None,
            "rank": None,
            "diagnostic": None,
        }
        rows.append(row)
        if required - set(menu):
            row.update(status="excluded", issues=["required sensor absent"])
            continue
        selected = [i for i, m in enumerate(q.measurements) if m.sensor_id in menu]
        if not selected:
            row.update(
                status="rank_deficient", rank=0, issues=["no measurement rows; no full-parameter information"]
            )
            continue
        sensors = [candidates[name] for name in menu]
        if any(v is None for s in sensors for v in (*s.budget_use.values(), *s.resource_use.values())):
            row["issues"] = ["selected resource/quote usage is unknown"]
            continue
        noise = [[covariance[i][j] for j in selected] for i in selected]
        gradients = [jacobian[i] for i in selected]
        if any(v is None for line in (*noise, *gradients) for v in line):
            row["issues"] = ["selected sensitivity or joint noise is unknown; no independence inferred"]
            continue
        try:
            deviations, factor = _covariance_factor(noise)
            with np.errstate(over="raise", invalid="raise", divide="raise", under="raise"):
                normalized = np.asarray(gradients, dtype=float) * scales[None, :] / deviations[:, None]
                whitened = scipy.linalg.solve_triangular(factor, normalized, lower=True, check_finite=True)
                _, singular, vh = scipy.linalg.svd(whitened, full_matrices=False, check_finite=True)
                rank = (
                    int(np.count_nonzero(singular > q.rank_relative_cutoff * singular[0]))
                    if singular.size and singular[0] > 0
                    else 0
                )
                row["rank"] = rank
                if rank < len(q.parameters):
                    row.update(
                        status="rank_deficient", issues=["not locally observable for all declared parameters"]
                    )
                    continue
                condition = float(singular[0] / singular[-1])
                if condition > q.maximum_scaled_condition:
                    row.update(
                        status="out_of_scope",
                        issues=["scaled sensitivity exceeds the declared condition limit"],
                    )
                    continue
                inverse = (vh.T / singular) @ (vh.T / singular).T
                information = whitened.T @ whitened
                logdet = 2 * math.fsum(math.log(float(v)) for v in singular)
                a_opt = float(np.trace(inverse))
                smallest = float(singular[-1] ** 2)
                loss = {"D_opt": -logdet, "A_opt": a_opt, "E_opt": -smallest}[q.criterion]
                if (
                    not all(math.isfinite(float(v)) for array in (inverse, information) for v in array.flat)
                    or not math.isfinite(loss)
                    or smallest <= 0
                ):
                    raise ArithmeticError("information/covariance metric exceeds numeric range; no clipping")
            row.update(
                status="qualified",
                loss=loss,
                diagnostic={
                    "measurement_ids": [q.measurements[i].id for i in selected],
                    "normalized_information": information.tolist(),
                    "inverse_information_normalized_coordinates": inverse.tolist(),
                    "scaled_singular_values": singular.tolist(),
                    "condition_number": condition,
                    "log_determinant_information": logdet,
                    "trace_inverse_information": a_opt,
                    "minimum_information_eigenvalue": smallest,
                },
            )
        except (ValueError, ArithmeticError, np.linalg.LinAlgError) as error:
            row.update(status="out_of_scope", issues=[str(error)])
    return rows, joint_complete


def _scheduler_snapshot(result):
    # Wall time is not part of the derived deterministic mathematical snapshot.
    data = result.model_dump(mode="json")
    data["native"]["provider"].pop("wall_time_s", None)
    data["result_digest"] = digest({k: v for k, v in data.items() if k != "result_digest"})
    return data


def plan_sensor_placement(request: SensorPlacementRequest, context: RunContext, *, backend=None):
    q, c = _checked(request, context)
    body = {
        "schema": "pipeline_integrity.sensor_placement.v1",
        "request": q.model_dump(mode="json"),
        "context": c.model_dump(mode="json"),
        "implementation_sha256": file_digest(Path(__file__)),
        "shared_sources": {
            "contracts": file_digest(Path(contracts.__file__)),
            "scheduler": file_digest(Path(scheduling.__file__)),
        },
        "status": "needs_data",
        "issues": [],
        "native_information_called": False,
        "scheduler_called": False,
        "runtime": None,
        "subsets": [],
        "schedule_request": None,
        "schedule_semantics": None,
        "selected": None,
        "global_design_proven_optimal": False,
        "finite_menu_proven_optimal": False,
        "global_scope": "all candidate subsets" if q.menus is None else "caller supplied finite menus only",
        "joint_noise_completion_qualified": False,
        "field_qualified": False,
        "detection_probability_calculated": False,
        "work_authorized": False,
        "native_wall_time_omitted": True,
        "validation_basis": "caller-declared local model/noise; no field calibration",
    }

    def seal():
        return {**body, "record_digest": digest(body)}

    missing = [key for key in REVIEWS if q.reviews.get(key) is not True or key not in q.review_evidence_ids]
    if q.linearization is None or q.linearization.jacobian is None:
        missing.append("reviewed forward Jacobian")
    if q.noise is None or q.noise.covariance is None or q.noise.law is None:
        missing.append("explicit parameter-independent Gaussian joint noise law")
    if any(p.reference_scale is None for p in q.parameters):
        missing.append("parameter reference scales")
    if any(s.position_m is None and s.physical_port_id is None for s in q.candidates):
        missing.append("physical candidate geometry or port mapping")
    if q.observation_start_s is None or q.observation_duration_s is None:
        missing.append("observation window")
    if missing:
        body["issues"] = missing
        return seal()
    try:
        from scipy.linalg import _flapack

        body.update(
            native_information_called=True,
            runtime={
                "python": platform.python_version(),
                **{name: version(name) for name in ("numpy", "scipy", "pydantic", "pydantic-core")},
                "scipy_lapack_sha256": file_digest(Path(_flapack.__file__)),
            },
        )
        rows, joint_complete = _analyse(q)
        body.update(subsets=rows, joint_noise_completion_qualified=joint_complete)
        qualified = [r for r in rows if r["status"] == "qualified"]
        unresolved = any(r["status"] in {"needs_data", "out_of_scope"} for r in rows) or not joint_complete
        if not qualified:
            body.update(
                status="needs_data" if unresolved else "infeasible",
                issues=["no qualified full-rank subset available"],
            )
            return seal()
        # One whole subset is one mutually exclusive mode: its finite objective
        # order is preserved exactly, without additive sensor scores or rounding.
        ranks = {r["id"]: i for i, r in enumerate(sorted(qualified, key=lambda r: (r["loss"], r["id"])))}
        by_sensor = {s.id: s for s in q.candidates}
        finish = seconds(
            ticks(q.observation_start_s, q.time_quantum_s)
            + ticks(q.observation_duration_s, q.time_quantum_s),
            q.time_quantum_s,
        )
        modes = []
        for row in qualified:
            sensors = [by_sensor[name] for name in row["sensor_ids"]]
            resource = {
                name: sum(s.resource_use[name] for s in sensors) for name in (r.id for r in q.resources)
            }
            budget = {name: sum(s.budget_use[name] for s in sensors) for name in q.budgets}
            modes.append(
                OperationMode(
                    id=row["id"],
                    duration_s=q.observation_duration_s,
                    resources={k: v for k, v in resource.items() if v},
                    consumables={k: v for k, v in budget.items() if v},
                    fixed_cost=ranks[row["id"]],
                    evidence_id=q.evidence_id,
                    earliest_start_s=q.observation_start_s,
                    latest_finish_s=finish,
                )
            )
        spec = ScheduleRequest(
            horizon_s=finish,
            time_quantum_s=q.time_quantum_s,
            operations=(
                Operation(
                    id="joint_observation",
                    modes=tuple(modes),
                    earliest_start_s=q.observation_start_s,
                    latest_finish_s=finish,
                ),
            ),
            resources=q.resources,
            consumable_budgets=q.budgets,
            makespan_cost_per_tick=0,
            objective_unit="finite_joint_design_loss_order",
            evidence_id=q.evidence_id,
            maximum_intervals=1000000,
            maximum_seconds=q.maximum_solver_seconds,
            relative_gap_limit=0.0,
            workers=1,
            random_seed=q.random_seed,
        )
        body.update(scheduler_called=True, schedule_request=spec.model_dump(mode="json"))
        schedule = solve_schedule(spec, c, backend=backend)
        body["schedule_semantics"] = _scheduler_snapshot(schedule)
        if schedule.status in {"optimal", "feasible"}:
            selected = next(r for r in rows if r["id"] == schedule.timeline[0]["mode_id"])
            body.update(
                selected=selected,
                status="needs_data" if unresolved else schedule.status,
                finite_menu_proven_optimal=schedule.proven_optimal and not unresolved,
                global_design_proven_optimal=schedule.proven_optimal and not unresolved and q.menus is None,
                issues=[
                    "selection is conditional on evaluated subsets; joint noise or another menu remains unqualified"
                ]
                if unresolved
                else [],
            )
        else:
            body.update(
                status=schedule.status,
                issues=["shared scheduler did not provide an accepted complete selection"],
            )
    except (ValueError, ArithmeticError) as error:
        body.update(
            status="out_of_scope", issues=[str(error)], selected=None, global_design_proven_optimal=False
        )
    return seal()


def verify_sensor_placement_record(record, *, request=None, context=None, backend=None):
    """Current mathematical replay; the semantic scheduler snapshot omits wall time."""
    if not isinstance(record, dict) or record.get("record_digest") != digest(
        {k: v for k, v in record.items() if k != "record_digest"}
    ):
        raise ValueError("sensor placement record seal changed")
    q = SensorPlacementRequest.model_validate_json(json.dumps(record["request"], allow_nan=False))
    c = RunContext.model_validate_json(json.dumps(record["context"], allow_nan=False))
    if (request is not None and digest(q) != digest(request)) or (context is not None and digest(c) != digest(context)):
        raise ValueError("sensor placement caller boundary changed")
    if plan_sensor_placement(q, c, backend=backend) != record:
        raise ValueError("information, noise, resource, native selection or qualification differs on replay")
    return True
