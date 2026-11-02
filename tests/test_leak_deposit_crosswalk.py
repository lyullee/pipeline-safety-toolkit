"""Synthetic member-ledger comparison; no publisher observations are embedded."""
import io
import zipfile

import pytest

from pipeline_integrity.leak_deposit_crosswalk import (
    LeakDepositCrosswalkError,
    _copy_zip_member_bounded,
    _safe_zip_path,
    compare_member_ledgers,
)


def test_member_crosswalk_requires_path_size_and_hash_identity():
    original = {"accelerometer/a.csv": {"bytes": 3, "sha256": "a" * 64}}
    assert compare_member_ledgers(original, dict(original))["byte_identical_member_set"] is True
    changed = {"accelerometer/a.csv": {"bytes": 3, "sha256": "b" * 64}}
    result = compare_member_ledgers(original, changed)
    assert result["same_path_count"] == 1
    assert result["same_path_and_size_count"] == 1
    assert result["same_path_size_and_sha256_count"] == 0
    assert result["byte_identical_member_set"] is False


def test_member_crosswalk_reports_missing_members_and_rejects_invalid_ledger():
    left = {"pressure/a.csv": {"bytes": 4, "sha256": "c" * 64}}
    right = {"pressure/b.csv": {"bytes": 4, "sha256": "c" * 64}}
    result = compare_member_ledgers(left, right)
    assert result["left_only"] == ["pressure/a.csv"]
    assert result["right_only"] == ["pressure/b.csv"]
    assert result["byte_identical_member_set"] is False
    with pytest.raises(ValueError, match="invalid"):
        compare_member_ledgers({"pressure/a.csv": {"bytes": -1, "sha256": "c" * 64}}, right)


def test_zip_path_guard_rejects_traversal_and_ambiguous_slashes():
    assert _safe_zip_path("Accelerometer/Signals/A1.csv").as_posix() == "Accelerometer/Signals/A1.csv"
    with pytest.raises(LeakDepositCrosswalkError, match="unsafe or ambiguous"):
        _safe_zip_path("Accelerometer/../outside.csv")
    with pytest.raises(LeakDepositCrosswalkError, match="unsafe or ambiguous"):
        _safe_zip_path("Accelerometer//A1.csv")


def test_nested_zip_copy_enforces_declared_size_and_budget_during_streaming():
    payload = b"bounded synthetic fixture"
    source = io.BytesIO()
    with zipfile.ZipFile(source, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("nested.zip", payload)
    source.seek(0)
    with zipfile.ZipFile(source) as archive:
        info = archive.getinfo("nested.zip")
        destination = io.BytesIO()
        assert _copy_zip_member_bounded(archive, info, destination, max_bytes=len(payload)) == len(payload)
        assert destination.getvalue() == payload
        with pytest.raises(LeakDepositCrosswalkError, match="declared bound|explicit content budget"):
            _copy_zip_member_bounded(archive, info, io.BytesIO(), max_bytes=len(payload) - 1)
