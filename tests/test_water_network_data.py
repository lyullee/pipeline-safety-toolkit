"""Authored malformed-archive and schema cases; execution is deferred to Nurion."""

import hashlib
import io
import zipfile
from pathlib import Path

import pytest

from pipeline_integrity import source_archive
from pipeline_integrity.contracts import EvidenceRef, digest
from pipeline_integrity.datasets import catalog, file_digest
from pipeline_integrity.inverse_localization import LocalizationRequest
from pipeline_integrity.metrology import MeasurementBasis
from pipeline_integrity.water_network_data import (
    DATASET_ID,
    NETWORK_LINKS,
    WaterHeadSensorBinding,
    WaterNetworkCoordinateTransform,
    _csv_identifier_column,
    _csv_profile,
    _epanet_coordinate_rows,
    _inp_identities,
    _member_name,
    _pressure_window_rows,
    bind_water_network_localization_context,
    build_water_network_inverse_bridge,
    inspect_water_network_archive,
    parse_water_network_event_table,
    to_water_network_location_candidates,
    verify_water_network_archive_inventory,
)


def test_csv_profile_preserves_labels_and_missing_cells():
    profile = _csv_profile(b"datetime,head_A,head_B\n2020-01-01 00:00:00,4.2,\n")
    assert profile["header"] == ["datetime", "head_A", "head_B"]
    assert profile["row_count"] == 1
    assert profile["blank_cells_by_column"] == [0, 0, 1]
    assert "sample_rows" not in profile
    assert profile["interpretation"].startswith("raw text only")


def test_csv_profile_rejects_changed_width_and_member_traversal():
    with pytest.raises(ValueError, match="row width"):
        _csv_profile(b"time,a,b\n1,2\n")
    with pytest.raises(ValueError, match="below"):
        _member_name("data/../outside.csv")


def test_event_table_preserves_source_cells_without_interpretation():
    record = parse_water_network_event_table(
        b"Datum,Beginuur,Einduur,Locatie,3GE FID,Opmerkingen\n16/12/2020,9:05,9:45, ,1239007,5.76 m3\n",
        member_name="data/raw/BKTown/experiments/infield_logging_leaks_MC_3.csv",
    )
    row = record["observations"][0]
    assert row["source_cells"]["Datum"] == "16/12/2020"
    assert row["source_cells"]["3GE FID"] == "1239007"
    assert record["date_and_times_parsed"] is False
    assert record["event_duration_seconds"] is None


def test_pressure_window_to_inverse_rows_requires_reviewed_head_basis_and_keeps_missingness():
    header, rows = _pressure_window_rows(
        b"datetime,sensor_A\n2020-08-05 09:35:00,4.2\n2020-08-05 09:36:00,\n",
        sensor_ids=("sensor_A",),
        start="2020-08-05 09:35:00",
        end="2020-08-05 09:36:00",
        maximum_rows=10,
    )
    assert header == ["datetime", "sensor_A"]
    record = {
        "schema": "pipeline_integrity.water_network_pressure_window.v1",
        "clock_timezone": None,
        "timestamps_parsed_as_naive_local": True,
        "selected_sensor_ids": ["sensor_A"],
        "member_sha256": "c" * 64,
        "reader_sha256": file_digest(
            Path(__file__).parents[1] / "src/pipeline_integrity/water_network_data.py"
        ),
        "provenance": {"supplier_checksum_verified": True, "archive_sha256": "b" * 64},
        "rows": rows,
        "record_digest": None,
    }
    record["record_digest"] = digest(record)
    basis = MeasurementBasis(
        quantity="pressure_head", unit="m", definition_id="reviewed_head", evidence_id="source"
    )
    bindings = {
        "sensor_A": WaterHeadSensorBinding(
            channel_id="channel_A",
            physical_port_id="node_A",
            basis=basis,
            mapping_evidence_id="reviewed_mapping",
        )
    }
    bridge = build_water_network_inverse_bridge(
        record,
        event_origin_timestamp="2020-08-05 09:35:00",
        sensor_bindings=bindings,
    )
    bridge.verify_digest()
    output = bridge.rows
    assert output[0].time_s == 0
    assert output[0].value == 4.2 and output[0].valid is True
    assert output[1].value is None and output[1].valid is False
    assert bridge.sensor_bindings["sensor_A"].mapping_evidence_id == "reviewed_mapping"


def test_network_identity_readers_keep_sections_and_exact_ids_separate():
    junctions, coordinates = _inp_identities(
        b"[JUNCTIONS]\nnode_A 3.0 0\n[TANKS]\ntank_A 1 0 2 0 3\n[COORDINATES]\nnode_A 10 20\n"
    )
    assert junctions == {"node_A"}
    assert coordinates == {"node_A"}
    assert _csv_identifier_column(b"asset_id\nnode_A\n", "asset_id") == ("node_A",)
    coordinate_rows = _epanet_coordinate_rows(b"[COORDINATES]\nnode_A 10.0 20.0\n", ("node_A",))
    assert coordinate_rows[0]["source_x_text"] == "10.0"


def test_reviewed_coordinate_transform_builds_sealed_inverse_candidates():
    coordinate_record = {
        "schema": "pipeline_integrity.water_network_coordinates.v1",
        "rows": [
            {"node_id": "node_A", "source_x": 10.0, "source_y": 20.0},
            {"node_id": "node_B", "source_x": 30.0, "source_y": 40.0},
        ],
        "member_sha256": "a" * 64,
        "reader_sha256": file_digest(
            Path(__file__).parents[1] / "src/pipeline_integrity/water_network_data.py"
        ),
        "provenance": {"supplier_checksum_verified": True, "archive_sha256": "b" * 64},
        "record_digest": None,
    }
    coordinate_record["record_digest"] = digest(coordinate_record)
    transform = WaterNetworkCoordinateTransform(
        source_frame_id="source_grid",
        source_coordinate_unit_id="source_metre",
        target_frame_id="site_metre",
        linear_m_per_source_unit=((0.0, -1.0), (1.0, 0.0), (0.0, 0.0)),
        offset_m=(100.0, 200.0, 0.0),
        evidence_id="reviewed_coordinate_transform",
    )
    candidates = to_water_network_location_candidates(
        coordinate_record,
        transform=transform,
        candidate_id_by_node={"node_A": "candidate_A", "node_B": "candidate_B"},
        location_id_by_node={"node_A": "location_A", "node_B": "location_B"},
        physical_port_id_by_node={"node_A": "port_A", "node_B": "port_B"},
        evidence_id_by_node={"node_A": "source_A", "node_B": "source_B"},
        candidate_parameters_by_node={"node_A": {"leak_size": 0.1}, "node_B": {"leak_size": 0.1}},
    )
    candidates.verify_digest()
    assert candidates.candidates[0].position_m == (80.0, 210.0, 0.0)
    assert candidates.source_archive_sha256 == "b" * 64


def test_localization_context_binds_same_archive_rows_candidates_and_evidence():
    _, rows = _pressure_window_rows(
        b"datetime,sensor_A\n2020-08-05 09:35:00,4.2\n",
        sensor_ids=("sensor_A",),
        start="2020-08-05 09:35:00",
        end="2020-08-05 09:35:00",
        maximum_rows=10,
    )
    reader_sha = file_digest(Path(__file__).parents[1] / "src/pipeline_integrity/water_network_data.py")
    pressure_record = {
        "schema": "pipeline_integrity.water_network_pressure_window.v1",
        "clock_timezone": None,
        "timestamps_parsed_as_naive_local": True,
        "selected_sensor_ids": ["sensor_A"],
        "member_sha256": "c" * 64,
        "reader_sha256": reader_sha,
        "provenance": {"supplier_checksum_verified": True, "archive_sha256": "b" * 64},
        "rows": rows,
        "record_digest": None,
    }
    pressure_record["record_digest"] = digest(pressure_record)
    head_basis = MeasurementBasis(
        quantity="pressure_head", unit="m", definition_id="head_definition", evidence_id="head_evidence"
    )
    bridge = build_water_network_inverse_bridge(
        pressure_record,
        event_origin_timestamp="2020-08-05 09:35:00",
        sensor_bindings={
            "sensor_A": WaterHeadSensorBinding(
                channel_id="channel_A",
                physical_port_id="port_A",
                basis=head_basis,
                mapping_evidence_id="mapping_evidence",
            )
        },
    )
    coordinate_record = {
        "schema": "pipeline_integrity.water_network_coordinates.v1",
        "rows": [
            {"node_id": "node_A", "source_x": 10.0, "source_y": 20.0},
            {"node_id": "node_B", "source_x": 30.0, "source_y": 40.0},
        ],
        "member_sha256": "a" * 64,
        "reader_sha256": reader_sha,
        "provenance": {"supplier_checksum_verified": True, "archive_sha256": "b" * 64},
        "record_digest": None,
    }
    coordinate_record["record_digest"] = digest(coordinate_record)
    transform = WaterNetworkCoordinateTransform(
        source_frame_id="source_frame",
        source_coordinate_unit_id="source_unit",
        target_frame_id="site_frame",
        linear_m_per_source_unit=((1.0, 0.0), (0.0, 1.0), (0.0, 0.0)),
        offset_m=(0.0, 0.0, 0.0),
        evidence_id="transform_evidence",
    )
    candidates = to_water_network_location_candidates(
        coordinate_record,
        transform=transform,
        candidate_id_by_node={"node_A": "candidate_A", "node_B": "candidate_B"},
        location_id_by_node={"node_A": "location_A", "node_B": "location_B"},
        physical_port_id_by_node={"node_A": "port_A", "node_B": "port_B"},
        evidence_id_by_node={"node_A": "node_evidence_A", "node_B": "node_evidence_B"},
        candidate_parameters_by_node={"node_A": {"leak_size": 0.1}, "node_B": {"leak_size": 0.1}},
    )
    parameter_basis = MeasurementBasis(
        quantity="leak_area", unit="m^2", definition_id="area_definition", evidence_id="parameter_evidence"
    )
    review_ids = {
        "candidate_geometry_and_fixed_nuisance_parameters": "review_geometry",
        "clock_synchronization_and_bias_corrections": "review_clock",
        "joint_residual_noise_includes_model_discrepancy": "review_residuals",
        "forward_model_applicability_and_units": "review_forward_model",
    }
    request = LocalizationRequest(
        id="request_A",
        asset_id="asset_A",
        clock_id="clock_A",
        event_id="event_A",
        rows=bridge.rows,
        candidates=candidates.candidates,
        parameter_bases={"leak_size": parameter_basis},
        covariance=((1.0,),),
        covariance_row_ids=(bridge.rows[0].id,),
        noise_law="common_candidate_independent_gaussian",
        provider_id="native_provider",
        provider_version="1",
        provider_source_sha256="d" * 64,
        provider_configuration_sha256="e" * 64,
        observation_source_sha256=bridge.source_member_sha256,
        evidence_id="dataset_archive",
        reviews={key: True for key in review_ids},
        review_evidence_ids=review_ids,
    )
    evidence = (
        EvidenceRef(
            id="dataset_archive",
            citation="Zenodo record 10.5281/zenodo.7255403",
            origin="measured",
            locator="https://zenodo.org/records/7255403",
            sha256="b" * 64,
            license_id="CC-BY-4.0",
        ),
        EvidenceRef(id="head_evidence", citation="Reviewed head basis", origin="user_declared"),
        EvidenceRef(id="parameter_evidence", citation="Reviewed parameter basis", origin="user_declared"),
        EvidenceRef(id="mapping_evidence", citation="Reviewed sensor port mapping", origin="user_declared"),
        EvidenceRef(
            id="transform_evidence", citation="Reviewed coordinate transform", origin="user_declared"
        ),
        EvidenceRef(id="node_evidence_A", citation="Source candidate A", origin="simulation"),
        EvidenceRef(id="node_evidence_B", citation="Source candidate B", origin="simulation"),
        *(EvidenceRef(id=value, citation=value, origin="user_declared") for value in review_ids.values()),
    )
    context = bind_water_network_localization_context(
        request,
        bridge,
        candidates,
        scenario_id="scenario_A",
        origin="measured",
        dataset_evidence_id="dataset_archive",
        evidence=evidence,
    )
    assert context.metadata["inverse_localization_boundary"]["request_sha256"]
    assert context.metadata["water_network_source"]["archive_sha256"] == "b" * 64


def test_archive_inventory_is_pinned_opaque_for_pickle_and_replayable(tmp_path, monkeypatch):
    official = next(spec for spec in catalog() if spec.id == DATASET_ID)
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
        output.writestr(
            "data/raw/BKTown/pressure_meas/observations.csv", "datetime,P1\n2020-01-01 00:00:00,4.2\n"
        )
        output.writestr("data/simulated/BKTown/HPC_run_01/results.pkl", b"opaque pickle bytes")
        written = set()
        for binding in NETWORK_LINKS:
            output.writestr(binding["input"], "[JUNCTIONS]\nnode_A 3 0\n[COORDINATES]\nnode_A 10 20\n")
            output.writestr(binding["sensors"], "asset_id\nnode_A\n")
            if binding["events"] not in written:
                output.writestr(
                    binding["events"],
                    "Datum,Beginuur,Einduur,Locatie,3GE FID,Opmerkingen\n"
                    "01/01/2020,9:00,9:10,,node_A,authored\n",
                )
                written.add(binding["events"])
    payload = archive.getvalue()
    asset = official.assets[0].model_copy(
        update={
            "bytes": len(payload),
            "checksum": "sha256:" + hashlib.sha256(payload).hexdigest(),
            "checksum_origin": "publisher_metadata",
        }
    )
    fixture_spec = official.model_copy(update={"assets": (asset,)})
    assert fixture_spec.id == DATASET_ID
    monkeypatch.setattr(source_archive, "catalog", lambda: (fixture_spec,))
    path = tmp_path / asset.filename
    path.write_bytes(payload)
    record = inspect_water_network_archive(path)
    assert record["file_count"] == 10
    assert record["csv_count"] == 6
    assert record["pickle_count"] == 1
    assert record["members"][1]["interpretation"].startswith("opaque pickle")
    assert record["network_id_coverage"][0]["sensor_ids_in_coordinate_section"] == 1
    assert record["members"][-1]["event_table_summary"]["source_cells_embedded"] is False
    assert verify_water_network_archive_inventory(record, path=path)
    changed = dict(record, csv_count=2)
    with pytest.raises(ValueError, match="seal"):
        verify_water_network_archive_inventory(changed, path=path)
