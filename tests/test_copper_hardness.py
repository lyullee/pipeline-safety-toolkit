"""Authored workbook fixtures test source retention and explicit assumption boundaries."""

import io
import zipfile
from copy import deepcopy

import openpyxl
import pytest

from pipeline_integrity.contracts import EvidenceRef, digest
from pipeline_integrity.copper_hardness import (
    CopperGeometryReview,
    _parse_workbook,
    _xlsx_snapshot,
    evaluate_copper_indentation,
)


def fixture(
    method="testing.vickers", *, unit=None, force=40.0, mean=210.0, angle=136, notes="retain this note"
):
    book = openpyxl.Workbook()
    sheet = book.active
    metadata = [
        ("Versuchsbezeichnung", "authored indentation", None),
        ("Probenbezeichnung", "synthetic-group", None),
        ("Werkstoff", "nominal-only", None),
        ("Probe Vorbereitung", "NN", None),
        ("Versuchs-Datum", "01.01.2020", None),
        ("Norm", "authored method label", None),
        ("Prüfkraft F", force, "N"),
        ("Einwirkdauer der Prüfkraft", 12, "s"),
        ("Eindringwinkel", angle, None),
        ("Kugeldurchmesser", 2.0, "mm"),
    ]
    for row in metadata:
        sheet.append(row)
    sheet.append(["[Daten]"])
    sheet.append(
        ["Probennummer", "d1", "d2", "d", "HV " if method == "testing.vickers" else "HBW", "Bemerkung"]
    )
    length_unit = unit or ("µm" if method == "testing.vickers" else "mm")
    sheet.append(
        [
            None,
            length_unit,
            length_unit,
            length_unit,
            "HV 5" if method == "testing.vickers" else "HBW 2/4",
            None,
        ]
    )
    values = (200.0, 220.0, mean) if method == "testing.vickers" else (0.7, 0.9, 0.75)
    sheet.append(["test-2", *values, 99.0, notes])
    sheet.append(["test-1", *values, 101.0, None])
    stream = io.BytesIO()
    book.save(stream)
    return _parse_workbook(
        stream.getvalue(), "authored.xlsx", method, dict(citation="Authored test fixture", license_id="MIT")
    )


def review(test, **changes):
    data = dict(
        expected_test_digest=test["record_digest"],
        expected_row_digest=test["rows"][0]["record_digest"],
        units_and_column_mapping_reviewed=True,
        force_dimensions_dwell_and_method_reviewed=True,
        geometry_model_reviewed=True,
        diagnostic_limitations_reviewed=True,
        declared_material_condition_id="assumed_material",
        declared_surface_condition_id="assumed_surface",
        assumption_evidence=EvidenceRef(
            id="assumptions", citation="Authored numerical scenario only", origin="synthetic"
        ),
    )
    return CopperGeometryReview(**(data | changes))


def apply(test, r=None, **kwargs):
    return evaluate_copper_indentation(test, test["rows"][0]["row_id"], r, **kwargs)


def test_original_row_order_notes_units_and_NN_are_preserved_without_native_import():
    test = fixture()
    assert [r["cells"]["provider_indent_id"]["value"] for r in test["rows"]] == ["test-2", "test-1"]
    assert test["rows"][0]["cells"]["notes"]["value"] == "retain this note"
    assert test["metadata"]["Probe Vorbereitung"]["state"] == "unresolved_placeholder"
    result = apply(test)
    assert result["source_condition_result"]["status"] == "needs_data"
    assert result["declared_assumptions_geometry_diagnostic"] is None
    assert result["source_condition_result"]["request"]["parameters"]["test_force_N"] == 40.0
    assert result["source_condition_result"]["request"]["parameters"]["first_diagonal_m"] == pytest.approx(
        0.0002
    )


@pytest.mark.parametrize(
    "flag",
    [
        "units_and_column_mapping_reviewed",
        "force_dimensions_dwell_and_method_reviewed",
        "geometry_model_reviewed",
        "diagnostic_limitations_reviewed",
    ],
)
def test_each_missing_review_withholds_assumed_geometry(flag):
    test = fixture()
    result = apply(test, review(test, **{flag: False}))
    assert result["declared_assumptions_geometry_diagnostic"]["status"] == "needs_data"


@pytest.mark.parametrize("key", ["declared_material_condition_id", "declared_surface_condition_id"])
def test_unknown_assumed_condition_remains_unknown(key):
    test = fixture()
    result = apply(test, review(test, **{key: None}))
    assert result["declared_assumptions_geometry_diagnostic"]["status"] == "needs_data"


def test_unknown_length_unit_does_not_gain_nominal_HV_force_or_dimensions():
    test = fixture(unit="unknown")
    r = apply(test, review(test))["declared_assumptions_geometry_diagnostic"]
    assert r["status"] == "needs_data"
    assert r["request"]["parameters"]["first_diagonal_m"] is None
    assert r["request"]["parameters"]["test_force_N"] == 40.0


def test_formula_force_is_preserved_and_never_executed():
    test = fixture(force="=20*2")
    assert test["metadata"]["Prüfkraft F"]["state"] == "unevaluated_formula"
    assert apply(test, review(test))["declared_assumptions_geometry_diagnostic"]["status"] == "needs_data"


@pytest.mark.parametrize("force", [0, -1, True])
def test_observed_invalid_force_is_rejected_not_zero_filled(force):
    with pytest.raises(ValueError, match="positive"):
        apply(fixture(force=force))


def test_nonstandard_explicit_angle_is_outside_selected_native_geometry():
    test = fixture(angle=120)
    assert apply(test, review(test))["declared_assumptions_geometry_diagnostic"]["status"] == "out_of_scope"


@pytest.mark.parametrize("mutation", ["row", "test", "review"])
def test_source_and_review_pins_reject_tampering(mutation):
    test = fixture()
    r = review(test)
    if mutation == "row":
        test["rows"][0]["cells"]["d1"]["value"] = 1.0
        test["record_digest"] = digest({k: v for k, v in test.items() if k != "record_digest"})
    elif mutation == "test":
        test["metadata"]["Werkstoff"]["value"] = "changed"
    else:
        r = r.model_copy(update={"expected_row_digest": "0" * 64})
    with pytest.raises(ValueError):
        apply(test, r)


def test_assumptions_cannot_be_relabelled_laboratory_observations():
    test = fixture()
    with pytest.raises(ValueError, match="synthetic or user_declared"):
        review(
            test, assumption_evidence=EvidenceRef(id="bad", citation="Not a measurement", origin="laboratory")
        )


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_review_model_copy_is_invalid_before_JSON_clone(value):
    test = fixture()
    r = review(test).model_copy(update={"diagnostic_limitations_reviewed": value})
    with pytest.warns(UserWarning, match="serializer warnings"), pytest.raises(ValueError):
        apply(test, r)


@pytest.mark.parametrize(
    "member,content",
    [
        ("../escape.xml", b"<x/>"),
        ("xl/vbaProject.bin", b"x"),
        ("xl/externalLinks/link.xml", b"<x/>"),
        ("x.xml", b'<!DOCTYPE x [<!ENTITY a "x">]><x/>'),
        ("x.rels", b'<Relationships><Relationship TargetMode="External"/></Relationships>'),
    ],
)
def test_inner_workbook_unsafe_or_active_parts_are_rejected(member, content):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as z:
        z.writestr(member, content)
    with pytest.raises(ValueError):
        _xlsx_snapshot(output.getvalue())


def test_brinell_source_mean_is_not_replaced_by_two_diameter_average():
    test = fixture("testing.brinell")
    result = apply(test)["source_condition_result"]
    assert result["request"]["parameters"]["mean_indentation_diameter_m"] == 0.00075
    assert test["rows"][0]["cells"]["d1"]["value"] == 0.7


def test_source_input_is_not_modified_when_constructing_diagnostic_with_owner():
    # Reuse a protocol spy, not a second implementation of indentation physics.
    from test_hardness_exchange import OwnerSpy

    test = fixture()
    original = deepcopy(test)
    owner = OwnerSpy()
    result = apply(test, review(test), client=owner)
    assert test == original and len(owner.calls) == 1
    assert result["source_condition_result"]["status"] == "needs_data"
    diagnostic = result["declared_assumptions_geometry_diagnostic"]
    assert diagnostic["status"] == "computed" and diagnostic["native_receipt"] is not None
    assert diagnostic["context"]["origin"] == "simulation"
    assert result["original_observation_modified"] is False
