"""Synthetic identity and error-bound gates for the inspection/profile handoff."""

import runpy

import pytest

from pipeline_integrity.contracts import EvidenceRef
from pipeline_integrity.inspection import InspectionFeature, InspectionRun
from pipeline_integrity.inspection_profile_bridge import bind_inspection_profile

PROFILE_CASE = runpy.run_path("examples/profile_capacity.py")["make_case"]


def case():
    request, base_context = PROFILE_CASE()
    source_sha = "a" * 64
    evidence = (
        EvidenceRef(
            id="authored",
            citation="Original hypothetical profile and provider inputs",
            origin="synthetic",
            license_id="MIT",
        ),
        EvidenceRef(
            id="authored_inspection",
            citation="Synthetic inspection source with declared checksum",
            origin="synthetic",
            sha256=source_sha,
            license_id="MIT",
        ),
        EvidenceRef(
            id="synthetic_clock",
            citation="Synthetic inspection clock basis",
            origin="synthetic",
            license_id="MIT",
        ),
    )
    context = base_context.model_copy(update={"evidence": evidence})
    request = request.model_copy(
        update={"evidence_ids": ("authored", "authored_inspection", "synthetic_clock")}
    )
    features = tuple(
        InspectionFeature(
            feature_id=point_id,
            joint_id="joint-1",
            joint_length_m=1.0,
            axial_position_m=position,
            circumferential_angle_rad=0.5,
            nominal_wall_m=0.01,
            maximum_depth_m=0.002,
            remaining_ligament_error_bound_m=0.0001,
            source_row=index + 2,
        )
        for index, (point_id, position) in enumerate(
            zip(request.source_point_ids, (0.0, 0.5, 1.0), strict=True)
        )
    )
    run = InspectionRun(
        run_id="inspection-1",
        asset_id=context.asset_id,
        coordinate_frame_id="frame-1",
        source_id="authored_inspection",
        source_sha256=source_sha,
        reported_time_label="campaign 1",
        inspection_time_s=10.0,
        clock_id=context.clock_id,
        time_basis_evidence_id="synthetic_clock",
        features=features,
        origin="synthetic",
        source_scan_complete=True,
    )
    request = request.model_copy(
        update={"parameters": request.parameters | {"measured_remaining_thickness_m": [0.0079] * 3}}
    )
    return run, request, context


def test_inspection_points_bind_only_at_conservative_declared_thickness():
    run, request, context = case()
    binding = bind_inspection_profile(run, request, context)
    assert binding["point_ids_in_profile_order"] == list(request.source_point_ids)
    assert binding["conservative_remaining_thickness_m"] == [0.0079] * 3
    assert not binding["scan_coverage_inferred"]
    assert not binding["field_or_standard_qualification"]


def test_unresolved_error_and_nonconservative_thickness_are_rejected():
    run, request, context = case()
    feature = run.features[0].model_copy(update={"remaining_ligament_error_bound_m": None})
    changed = run.model_copy(update={"features": (feature, *run.features[1:])})
    with pytest.raises(ValueError, match="error bound is unresolved"):
        bind_inspection_profile(changed, request, context)
    parameters = request.parameters | {"measured_remaining_thickness_m": [0.008] * 3}
    request = request.model_copy(update={"parameters": parameters})
    with pytest.raises(ValueError, match="worst-case error bound"):
        bind_inspection_profile(run, request, context)


def test_source_hash_clock_and_point_identity_must_match():
    run, request, context = case()
    with pytest.raises(ValueError, match="source hash"):
        bind_inspection_profile(run.model_copy(update={"source_sha256": "b" * 64}), request, context)
    with pytest.raises(ValueError, match="clocks differ"):
        bind_inspection_profile(run, request.model_copy(update={"clock_id": "other_clock"}), context)
    with pytest.raises(ValueError, match="point IDs"):
        bind_inspection_profile(run, request.model_copy(update={"source_point_ids": ("point-a",)}), context)


def test_incomplete_or_excluded_scan_is_rejected():
    run, request, context = case()
    with pytest.raises(ValueError, match="complete source scan"):
        bind_inspection_profile(run.model_copy(update={"source_scan_complete": False}), request, context)
    with pytest.raises(ValueError, match="complete source scan"):
        bind_inspection_profile(run.model_copy(update={"excluded_selected_source_rows": 1}), request, context)
