"""Authored calibration sources test original extents, uncertainty and covariance."""

import hashlib
import io
import json
import re
import subprocess
import sys
import zipfile
from copy import deepcopy

import pytest
from openpyxl import Workbook

from pipeline_integrity.calibration_data import (
    ARCHIVE_NAME,
    EMUE_ID,
    METHOD_FILES,
    _csv_observations,
    emue_calibration_inputs,
    parse_emue_archive,
    read_emue_calibration,
    verify_calibration_source_record,
)
from pipeline_integrity.contracts import digest
from pipeline_integrity.datasets import DatasetSpec


def workbook(*, changed_header=False, missing=False, formula=False):
    book = Workbook()
    book.remove(book.active)
    for name in ("Data", "Var_x", "Var_y", "Var_xy"):
        sheet = book.create_sheet(name)
        sheet.append(
            ["y", "x"]
            if changed_header and name == "Data"
            else ["x", "y"]
            if name == "Data"
            else [f"V{i}" for i in range(1, 31)]
        )
        for i in range(30):
            if name == "Data":
                sheet.append([0.008 + i * 0.0001, 0.97 - i * 0.001])
            else:
                sheet.append(
                    [
                        (
                            (1e-8 if i == j else 1e-9)
                            if name == "Var_x"
                            else (1e-4 if i == j else 1e-5)
                            if name == "Var_y"
                            else (i + 1) / (j + 1) * 1e-10
                        )
                        for j in range(30)
                    ]
                )
        if missing and name == "Var_y":
            sheet["C4"] = None
        if formula and name == "Data":
            sheet["A2"] = "=1/0"
    stream = io.BytesIO()
    book.save(stream)
    # Match the genuine upstream dimension defect without changing source cells.
    old = zipfile.ZipFile(io.BytesIO(stream.getvalue()))
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as new:
        for member in old.infolist():
            raw = old.read(member.filename)
            if member.filename.startswith("xl/worksheets/"):
                raw = re.sub(b'<dimension ref="[^"]+"', b'<dimension ref="A1:A1"', raw)
            new.writestr(member.filename, raw)
    return output.getvalue()


def csv_bytes(*, missing=False):
    rows = ["mean.Q;SU.Q;mean.Psi;SU.Psi"]
    for i in range(30):
        rows.append(f"{0.0001 + i * 0.000001};{'' if missing and i == 0 else '0.00000001'};100;0.01")
    return ("\r\n".join(rows) + "\r\n").encode()


def archive(**options):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("data_A223.csv", csv_bytes(missing=options.pop("missing_csv", False)))
        z.writestr("MC_data_A223.xlsx", workbook(**options))
        for name in METHOD_FILES:
            z.writestr(name, b"Authored non-executable method fixture")
    return output.getvalue()


@pytest.fixture
def source(tmp_path):
    raw = archive()
    folder = tmp_path / EMUE_ID
    folder.mkdir()
    path = folder / ARCHIVE_NAME
    path.write_bytes(raw)
    spec = DatasetSpec.model_validate_json(
        json.dumps(
            dict(
                id=EMUE_ID,
                title="Authored calibration fixture",
                source="https://example.org/data",
                citation="Original source-contract fixture",
                creators=["Fixture author"],
                version="fixture",
                origin="mixed_compilation",
                license_id="CC-BY-4.0",
                rights_evidence_url="https://example.org/license",
                rights_review="reviewed_license",
                acquisition="allowed",
                checked_on="2026-10-27",
                intended_uses=["source contract"],
                limitations=["authored values"],
                split_unit="whole correlated campaign",
                assets=[
                    dict(
                        filename=ARCHIVE_NAME,
                        url="https://example.org/source.zip",
                        bytes=len(raw),
                        checksum="sha256:" + hashlib.sha256(raw).hexdigest(),
                        checksum_origin="publisher_metadata",
                        default=True,
                    )
                ],
                raw_in_code_release=False,
            )
        )
    )
    return tmp_path, path, spec


def test_actual_xml_extents_preserved_without_repair():
    result = parse_emue_archive(archive())
    assert result["status"] == "source_values_available"
    assert result["worksheets"]["Data"]["declared_dimension"] == "A1:A1"
    assert result["worksheets"]["Data"]["observed_dimension"] == "A1:B31"
    assert result["worksheets"]["Var_x"]["observed_dimension"] == "A1:AD31"
    assert all(
        not s["source_bytes_changed"] and not s["declared_dimension_matches_observed"]
        for s in result["worksheets"].values()
    )
    assert len(result["intermediate"]["x"]) == 30


def test_full_covariance_axis_and_point_order_not_diagonalized():
    value = parse_emue_archive(archive())["intermediate"]
    cov = value["standard_joint_covariance"]
    xy = value["source_blocks"]["Var_xy"]
    assert len(cov) == 60 and all(len(row) == 60 for row in cov)
    assert cov[0][31] == xy[0][1]
    assert cov[31][0] == xy[0][1]
    assert cov[1][30] == xy[1][0] != xy[0][1]
    assert value["covariance_exactly_symmetric"] and value["covariance_positive_diagonal"]
    assert value["offdiagonal_nonzero_entries"] == 3540
    assert not value["covariance_repaired"] and not value["covariance_diagonalized"]
    assert value["covariance_order"][0] == "x:1" and value["covariance_order"][30] == "y:1"


def test_primitive_type_A_not_total_and_no_thermo_defaults():
    result = parse_emue_archive(archive())
    primitive = result["primitive"]
    row = primitive["observations"][0]
    assert row["mass_flow_kg_s"] == 0.0001
    assert row["mass_flow_type_A_standard_uncertainty_kg_s"] == 1e-8
    assert row["critical_mass_flux_kg_m2_s"] == 100
    assert row["coverage_factor"] is None and row["degrees_of_freedom"] is None
    assert not row["type_A_is_total_uncertainty"]
    assert primitive["upstream_pressure_pa_abs"] is None and primitive["gas_mole_fractions"] is None
    assert result["intermediate"]["source_origin"].startswith("publisher_Monte_Carlo")
    assert (
        not result["intermediate"]["publisher_MC_samples_acquired"] and not result["intermediate"]["MC_rerun"]
    )
    assert (
        result["intermediate"]["independent_validation"] is None
        and result["intermediate"]["observation_time_s"] is None
    )


@pytest.mark.parametrize("options", [{"missing": True}, {"missing_csv": True}])
def test_unknown_values_keep_source_and_withhold(options):
    result = parse_emue_archive(archive(**options))
    assert result["status"] == "needs_data"
    if options.get("missing"):
        assert result["intermediate"]["source_blocks"]["Var_y"][2][2] is None
        assert not result["intermediate"]["covariance_complete"]
    else:
        assert result["primitive"]["observations"][0]["source_tokens"][1] == ""
        assert result["primitive"]["observations"][0]["mass_flow_type_A_standard_uncertainty_kg_s"] is None


@pytest.mark.parametrize("token", ["nan", "inf", "-inf", "", "bad"])
def test_nonfinite_or_missing_csv_values_not_imputed(token):
    raw = csv_bytes().replace(b"0.00000001", token.encode(), 1)
    row = _csv_observations(raw)["observations"][0]
    assert row["source_tokens"][1] == token
    assert row["mass_flow_type_A_standard_uncertainty_kg_s"] is None
    assert not row["numeric_domain_valid"]


def test_negative_standard_uncertainty_is_invalid():
    row = _csv_observations(csv_bytes().replace(b"0.00000001", b"-0.00000001", 1))["observations"][0]
    assert row["mass_flow_type_A_standard_uncertainty_kg_s"] < 0
    assert not row["numeric_domain_valid"]


def test_explicit_zero_type_A_component_is_not_missing_or_total_zero():
    row = _csv_observations(csv_bytes().replace(b"0.00000001", b"0", 1))["observations"][0]
    assert row["mass_flow_type_A_standard_uncertainty_kg_s"] == 0
    assert row["numeric_domain_valid"] and not row["type_A_is_total_uncertainty"]


@pytest.mark.parametrize("options", [{"changed_header": True}, {"formula": True}])
def test_changed_axis_or_formula_not_repaired(options):
    with pytest.raises(ValueError):
        parse_emue_archive(archive(**options))


@pytest.mark.parametrize("payload", [b"", b"x" * 2000001], ids=["empty", "too_large"])
def test_archive_budget(payload):
    with pytest.raises(ValueError, match="2 MB"):
        parse_emue_archive(payload)


def test_unsafe_archive_member_refused():
    raw = io.BytesIO(archive())
    with zipfile.ZipFile(raw, "a") as z:
        z.writestr("../outside", b"x")
    with pytest.raises(ValueError, match="unsafe"):
        parse_emue_archive(raw.getvalue())


def test_source_replay_requires_original_asset_bytes(source):
    root, path, spec = source
    record = read_emue_calibration(path, source_spec=spec)
    assert verify_calibration_source_record(record, source_root=root, source_spec=spec)
    inputs = emue_calibration_inputs(record, source_root=root, source_spec=spec)
    assert len(inputs["standard_joint_covariance"]) == 60
    assert inputs["x_unit"] == inputs["y_unit"] == "1"
    assert inputs["independent_validation"] is None and inputs["observation_time_s"] is None
    raw = bytearray(path.read_bytes())
    raw[-10] ^= 1
    path.write_bytes(raw)
    with pytest.raises(ValueError, match="checksum"):
        emue_calibration_inputs(record, source_root=root, source_spec=spec)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda r: r.update(origin="measured_process"),
        lambda r: r["primitive"].update(uncertainty_kind="expanded_total"),
        lambda r: r["intermediate"].update(source_origin="raw_independent_reference_readings"),
        lambda r: r["intermediate"].update(covariance_diagonalized=True),
        lambda r: r["intermediate"]["standard_joint_covariance"][0].__setitem__(1, 0),
        lambda r: r["intermediate"]["covariance_order"].reverse(),
        lambda r: r["intermediate"].update(x_unit="kg/s"),
        lambda r: r["intermediate"].update(observation_time_s=list(range(30))),
        lambda r: r["intermediate"].update(independent_validation={"approved": True}),
        lambda r: r["intermediate"].update(publisher_MC_samples_acquired=True),
        lambda r: r["selected_members"]["MC_data_A223.xlsx"].update(sha256="0" * 64),
    ],
)
def test_rehashed_source_semantics_change_refused(source, mutation):
    root, path, spec = source
    record = read_emue_calibration(path, source_spec=spec)
    mutation(record)
    record["record_digest"] = digest({k: v for k, v in record.items() if k != "record_digest"})
    with pytest.raises(ValueError, match="differ"):
        verify_calibration_source_record(record, source_root=root, source_spec=spec)


@pytest.mark.parametrize(
    "key,value", [("origin", "simulation"), ("license_id", "Unresolved"), ("rights_review", "unresolved")]
)
def test_unknown_rights_or_wrong_dataset_origin_refused(source, key, value):
    _, path, spec = source
    body = json.loads(spec.model_dump_json())
    body[key] = value
    changed = DatasetSpec.model_validate_json(json.dumps(body))
    with pytest.raises(ValueError):
        read_emue_calibration(path, source_spec=changed)


def test_missing_original_is_not_a_self_authenticating_receipt(source):
    root, path, spec = source
    record = read_emue_calibration(path, source_spec=spec)
    path.unlink()
    with pytest.raises(FileNotFoundError):
        verify_calibration_source_record(record, source_root=root, source_spec=spec)


def test_selection_copy_does_not_mutate_source_record(source):
    root, path, spec = source
    record = read_emue_calibration(path, source_spec=spec)
    before = deepcopy(record)
    selected = emue_calibration_inputs(record, source_root=root, source_spec=spec)
    assert isinstance(selected["x"], tuple) and isinstance(selected["standard_joint_covariance"][0], tuple)
    assert record == before


def test_calibration_import_is_lazy():
    code = "import sys;import pipeline_integrity.calibration_data;assert 'openpyxl' not in sys.modules;assert 'numpy' not in sys.modules;assert 'scipy' not in sys.modules;assert 'pypdf' not in sys.modules"
    subprocess.run([sys.executable, "-c", code], check=True, text=True, capture_output=True)
