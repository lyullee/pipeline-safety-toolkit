"""Source conservation, physical unit factors and explicit missing-data boundaries."""

import csv
import hashlib
import io
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

import pipeline_integrity.corrosion_data as data
from pipeline_integrity.contracts import EvidenceRef, digest


def copper_rows():
    def row(clock, corrosion, difference):
        values = dict.fromkeys(data.COPPER_HEADER, "0")
        values.update(Datetime=clock, corrosion=corrosion, corrosion_diff=difference)
        return [values[k] for k in data.COPPER_HEADER]

    return [
        data.COPPER_HEADER.copy(),
        row("2022-04-13 10:43:09", "0.01", "0"),
        row("2022-04-13 11:43:09", "0.009", "-0.001"),
        row("2022-04-13 11:43:09", "NaN", ""),
        row("2022-04-13 10:43:09", "invalid", "inf"),
    ]


def afm_rows():
    def padded(*values):
        return list(values) + [""] * (10 - len(values))

    blocks = {
        "FIGURE 4A": [padded("Depth", "Load"), padded("nm", "mN"), padded("-1", "2")],
        "Figure 4B": [
            padded(),
            padded("Load", "Hardness", "Error"),
            padded("mN", "Gpa", "%"),
            padded("7", "11", "0.5"),
        ],
        "Figure 6A": [
            padded(),
            padded("Number of Passes", "Average Volume Removed", "Error"),
            padded("-", "micron^3", "micron^3"),
            padded("8", "2", "0.2"),
        ],
        "Figure 7": [padded()],
        "Figure 8": [padded()],
        "Figure 10": [
            padded(),
            padded("Load, [microN]", "Pile Up Volume, [micron^3]", "Removed Volume, [micron^3]"),
            padded("10", "1", "2"),
            padded("10", "NaN", "-0.5"),
            padded(),
        ],
        "Figure 12": [padded()],
        "Figure 13A": [padded()],
        "Figure 13B": [padded()],
    }
    return [row for name in data.AFM_SECTIONS for row in [padded(name), *blocks[name]]]


def method_evidence():
    return EvidenceRef(
        id="scratch-method",
        citation="Synthetic primary-method review fixture",
        origin="literature",
        sha256="1" * 64,
        license_id="CC-BY-4.0",
        locator="section 2b and figure 10",
    )


def test_copper_keeps_signs_missingness_duplicate_and_reversed_clocks_without_conversion():
    rows = copper_rows()
    result = data.parse_copper_rows(rows)
    assert [r["source_cells"] for r in result["rows"]] == rows[1:]
    assert result["rows"][1]["corrosion_difference_source_value"] == -0.001
    assert result["rows"][2]["numeric_cells"]["corrosion"]["status"] == "nonfinite"
    assert result["rows"][2]["numeric_cells"]["corrosion_diff"]["status"] == "missing"
    assert result["rows"][3]["numeric_cells"]["corrosion"]["status"] == "invalid"
    assert result["summary"]["repeated_adjacent_clocks"] == 1
    assert result["summary"]["decreasing_adjacent_clocks"] == 1
    assert result["summary"]["negative_provider_differences"] == 1
    assert all(r["corrosion_m"] is None and r["physical_exposure_age_s"] is None for r in result["rows"])
    assert result["rows"][0]["elapsed_from_first_observation_s"] == 0
    assert result["source_depth_unit"] is None
    json.dumps(result, allow_nan=False)


def test_missing_first_clock_does_not_create_an_exposure_origin():
    rows = copper_rows()
    rows[1][0] = "unresolved"
    result = data.parse_copper_rows(rows)
    assert result["summary"]["invalid_clocks"] == 1
    assert all(r["elapsed_from_first_observation_s"] is None for r in result["rows"])


@pytest.mark.parametrize("mutation", ["header", "width"])
def test_copper_schema_changes_are_not_silently_repaired(mutation):
    rows = copper_rows()
    if mutation == "header":
        rows[0][0] = "Date"
    else:
        rows[2].pop()
    with pytest.raises(ValueError, match="header or row width"):
        data.parse_copper_rows(rows)


def test_afm_converts_force_and_cubic_micrometres_but_does_not_assign_distance_or_hardness():
    rows = afm_rows()
    result = data.parse_afm_rows(rows)
    assert result["source_rows"] == len(rows)
    assert [r["source_cells"] for r in result["sections"]["FIGURE 4A"]][-1][0] == "-1"
    assert result["nanoindentation_hardness"][0]["load_N"] == pytest.approx(0.007)
    assert result["nanoindentation_hardness"][0]["hardness_Pa"] == 11e9
    a, b = result["continuous_load_volume"]
    assert a["normal_force_N"] == pytest.approx(1e-5)
    assert a["reported_removed_volume_m3"] == 2e-18
    assert a["reported_pile_up_volume_m3"] == 1e-18
    assert b["reported_removed_volume_m3"] == -0.5e-18
    assert b["reported_pile_up_volume_m3"] is None
    assert result["summary"]["repeated_load_occurrences"] == 1
    assert all(r["sliding_distance_m"] is None for r in result["continuous_load_volume"])
    assert all(r["matched_scratch_hardness_Pa"] is None for r in result["continuous_load_volume"])


def test_reviewed_pass_distance_produces_dimensional_specific_volume_without_reclassifying_volume():
    result = data.parse_afm_rows(afm_rows(), scratch_method_evidence=method_evidence())
    assert result["interrupted_pass_average_volume"][0]["sliding_distance_m"] == pytest.approx(40e-6)
    a, b = result["continuous_load_volume"]
    assert a["sliding_distance_m"] == 0.01
    assert a["specific_reported_removed_volume_m3_per_N_m"] == pytest.approx(2e-11)
    assert b["specific_reported_removed_volume_m3_per_N_m"] < 0
    assert a["reported_removed_volume_m3"] == 2e-18  # Pile-up has not been subtracted.
    assert result["record_digest"] == digest({k: v for k, v in result.items() if k != "record_digest"})
    json.dumps(result, allow_nan=False)


def test_unhashed_or_nonprimary_distance_evidence_is_rejected():
    evidence = method_evidence().model_copy(update={"sha256": None})
    with pytest.raises(ValueError, match="hashed primary-method"):
        data.parse_afm_rows(afm_rows(), scratch_method_evidence=evidence)


@pytest.mark.parametrize("change", ["section", "units", "trailing"])
def test_afm_structural_changes_require_review(change):
    rows = afm_rows()
    if change == "section":
        rows[0][0] = "Figure 4A changed"
    elif change == "units":
        next(r for r in rows if r[:3] == ["mN", "Gpa", "%"])[1] = "MPa"
    else:
        next(r for r in rows if r[:3] == ["10", "1", "2"])[9] = "unexpected"
    with pytest.raises(ValueError):
        data.parse_afm_rows(rows)


def pinned_fixture(tmp_path, monkeypatch):
    stream = io.StringIO(newline="")
    csv.writer(stream).writerows(copper_rows())
    raw = stream.getvalue().encode("utf-8")
    path = tmp_path / "source.csv"
    path.write_bytes(raw)
    asset = SimpleNamespace(
        filename=path.name,
        bytes=len(raw),
        checksum="md5:" + hashlib.md5(raw).hexdigest(),
        checksum_origin="publisher_metadata",
    )
    spec = SimpleNamespace(
        id="copper_wire_corrosion",
        acquisition="allowed",
        rights_review="reviewed_license",
        assets=(asset,),
        source="https://example.org",
        citation="Synthetic pin fixture",
        creators=(),
        version="fixture",
        license_id="CC0-1.0",
        rights_evidence_url="https://example.org/license",
        checked_on="2026-10-27",
    )
    monkeypatch.setattr(data, "catalog", lambda: (spec,))
    return path, spec


def test_reader_snapshot_verifies_supplier_hash_and_records_local_sha256(tmp_path, monkeypatch):
    path, _ = pinned_fixture(tmp_path, monkeypatch)
    result = data.read_copper_wire(path)
    assert result["provenance"]["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert result["provenance"]["publisher_checksum_verified"]
    assert result["observations"]["summary"]["rows"] == 4


@pytest.mark.parametrize("change", ["same_size_hash", "size", "rights", "checksum_origin"])
def test_unverified_sources_never_reach_csv_parsing(tmp_path, monkeypatch, change):
    path, spec = pinned_fixture(tmp_path, monkeypatch)
    if change == "same_size_hash":
        path.write_bytes(b"!" + path.read_bytes()[1:])
    elif change == "size":
        path.write_bytes(path.read_bytes() + b"!")
    elif change == "rights":
        spec.rights_review = "unresolved"
    else:
        spec.assets[0].checksum_origin = "previous_acquisition"
    monkeypatch.setattr(data.csv, "reader", lambda *a, **kw: pytest.fail("CSV parsed before pin check"))
    with pytest.raises(ValueError):
        data.read_copper_wire(path)


def test_input_fixture_is_not_mutated():
    rows = afm_rows()
    original = deepcopy(rows)
    data.parse_afm_rows(rows, scratch_method_evidence=method_evidence())
    assert rows == original
