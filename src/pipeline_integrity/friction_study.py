"""Source-bound pilot-pipe diagnostics through shared hydraulic and inverse owners."""

from __future__ import annotations

import inspect
import json
import platform
from importlib.metadata import version
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .contracts import Contract, Name, Nonnegative, Positive, RunContext, digest
from .datasets import file_digest
from .friction_data import read_friction_observations
from .inverse import (
    FitRequest,
    Observations,
    ObservedChannel,
    OperatingPoint,
    OperatingPointFitRequest,
    OperatingPointObservations,
    OperatingPointPrediction,
    Prediction,
    ResidualScale,
    fit_operating_points,
    fit_parameters,
)
from .models.hydraulics import LiquidHeadLoss, LiquidPressureLoss, liquid_head_loss

_PARAMETER_UNITS = {"roughness_m": "m", "minor_loss_coefficient": "1"}


class FrictionStudyRequest(Contract):
    id: Name
    asset_id: Name
    event_id: Name
    clock_id: Name
    inlet_port_id: Name
    outlet_port_id: Name
    source_record_digest: str | None = Field(default=None, pattern="^[a-f0-9]{64}$")
    source_evidence_id: Name
    geometry_property_evidence_id: Name
    physical_straight_length_m: Positive | None
    internal_diameter_m: Positive | None
    gravity_m_s2: Positive | None
    outlet_minus_inlet_elevation_m: float | None
    density_kg_m3_by_source_row: tuple[Positive, ...] | None
    roughness_m: Nonnegative | None
    minor_loss_coefficient: Nonnegative | None
    head_interpretation: Literal["unknown", "same_reference_pressure_head_difference"] = "unknown"
    geometry_density_viscosity_and_local_loss_boundary_reviewed: bool = False
    pressure_head_reference_and_elevation_reviewed: bool = False
    transition_policy: Literal["withhold", "use_declared_correlation"] = "withhold"
    friction_method: Literal["Clamond", "Colebrook", "Haaland", "Swamee_Jain_1976"] = "Clamond"
    calibration: FitRequest | None = None
    operating_point_calibration: OperatingPointFitRequest | None = None
    calibration_evidence_id: Name | None = None
    source_row_time_s: tuple[Nonnegative, ...] | None = None
    clock_evidence_id: Name | None = None
    external_measurement_clock_reviewed: bool = False
    residual_scale_m: ResidualScale | None = None
    calibration_error_model: Literal[
        "unknown", "deterministic_scaled_residuals_only", "explicit_joint_residual_metric"
    ] = "unknown"
    scale_bounds_and_same_apparatus_split_reviewed: bool = False
    head_crosscheck_absolute_tolerance_m: Nonnegative = 1e-10
    head_crosscheck_relative_tolerance: Nonnegative = Field(default=1e-10, le=1e-3)

    @model_validator(mode="after")
    def domain(self):
        if self.inlet_port_id == self.outlet_port_id:
            raise ValueError("friction-study ports must be distinct")
        if self.density_kg_m3_by_source_row is not None and len(self.density_kg_m3_by_source_row) != 6:
            raise ValueError("each of the six actual source rows needs an explicit density")
        if self.source_row_time_s is not None and (
            len(self.source_row_time_s) != 6
            or any(b <= a for a, b in zip(self.source_row_time_s, self.source_row_time_s[1:], strict=False))
        ):
            raise ValueError("six externally supplied actual measurement times must increase")
        if self.residual_scale_m is not None and self.residual_scale_m.unit != "m":
            raise ValueError("pressure-head residual scale must be expressed in metres")
        if (
            self.internal_diameter_m is not None
            and self.roughness_m is not None
            and self.roughness_m / self.internal_diameter_m > 0.1
        ):
            raise ValueError("roughness exceeds the shared hydraulic-owner domain")
        if self.calibration is not None and self.operating_point_calibration is not None:
            raise ValueError("choose one actual-time or operating-point fit, never both")
        metric = (
            None
            if self.operating_point_calibration is None
            else self.operating_point_calibration.residual_metric
        )
        if self.calibration is not None and self.calibration_error_model == "explicit_joint_residual_metric":
            raise ValueError(
                "the joint residual metric is supported on the explicit operating-point axis only"
            )
        if metric is not None and self.calibration_error_model == "deterministic_scaled_residuals_only":
            raise ValueError(
                "a supplied joint metric cannot silently replace the declared scalar residual model"
            )
        active_fit = self.calibration or self.operating_point_calibration
        if active_fit is not None:
            if (active_fit.provider_id, active_fit.provider_version) != (
                "friction_hydraulic_loss",
                "1",
            ):
                raise ValueError("calibration must select this actual forward provider and version")
            for parameter in active_fit.parameters:
                if (
                    parameter.id not in _PARAMETER_UNITS
                    or parameter.unit != _PARAMETER_UNITS[parameter.id]
                    or parameter.lower < 0
                ):
                    raise ValueError(
                        "fit parameters require supported nonnegative hydraulic variables and exact units"
                    )
                if (
                    parameter.id == "roughness_m"
                    and self.internal_diameter_m is not None
                    and parameter.upper / self.internal_diameter_m > 0.1
                ):
                    raise ValueError(
                        "the entire fit roughness interval must remain in the hydraulic-owner domain"
                    )
        return self


def friction_study_boundary_metadata(q):
    return {
        "id": q.id,
        "asset_id": q.asset_id,
        "event_id": q.event_id,
        "clock_id": q.clock_id,
        "ports": [q.inlet_port_id, q.outlet_port_id],
        "request_digest": digest(q),
        "observable": "same-reference inlet minus outlet pressure head in metres; no absolute pressure inferred",
    }


def _inputs(request, context, source):
    q = FrictionStudyRequest.model_validate(request.model_dump(mode="python"))
    c = RunContext.model_validate(context.model_dump(mode="python"))
    if (q.asset_id, q.event_id, q.clock_id) != (c.asset_id, c.scenario_id, c.clock_id) or c.metadata.get(
        "friction_study_boundary"
    ) != friction_study_boundary_metadata(q):
        raise ValueError("friction-study source/asset/event/clock/ports/request context differs")
    source_origin = {
        "laboratory_experiment": "laboratory",
        "measured_process": "measured",
        "simulation": "simulation",
        "synthetic": "synthetic",
    }.get(source["origin"])
    if source_origin is None or c.origin != source_origin:
        raise ValueError("friction-study context cannot relabel the actual source observation origin")
    if q.source_record_digest is not None and q.source_record_digest != source["record_digest"]:
        raise ValueError("current source observation receipt differs from the supplied source pin")
    ids = [e.id for e in c.evidence]
    required = {q.source_evidence_id, q.geometry_property_evidence_id}
    required.update(
        x
        for x in (
            q.calibration_evidence_id,
            q.clock_evidence_id,
            None if q.residual_scale_m is None else q.residual_scale_m.source_id,
        )
        if x is not None
    )
    metric = None if q.operating_point_calibration is None else q.operating_point_calibration.residual_metric
    if metric is not None:
        required.add(metric.evidence_id)
    if len(ids) != len(set(ids)) or required - set(ids):
        raise ValueError("source, geometry/property, clock, scale and fit evidence must exist uniquely")
    selected = next(e for e in c.evidence if e.id == q.source_evidence_id)
    csv_sha = next(s["sha256"] for s in source["sources"] if s["filename"] == "dataset.csv")
    if selected.sha256 != csv_sha:
        raise ValueError("selected source evidence does not bind the actual dataset CSV checksum")
    if metric is not None:
        metric_evidence = next(e for e in c.evidence if e.id == metric.evidence_id)
        if metric_evidence.sha256 is not None and metric_evidence.sha256 != metric.source_sha256:
            raise ValueError("supplied joint residual metric conflicts with its actual source evidence hash")
    return q, c


def _baseline(q):
    values = {"roughness_m": q.roughness_m, "minor_loss_coefficient": q.minor_loss_coefficient}
    active_fit = q.calibration or q.operating_point_calibration
    if active_fit:
        # Explicit supplied initial values are not the published fitted answers.
        values.update({p.id: p.initial for p in active_fit.parameters})
    return values


def build_friction_operating_points(q, source):
    """Preserve actual source row identities and apparatus conditions without timestamps."""
    csv_sha = next(s["sha256"] for s in source["sources"] if s["filename"] == "dataset.csv")
    return tuple(
        OperatingPoint(
            id=f"{csv_sha}:row:{row['source_row']}",
            state={
                "volume_flow_m3_s": row["volume_flow_m3_s"],
                "kinematic_viscosity_m2_s": row["kinematic_viscosity_m2_s"],
                "density_kg_m3": None
                if q.density_kg_m3_by_source_row is None
                else q.density_kg_m3_by_source_row[i],
                "physical_straight_length_m": q.physical_straight_length_m,
                "internal_diameter_m": q.internal_diameter_m,
                "gravity_m_s2": q.gravity_m_s2,
                "outlet_minus_inlet_elevation_m": q.outlet_minus_inlet_elevation_m,
            },
            state_units={
                "volume_flow_m3_s": "m^3/s",
                "kinematic_viscosity_m2_s": "m^2/s",
                "density_kg_m3": "kg/m^3",
                "physical_straight_length_m": "m",
                "internal_diameter_m": "m",
                "gravity_m_s2": "m/s^2",
                "outlet_minus_inlet_elevation_m": "m",
            },
            group_id=source["dataset_id"] + ":same_supplied_apparatus",
            evidence_ids=tuple(dict.fromkeys((q.source_evidence_id, q.geometry_property_evidence_id))),
            source_digests={s["filename"]: s["sha256"] for s in source["sources"]},
        )
        for i, row in enumerate(source["rows"])
    )


class _Forward:
    def __init__(self, q, source, engine):
        self.q, self.source, self.engine = q, source, engine
        active_fit = q.calibration or q.operating_point_calibration
        self.parameter_units = {} if active_fit is None else {p.id: p.unit for p in active_fit.parameters}

    def rows(self, parameters, indices, context):
        q = self.q
        values = _baseline(q) | parameters
        result = []
        for index in indices:
            row = self.source["rows"][index]
            rho = None if q.density_kg_m3_by_source_row is None else q.density_kg_m3_by_source_row[index]
            common = dict(
                diameter_m=q.internal_diameter_m,
                length_m=q.physical_straight_length_m,
                roughness_m=values["roughness_m"],
                volume_flow_m3_s=row["volume_flow_m3_s"],
                minor_loss_coefficient=values["minor_loss_coefficient"],
                outlet_minus_inlet_elevation_m=q.outlet_minus_inlet_elevation_m,
                gravity_m_s2=q.gravity_m_s2,
                transition_policy=q.transition_policy,
                friction_method=q.friction_method,
            )
            head_inputs = LiquidHeadLoss(**common, kinematic_viscosity_m2_s=row["kinematic_viscosity_m2_s"])
            head = liquid_head_loss(head_inputs)
            if head.status != "computed":
                return None
            predicted = head.values["required_inlet_minus_outlet_head_m"]
            native = None
            viscosity = pressure = mass_flow = pressure_work = potential_work = dissipated = crosscheck = None
            if rho is not None:
                inputs = LiquidPressureLoss(
                    **common, density_kg_m3=rho, viscosity_pa_s=rho * row["kinematic_viscosity_m2_s"]
                )
                native = self.engine.evaluate("hydraulics.liquid_loss", inputs, context)
                if native.status != "computed":
                    return None
                pressure_head = native.values["required_inlet_minus_outlet_pa"] / (rho * q.gravity_m_s2)
                crosscheck = pressure_head - predicted
                limit = q.head_crosscheck_absolute_tolerance_m + q.head_crosscheck_relative_tolerance * max(
                    abs(predicted), abs(pressure_head)
                )
                if abs(crosscheck) > limit:
                    raise ValueError(
                        "native pressure and density-free head owner outputs disagree beyond the numerical crosscheck budget"
                    )
                viscosity = inputs.viscosity_pa_s
                pressure = native.values["required_inlet_minus_outlet_pa"]
                mass_flow = rho * row["volume_flow_m3_s"]
                pressure_work = row["volume_flow_m3_s"] * pressure
                potential_work = row["volume_flow_m3_s"] * native.values["static_pressure_change_pa"]
                dissipated = native.values["dissipated_power_w"]
            observed = row["inlet_pressure_head_m"] - row["outlet_pressure_head_m"]
            result.append(
                {
                    "source_row": row["source_row"],
                    "observed_pressure_head_difference_m": observed,
                    "predicted_pressure_head_difference_m": predicted,
                    "residual_m": predicted - observed,
                    "density_kg_m3": rho,
                    "dynamic_viscosity_Pa_s": viscosity,
                    "pressure_difference_Pa": pressure,
                    "mass_flow_kg_s": mass_flow,
                    "pressure_work_rate_W": pressure_work,
                    "potential_energy_rate_W": potential_work,
                    "dissipated_power_W": dissipated,
                    "pressure_to_head_crosscheck_residual_m": crosscheck,
                    "head_native": head.model_dump(mode="json"),
                    "native": None if native is None else native.model_dump(mode="json"),
                    "density_dependent_quantities_computed": rho is not None,
                }
            )
        return result

    def __call__(self, parameters, times, context):
        lookup = {time: i for i, time in enumerate(self.q.source_row_time_s)}
        indices = [lookup[time] for time in times]
        rows = self.rows(parameters, indices, context)
        return Prediction(
            status="withheld" if rows is None else "computed",
            asset_id=self.q.asset_id,
            clock_id=self.q.clock_id,
            time_s=times,
            values={}
            if rows is None
            else {
                "pressure_head_difference_m": tuple(r["predicted_pressure_head_difference_m"] for r in rows)
            },
            units={"pressure_head_difference_m": "m"},
            provider_source_digest=file_digest(Path(__file__)),
            evaluation_digest=digest({"parameters": parameters, "times": times, "rows": rows}),
            diagnostics={
                "source_rows": [self.source["rows"][i]["source_row"] for i in indices],
                "source_record_digest": self.source["record_digest"],
                "same_apparatus": True,
            },
        )


class _PointForward(_Forward):
    def __init__(self, q, source, engine):
        super().__init__(q, source, engine)
        self.points = build_friction_operating_points(q, source)
        self.indices = {point.id: i for i, point in enumerate(self.points)}

    def __call__(self, parameters, requested_points, context):
        ids = tuple(point.id for point in requested_points)
        if len(set(ids)) != len(ids) or any(
            point.id not in self.indices or point != self.points[self.indices[point.id]]
            for point in requested_points
        ):
            raise ValueError("requested source operating-point identity/state/units differ")
        rows = self.rows(parameters, [self.indices[name] for name in ids], context)
        return OperatingPointPrediction(
            status="withheld" if rows is None else "computed",
            asset_id=self.q.asset_id,
            point_ids=ids,
            point_digests={p.id: digest(p) for p in requested_points},
            values={}
            if rows is None
            else {
                "pressure_head_difference_m": tuple(
                    row["predicted_pressure_head_difference_m"] for row in rows
                )
            },
            units={"pressure_head_difference_m": "m"},
            provider_source_digest=file_digest(Path(__file__)),
            evaluation_digest=digest({"parameters": parameters, "points": requested_points, "rows": rows}),
            diagnostics={
                "source_rows": [self.source["rows"][self.indices[name]]["source_row"] for name in ids],
                "source_record_digest": self.source["record_digest"],
                "same_apparatus": True,
                "clock_invented": False,
                "viscosity_origin": self.source["viscosity_origin"],
            },
        )


def evaluate_friction_study(request, context, *, source_root):
    source = read_friction_observations(source_root)
    q, c = _inputs(request, context, source)
    metric = None if q.operating_point_calibration is None else q.operating_point_calibration.residual_metric
    body = {
        "schema": "pipeline_integrity.friction_study.v1",
        "request": q.model_dump(mode="json"),
        "context": c.model_dump(mode="json"),
        "source_observations": source,
        "implementation_sha256": file_digest(Path(__file__)),
        "owner_source_sha256": {
            name: file_digest(Path(__file__).with_name(name + ".py"))
            for name in ("friction_data", "inverse", "registry", "builtins")
        },
        "runtime": {"python": platform.python_version()},
        "status": "needs_data",
        "forward_rows": None,
        "calibration_record": None,
        "calibration_status": "not_requested"
        if q.calibration is None and q.operating_point_calibration is None
        else "needs_data",
        "calibration_axis": "operating_points"
        if q.operating_point_calibration is not None
        else "actual_time"
        if q.calibration is not None
        else None,
        "operating_points": [
            point.model_dump(mode="json") for point in build_friction_operating_points(q, source)
        ]
        if q.operating_point_calibration is not None
        else None,
        "clock_invented": False,
        "covariance_inferred": False,
        "joint_residual_metric_supplied": metric is not None,
        "residual_metric_kind": None if metric is None else metric.kind,
        "scalar_residual_scale_role": "unit metadata only; value is not applied on top of joint whitening"
        if metric is not None
        else "not applied: explicit joint metric is missing"
        if q.calibration_error_model == "explicit_joint_residual_metric"
        else "explicit normalization",
        "same_apparatus_six_operating_points": True,
        "head_comparison_requires_density": False,
        "density_dependent_quantities_requested": q.density_kg_m3_by_source_row is not None,
        "published_fitted_parameters_used_as_truth": False,
        "independent_experiment_validation": False,
        "field_qualified": False,
        "issues": [],
    }

    def seal():
        return {**body, "record_digest": digest(body)}

    missing = []
    if q.source_record_digest is None:
        missing.append("reviewed source observation receipt pin")
    if not q.geometry_density_viscosity_and_local_loss_boundary_reviewed:
        missing.append(
            "reviewed physical length, kinematic viscosity, optional supplied density and distributed/fittings boundary"
        )
    if not q.pressure_head_reference_and_elevation_reviewed or q.head_interpretation == "unknown":
        missing.append("same-reference pressure-head and elevation interpretation")
    for field in (
        "physical_straight_length_m",
        "internal_diameter_m",
        "gravity_m_s2",
        "outlet_minus_inlet_elevation_m",
    ):
        if getattr(q, field) is None:
            missing.append(field)
    missing.extend(key for key, value in _baseline(q).items() if value is None)
    if missing:
        body["issues"] = missing
        return seal()
    from fluids import friction

    from . import create_registry

    body["runtime"].update({name: version(name) for name in ("fluids", "pydantic")})
    body["owner_source_sha256"]["models/hydraulics"] = file_digest(
        Path(__file__).parent / "models" / "hydraulics.py"
    )
    body["owner_source_sha256"]["fluids/friction"] = file_digest(Path(friction.__file__))
    forward = _Forward(q, source, None if q.density_kg_m3_by_source_row is None else create_registry())
    try:
        rows = forward.rows({}, range(6), c)
        if rows is None:
            body.update(
                status="out_of_scope", issues=["shared hydraulic owner withheld a baseline operating point"]
            )
            return seal()
        body.update(status="computed_diagnostic", forward_rows=rows)
        if q.calibration is None and q.operating_point_calibration is None:
            return seal()
        fit_missing = []
        if q.calibration_error_model == "explicit_joint_residual_metric" and metric is None:
            fit_missing.append("explicit operating-point joint residual metric")
        if metric is not None and not metric.applicability_reviewed:
            fit_missing.append("reviewed joint residual metric applicability")
        if metric is not None and next(e for e in c.evidence if e.id == metric.evidence_id).sha256 is None:
            fit_missing.append("known joint residual metric source evidence hash")
        if q.calibration is not None and (
            q.source_row_time_s is None
            or q.clock_evidence_id is None
            or not q.external_measurement_clock_reviewed
        ):
            fit_missing.append("actual external six-row measurement clock; source has no timestamps")
        if (
            q.residual_scale_m is None
            or q.calibration_error_model == "unknown"
            or not q.scale_bounds_and_same_apparatus_split_reviewed
            or q.calibration_evidence_id is None
        ):
            fit_missing.append(
                "reviewed external residual scale, parameter bounds and descriptive same-apparatus holdout"
            )
        if fit_missing:
            body.update(status="needs_data", issues=fit_missing)
            return seal()
        channel = ObservedChannel(
            id="pressure_head_difference_m",
            scale=q.residual_scale_m,
            values=tuple(r["observed_pressure_head_difference_m"] for r in rows),
            valid=(True,) * 6,
        )
        if q.operating_point_calibration is not None:
            observations = OperatingPointObservations(
                id=q.id,
                asset_id=q.asset_id,
                origin=c.origin,
                source_digests={s["filename"]: s["sha256"] for s in source["sources"]},
                points=build_friction_operating_points(q, source),
                channels=(channel,),
            )
            active_forward = _PointForward(q, source, forward.engine)
        else:
            observations = Observations(
                id=q.id,
                asset_id=q.asset_id,
                clock_id=q.clock_id,
                origin=c.origin,
                source_digests={s["filename"]: s["sha256"] for s in source["sources"]},
                time_s=q.source_row_time_s,
                channels=(channel,),
            )
            active_forward = forward
        body["runtime"]["scipy"] = version("scipy")
        from scipy.optimize import least_squares

        body["owner_source_sha256"]["scipy/least_squares"] = file_digest(Path(inspect.getfile(least_squares)))
        fitted = (
            fit_operating_points(observations, q.operating_point_calibration, c, active_forward)
            if q.operating_point_calibration is not None
            else fit_parameters(observations, q.calibration, c, active_forward)
        )
        body["calibration_record"] = fitted
        body["calibration_status"] = fitted["status"]
        if fitted["status"] != "fitted":
            body["status"] = "needs_data"
            body["issues"] = [
                "shared inverse owner did not establish a converged locally identifiable descriptive fit"
            ]
        digest(body)
    except (ArithmeticError, ValueError) as exc:
        body.update(status="out_of_scope", issues=[str(exc)])
    return seal()


def verify_friction_study_record(record, *, source_root, request=None, context=None):
    """Reread actual pinned files and rerun current owners; not historical authentication."""
    if not isinstance(record, dict) or record.get("record_digest") != digest(
        {k: v for k, v in record.items() if k != "record_digest"}
    ):
        raise ValueError("friction-study seal changed")
    q = FrictionStudyRequest.model_validate_json(json.dumps(record["request"], allow_nan=False))
    c = RunContext.model_validate_json(json.dumps(record["context"], allow_nan=False))
    if request is not None and q != request or context is not None and c != context:
        raise ValueError("friction-study caller request/context differs")
    if evaluate_friction_study(q, c, source_root=source_root) != record:
        raise ValueError("actual source, forward/fit owner results, units or qualification differ on replay")
    return True
