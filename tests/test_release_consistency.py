"""Release consistency detects stale code, omitted package files and mismatched core proof."""

import hashlib
import io
import json
import runpy
import stat
import tarfile
import zipfile

import pytest

api = runpy.run_path("scripts/audit_release.py")
check, audit = api["check_consistency"], api["audit"]


def build_pair(tmp_path, *, duplicate=False, zip_link=False):
    module = tmp_path / "src/pipeline_integrity/example.py"
    module.parent.mkdir(parents=True)
    module.write_bytes(b"value = 1\n")
    wheel = tmp_path / "example.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        if duplicate:
            content = "C:" + "/Users" + "/Example/private-observation.csv"
            archive.writestr("pipeline_integrity/example.py", content)
            with pytest.warns(UserWarning, match="Duplicate name"):
                archive.writestr("pipeline_integrity/example.py", module.read_bytes())
        else:
            archive.writestr("pipeline_integrity/example.py", module.read_bytes())
        if zip_link:
            link = zipfile.ZipInfo("pipeline_integrity/link")
            link.create_system = 3
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(link, "outside-package")
    receipt = tmp_path / "research/core_installation.json"
    receipt.parent.mkdir()
    receipt.write_text(
        json.dumps(
            {
                "passed": True,
                "installed_wheel_used": True,
                "wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
            }
        )
    )
    sdist = tmp_path / "example.tar.gz"
    with tarfile.open(sdist, "w:gz") as archive:
        for path in (module, receipt):
            archive.add(path, arcname="example/" + path.relative_to(tmp_path).as_posix())
        payload = b"generated metadata"
        member = tarfile.TarInfo("example/PKG-INFO")
        member.size = len(payload)
        archive.addfile(member, io.BytesIO(payload))
    return module, wheel, sdist


def test_matching_wheel_source_distribution_and_core_receipt(tmp_path):
    _, wheel, sdist = build_pair(tmp_path)
    result = check(tmp_path, wheel, sdist)
    assert result["passed"] and result["source_distribution_core_receipt_matches_wheel_sha256"]
    assert result["wheel_package_files_match_workspace"] == 1
    assert result["source_distribution_files_match_workspace"] == 2


def test_stale_sources_and_omitted_package_files_fail(tmp_path):
    module, wheel, sdist = build_pair(tmp_path)
    module.write_bytes(b"value = 2\n")
    extra = module.with_name("new.py")
    extra.write_bytes(b"new = True\n")
    result = check(tmp_path, wheel, sdist)
    assert not result["passed"]
    reasons = {i["reason"] for i in result["issues"]}
    assert {
        "wheel differs from workspace source",
        "source distribution differs from workspace",
        "workspace package file missing from wheel",
        "workspace package file missing from source distribution",
    } <= reasons


def test_another_wheel_cannot_reuse_a_successful_core_receipt(tmp_path):
    _, wheel, sdist = build_pair(tmp_path)
    with zipfile.ZipFile(wheel, "a") as archive:
        archive.writestr("example.dist-info/metadata.txt", "a different wheel")
    result = check(tmp_path, wheel, sdist)
    assert not result["passed"] and not result["source_distribution_core_receipt_matches_wheel_sha256"]


def test_duplicate_wheel_names_cannot_hide_an_earlier_entry(tmp_path):
    _, wheel, sdist = build_pair(tmp_path, duplicate=True)
    inspected = audit(wheel)
    assert not inspected["passed"]
    assert {"duplicate archive member", "local user directory in public artifact"} <= {
        row["reason"] for row in inspected["violations"]
    }
    result = check(tmp_path, wheel, sdist)
    assert not result["passed"] and result["source_distribution_core_receipt_matches_wheel_sha256"]
    assert {"duplicate wheel member", "wheel differs from workspace source"} <= {
        row["reason"] for row in result["issues"]
    }


def test_duplicate_tar_names_are_also_rejected(tmp_path):
    _, wheel, sdist = build_pair(tmp_path)
    with tarfile.open(sdist) as archive:
        entries = [(m.name, archive.extractfile(m).read()) for m in archive.getmembers() if m.isfile()]
    with tarfile.open(sdist, "w:gz") as archive:
        for name, payload in (*entries, entries[0]):
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
    assert not audit(sdist)["passed"]
    result = check(tmp_path, wheel, sdist)
    assert not result["passed"]
    assert "duplicate source distribution member" in {row["reason"] for row in result["issues"]}


@pytest.mark.parametrize("separator", ["/", "\\"])
def test_windows_drive_paths_are_checked_before_stripping_the_tar_root(tmp_path, separator):
    _, wheel, sdist = build_pair(tmp_path)
    with tarfile.open(sdist) as archive:
        entries = [(m.name, archive.extractfile(m).read()) for m in archive.getmembers() if m.isfile()]
    with tarfile.open(sdist, "w:gz") as archive:
        for name, payload in entries:
            info = tarfile.TarInfo("C:" + separator + name.split("/", 1)[1].replace("/", separator))
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
    assert not audit(sdist)["passed"]
    result = check(tmp_path, wheel, sdist)
    assert not result["passed"]
    assert "unsafe source distribution path" in {row["reason"] for row in result["issues"]}


@pytest.mark.parametrize("kind", [tarfile.SYMTYPE, tarfile.LNKTYPE])
def test_tar_links_and_their_excluded_names_are_not_skipped(tmp_path, kind):
    _, wheel, sdist = build_pair(tmp_path)
    with tarfile.open(sdist) as archive:
        entries = [(m.name, archive.extractfile(m).read()) for m in archive.getmembers() if m.isfile()]
    with tarfile.open(sdist, "w:gz") as archive:
        for name, payload in entries:
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
        link = tarfile.TarInfo("example/artifacts/private-link")
        link.type = kind
        link.linkname = "C:" + "/Users" + "/Example/private-observation.csv"
        archive.addfile(link)
    inspected = audit(sdist)
    assert not inspected["passed"]
    assert {"unsupported archive member type", "excluded source or runtime directory"} <= {
        row["reason"] for row in inspected["violations"]
    }
    result = check(tmp_path, wheel, sdist)
    assert not result["passed"]
    assert "unsupported source distribution member type" in {row["reason"] for row in result["issues"]}


def test_wheel_symbolic_links_are_also_rejected(tmp_path):
    _, wheel, sdist = build_pair(tmp_path, zip_link=True)
    assert not audit(wheel)["passed"]
    result = check(tmp_path, wheel, sdist)
    assert not result["passed"]
    assert "unsupported wheel member type" in {row["reason"] for row in result["issues"]}
