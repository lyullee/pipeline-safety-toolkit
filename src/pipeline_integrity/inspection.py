"""Registered repeat-inspection correspondence and reviewed handoff to the existing material engine."""

import hashlib
import json
import math
import platform
from importlib.metadata import version
from pathlib import Path
from typing import Literal

import numpy as np
from pydantic import Field, model_validator

from .contracts import Contract, Name, Nonnegative, Positive, RunContext, digest


class InspectionFeature(Contract):
    feature_id: Name
    joint_id: Name
    joint_length_m: Positive
    axial_position_m: float
    circumferential_angle_rad: float = Field(ge=0, lt=2 * math.pi)
    nominal_wall_m: Positive
    maximum_depth_m: Nonnegative
    length_m: Nonnegative | None = None
    width_m: Nonnegative | None = None
    remaining_ligament_error_bound_m: Nonnegative | None = None
    source_row: int = Field(ge=2)

    @property
    def nominal_ligament_m(self):
        return self.nominal_wall_m - self.maximum_depth_m


class InspectionRun(Contract):
    run_id: Name
    asset_id: Name
    coordinate_frame_id: Name
    source_id: Name
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    reported_time_label: Name
    inspection_time_s: Nonnegative | None = None
    clock_id: Name | None = None
    time_basis_evidence_id: Name | None = None
    features: tuple[InspectionFeature, ...]
    origin: Literal["measured", "laboratory", "synthetic", "simulation", "user_declared"]
    source_scan_complete: bool
    excluded_selected_source_rows: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def distinct_ids_and_timing(self):
        if len({feature.feature_id for feature in self.features}) != len(self.features):
            raise ValueError("feature identifiers must be unique within the inspection run")
        timing = (self.inspection_time_s, self.clock_id, self.time_basis_evidence_id)
        if any(v is None for v in timing) and not all(v is None for v in timing):
            raise ValueError("an elapsed inspection time and its basis evidence must be supplied together")
        return self


class InspectionRegistration(Contract):
    registration_id: Name
    baseline_run_id: Name
    followup_run_id: Name
    baseline_frame_id: Name
    followup_frame_id: Name
    joint_map: dict[str, str]
    axial_mode: Literal["joint_distance", "fraction_of_joint_length"]
    axial_offset_m: float
    circumferential_offset_rad: float
    provenance: Name
    review_evidence_id: Name | None = None

    @model_validator(mode="after")
    def one_to_one_joints(self):
        if not self.joint_map or any(not k.strip() or not v.strip() for k, v in self.joint_map.items()):
            raise ValueError("provide a nonempty explicit joint correspondence")
        if len(set(self.joint_map.values())) != len(self.joint_map):
            raise ValueError("joint correspondence must be one-to-one")
        if self.baseline_run_id == self.followup_run_id:
            raise ValueError("repeat inspection requires distinct runs")
        return self


class FeatureMatchPolicy(Contract):
    axial_gate_m: Positive
    angular_gate_rad: Positive = Field(le=math.pi)
    unmatched_cost: Positive
    ambiguity_gap: Nonnegative
    maximum_matrix_cells: int = Field(default=2000000, ge=1, le=100000000)


class FeatureCorrespondence(Contract):
    baseline_feature_id: Name
    followup_feature_id: Name
    cost: Nonnegative
    axial_difference_m: float
    angular_difference_rad: float
    local_alternative_gap: float
    ambiguous: bool


class InspectionMatches(Contract):
    schema_version: Literal["pipeline_integrity.inspection_matches.v1"] = (
        "pipeline_integrity.inspection_matches.v1"
    )
    asset_id: Name
    algorithm_version: Literal["1.0.0"] = "1.0.0"
    implementation_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    runtime_versions: dict[str, str]
    baseline_run_digest: Name
    followup_run_digest: Name
    registration: InspectionRegistration
    policy: FeatureMatchPolicy
    proposals: tuple[FeatureCorrespondence, ...]
    unmatched_baseline_ids: tuple[str, ...]
    unmatched_followup_ids: tuple[str, ...]
    unregistered_baseline_ids: tuple[str, ...]
    unregistered_followup_ids: tuple[str, ...]
    candidate_edges: int
    input_exclusions_present: bool
    matching_is_qualified_identity_or_probability: Literal[False] = False
    result_digest: Name

    def check_integrity(self):
        if digest(self.model_dump(mode="json", exclude={"result_digest"})) != self.result_digest:
            raise ValueError("the correspondence result changed after calculation")


def match_inspections(
    baseline: InspectionRun,
    followup: InspectionRun,
    registration: InspectionRegistration,
    policy: FeatureMatchPolicy,
):
    """Minimise gated positional cost with explicit unmatched choices, without using observed depth as a target."""
    from scipy.optimize import linear_sum_assignment

    if baseline.asset_id != followup.asset_id or (
        baseline.run_id,
        followup.run_id,
        baseline.coordinate_frame_id,
        followup.coordinate_frame_id,
    ) != (
        registration.baseline_run_id,
        registration.followup_run_id,
        registration.baseline_frame_id,
        registration.followup_frame_id,
    ):
        raise ValueError("inspection assets, runs or coordinate frames disagree with the registration")
    if not baseline.source_scan_complete or not followup.source_scan_complete:
        raise ValueError("complete source scans are required to label unmatched observations")
    proposals, used_left, used_right, edge_count = [], set(), set(), 0
    for left_joint, right_joint in sorted(registration.joint_map.items()):
        left = sorted((f for f in baseline.features if f.joint_id == left_joint), key=lambda f: f.feature_id)
        right = sorted(
            (f for f in followup.features if f.joint_id == right_joint), key=lambda f: f.feature_id
        )
        n, m = len(left), len(right)
        if not n or not m:
            continue
        if n * (m + n) > policy.maximum_matrix_cells:
            raise ValueError(
                "joint assignment exceeds the declared matrix budget; partition reviewed geometry"
            )
        cost = np.full((n, m + n), np.inf)
        cost[:, m:] = policy.unmatched_cost
        differences = {}
        for i, a in enumerate(left):
            for j, b in enumerate(right):
                axial = a.axial_position_m
                if registration.axial_mode == "fraction_of_joint_length":
                    axial = axial / a.joint_length_m * b.joint_length_m
                dx = b.axial_position_m - (axial + registration.axial_offset_m)
                da = math.remainder(
                    b.circumferential_angle_rad
                    - a.circumferential_angle_rad
                    - registration.circumferential_offset_rad,
                    2 * math.pi,
                )
                if abs(dx) <= policy.axial_gate_m and abs(da) <= policy.angular_gate_rad:
                    cost[i, j] = (dx / policy.axial_gate_m) ** 2 + (da / policy.angular_gate_rad) ** 2
                    differences[(i, j)] = (dx, da)
                    edge_count += 1
        rows, columns = linear_sum_assignment(cost)
        for i, j in zip(rows, columns, strict=True):
            if j >= m:
                continue
            chosen = float(cost[i, j])
            alternatives = [
                policy.unmatched_cost,
                *(float(v) for k, v in enumerate(cost[i, :m]) if k != j),
                *(float(v) for k, v in enumerate(cost[:, j]) if k != i),
            ]
            gap = min(alternatives) - chosen
            dx, da = differences[(i, j)]
            proposals.append(
                FeatureCorrespondence(
                    baseline_feature_id=left[i].feature_id,
                    followup_feature_id=right[j].feature_id,
                    cost=chosen,
                    axial_difference_m=dx,
                    angular_difference_rad=da,
                    local_alternative_gap=gap,
                    ambiguous=gap <= policy.ambiguity_gap,
                )
            )
            used_left.add(left[i].feature_id)
            used_right.add(right[j].feature_id)
    body = {
        "schema_version": "pipeline_integrity.inspection_matches.v1",
        "asset_id": baseline.asset_id,
        "algorithm_version": "1.0.0",
        "implementation_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "runtime_versions": {
            "python": platform.python_version(),
            **{name: version(name) for name in ("numpy", "scipy", "pydantic")},
        },
        "baseline_run_digest": digest(baseline),
        "followup_run_digest": digest(followup),
        "registration": registration.model_dump(mode="json"),
        "policy": policy.model_dump(mode="json"),
        "proposals": [p.model_dump(mode="json") for p in proposals],
        "unmatched_baseline_ids": sorted(
            f.feature_id
            for f in baseline.features
            if f.joint_id in registration.joint_map and f.feature_id not in used_left
        ),
        "unmatched_followup_ids": sorted(
            f.feature_id
            for f in followup.features
            if f.joint_id in registration.joint_map.values() and f.feature_id not in used_right
        ),
        "unregistered_baseline_ids": sorted(
            f.feature_id for f in baseline.features if f.joint_id not in registration.joint_map
        ),
        "unregistered_followup_ids": sorted(
            f.feature_id for f in followup.features if f.joint_id not in registration.joint_map.values()
        ),
        "candidate_edges": edge_count,
        "input_exclusions_present": bool(
            baseline.excluded_selected_source_rows or followup.excluded_selected_source_rows
        ),
        "matching_is_qualified_identity_or_probability": False,
    }
    return InspectionMatches.model_validate_json(
        json.dumps({**body, "result_digest": digest(body)}, allow_nan=False)
    )


class CorrespondenceReview(Contract):
    match_result_digest: Name
    baseline_feature_id: Name
    followup_feature_id: Name
    reviewer_id: Name
    evidence_id: Name
    approved: bool


class InspectionTrack(Contract):
    track_id: Name
    feature_id_by_run: dict[str, str]
    reviews: tuple[CorrespondenceReview, ...]
    observation_meaning: Literal["nominal_ligament_at_reported_feature_maximum"] = (
        "nominal_ligament_at_reported_feature_maximum"
    )


def evaluate_inspection_track(
    track: InspectionTrack,
    runs: tuple[InspectionRun, ...],
    matches: tuple[InspectionMatches, ...],
    material_client,
    context: RunContext,
    *,
    method="theil_sen",
    nominal_wall_tolerance_m=1e-9,
):
    """Use reviewed correspondences and bounded observation errors before calling the material trend model."""
    if (
        method not in {"ols", "theil_sen"}
        or isinstance(nominal_wall_tolerance_m, bool)
        or (not math.isfinite(nominal_wall_tolerance_m) or nominal_wall_tolerance_m < 0)
    ):
        raise ValueError("provide an inspected trend method and finite nonnegative nominal-wall tolerance")
    if len(runs) < 3 or len({run.run_id for run in runs}) != len(runs):
        raise ValueError("the material trend requires at least three distinct ordered inspection runs")
    if any(run.asset_id != context.asset_id for run in runs) or set(track.feature_id_by_run) != {
        r.run_id for r in runs
    }:
        raise ValueError("track, context and inspection asset/run identities disagree")
    if any(run.origin != context.origin for run in runs):
        raise ValueError("retain the actual observation origin in the track context")
    evidence = {item.id: item for item in context.evidence}
    if len(evidence) != len(context.evidence):
        raise ValueError("context evidence identifiers must be unique")
    features = []
    for run in runs:
        found = [f for f in run.features if f.feature_id == track.feature_id_by_run[run.run_id]]
        if len(found) != 1:
            raise ValueError("a track observation is absent from its declared run")
        features.append(found[0])
    issues = []
    for run, feature in zip(runs, features, strict=True):
        source_evidence = evidence.get(run.source_id)
        if source_evidence is None:
            issues.append(f"inspection_source_evidence_missing:{run.run_id}")
        elif source_evidence.sha256 != run.source_sha256:
            issues.append(f"inspection_source_checksum_mismatch:{run.run_id}")
        elif source_evidence.origin != run.origin:
            issues.append(f"inspection_source_origin_mismatch:{run.run_id}")
        if run.inspection_time_s is None:
            issues.append(f"missing_inspection_time:{run.run_id}")
        elif run.clock_id != context.clock_id:
            issues.append(f"inspection_clock_mismatch:{run.run_id}")
        if run.time_basis_evidence_id is None or run.time_basis_evidence_id not in evidence:
            issues.append(f"inspection_time_basis_evidence_missing:{run.run_id}")
        if feature.remaining_ligament_error_bound_m is None:
            issues.append(f"missing_ligament_error_bound:{run.run_id}")
        if feature.nominal_ligament_m <= 0:
            issues.append(f"nonpositive_nominal_ligament:{run.run_id}")
        if not run.source_scan_complete:
            issues.append(f"incomplete_source_scan:{run.run_id}")
        if run.excluded_selected_source_rows:
            issues.append(f"excluded_selected_observations:{run.run_id}")
        if abs(feature.nominal_wall_m - features[0].nominal_wall_m) > nominal_wall_tolerance_m:
            issues.append(f"nominal_wall_basis_changed:{run.run_id}")
    if all(run.inspection_time_s is not None for run in runs) and any(
        b.inspection_time_s <= a.inspection_time_s for a, b in zip(runs, runs[1:], strict=False)
    ):
        raise ValueError("inspection times must strictly increase in the supplied order")
    for i, (a, b) in enumerate(zip(runs, runs[1:], strict=False)):
        pair = [
            m for m in matches if m.baseline_run_digest == digest(a) and m.followup_run_digest == digest(b)
        ]
        if len(pair) != 1:
            issues.append(f"missing_or_ambiguous_match_result:{a.run_id}:{b.run_id}")
            continue
        match = pair[0]
        match.check_integrity()
        registration_evidence_id = match.registration.review_evidence_id
        if registration_evidence_id is None or registration_evidence_id not in evidence:
            issues.append(f"registration_review_evidence_missing:{a.run_id}:{b.run_id}")
        ids = features[i].feature_id, features[i + 1].feature_id
        proposed = [p for p in match.proposals if (p.baseline_feature_id, p.followup_feature_id) == ids]
        reviews = [
            r
            for r in track.reviews
            if r.match_result_digest == match.result_digest
            and (r.baseline_feature_id, r.followup_feature_id) == ids
        ]
        if not proposed or len(reviews) != 1 or not reviews[0].approved:
            issues.append(f"correspondence_not_reviewed:{a.run_id}:{b.run_id}")
        elif reviews[0].evidence_id not in evidence:
            issues.append(f"correspondence_review_evidence_missing:{a.run_id}:{b.run_id}")
    body = {
        "schema": "pipeline_integrity.inspection_track.v1",
        "implementation_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "track": track.model_dump(mode="json"),
        "context": context.model_dump(mode="json"),
        "run_digests": [digest(r) for r in runs],
        "observations": [
            {
                "run_id": run.run_id,
                "feature": feature.model_dump(mode="json"),
                "source_id": run.source_id,
                "source_sha256": run.source_sha256,
                "origin": run.origin,
                "time_s": run.inspection_time_s,
                "clock_id": run.clock_id,
                "time_basis_evidence_id": run.time_basis_evidence_id,
            }
            for run, feature in zip(runs, features, strict=True)
        ],
        "issues": issues,
        "status": "needs_data" if issues else "computed",
        "source_observations_are_nominal_ligament_estimates": True,
        "field_validation_claim": False,
    }
    if issues:
        return body
    parameters = {
        "time_s": [r.inspection_time_s for r in runs],
        "thickness_m": [f.nominal_ligament_m for f in features],
        "method": method,
    }
    result = material_client.evaluate(
        "corrosion.thickness_trend", parameters, context, location_id=track.track_id
    )
    bounds = []
    for a, b, fa, fb in zip(runs, runs[1:], features, features[1:], strict=False):
        dt = b.inspection_time_s - a.inspection_time_s
        loss = fa.nominal_ligament_m - fb.nominal_ligament_m
        error = fa.remaining_ligament_error_bound_m + fb.remaining_ligament_error_bound_m
        bounds.append(
            {
                "baseline_run_id": a.run_id,
                "followup_run_id": b.run_id,
                "rate_bounds_m_s": [(loss - error) / dt, (loss + error) / dt],
                "bounds_meaning": "worst_case_declared_measurement_bounds_not_confidence_interval",
            }
        )
    body.update(
        status=result.status,
        parameters=parameters,
        external_result=result.model_dump(mode="json"),
        adjacent_measurement_rate_bounds=bounds,
    )
    return body
