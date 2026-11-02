"""Original miniature LIS fixtures preserve source axes, units, signed and missing rows."""

import pytest

from pipeline_integrity.copper_tensile import parse_copper_tensile


def payload(
    *,
    columns="Zeit\tWeg\tKraft\tDehnung\tSpannung",
    units="sec\tmm\tkN\t%\tMPa",
    extra_metadata="",
    rows=None,
):
    rows = "0\t0\t0,1\t0,01\t5\n0,1\t1\t0,2\t0,02\t10\n0,1\t2\t-0,3\t0,015\tNN\n" if rows is None else rows
    return (
        "Authored fixture\nProbenbezeichnung\t\tA_02\nWerkstoff\t\tCu_example\nProbenquerschnitt\tmm²\t20\nAnfangsmesslänge\tmm\t25\nSteigung des elastischen Teils (mE)\tGPa\t100\n"
        + extra_metadata
        + "[Daten]\n"
        + columns
        + "\n"
        + units
        + "\n"
        + rows
    ).encode("cp1252")


def parse(raw=None):
    return parse_copper_tensile(
        payload() if raw is None else raw,
        member="Tensile test/Primary data/Tensile_A_02.lis",
        source={"dataset_id": "authored", "license_id": "MIT"},
    )


def test_clock_duplicates_signed_load_and_reported_strain_stay_in_original_order():
    record = parse()
    record.check_integrity()
    si = record.si_columns()
    assert si["time_s"] == (0.0, 0.1, 0.1)
    assert si["force_N"] == (100.0, 200.0, -300.0)
    assert si["reported_engineering_strain"] == pytest.approx((0.0001, 0.0002, 0.00015))
    assert si["machine_travel_m"] == (0.0, 0.001, 0.002)
    assert si["reported_engineering_stress_Pa"][-1] is None
    assert record.quantity("Probenquerschnitt", "mm²", 1e-6) == pytest.approx(20e-6)
    assert len(record.source_line_numbers) == len(record.data_rows) == 3


@pytest.mark.parametrize(
    "changes",
    [
        {"units": "sec\tmm\tN\t%\tMPa"},
        {"columns": "Zeit\tWeg\tKraft\tStrain\tSpannung"},
        {"extra_metadata": "Probenbezeichnung\t\tB_01\n"},
        {"rows": "0\t0\t0,1\t0,01\t5\n\n0,2\t1\t0,2\t0,02\t10\n"},
        {"rows": "0\t0\t0,1\t0,01\t5\n0.1\t1\t0,2\t0,02\t10\n0,2\t2\t0,3\t0,03\t15\n"},
    ],
)
def test_unreviewed_schema_unit_or_row_repairs_are_rejected(changes):
    with pytest.raises(ValueError):
        parse(payload(**changes))


def test_specimen_name_does_not_override_header_identity():
    with pytest.raises(ValueError, match="identifiers differ"):
        parse_copper_tensile(payload(), member="Tensile_A_03.lis", source={})


def test_tampering_is_detected_before_quantity_or_si_export():
    record = parse()
    changed = record.model_copy(update={"metadata_rows": record.metadata_rows + (("extra", None, "value"),)})
    with pytest.raises(ValueError, match="changed"):
        changed.quantity("Probenquerschnitt", "mm²", 1e-6)
    record.source["license_id"] = "changed"
    with pytest.raises(ValueError, match="changed"):
        record.si_columns()
