"""Evidence-gated handoff from recorded inspection indications to the owner capacity model."""
from __future__ import annotations
from .contracts import RunContext, digest, json_value
from .inspection import InspectionRun
from .profile_capacity import ProfileCapacityRequest, evaluate_profile_capacity, verify_profile_capacity_record

def bind_inspection_profile(run: InspectionRun, request: ProfileCapacityRequest, context: RunContext):
    """Validate inspection identity and worst-case error before using the owner profile model.

    This does not infer scan coverage. Full-extent and applicability reviews remain required
    on the native profile request; the structural owner remains responsible for capacity.
    """
    if not isinstance(run, InspectionRun) or not isinstance(request, ProfileCapacityRequest):
        raise TypeError("InspectionRun and ProfileCapacityRequest are required")
    if not isinstance(context, RunContext):
        raise TypeError("RunContext is required")
    if (run.asset_id, run.origin) != (context.asset_id, context.origin):
        raise ValueError("inspection and run context asset/origin differ")
    if run.clock_id != request.clock_id or context.clock_id != request.clock_id:
        raise ValueError("inspection, profile request and context clocks differ")
    if run.source_id != request.source_observation_id:
        raise ValueError("profile source observation must identify the inspection source")
    if run.inspection_time_s is None or run.clock_id is None or run.time_basis_evidence_id is None:
        raise ValueError("inspection elapsed time and clock basis evidence are required")
    if not run.source_scan_complete or run.excluded_selected_source_rows:
        raise ValueError("a complete source scan without excluded selected rows is required")
    evidence = {item.id: item for item in context.evidence}
    if len(evidence) != len(context.evidence):
        raise ValueError("context evidence identifiers must be unique")
    source = evidence.get(run.source_id)
    if source is None or source.sha256 != run.source_sha256:
        raise ValueError("inspection source hash must match selected context evidence")
    if run.time_basis_evidence_id not in evidence:
        raise ValueError("inspection time basis evidence is absent from context")
    by_id = {feature.feature_id: feature for feature in run.features}
    ids = tuple(request.source_point_ids)
    if not ids or len(ids) != len(set(ids)) or set(ids) != set(by_id):
        raise ValueError("profile point IDs must map exactly to selected inspection features")
    p = request.parameters
    if not isinstance(p, dict):
        raise ValueError("reviewed profile parameters are required")
    positions, measured = p.get("axial_position_m"), p.get("measured_remaining_thickness_m")
    if not isinstance(positions, (list, tuple)) or not isinstance(measured, (list, tuple)):
        raise ValueError("profile axial positions and thicknesses must be explicit arrays")
    if len(positions) != len(ids) or len(measured) != len(ids):
        raise ValueError("profile arrays must align with every source point ID")
    reference_wall = p.get("reference_wall_thickness_m")
    if reference_wall is None:
        raise ValueError("reference wall thickness is unresolved")
    conservative, source_features = [], []
    for i, feature_id in enumerate(ids):
        feature = by_id[feature_id]
        error = feature.remaining_ligament_error_bound_m
        if error is None:
            raise ValueError(f"inspection error bound is unresolved for {feature_id}")
        if feature.nominal_wall_m != reference_wall:
            raise ValueError("inspection nominal wall differs from the reviewed profile basis")
        if positions[i] != feature.axial_position_m:
            raise ValueError("profile coordinate differs from its inspection source feature")
        lower = feature.nominal_ligament_m - error
        if lower <= 0:
            raise ValueError("declared measurement bound reaches zero remaining thickness")
        if measured[i] != lower:
            raise ValueError("profile thickness must equal nominal ligament minus worst-case error bound")
        conservative.append(lower)
        source_features.append({
            "feature_id": feature.feature_id,
            "source_row": feature.source_row,
            "axial_position_m": feature.axial_position_m,
            "nominal_wall_m": feature.nominal_wall_m,
            "maximum_depth_m": feature.maximum_depth_m,
            "remaining_ligament_error_bound_m": error,
        })
    binding = {
        "schema": "pipeline_integrity.inspection_profile_binding.v1",
        "inspection_run_digest": digest(run),
        "profile_request_digest": digest(request),
        "context_digest": digest(context),
        "source_id": run.source_id,
        "source_sha256": run.source_sha256,
        "time_basis_evidence_id": run.time_basis_evidence_id,
        "inspection_time_s": run.inspection_time_s,
        "point_ids_in_profile_order": list(ids),
        "source_features_in_profile_order": source_features,
        "conservative_remaining_thickness_m": conservative,
        "uncertainty_treatment": "subtract declared worst-case bound; no probability or covariance propagation",
        "scan_coverage_inferred": False,
        "capacity_calculation_owner": "structural.profile_capacity",
        "field_or_standard_qualification": False,
    }
    return {**binding, "binding_digest": digest(binding)}

def evaluate_inspection_profile_capacity(
    run, request, context, *, client=None, pinned_job=None, owner_source_root=None
):
    """Bind reviewed inspection points, then delegate to the existing structural owner API."""
    binding = bind_inspection_profile(run, request, context)
    record = evaluate_profile_capacity(
        request, context, client=client, pinned_job=pinned_job, owner_source_root=owner_source_root
    )
    body = {key: value for key, value in record.items() if key != "record_digest"}
    body["inspection_binding"] = binding
    return {**json_value(body), "record_digest": digest(body)}

def verify_inspection_profile_capacity_record(record):
    """Verify the native owner record plus inspection source/point/error binding."""
    verify_profile_capacity_record(record)
    b = record.get("inspection_binding")
    if not isinstance(b, dict) or b.get("schema") != "pipeline_integrity.inspection_profile_binding.v1":
        raise ValueError("inspection binding is missing")
    if b.get("binding_digest") != digest({k: v for k, v in b.items() if k != "binding_digest"}):
        raise ValueError("inspection binding digest changed")
    if b.get("field_or_standard_qualification") is not False or b.get("scan_coverage_inferred") is not False:
        raise ValueError("inspection binding qualification or coverage meaning changed")
    if b.get("capacity_calculation_owner") != "structural.profile_capacity":
        raise ValueError("capacity calculation owner changed")
    request = ProfileCapacityRequest.model_validate(record["request"])
    context = RunContext.model_validate(record["context"])
    if b.get("profile_request_digest") != digest(request) or b.get("context_digest") != digest(context):
        raise ValueError("inspection binding belongs to a different request or context")
    if b.get("source_id") != request.source_observation_id:
        raise ValueError("inspection source identity differs from profile request")
    source = {e.id: e for e in context.evidence}.get(b["source_id"])
    if source is None or source.sha256 != b.get("source_sha256"):
        raise ValueError("inspection source checksum differs from context evidence")
    ids, features, p = request.source_point_ids, b.get("source_features_in_profile_order"), request.parameters
    if b.get("point_ids_in_profile_order") != list(ids) or not isinstance(features, list) or not isinstance(p, dict):
        raise ValueError("inspection source points do not match the profile request")
    if len(features) != len(ids):
        raise ValueError("inspection feature evidence count differs from profile points")
    for i, f in enumerate(features):
        lower = f.get("nominal_wall_m") - f.get("maximum_depth_m") - f.get("remaining_ligament_error_bound_m")
        if (f.get("feature_id") != ids[i] or f.get("axial_position_m") != p["axial_position_m"][i]
                or f.get("nominal_wall_m") != p["reference_wall_thickness_m"]
                or lower != p["measured_remaining_thickness_m"][i]
                or lower != b["conservative_remaining_thickness_m"][i]):
            raise ValueError("inspection feature evidence no longer supports the conservative profile input")
    return True
