"""Source identity, chronology, dimensional conversion and missing physics conditions."""

import hashlib
import json
import runpy
from copy import deepcopy
from types import SimpleNamespace

import pytest

import pipeline_integrity.material_process_data as data


def protocol(group="KX"):
    rows = [[None] * 10 for _ in range(53)]
    for r, c, value in (
        (2, 1, "Probenbezeichnung:"),
        (2, 5, "Werkstoff:"),
        (2, 6, "S355"),
        (6, 3, "V-Kerbe"),
        (6, 6, "in Walzrichtung" if group == "KX" else "gegen Walzrichtung"),
        (8, 5, "Prüftemp.:"),
        (8, 6, "22,5 °C"),
        (10, 6, "2021-11-12T00:00:00"),
        (48, 1, "Die Proben sind nicht gebrochen."),
    ):
        rows[r - 1][c - 1] = value
    rows[38][:7] = ["Probe", "Länge L", "Breite W", "Dicke B", "Steghöhe", "KV2", " LE "]
    rows[39][:7] = [None, "in mm", "in mm", "in mm", "in mm", "in J", "in mm"]
    for i in range(3):
        rows[1 + i][2] = f"{group}{i + 1}"
        rows[40 + i][:7] = [f"{group}{i + 1}", 55.0, 10.0, 5.0, 8.0, 50.04 + i, '="-"']
    return {"Tabelle1": dict(source_cells=rows, merged_cells=[])}


def secondary(primaries):
    rows = [[None] * 6 for _ in range(32)]
    rows[3][:2] = ["material", "steel S355J2 + N"]
    rows[7] = [
        "test piece",
        "crosspiece value / mm",
        "test piece length / mm",
        "test piece width / mm",
        "test piece thickness / mm",
        "impact energy KV2 750/5 / J",
    ]
    for i, primary in enumerate(primaries):
        rows[8 + i] = [primary["specimen_id"], 8.0, 55.0, 10.0, 5.0, round(primary["absorbed_energy_J"], 1)]
    rows[20][0] = "Expanded uncertainty is 7.1 J. Coverage k = 3 for this synthetic fixture."
    return {"Charpy_test_results": dict(source_cells=rows, merged_cells=[])}


def ahss():
    result = {}
    for name in data.AHSS_SHEETS:
        result[name] = dict(
            source_cells=[
                ["Time, s", "Current, μA", None, "Temperature", None, "293 K"],
                [12.0, 20.0, None, None, None, None],
                [24.0, 21.0, None, "Material", "MS AHSS grade 1300 ", None],
                [24.0, -2.0, None, None, None, None],
                [6.0, None, None, None, None, None],
            ],
            merged_cells=[],
        )
    return result


def test_protocol_converts_dimensions_keeps_energy_and_unbroken_state(monkeypatch):
    book = protocol()
    monkeypatch.setattr(data, "_workbook", lambda _: deepcopy(book))
    record = data.parse_bam_protocol(b"fixture", member=data.BAM_PROTOCOLS[0])
    a = record["specimens"][0]
    assert a["temperature_K"] == 295.65
    assert a["length_m"] == 0.055 and a["thickness_m"] == 0.005
    assert a["absorbed_energy_J"] == 50.04 and a["direction"] == "rolling"
    assert a["source_cells"][6] == '="-"' and a["lateral_expansion_m"] is None
    assert not a["broke"] and a["measured_composition_mass_fraction"] is None
    assert record["worksheet"]["source_cells"] == book["Tabelle1"]["source_cells"]
    data.verify_material_process_data(record)
    json.dumps(record, allow_nan=False)


@pytest.mark.parametrize(
    "r,c,v", [(8, 6, "room temperature"), (40, 6, "kJ"), (48, 1, "broken"), (41, 1, "KY1"), (41, 7, 0)]
)
def test_protocol_schema_changes_require_review(monkeypatch, r, c, v):
    book = protocol()
    book["Tabelle1"]["source_cells"][r - 1][c - 1] = v
    monkeypatch.setattr(data, "_workbook", lambda _: book)
    with pytest.raises(ValueError):
        data.parse_bam_protocol(b"fixture", member=data.BAM_PROTOCOLS[0])


def test_secondary_reconciles_rounding_without_creating_independent_observations(monkeypatch):
    primaries = []
    for group, member in zip(("KX", "KY"), data.BAM_PROTOCOLS, strict=True):
        monkeypatch.setattr(data, "_workbook", lambda _, group=group: protocol(group))
        primaries.extend(data.parse_bam_protocol(b"fixture", member=member)["specimens"])
    monkeypatch.setattr(data, "_workbook", lambda _: secondary(primaries))
    record = data.parse_bam_secondary(b"report", primary_specimens=primaries)
    assert len(record["comparisons"]) == 6
    assert all(c["dimension_residuals_m"] == [0.0] * 4 for c in record["comparisons"])
    assert record["comparisons"][0]["primary_minus_secondary_energy_J"] == pytest.approx(0.04)
    assert not record["independent_validation"]
    assert all(not c["independent_observation"] for c in record["comparisons"])
    assert record["reported_expanded_energy_uncertainty_J"] == 7.1
    assert record["reported_coverage_factor"] == 3.0


def test_lot_interpretation_requires_reviewed_primary_mission_document(monkeypatch):
    monkeypatch.setattr(data, "_pinned_file", lambda *_: (b"changed", dict(sha256="0" * 64)))
    with pytest.raises(ValueError, match="material-lot interpretation"):
        data.read_bam_charpy("unused")


def test_ahss_retains_dependent_clocks_negative_current_and_missing_values(monkeypatch):
    book = ahss()
    monkeypatch.setattr(data, "_workbook", lambda _: deepcopy(book))
    record = data.parse_ahss_permeation(b"fixture")
    assert record["summary"] == dict(
        histories=2, rows=8, physical_specimens_resolved=None, histories_are_independent_specimens=False
    )
    h = record["histories"][0]
    assert [r["time_s"] for r in h["rows"]] == [12.0, 24.0, 24.0, 6.0]
    assert h["rows"][2]["current_A"] == -2e-6 and h["rows"][3]["current_A"] is None
    assert h["summary"]["repeated_adjacent_times"] == 1
    assert h["summary"]["decreasing_adjacent_times"] == 1
    assert h["effective_permeation_area_m2"] is None
    assert h["time_origin_for_concentration_boundary"] is None
    assert h["continuous_entry_current_schedule"] is None
    assert h["source_cathodic_current_density_endpoints_A_m2"] == [5.0, 10.0]
    assert record["histories"][1]["source_cathodic_current_density_endpoints_A_m2"] == [50.0, 100.0]
    assert all(r["measured_flux_mol_m2_s"] is None for h in record["histories"] for r in h["rows"])
    data.verify_material_process_data(record)
    json.dumps(record, allow_nan=False)


@pytest.mark.parametrize("value", [True, "NaN", float("inf"), "", None])
def test_numeric_invalid_values_never_become_zero(value):
    assert data._numeric(value)["value"] is None
    assert data._numeric(0.0)["value"] == 0.0


@pytest.mark.parametrize("mutation", ["temperature", "units", "sheet_order"])
def test_ahss_metadata_changes_require_review(monkeypatch, mutation):
    book = ahss()
    if mutation == "sheet_order":
        book = dict(reversed(list(book.items())))
    else:
        first = next(iter(book.values()))["source_cells"][0]
        first[5 if mutation == "temperature" else 1] = "unreviewed"
    monkeypatch.setattr(data, "_workbook", lambda _: book)
    with pytest.raises(ValueError):
        data.parse_ahss_permeation(b"fixture")


def test_changed_observations_invalidate_seal(monkeypatch):
    monkeypatch.setattr(data, "_workbook", lambda _: ahss())
    record = data.parse_ahss_permeation(b"fixture")
    record["histories"][0]["rows"][0]["current_A"] = 0.0
    with pytest.raises(ValueError, match="changed"):
        data.verify_material_process_data(record)


def test_file_pin_checks_rights_size_supplier_digest_and_conditions(tmp_path, monkeypatch):
    path = tmp_path / data.AHSS_FILENAME
    payload = b"reviewed fixture"
    path.write_bytes(payload)
    asset = SimpleNamespace(
        filename=path.name,
        bytes=len(payload),
        checksum="sha256:" + hashlib.sha256(payload).hexdigest(),
        checksum_origin="publisher_metadata",
    )
    spec = SimpleNamespace(
        id=data.AHSS_ID,
        assets=[asset],
        rights_review="reviewed_license",
        acquisition="allowed",
        citation="Synthetic fixture",
        creators=["Fixture"],
        version="1",
        license_id="CC-BY-4.0",
        source="https://example.org",
        rights_evidence_url="https://example.org/rights",
    )
    monkeypatch.setattr(data, "catalog", lambda: [spec])
    monkeypatch.setattr(data, "_workbook", lambda _: ahss())
    record = data.read_ahss_permeation(path)
    assert record["provenance"]["supplier_checksum_verified"]
    assert record["conditions"]["thickness_m"] == 0.0012
    assert record["conditions"]["effective_permeation_area_m2"] is None
    assert record["applicability"]["fitted_transport_parameters"] is None
    assert record["applicability"]["slab_kinetic"] == "needs_data"
    path.write_bytes(b"unreviewed data!")
    with pytest.raises(ValueError, match="checksum|size"):
        data.read_ahss_permeation(path)
    path.write_bytes(payload)
    spec.rights_review = "unresolved"
    with pytest.raises(ValueError, match="reviewed rights"):
        data.read_ahss_permeation(path)


def test_original_source_conditions_reach_nine_real_facade_gates_before_native_import(monkeypatch):
    import pipeline_integrity.hydrogen_transport as hydrogen
    import pipeline_integrity.material_quality as quality

    def unexpected(*_, **__):
        raise AssertionError("unresolved source conditions imported the native owner")

    monkeypatch.setattr(hydrogen, "material_source_guard", unexpected)
    monkeypatch.setattr(quality, "material_source_guard", unexpected)
    bam = dict(
        provenance=dict(citation="Synthetic source fixture", archive_sha256="1" * 64, license_id="CC-BY-4.0"),
        applicability=dict(reasons=["Measured tested-lot chemistry and transition support are unresolved."]),
        record_digest="2" * 64,
    )
    ahss = dict(
        provenance=dict(citation="Synthetic source fixture", sha256="3" * 64, license_id="CC-BY-4.0"),
        applicability=dict(reasons=["Effective area and transport parameters are unresolved."]),
        record_digest="4" * 64,
        observations=dict(
            histories=[
                dict(history_id=name, temperature_K=293.0, summary=dict(rows=4)) for name in data.AHSS_SHEETS
            ]
        ),
    )
    apply = runpy.run_path("scripts/review_material_process_data.py")["apply_source_boundaries"]
    records = apply(bam, ahss)
    assert len(records) == 9
    assert all(row["status"] == "needs_data" and row["native_receipt"] is None for row in records)
    assert records[3]["context"]["asset_id"] == records[6]["context"]["asset_id"]
    assert all(row["context"]["metadata"]["physical_specimen_id"] is None for row in records[3:])
    assert all(row["request"]["concentration_basis"] == "unresolved" for row in records[3:])
