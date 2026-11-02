import hashlib
import json
import runpy
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from pipeline_integrity.datasets import (
    AssetSpec,
    acquire,
    acquire_asset,
    acquisition_provenance,
    catalog,
    inspect_zip,
    require_acquisition_allowed,
    verify_acquisition_provenance,
)
from pipeline_integrity.loaders import hydraulic_cycle, iter_battledim_frames


def test_reviewed_catalog_has_honest_access_and_origins():
    specs = {row.id: row for row in catalog()}
    assert len(specs) >= 20
    assert specs["battledim"].origin == "simulation"
    assert specs["phmsa_flagged"].rights_review == "public_access_analysis_only"
    assert specs["phmsa_flagged"].acquisition == "metadata_only"
    with pytest.raises(ValueError, match="withholds acquisition"):
        require_acquisition_allowed(specs["phmsa_flagged"])
    assert specs["swat"].acquisition != "allowed"
    assert all(not row.raw_in_code_release for row in specs.values())


def test_existing_asset_cli_obeys_catalog_acquisition_gate(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "acquire_data.py",
            "--only",
            "phmsa_flagged",
            "--existing-asset",
            f"phmsa_flagged={tmp_path / 'never-read.zip'}",
        ],
    )
    script = Path(__file__).parents[1] / "scripts" / "acquire_data.py"
    main = runpy.run_path(str(script))["main"]
    with pytest.raises(ValueError, match="withholds acquisition"):
        main()
    assert not (tmp_path / "data" / "raw" / "phmsa_flagged").exists()


@pytest.mark.parametrize("value", ["../escape.zip", "C:drive.zip", "a/b.zip", "a\\b.zip"])
def test_asset_name_does_not_escape(value):
    with pytest.raises(ValueError):
        AssetSpec(filename=value, url="https://example.org/a")


def test_checksum_is_validated_and_cache_corruption_rejected(tmp_path):
    spec = next(row for row in catalog() if row.id == "hydraulic")
    folder = tmp_path / spec.id
    folder.mkdir()
    payload = b"known data"
    (folder / "test.txt").write_bytes(payload)
    asset = AssetSpec(
        filename="test.txt",
        url="https://example.org/data",
        checksum="sha256:" + hashlib.sha256(payload).hexdigest(),
        checksum_origin="previous_acquisition",
    )
    row = acquire_asset(spec, asset, tmp_path)
    assert row["expected_checksum_verified"] and not row["supplier_checksum_verified"]
    assert row["catalog_snapshot"]["id"] == spec.id
    assert row["catalog_snapshot"]["license_id"] == spec.license_id
    assert row["catalog_snapshot"]["rights_review"] == spec.rights_review
    assert row["catalog_snapshot_sha256"] == hashlib.sha256(
        json.dumps(row["catalog_snapshot"], sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
            "utf-8"
        )
    ).hexdigest()
    assert row["asset_snapshot"]["filename"] == asset.filename
    assert len(row["asset_snapshot_sha256"]) == 64
    assert row["asset_catalogued"] is False
    assert verify_acquisition_provenance(row)
    changed = {**row, "catalog_snapshot": {**row["catalog_snapshot"], "license_id": "changed"}}
    with pytest.raises(ValueError, match="snapshot"):
        verify_acquisition_provenance(changed)
    with pytest.raises(ValueError, match="snapshot"):
        verify_acquisition_provenance({**row, "asset_catalogued": True})
    with pytest.raises(ValueError, match="incomplete"):
        verify_acquisition_provenance({key: value for key, value in row.items() if key != "asset_snapshot_sha256"})
    catalog_asset = next(row for row in catalog() if row.id == "hydraulic").assets[0]
    assert acquisition_provenance(spec, catalog_asset)["asset_catalogued"] is True
    (folder / "test.txt").write_bytes(b"changed")
    with pytest.raises(ValueError, match="integrity"):
        acquire_asset(spec, asset, tmp_path)
    with pytest.raises(ValueError):
        AssetSpec(filename="a", url="https://example.org", checksum="md5:bad")


def test_zip_metadata_rejects_escaping_member_without_extracting(tmp_path):
    target = tmp_path / "source.zip"
    with zipfile.ZipFile(target, "w") as archive:
        archive.writestr("../escape.txt", "data")
    with pytest.raises(ValueError, match="outside"):
        inspect_zip(target)
    assert not (tmp_path.parent / "escape.txt").exists()


def test_zip_metadata_rejects_duplicate_names(tmp_path):
    target = tmp_path / "duplicate.zip"
    with zipfile.ZipFile(target, "w") as archive:
        archive.writestr("sensor.csv", b"first")
        archive.writestr("sensor.csv", b"second")
    with pytest.raises(ValueError, match="duplicate"):
        inspect_zip(target)


@pytest.mark.parametrize(
    "entries",
    [
        (("node", b"file"), ("node/sensor.csv", b"child")),
        (("node/sensor.csv", b"child"), ("node", b"file")),
        (("Node", b"file"), ("node/sensor.csv", b"child")),
    ],
)
def test_zip_metadata_rejects_file_parent_path_conflicts(tmp_path, entries):
    target = tmp_path / "parent-conflict.zip"
    with zipfile.ZipFile(target, "w") as archive:
        for name, content in entries:
            archive.writestr(name, content)
    with pytest.raises(ValueError, match="blocks a descendant|conflicts with an existing descendant"):
        inspect_zip(target)


def test_zip_metadata_rejects_casefold_collisions_for_portable_extraction(tmp_path):
    target = tmp_path / "case-collision.zip"
    with zipfile.ZipFile(target, "w") as archive:
        archive.writestr("Signals/A.csv", b"upper")
        archive.writestr("signals/a.csv", b"lower")
    with pytest.raises(ValueError, match="ambiguous"):
        inspect_zip(target)


def test_zip_metadata_rejects_symlink_entries(tmp_path):
    target = tmp_path / "symlink.zip"
    link = zipfile.ZipInfo("sensor.csv")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(target, "w") as archive:
        archive.writestr(link, b"target.csv")
    with pytest.raises(ValueError, match="link or non-regular"):
        inspect_zip(target)


def test_zip_metadata_rejects_encrypted_member_flag(tmp_path):
    target = tmp_path / "encrypted-flag.zip"
    with zipfile.ZipFile(target, "w") as archive:
        archive.writestr("sensor.csv", b"payload")
    encoded = bytearray(target.read_bytes())
    central = encoded.find(b"PK\x01\x02")
    assert central >= 0
    flags = int.from_bytes(encoded[central + 8 : central + 10], "little")
    encoded[central + 8 : central + 10] = (flags | 0x1).to_bytes(2, "little")
    target.write_bytes(encoded)
    with pytest.raises(ValueError, match="encrypted"):
        inspect_zip(target)


def test_download_wall_clock_timeout_is_a_failure(monkeypatch, tmp_path):
    spec = next(row for row in catalog() if row.id == "hydraulic")

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", timeout)
    asset = AssetSpec(filename="test.txt", url="https://example.org/test")
    with pytest.raises(TimeoutError, match="wall-clock"):
        acquire_asset(spec, asset, tmp_path, maximum_seconds=0.01)
    assert not (tmp_path / spec.id / "test.txt").exists()
    with pytest.raises(ValueError):
        acquire([], tmp_path)


def test_battledim_preserves_head_clock_and_volume_basis(tmp_path):
    contents = {
        "Pressures": "Timestamp;n1\n2018-01-01 00:00;40,0\n2018-01-01 00:05;41,0\n",
        "Flows": "Timestamp;P1\n2018-01-01 00:00;36,0\n2018-01-01 00:05;72,0\n",
        "Levels": "Timestamp;T1\n2018-01-01 00:00;2,0\n2018-01-01 00:05;2,1\n",
    }
    for suffix, content in contents.items():
        (tmp_path / f"2018_SCADA_{suffix}.csv").write_text(content)
    frames = list(iter_battledim_frames(tmp_path, 2018))
    assert frames[1].time_s == 300
    assert frames[0].values["volume_flow:P1"] == pytest.approx(0.01)
    assert frames[0].units["pressure_head:n1"] == "m"
    assert frames[0].origin == "simulation" and frames[0].timestamp_utc is None
    with pytest.raises(ValueError):
        list(iter_battledim_frames(tmp_path, 2018, maximum_rows=0))
    (tmp_path / "2018_SCADA_Levels.csv").write_text(contents["Levels"].replace("00:05", "00:06"))
    with pytest.raises(ValueError, match="clocks"):
        list(iter_battledim_frames(tmp_path, 2018))


def test_hydraulic_rates_and_labels_are_separate(tmp_path):
    target = tmp_path / "cycle.zip"
    with zipfile.ZipFile(target, "w") as archive:
        archive.writestr("profile.txt", "100\t0\t0\t130\t1\n")
        archive.writestr("PS1.txt", "\t".join(["10"] * 6000))
        archive.writestr("FS1.txt", "\t".join(["60"] * 600))
        archive.writestr("TS1.txt", "\t".join(["20"] * 60))
    result = hydraulic_cycle(target, 0)
    assert result["profile_is_model_input"] is False
    assert result["sensors"]["FS1"]["values"][0] == pytest.approx(0.001)
    assert result["sensors"]["TS1"]["values"][0] == pytest.approx(293.15)
    assert len(result["sensors"]["PS1"]["time_s"]) == 6000
    assert "unspecified" in result["sensors"]["PS1"]["unit"]


def test_catalog_models_secondary_sha256_and_guide_metadata():
    sources = {item.id: item for item in catalog()}
    elbow = sources["potash_elbow_cp_slurry"]
    assert elbow.guide == "docs/POTASH_ELBOW_CP_DATA_KO.md"
    assert elbow.assets[0].sha256 == "9b6ba2bd756d402596783353c9dfb7ecad951fb6f3780e67e239acc7f9aa5103"
    event_db = sources["wds_event_db"].assets[0]
    assert event_db.sha256 == "d1452c4a311f2c3132ddda1b78caab4c782f5c658553c5e32266cee230db459c"
    assert event_db.repository_blob_sha == "2e796d1727c638b73c68aa3c2dfea9a30b898102"
    assert event_db.checksum_origin == "publisher_metadata"
    assert "Zenodo" in event_db.checksum_note


def test_asset_sha256_only_pin_is_enforced(tmp_path):
    import hashlib
    from pipeline_integrity.datasets import _matches

    payload = b"pinned bytes"
    path = tmp_path / "asset.bin"
    path.write_bytes(payload)
    asset = AssetSpec(
        filename=path.name,
        url="https://example.org/asset.bin",
        sha256=hashlib.sha256(payload).hexdigest(),
        bytes=len(payload),
    )
    assert _matches(path, asset)
    path.write_bytes(b"changed bytes")
    assert not _matches(path, asset)
