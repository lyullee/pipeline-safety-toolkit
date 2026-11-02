import zipfile

import pytest

from pipeline_integrity.loaders import elvhys_table, h2safe_table, iter_elvhys_frames


def test_cryogenic_event_clock_and_contradictory_pressure_references_are_preserved(tmp_path):
    source = tmp_path / "signals.csv"
    source.write_text(
        "Time (seconds);PC101 - p_2a (bar_a);PW111 (bar_a);p_1 (bar_g);T_2a (K)\n"
        "-1.0;3.0;-0.01;2.0;0\n-0.99999;3.1;0.01;2.1;20.0\n",
        encoding="utf-8",
    )
    table = elvhys_table(source)
    assert table["median_observed_interval_s"] == pytest.approx(1e-5)
    assert table["units"]["PC101 - p_2a (bar_a)"] == "Pa_abs"
    assert table["units"]["PW111 (bar_a)"] == "Pa_unresolved_reference"
    assert table["units"]["p_1 (bar_g)"] == "Pa_unresolved_reference"
    assert not table["valid"].iloc[0]["T_2a (K)"]
    frames = list(iter_elvhys_frames(source, event_time_offset_s=10.0, clock_id="reviewed_common_clock"))
    assert frames[0].time_s == 9.0 and frames[0].values["PC101 - p_2a (bar_a)"] == 300000.0
    assert frames[0].values["T_2a (K)"] == 0.0 and not frames[0].valid["T_2a (K)"]
    with pytest.raises(ValueError, match="negative"):
        list(iter_elvhys_frames(source, event_time_offset_s=0.0, clock_id="wrong_origin"))
    source.write_text("Time (seconds);T_2a (K)\n-1;20\n-1;21\n", encoding="utf-8")
    reviewed = elvhys_table(source)
    assert reviewed["duplicate_adjacent_timestamps"] == 1 and not reviewed["clock_strictly_increasing"]
    with pytest.raises(ValueError, match="repeat"):
        list(iter_elvhys_frames(source, event_time_offset_s=10.0, clock_id="instrument_clock"))


def test_hydrogen_signal_missing_values_and_unknown_units_are_not_inferred(tmp_path):
    source = tmp_path / "signals.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("Lab1_Small/test1.csv", "time,p1,p2\n0,3,#N/A\n1,4,5\n")
        archive.writestr("Lab1_Small/sensors.csv", "id,X,Y,Z\nP1,1,2,3\nP2,4,5,6\n")
    table = h2safe_table(source, "Lab1_Small", "test1")
    assert not table["valid"].iloc[0]["p2"] and table["valid"].iloc[1]["p2"]
    assert table["channels_with_coordinate_match"] == {"p1": True, "p2": True}
    assert table["signal_unit"] == "source_unit_unresolved"
    assert table["physical_model_input_qualified"] is False
