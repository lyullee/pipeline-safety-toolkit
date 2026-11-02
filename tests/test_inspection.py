import math

import pytest

from pipeline_integrity.adapters import ExternalResult, MaterialClient
from pipeline_integrity.contracts import EvidenceRef, RunContext, digest
from pipeline_integrity.ili import ILILoadRequest, ili_feature_from_row
from pipeline_integrity.inspection import (
    CorrespondenceReview,
    FeatureMatchPolicy,
    InspectionFeature,
    InspectionRegistration,
    InspectionRun,
    InspectionTrack,
    evaluate_inspection_track,
    match_inspections,
)


def run(index, *, features=None, **changes):
    feature = InspectionFeature(
        feature_id=f"pit-{index}",
        joint_id="14",
        joint_length_m=10.0,
        axial_position_m=1.0 + index * 0.05,
        circumferential_angle_rad=(2 * math.pi - 0.02 + index * 0.04) % (2 * math.pi),
        nominal_wall_m=0.01,
        maximum_depth_m=0.001 + index * 0.0001,
        remaining_ligament_error_bound_m=0.00001,
        source_row=2,
    )
    return InspectionRun(
        **{
            "run_id": f"run-{index}",
            "asset_id": "line",
            "coordinate_frame_id": f"frame-{index}",
            "source_id": "synthetic_ground_truth",
            "source_sha256": "a" * 64,
            "reported_time_label": str(index),
            "inspection_time_s": index * 10.0,
            "clock_id": "inspection_elapsed",
            "time_basis_evidence_id": "synthetic_clock",
            "features": (feature,) if features is None else tuple(features),
            "origin": "synthetic",
            "source_scan_complete": True,
            **changes,
        }
    )


def registration(a, b, **changes):
    return InspectionRegistration(
        **{
            "registration_id": "synthetic_affine_truth",
            "baseline_run_id": a.run_id,
            "followup_run_id": b.run_id,
            "baseline_frame_id": a.coordinate_frame_id,
            "followup_frame_id": b.coordinate_frame_id,
            "joint_map": {"14": "14"},
            "axial_mode": "joint_distance",
            "axial_offset_m": 0.05,
            "circumferential_offset_rad": 0.04,
            "provenance": "known_synthetic_coordinate_transform",
            "review_evidence_id": "synthetic_truth",
            **changes,
        }
    )


def policy(**changes):
    return FeatureMatchPolicy(
        **{
            "axial_gate_m": 0.1,
            "angular_gate_rad": 0.1,
            "unmatched_cost": 2.5,
            "ambiguity_gap": 0.05,
            **changes,
        }
    )


def test_registered_motion_wraps_angles_and_does_not_fit_observed_depth():
    a, b = run(0), run(1)
    expected = match_inspections(a, b, registration(a, b), policy())
    assert len(expected.proposals) == 1 and expected.proposals[0].cost == pytest.approx(0.0, abs=1e-20)
    assert expected.proposals[0].angular_difference_rad == pytest.approx(0.0, abs=1e-14)
    extreme = b.model_copy(
        update={"features": (b.features[0].model_copy(update={"maximum_depth_m": 0.008}),)}
    )
    changed = match_inspections(a, extreme, registration(a, extreme), policy())
    assert changed.proposals == expected.proposals
    assert not expected.matching_is_qualified_identity_or_probability


def test_new_missing_and_unregistered_observations_are_not_forced_into_pairs():
    a, b = run(0), run(1)
    extra = b.features[0].model_copy(update={"feature_id": "extra", "axial_position_m": 7.0})
    outside = a.features[0].model_copy(update={"feature_id": "unregistered", "joint_id": "99"})
    a = a.model_copy(update={"features": (*a.features, outside)})
    b = b.model_copy(update={"features": (*b.features, extra)})
    matched = match_inspections(a, b, registration(a, b), policy())
    assert matched.unmatched_followup_ids == ("extra",)
    assert matched.unregistered_baseline_ids == ("unregistered",) and not matched.unmatched_baseline_ids
    assert len(matched.proposals) == 1


def test_tied_split_candidates_are_ambiguous_and_assignment_is_one_to_one():
    a, b = run(0), run(1)
    duplicate = b.features[0].model_copy(update={"feature_id": "duplicate"})
    b = b.model_copy(update={"features": (*b.features, duplicate)})
    matched = match_inspections(a, b, registration(a, b), policy())
    assert len(matched.proposals) == 1 and matched.proposals[0].ambiguous
    assert matched.proposals[0].local_alternative_gap == 0 and len(matched.unmatched_followup_ids) == 1
    assert matched.candidate_edges == 2


def test_matching_rejects_incomplete_scans_wrong_frames_and_unbounded_matrices():
    a, b = run(0), run(1)
    with pytest.raises(ValueError, match="complete"):
        match_inspections(
            a.model_copy(update={"source_scan_complete": False}), b, registration(a, b), policy()
        )
    with pytest.raises(ValueError, match="frames"):
        match_inspections(a, b, registration(a, b, followup_frame_id="wrong"), policy())
    with pytest.raises(ValueError, match="budget"):
        match_inspections(a, b, registration(a, b), policy(maximum_matrix_cells=1))


def reviewed_track(runs):
    matches, reviews = [], []
    for a, b in zip(runs, runs[1:], strict=False):
        matched = match_inspections(a, b, registration(a, b), policy())
        matches.append(matched)
        reviews.append(
            CorrespondenceReview(
                match_result_digest=matched.result_digest,
                baseline_feature_id=a.features[0].feature_id,
                followup_feature_id=b.features[0].feature_id,
                reviewer_id="synthetic_fixture",
                evidence_id="known_feature_identity",
                approved=True,
            )
        )
    return InspectionTrack(
        track_id="declared_track",
        feature_id_by_run={r.run_id: r.features[0].feature_id for r in runs},
        reviews=tuple(reviews),
    ), tuple(matches)


class MaterialSpy:
    def __init__(self):
        self.calls = []

    def evaluate(self, model, parameters, context, *, location_id):
        self.calls.append((model, parameters, context, location_id))
        return ExternalResult(
            owner="matintegrity",
            owner_schema="matintegrity.assessment.v1",
            status="computed",
            asset_id=context.asset_id,
            payload=parameters,
            payload_digest=digest(parameters),
        )


def context():
    return RunContext(
        asset_id="line",
        scenario_id="synthetic_repeat",
        clock_id="inspection_elapsed",
        origin="synthetic",
        evidence=(
            EvidenceRef(
                id="synthetic_ground_truth",
                citation="Synthetic fixture source",
                origin="synthetic",
                sha256="a" * 64,
            ),
            EvidenceRef(
                id="synthetic_clock",
                citation="Synthetic fixture clock basis",
                origin="synthetic",
            ),
            EvidenceRef(
                id="synthetic_truth",
                citation="Synthetic fixture registration review",
                origin="synthetic",
            ),
            EvidenceRef(
                id="known_feature_identity",
                citation="Synthetic fixture correspondence review",
                origin="synthetic",
            ),
        ),
    )


def test_reviewed_track_sends_ligament_estimates_and_distinguishes_bounded_error_from_confidence():
    runs = tuple(run(i) for i in range(3))
    track, matches = reviewed_track(runs)
    spy = MaterialSpy()
    result = evaluate_inspection_track(track, runs, matches, spy, context())
    assert result["status"] == "computed" and len(spy.calls) == 1
    assert spy.calls[0][0] == "corrosion.thickness_trend"
    assert spy.calls[0][1]["thickness_m"] == pytest.approx([0.009, 0.0089, 0.0088])
    assert result["adjacent_measurement_rate_bounds"][0]["rate_bounds_m_s"] == pytest.approx([8e-6, 12e-6])
    assert not result["field_validation_claim"]


def test_unknown_time_error_or_unreviewed_identity_does_not_call_growth_engine():
    runs = tuple(run(i, inspection_time_s=None, clock_id=None, time_basis_evidence_id=None) for i in range(3))
    track, matches = reviewed_track(runs)
    track = track.model_copy(update={"reviews": ()})
    spy = MaterialSpy()
    result = evaluate_inspection_track(track, runs, matches, spy, context())
    assert result["status"] == "needs_data" and not spy.calls
    assert any(issue.startswith("missing_inspection_time") for issue in result["issues"])
    assert any(issue.startswith("correspondence_not_reviewed") for issue in result["issues"])
    matches[0].registration.joint_map["14"] = "different"
    with pytest.raises(ValueError, match="changed"):
        evaluate_inspection_track(track, runs, matches, spy, context())


@pytest.mark.parametrize(
    ("evidence_id", "issue_prefix"),
    (
        ("synthetic_ground_truth", "inspection_source_evidence_missing"),
        ("synthetic_clock", "inspection_time_basis_evidence_missing"),
        ("synthetic_truth", "registration_review_evidence_missing"),
        ("known_feature_identity", "correspondence_review_evidence_missing"),
    ),
)
def test_track_withholds_owner_call_when_provenance_or_approval_evidence_is_unbound(
    evidence_id, issue_prefix
):
    runs = tuple(run(i) for i in range(3))
    track, matches = reviewed_track(runs)
    ctx = context().model_copy(
        update={"evidence": tuple(item for item in context().evidence if item.id != evidence_id)}
    )
    spy = MaterialSpy()
    result = evaluate_inspection_track(track, runs, matches, spy, ctx)
    assert result["status"] == "needs_data" and not spy.calls
    assert any(issue.startswith(issue_prefix) for issue in result["issues"])


def test_ili_source_conversion_keeps_missing_values_and_uses_joint_relative_coordinates():
    load = ILILoadRequest(
        run_id="run",
        asset_id="line",
        coordinate_frame_id="source_clock",
        source_id="open_dataset",
        reported_time_label="1",
        joint_ids=("14",),
    )
    row = {
        "Year": 1,
        "GWNUM": 14,
        "JL.m": 12.0,
        "RD.m": 0.2,
        "SPO.deg": 360,
        "WT.mm": 7.1,
        "MD.mm": 0.7,
        "L.mm": 19,
        "W.mm": None,
        "SPD.m": 9999.0,
    }
    feature = ili_feature_from_row(row, load, source_row=2)
    assert feature.axial_position_m == 0.2 and feature.circumferential_angle_rad == 0.0
    assert feature.nominal_wall_m == pytest.approx(0.0071) and feature.maximum_depth_m == pytest.approx(
        0.0007
    )
    assert feature.width_m is None and feature.remaining_ligament_error_bound_m is None
    for changes in ({"SPO.deg": 361}, {"WT.mm": True}, {"MD.mm": None}, {"Year": 3}):
        with pytest.raises(ValueError):
            ili_feature_from_row({**row, **changes}, load, source_row=2)


def test_ili_scan_report_seals_selected_row_identity_and_partial_stop_reason(tmp_path):
    from pipeline_integrity.ili import load_ili_run, verify_ili_load_record

    pytest.importorskip("openpyxl")
    import io
    import zipfile

    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.append(("Year", "GWNUM", "JL.m", "RD.m", "SPO.deg", "WT.mm", "MD.mm", "L.mm", "W.mm"))
    sheet.append((1, 14, 12.0, 0.2, 360.0, 7.1, 0.7, 19.0, None))
    sheet.append((1, 15, 10.0, 0.3, 90.0, 8.0, 1.0, 10.0, 3.0))
    buffer = io.BytesIO()
    workbook.save(buffer)
    workbook.close()
    archive_path = io.BytesIO()
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("run.xlsx", buffer.getvalue())
    archive_path.seek(0)

    # Close the fixture before the reader pins and reopens it, including on Windows.
    source = tmp_path / "run.zip"
    source.write_bytes(archive_path.getvalue())
    request = ILILoadRequest(
        run_id="run", asset_id="line", coordinate_frame_id="frame", source_id="source",
        reported_time_label="1", joint_ids=("14",), maximum_source_rows=1,
    )
    run, report = load_ili_run(source, "run.xlsx", request)
    assert report["source_scan_complete"] is False
    assert report["scan_stop_reason"] == "maximum_source_rows_reached"
    assert report["actual_source_rows_scanned"] == report["maximum_source_rows"] == 1
    assert report["selected_source_rows"] == len(report["selected_source_row_numbers"]) == 1
    assert report["selected_feature_source_rows"] == [feature.source_row for feature in run.features]
    assert report["selected_source_row_numbers"] == report["selected_feature_source_rows"]
    assert len(report["selected_source_row_digest"]) == 64
    assert report["archive_sha256"] == run.source_sha256
    assert report["archive_bytes"] <= report["maximum_archive_bytes"]
    assert verify_ili_load_record(run, report, request, archive_path=source)["valid"]
    changed_report = {**report, "selected_source_row_numbers": [3]}
    assert not verify_ili_load_record(run, changed_report, request)["valid"]
    with pytest.raises(ValueError, match="bounded regular file"):
        load_ili_run(
            source,
            "run.xlsx",
            request.model_copy(update={"maximum_archive_bytes": 1}),
        )


def test_material_discovery_uses_owner_schema_and_rejects_changed_identity():
    class Registry:
        def list_models(self):
            return ("owner.trend",)

        def describe(self, model):
            return {
                "model_id": model,
                "input_schema": {"required": ["declared_input"]},
                "output_units": {"rate": "m/s"},
            }

    client = MaterialClient(
        evaluator=lambda *args: None,
        context_factory=object,
        evidence_factory=object,
        registry_factory=Registry,
    )
    assert client.models() == ("owner.trend",)
    assert client.describe("owner.trend")["input_schema"]["required"] == ["declared_input"]
    client.registry_factory = lambda: type(
        "Changed", (), {"describe": lambda self, model: {"model_id": "other"}}
    )()
    with pytest.raises(ValueError, match="identity"):
        client.describe("owner.trend")
