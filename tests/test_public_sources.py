"""Deferred source-boundary regressions; fabricated inputs contain no real secrets or book text."""

import importlib.util
import json
from pathlib import Path

import pytest


@pytest.fixture
def auditor():
    path = Path(__file__).parents[1] / "scripts/audit_public_sources.py"
    spec = importlib.util.spec_from_file_location("public_source_auditor_under_review", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def source(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "LICENSE").write_text("Authored fixture license notice\n")
    (tmp_path / "THIRD_PARTY_NOTICES.md").write_text("Authored fixture dependency notices\n")
    return tmp_path


def test_public_nasa_pdf_reference_is_not_a_local_book_leak(auditor, source):
    address = "https://ntrs.nasa.gov/api/citations/19670028965/downloads/19670028965" + ".pdf"
    (source / "docs/reference.md").write_text(
        "[Public source](" + address + ")\nPublic-domain status must be independently reviewed.\n"
    )
    result = auditor.audit_sources(source)
    assert result["passed"] is True
    assert result["provenance"]["pdf_url_domains"] == {"ntrs.nasa.gov": 1}
    assert result["copyright_clearance_confirmed"] is False


def test_local_user_book_path_is_redacted_not_printed(auditor, source):
    private = "C:" + "/Users/" + "ExampleReader/Desktop/private_book" + ".pdf"
    (source / "docs/local.md").write_text("source = " + private + "\n")
    result = auditor.audit_sources(source)
    assert result["passed"] is False
    assert any(f["rule"] == "local_user_path_in_public_source" for f in result["findings"])
    assert private not in json.dumps(result)


def test_secret_candidates_never_include_whole_value(auditor, source):
    token = "ghp_" + "A" * 36
    (source / "docs/credential.md").write_text("credential=" + token + "\n")
    result = auditor.audit_sources(source)
    assert any(f["rule"] == "credential_like_content" for f in result["findings"])
    assert token not in json.dumps(result)


def test_manifest_local_source_map_is_checked_even_when_hpc_excludes_it(auditor, source):
    (source / "jobs").mkdir()
    (source / "jobs/nurion_sources.local.json").write_text('{"owner":"/reviewed/private/root"}')
    (source / "MANIFEST.in").write_text("recursive-include jobs *.json\n")
    result = auditor.audit_sources(source)
    assert result["passed"] is False
    assert any(
        f["rule"] == "private_raw_repository_or_local_configuration_member" for f in result["findings"]
    )


def test_original_pdf_bytes_are_never_opened(auditor, source, monkeypatch):
    original = source / "docs/original.pdf"
    original.write_bytes(b"authored fixture; never a real original document")
    (source / "MANIFEST.in").write_text("recursive-include docs *.pdf\n")
    real = Path.open

    def bounded(path, *args, **kwargs):
        if path == original:
            pytest.fail("original PDF bytes must not be opened")
        return real(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", bounded)
    result = auditor.audit_sources(source)
    assert any(f["rule"] == "raw_or_binary_research_asset_selected" for f in result["findings"])
    assert result["original_pdf_read"] is False


def test_non_pdf_name_does_not_hide_raw_archive_magic(auditor, source):
    (source / "docs/figure.png").write_bytes(b"PK\x03\x04authored fake archive")
    result = auditor.audit_sources(source)
    assert any(
        f["rule"] == "raw_archive_document_or_executable_disguised_as_source" for f in result["findings"]
    )


def test_broken_local_link_is_review_candidate_without_fetching(auditor, source):
    (source / "docs/guide.md").write_text("[Missing](not-created.md)\n")
    result = auditor.audit_sources(source)
    assert any(f["rule"] == "unresolved_or_outside_local_document_link" for f in result["findings"])
    assert result["passed"] is True and result["copyright_clearance_confirmed"] is False


def test_scan_budget_is_not_silent_truncation(auditor, source):
    (source / "docs/large.md").write_text("a" * 1024)
    result = auditor.audit_sources(source, maximum_file_bytes=128, maximum_total_bytes=256)
    assert result["scan_complete"] is False and result["passed"] is False


def test_code_index_and_call_syntax_is_not_a_broken_markdown_link(auditor, source):
    (source / "docs/code.md").write_text(
        '```python\nrequest = example["make_request"](samples=512)\n```\n`example["make_request"](samples=512)`\n'
    )
    result = auditor.audit_sources(source)
    assert not any(f["rule"] == "unresolved_or_outside_local_document_link" for f in result["findings"])


def test_prior_validation_and_pending_state_are_explicit(auditor, source):
    (source / "research").mkdir()
    (source / "research/validation_queue.json").write_text(
        json.dumps(
            {
                "last_completed_full_suite": {"applies_to": "previous released source snapshot"},
                "local_policy": "static checks only; tests deferred to Nurion",
            }
        )
    )
    result = auditor.audit_sources(source)
    assert result["handover"]["prior_passes_not_current_source"] is True
    assert result["handover"]["pending_policy_declared"] is True
    assert result["tests_executed"] == result["native_executions"] == 0
