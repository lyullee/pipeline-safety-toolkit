"""Explicit unit/datum review, source clocks and incomplete command coverage."""

import csv

import pytest

from pipeline_integrity.tank_data import (
    CoupledTankReadRequest,
    coupled_tank_frames,
    review_coupled_tank_source,
)


def fixture(root):
    header = ["t_ref", *(f"Experimento{i}" for i in range(1, 11))]
    for name, value in [("h1.csv", 40.0), ("h2.csv", 30.0)]:
        with (root / name).open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(header)
            w.writerows([[t, *([value] * 10)] for t in [0.0, 4.001, 8.0002]])
    (root / "U.csv").write_text("t_ref,U\n0,50\n4.001,60\n", encoding="utf-8")
    (root / "complete_data.csv").write_text("t,h1,h2,u\n0,1,2,50\n4,2,3,60\n", encoding="utf-8")
    (root / "operational_parameters.json").write_text('{"unit":"mm"', encoding="utf-8")
    (root / "README.md").write_text("Original synthetic table fixture", encoding="utf-8")


def request(**changes):
    return CoupledTankReadRequest(
        **(
            dict(
                run_number=1,
                level_unit="cm",
                level_zero_offset_m=0.0,
                unit_mapping_evidence_id="unit",
                datum_evidence_id="datum",
                source_origin="synthetic",
            )
            | changes
        )
    )


def test_source_metadata_is_not_repaired_and_unit_is_not_inferred_from_unverified_document(tmp_path):
    fixture(tmp_path)
    before = (tmp_path / "operational_parameters.json").read_bytes()
    review = review_coupled_tank_source(tmp_path)
    assert review["status"] == "needs_data" and review["operating_metadata_parse_error"]
    assert review["readme_level_unit"] is None and review["invalid_json_contains_mm_unit_tokens"]
    assert review["level_rows_without_same_time_command"] == 1
    assert (
        not review["original_metadata_repaired"]
        and before == (tmp_path / "operational_parameters.json").read_bytes()
    )


def test_explicit_mapping_preserves_jitter_and_missing_final_command(tmp_path):
    fixture(tmp_path)
    frames, metadata = coupled_tank_frames(tmp_path, request(level_zero_offset_m=0.01))
    assert [f.time_s for f in frames] == [0.0, 4.001, 8.0002]
    assert frames[0].values["tank1_level_m"] == pytest.approx(0.41)
    assert frames[-1].values["pump_command_percent"] is None and not frames[-1].valid["pump_command_percent"]
    assert frames[0].origin == "synthetic" and metadata["source_rows"] == [2, 3, 4]
    assert not metadata["unit_datum_and_geometry_field_validated"]


def test_millimeter_mapping_and_experiment_clock_are_explicit(tmp_path):
    fixture(tmp_path)
    cm, _ = coupled_tank_frames(tmp_path, request())
    mm, _ = coupled_tank_frames(tmp_path, request(level_unit="mm", run_number=2))
    assert mm[0].values["tank1_level_m"] == cm[0].values["tank1_level_m"] / 10
    assert cm[0].clock_id != mm[0].clock_id and cm[0].stream_id != mm[0].stream_id


@pytest.mark.parametrize("time", ["4.001", "-1"])
def test_duplicate_or_negative_native_clock_is_not_sorted_or_repaired(tmp_path, time):
    fixture(tmp_path)
    for name in ["h1.csv", "h2.csv"]:
        p = tmp_path / name
        p.write_text(p.read_text(encoding="utf-8").replace("8.0002", time), encoding="utf-8")
    with pytest.raises(ValueError, match="native experiment clocks"):
        coupled_tank_frames(tmp_path, request())


def test_missing_level_is_not_interpolated(tmp_path):
    fixture(tmp_path)
    p = tmp_path / "h1.csv"
    rows = list(csv.reader(p.open(encoding="utf-8")))
    rows[2][1] = ""
    with p.open("w", encoding="utf-8", newline="") as f:
        csv.writer(f).writerows(rows)
    frames, _ = coupled_tank_frames(tmp_path, request())
    assert frames[1].values["tank1_level_m"] is None and not frames[1].valid["tank1_level_m"]


def test_native_clock_disagreement_between_level_files_is_not_aligned_by_nearest_row(tmp_path):
    fixture(tmp_path)
    p = tmp_path / "h2.csv"
    p.write_text(p.read_text(encoding="utf-8").replace("4.001", "4.002"), encoding="utf-8")
    with pytest.raises(ValueError, match="released t_ref"):
        coupled_tank_frames(tmp_path, request())


def test_python_boolean_cannot_become_a_run_number_or_datum(tmp_path):
    fixture(tmp_path)
    with pytest.raises(ValueError):
        coupled_tank_frames(tmp_path, request().model_copy(update={"run_number": True}))
