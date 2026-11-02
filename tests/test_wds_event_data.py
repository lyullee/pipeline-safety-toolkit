"""Synthetic source-reader contracts; numerical/real-source checks run on Nurion."""

from hashlib import md5, sha256
from io import BytesIO
import stat
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

import openpyxl
import pytest

import pipeline_integrity.wds_event_data as source


def workbook_bytes(rows=()):
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(source._HEADERS)
    for row in rows:
        sheet.append(row)
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def pinned_synthetic_archive(tmp_path, monkeypatch, rows=(), *, unix_mode=None):
    member = "WDSEventDB/Clean/CleanData.xlsx"
    workbook = workbook_bytes(rows)
    archive_path = tmp_path / "synthetic.zip"
    with ZipFile(archive_path, "w", ZIP_DEFLATED) as archive:
        entry = member
        if unix_mode is not None:
            entry = ZipInfo(member)
            entry.create_system = 3
            entry.external_attr = unix_mode << 16
        archive.writestr(entry, workbook)
    raw = archive_path.read_bytes()
    monkeypatch.setattr(source, "_WORKBOOKS", {member: ("clean", "CleanData")})
    monkeypatch.setattr(source, "ARCHIVE_BYTES", len(raw))
    monkeypatch.setattr(source, "ARCHIVE_MD5", md5(raw, usedforsecurity=False).hexdigest())
    monkeypatch.setattr(source, "ARCHIVE_SHA256", sha256(raw).hexdigest())
    return archive_path


@pytest.mark.parametrize("unix_mode", [0o600, stat.S_IFREG | 0o644])
def test_reader_accepts_permissions_without_requiring_optional_unix_type(tmp_path, monkeypatch, unix_mode):
    path = pinned_synthetic_archive(tmp_path, monkeypatch, unix_mode=unix_mode)
    record = source.read_wds_event_archive(path)
    assert len(record.tables) == 1 and record.check_integrity()


@pytest.mark.parametrize("kind", [stat.S_IFLNK, stat.S_IFIFO, stat.S_IFSOCK, stat.S_IFBLK, stat.S_IFCHR])
def test_reader_rejects_explicit_unix_links_and_special_files(tmp_path, monkeypatch, kind):
    path = pinned_synthetic_archive(tmp_path, monkeypatch, unix_mode=kind | 0o600)
    with pytest.raises(ValueError, match="link or special file"):
        source.read_wds_event_archive(path)


def two_workbook_archive(tmp_path, monkeypatch):
    members = {
        "WDSEventDB/Clean/CleanData.xlsx": ("clean", "CleanData"),
        "WDSEventDB/Leakage/LeakEvent1.xlsx": ("leakage", "LeakEvent1"),
    }
    archive_path = tmp_path / "two-runs.zip"
    with ZipFile(archive_path, "w", ZIP_DEFLATED) as archive:
        archive.writestr(next(iter(members)), workbook_bytes([[1.0] * 15 + [0]]))
        archive.writestr(list(members)[1], workbook_bytes([[2.0] * 15 + [1]]))
    raw = archive_path.read_bytes()
    monkeypatch.setattr(source, "_WORKBOOKS", members)
    monkeypatch.setattr(source, "ARCHIVE_BYTES", len(raw))
    monkeypatch.setattr(source, "ARCHIVE_MD5", md5(raw, usedforsecurity=False).hexdigest())
    monkeypatch.setattr(source, "ARCHIVE_SHA256", sha256(raw).hexdigest())
    return archive_path


def test_reader_preserves_formula_cells_and_unmapped_axes(tmp_path, monkeypatch):
    row = [1.0] * 15 + ["=0"]
    path = pinned_synthetic_archive(tmp_path, monkeypatch, [row])
    record = source.read_wds_event_archive(path)
    table = record.tables[0]
    assert table.rows[0][-1] == "=0"
    assert table.formula_cells == ((2, 16, "=0"),)
    assert record.units_mapped is False and record.time_axis_mapped is False
    assert record.formulas_evaluated is False and record.check_integrity()
    assert source.verify_wds_event_archive(record, path)


def test_reader_rejects_source_byte_change(tmp_path, monkeypatch):
    path = pinned_synthetic_archive(tmp_path, monkeypatch)
    path.write_bytes(path.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="size"):
        source.read_wds_event_archive(path)


def test_source_replay_rejects_changed_archive(tmp_path, monkeypatch):
    path = pinned_synthetic_archive(tmp_path, monkeypatch, [[1.0] * 15 + [0]])
    record = source.read_wds_event_archive(path)
    path.write_bytes(path.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="size"):
        source.verify_wds_event_archive(record, path)


def test_receipt_rejects_changed_reader_revision(tmp_path, monkeypatch):
    path = pinned_synthetic_archive(tmp_path, monkeypatch)
    record = source.read_wds_event_archive(path)
    monkeypatch.setattr(source, "_reader_sha256", lambda: "0" * 64)
    with pytest.raises(ValueError, match="reader code"):
        record.check_integrity()


def test_archive_receipt_rejects_resealed_table_payload_change(tmp_path, monkeypatch):
    path = pinned_synthetic_archive(tmp_path, monkeypatch, [[1.0] * 15 + [0]])
    record = source.read_wds_event_archive(path)
    altered_table = record.tables[0].model_copy(update={"rows": ((2.0,) * 15 + (0,),)})
    forged_base = record.model_copy(update={"tables": (altered_table,)})
    body = forged_base.model_dump(mode="json", exclude={"record_digest"})
    forged = forged_base.model_copy(update={"record_digest": source.digest(body)})
    with pytest.raises(ValueError, match="table changed"):
        forged.check_integrity()


def test_caller_reviewed_selection_replays_source_rows(tmp_path, monkeypatch):
    path = pinned_synthetic_archive(tmp_path, monkeypatch, [[1.0] * 15 + ["=0"], [2.0] * 15 + [1]])
    archive = source.read_wds_event_archive(path)
    selected = source.select_wds_event_rows(
        archive,
        "WDSEventDB/Clean/CleanData.xlsx",
        (3, 2),
        selection_rule="caller-selected workbook rows after reviewed run partition",
        selection_evidence_id="review-run-partition-01",
        selection_evidence_sha256="a" * 64,
    )
    assert selected.selected_excel_rows == (2, 3)
    assert selected.check_integrity(archive)


def test_selection_rejects_unreviewed_or_missing_rows(tmp_path, monkeypatch):
    path = pinned_synthetic_archive(tmp_path, monkeypatch, [[1.0] * 15 + [0]])
    archive = source.read_wds_event_archive(path)
    with pytest.raises(ValueError, match="review evidence"):
        source.select_wds_event_rows(archive, "WDSEventDB/Clean/CleanData.xlsx", (2,), selection_rule="pick", selection_evidence_id=" ", selection_evidence_sha256="a" * 64)
    with pytest.raises(ValueError, match="does not exist"):
        source.select_wds_event_rows(archive, "WDSEventDB/Clean/CleanData.xlsx", (99,), selection_rule="reviewed source row", selection_evidence_id="review-01", selection_evidence_sha256="a" * 64)


def test_selection_receipt_binds_archive_and_row_content(tmp_path, monkeypatch):
    path = pinned_synthetic_archive(tmp_path, monkeypatch, [[1.0] * 15 + [0]])
    archive = source.read_wds_event_archive(path)
    selection = source.select_wds_event_rows(archive, "WDSEventDB/Clean/CleanData.xlsx", (2,), selection_rule="reviewed", selection_evidence_id="review-01", selection_evidence_sha256="a" * 64)
    other = archive.model_copy(update={"record_digest": "0" * 64})
    with pytest.raises(ValueError, match="receipt changed"):
        selection.check_integrity(other)


def test_evidence_packet_is_provenance_only_and_replays_selection(tmp_path, monkeypatch):
    path = pinned_synthetic_archive(tmp_path, monkeypatch, [[1.0] * 15 + [0]])
    archive = source.read_wds_event_archive(path)
    selection = source.select_wds_event_rows(archive, "WDSEventDB/Clean/CleanData.xlsx", (2,), selection_rule="reviewed run selection", selection_evidence_id="review-01", selection_evidence_sha256="a" * 64)
    packet = source.build_wds_event_evidence_packet(archive, selection, purpose="risk_evidence_review", consumer_contract="risk_exchange.v1")
    assert packet.raw_rows_included is False
    assert packet.probability_estimated is False
    assert packet.check_integrity(archive, selection)


def test_evidence_packet_rejects_different_selection(tmp_path, monkeypatch):
    path = pinned_synthetic_archive(tmp_path, monkeypatch, [[1.0] * 15 + [0], [2.0] * 15 + [1]])
    archive = source.read_wds_event_archive(path)
    first = source.select_wds_event_rows(archive, "WDSEventDB/Clean/CleanData.xlsx", (2,), selection_rule="reviewed", selection_evidence_id="review-01", selection_evidence_sha256="a" * 64)
    second = source.select_wds_event_rows(archive, "WDSEventDB/Clean/CleanData.xlsx", (3,), selection_rule="reviewed", selection_evidence_id="review-01", selection_evidence_sha256="a" * 64)
    packet = source.build_wds_event_evidence_packet(archive, first, purpose="dataset_partition", consumer_contract="monitoring.v1")
    with pytest.raises(ValueError, match="differs from its reviewed selection"):
        packet.check_integrity(archive, second)


def test_selection_requires_a_valid_review_artifact_digest(tmp_path, monkeypatch):
    path = pinned_synthetic_archive(tmp_path, monkeypatch, [[1.0] * 15 + [0]])
    archive = source.read_wds_event_archive(path)
    with pytest.raises(ValueError, match="artifact SHA-256"):
        source.select_wds_event_rows(archive, "WDSEventDB/Clean/CleanData.xlsx", (2,), selection_rule="reviewed", selection_evidence_id="review-01", selection_evidence_sha256="not-a-digest")


def test_evidence_packet_binds_review_artifact_digest(tmp_path, monkeypatch):
    path = pinned_synthetic_archive(tmp_path, monkeypatch, [[1.0] * 15 + [0]])
    archive = source.read_wds_event_archive(path)
    selection = source.select_wds_event_rows(archive, "WDSEventDB/Clean/CleanData.xlsx", (2,), selection_rule="reviewed", selection_evidence_id="review-01", selection_evidence_sha256="a" * 64)
    packet = source.build_wds_event_evidence_packet(archive, selection, purpose="risk_evidence_review", consumer_contract="risk_exchange.v1")
    forged = packet.model_copy(update={"selection_evidence_sha256": "b" * 64})
    forged_body = forged.model_dump(mode="json", exclude={"record_digest"})
    forged = forged.model_copy(update={"record_digest": source.digest(forged_body)})
    with pytest.raises(ValueError, match="packet differs"):
        forged.check_integrity(archive, selection)


def test_reviewed_artifact_sha256_rejects_symlinks_and_tracks_bytes(tmp_path):
    from hashlib import sha256

    evidence = tmp_path / "review.txt"
    evidence.write_bytes(b"reviewed selection protocol")
    assert source.reviewed_artifact_sha256(evidence) == sha256(evidence.read_bytes()).hexdigest()
    link = tmp_path / "review-link.txt"
    try:
        link.symlink_to(evidence)
    except OSError as exc:
        if getattr(exc,'winerror',None)==1314:
            pytest.skip('Windows account lacks symbolic-link creation privilege (WinError1314); rejection requires an actual symlink')
        raise
    with pytest.raises(ValueError, match="regular non-symlink"):
        source.reviewed_artifact_sha256(link)


def test_group_partition_is_replayable_and_source_bound(tmp_path, monkeypatch):
    archive = source.read_wds_event_archive(two_workbook_archive(tmp_path, monkeypatch))
    partition = source.build_wds_event_partition(
        archive,
        {
            "run-clean": ("WDSEventDB/Clean/CleanData.xlsx",),
            "run-leak": ("WDSEventDB/Leakage/LeakEvent1.xlsx",),
        },
        {"train": ("run-clean",), "test": ("run-leak",)},
        grouping_rule="caller-reviewed experiment run identifiers",
        grouping_evidence_id="run-map-01",
        grouping_evidence_sha256="a" * 64,
    )
    assert partition.check_integrity(archive)
    assert partition.row_random_split is False
    assert partition.independence_established is False


def test_group_partition_rejects_cross_split_group_leakage(tmp_path, monkeypatch):
    archive = source.read_wds_event_archive(two_workbook_archive(tmp_path, monkeypatch))
    with pytest.raises(ValueError, match="exactly one split"):
        source.build_wds_event_partition(
            archive,
            {
                "run-clean": ("WDSEventDB/Clean/CleanData.xlsx",),
                "run-leak": ("WDSEventDB/Leakage/LeakEvent1.xlsx",),
            },
            {"train": ("run-clean",), "test": ("run-clean", "run-leak")},
            grouping_rule="reviewed",
            grouping_evidence_id="run-map-01",
            grouping_evidence_sha256="a" * 64,
        )


def test_group_partition_rejects_missing_workbook_and_unpinned_member(tmp_path, monkeypatch):
    archive = source.read_wds_event_archive(two_workbook_archive(tmp_path, monkeypatch))
    with pytest.raises(ValueError, match="cover every source workbook"):
        source.build_wds_event_partition(
            archive,
            {"run-clean": ("WDSEventDB/Clean/CleanData.xlsx",)},
            {"train": ("run-clean",), "test": ("run-clean",)},
            grouping_rule="reviewed",
            grouping_evidence_id="run-map-01",
            grouping_evidence_sha256="a" * 64,
        )


def test_group_partition_requires_review_artifact_digest(tmp_path, monkeypatch):
    archive = source.read_wds_event_archive(two_workbook_archive(tmp_path, monkeypatch))
    with pytest.raises(ValueError, match="artifact SHA-256"):
        source.build_wds_event_partition(
            archive,
            {
                "run-clean": ("WDSEventDB/Clean/CleanData.xlsx",),
                "run-leak": ("WDSEventDB/Leakage/LeakEvent1.xlsx",),
            },
            {"train": ("run-clean",), "test": ("run-leak",)},
            grouping_rule="reviewed",
            grouping_evidence_id="run-map-01",
            grouping_evidence_sha256="missing",
        )



def _reviewed_signal_mapping(archive):
    channel = source.WdsSignalChannelMapping(
        source_header="Pressure 1 Out", signal_name="pressure_out_pa",
        source_unit="kPa", si_unit="Pa", multiplier_to_si=1000.0,
    )
    return source.build_wds_signal_mapping(
        archive, member="WDSEventDB/Clean/CleanData.xlsx", stream_id="clean-run-01",
        clock_id="reviewed-clock-01", channels=(channel,),
        mapping_rule="caller-reviewed source instrument mapping and unit conversion",
        mapping_evidence_id="channel-review-01", mapping_evidence_sha256="a" * 64,
        time_basis="caller-supplied elapsed seconds from reviewed run clock",
        time_evidence_id="clock-review-01", time_evidence_sha256="b" * 64,
    )


def test_reviewed_wds_mapping_builds_replayable_owner_signal_frames(tmp_path, monkeypatch):
    rows = [
        [100.0] + [1.0] * 14 + [0],
        ["=100+10"] + [2.0] * 14 + [1],
    ]
    archive = source.read_wds_event_archive(pinned_synthetic_archive(tmp_path, monkeypatch, rows))
    mapping = _reviewed_signal_mapping(archive)
    selection = source.select_wds_event_rows(
        archive, mapping.member, (2, 3), selection_rule="reviewed clean-run slice",
        selection_evidence_id="row-review-01", selection_evidence_sha256="c" * 64,
    )
    axis = {2: 0.0, 3: 0.5}
    batch = source.build_wds_signal_batch(archive, selection, mapping, axis)
    assert [frame.time_s for frame in batch.frames] == [0.0, 0.5]
    assert batch.frames[0].values["pressure_out_pa"] == 100000.0
    assert batch.frames[0].valid["pressure_out_pa"] is True
    assert batch.frames[1].values["pressure_out_pa"] is None
    assert batch.frames[1].valid["pressure_out_pa"] is False
    assert batch.invalid_values_by_channel["pressure_out_pa"] == 1
    assert batch.row_random_split is False and batch.failure_probability_estimated is False
    assert source.verify_wds_signal_batch(batch, archive, selection, mapping, axis)


def test_wds_signal_adapter_requires_explicit_monotone_time_for_exact_selection(tmp_path, monkeypatch):
    archive = source.read_wds_event_archive(
        pinned_synthetic_archive(tmp_path, monkeypatch, [[100.0] + [1.0] * 14 + [0], [101.0] + [1.0] * 14 + [0]])
    )
    mapping = _reviewed_signal_mapping(archive)
    selection = source.select_wds_event_rows(
        archive, mapping.member, (2, 3), selection_rule="reviewed run slice",
        selection_evidence_id="row-review-02", selection_evidence_sha256="d" * 64,
    )
    with pytest.raises(ValueError, match="strictly increase"):
        source.build_wds_signal_batch(archive, selection, mapping, {2: 1.0, 3: 1.0})
    with pytest.raises(ValueError, match="every selected Excel row"):
        source.build_wds_signal_batch(archive, selection, mapping, {2: 0.0})



def test_wds_signal_batch_integrity_rejects_resealed_frame_or_time_tampering(tmp_path, monkeypatch):
    archive = source.read_wds_event_archive(
        pinned_synthetic_archive(tmp_path, monkeypatch, [[100.0] + [1.0] * 14 + [0], [101.0] + [1.0] * 14 + [0]])
    )
    mapping = _reviewed_signal_mapping(archive)
    selection = source.select_wds_event_rows(
        archive, mapping.member, (2, 3), selection_rule="reviewed run slice",
        selection_evidence_id="row-review-03", selection_evidence_sha256="e" * 64,
    )
    axis = {2: 0.0, 3: 0.5}
    batch = source.build_wds_signal_batch(archive, selection, mapping, axis)

    def reseal(changed):
        return changed.model_copy(update={
            "record_digest": source.digest(changed.model_dump(mode="json", exclude={"record_digest"}))
        })

    altered_time = batch.frames[0].model_copy(update={"time_s": 0.25})
    altered_value = batch.frames[0].model_copy(update={"values": {"pressure_out_pa": 1.0}})
    altered_unit = batch.frames[0].model_copy(update={"units": {"pressure_out_pa": "kPa"}})
    for frame in (altered_time, altered_value, altered_unit):
        forged = reseal(batch.model_copy(update={"frames": (frame, *batch.frames[1:])}))
        with pytest.raises(ValueError, match="SignalFrames differ"):
            forged.check_integrity(archive, mapping, selection)
        with pytest.raises(ValueError, match="SignalFrames differ"):
            source.verify_wds_signal_batch(forged, archive, selection, mapping, axis)

    forged_counts = reseal(batch.model_copy(update={"invalid_values_by_channel": {"pressure_out_pa": 1}}))
    with pytest.raises(ValueError, match="invalid-value counts differ"):
        forged_counts.check_integrity(archive, mapping, selection)

    forged_selection = reseal(batch.model_copy(update={"selection_digest": "0" * 64}))
    with pytest.raises(ValueError, match="reviewed row selection"):
        forged_selection.check_integrity(archive, mapping, selection)
