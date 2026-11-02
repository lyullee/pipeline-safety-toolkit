"""Explicit readers for selected source schemas; source truth stays outside model inputs."""

from __future__ import annotations

import math
import zipfile
from pathlib import Path

from .contracts import SignalFrame


def iter_battledim_frames(directory, year, *, selected_channels=None, maximum_rows=None):
    """Align pressure-head, flow and level observations on the released local elapsed clock."""
    import pandas as pd

    if year not in {2018, 2019}:
        raise ValueError("the reviewed source version contains years 2018 and 2019")
    if maximum_rows is not None and (type(maximum_rows) is not int or maximum_rows < 1):
        raise ValueError("maximum_rows must be a positive integer")
    root = Path(directory)
    sources = [
        ("Pressures", "pressure_head", "m", 1.0),
        ("Flows", "volume_flow", "m3/s", 1 / 3600),
        ("Levels", "water_level", "m", 1.0),
    ]
    assembled, clocks, units = [], [], {}
    for suffix, quantity, unit, scale in sources:
        table = pd.read_csv(root / f"{year}_SCADA_{suffix}.csv", sep=";", decimal=",", nrows=maximum_rows)
        clock = pd.to_datetime(table.pop("Timestamp"), errors="raise")
        if clock.duplicated().any() or not clock.is_monotonic_increasing:
            raise ValueError("source timestamps must strictly increase")
        clocks.append(clock)
        table.columns = [f"{quantity}:{name}" for name in table.columns]
        table = table.apply(pd.to_numeric, errors="raise") * scale
        assembled.append(table)
        units.update(dict.fromkeys(table.columns, unit))
    if not all(clock.equals(clocks[0]) for clock in clocks[1:]):
        raise ValueError("source sensor tables do not have identical clocks; no implicit resampling")
    table = pd.concat(assembled, axis=1)
    channels = tuple(table.columns) if selected_channels is None else tuple(selected_channels)
    if not channels or len(channels) != len(set(channels)) or set(channels) - set(table.columns):
        raise ValueError("select known unique observation channels")
    elapsed = (clocks[0] - clocks[0].iloc[0]).dt.total_seconds()
    for index, row in table.loc[:, list(channels)].iterrows():
        valid = {name: math.isfinite(float(row[name])) for name in channels}
        yield SignalFrame(
            stream_id=f"battledim_{year}",
            clock_id=f"battledim_{year}_local_elapsed",
            time_s=float(elapsed.iloc[index]),
            values={name: float(row[name]) if valid[name] else None for name in channels},
            units={name: units[name] for name in channels},
            valid=valid,
            origin="simulation",
        )


def hydraulic_cycle(archive_path, cycle_index, sensors=("PS1", "FS1", "TS1")):
    """Return separate sensor clocks; the released sample rates are deliberately not merged."""
    import numpy as np

    configuration = {
        **{f"PS{i}": (100.0, "bar_unspecified_pressure_reference", 1.0, 0.0) for i in range(1, 7)},
        "EPS1": (100.0, "W", 1.0, 0.0),
        "FS1": (10.0, "m3/s", 1 / 60000, 0.0),
        "FS2": (10.0, "m3/s", 1 / 60000, 0.0),
        **{f"TS{i}": (1.0, "K", 1.0, 273.15) for i in range(1, 5)},
        "VS1": (1.0, "m/s", 0.001, 0.0),
    }
    if (
        type(cycle_index) is not int
        or not 0 <= cycle_index < 2205
        or not sensors
        or len(set(sensors)) != len(sensors)
        or set(sensors) - set(configuration)
    ):
        raise ValueError("select a source cycle and supported unique sensors")
    output = {}
    with zipfile.ZipFile(archive_path) as archive:
        with archive.open("profile.txt") as stream:
            for index, line in enumerate(stream):
                if index == cycle_index:
                    profile = tuple(int(float(value)) for value in line.split())
                    break
            else:
                raise ValueError("cycle label row is absent")
        for sensor in sensors:
            rate, unit, scale, offset = configuration[sensor]
            with archive.open(sensor + ".txt") as stream:
                for index, line in enumerate(stream):
                    if index == cycle_index:
                        values = np.fromstring(line.decode("ascii"), sep="\t") * scale + offset
                        break
                else:
                    raise ValueError("cycle sensor row is absent")
            if len(values) != int(rate * 60):
                raise ValueError("cycle row does not match its declared source sample count")
            output[sensor] = {
                "time_s": np.arange(len(values)) / rate,
                "values": values,
                "valid": np.isfinite(values),
                "unit": unit,
                "clock_id": f"hydraulic_cycle_{cycle_index}_elapsed",
                "origin": "laboratory",
                "sample_rate_hz": rate,
            }
    return {
        "cycle_index": cycle_index,
        "profile": profile,
        "sensors": output,
        "profile_is_model_input": False,
        "independent_cycle_clock": True,
    }


def audit_ili_workbooks(archive_path):
    """Inspect workbook dimensions and headers without executing the companion analysis scripts."""
    import io

    import openpyxl

    tables = []
    with zipfile.ZipFile(archive_path) as archive:
        for name in archive.namelist():
            if not name.lower().endswith(".xlsx"):
                continue
            workbook = openpyxl.load_workbook(io.BytesIO(archive.read(name)), read_only=True, data_only=True)
            sheets = []
            for sheet in workbook:
                headers = next(sheet.iter_rows(min_row=1, max_row=1, values_only=True), ())
                sheets.append(
                    {
                        "sheet": sheet.title,
                        "reported_max_row": sheet.max_row,
                        "reported_max_column": sheet.max_column,
                        "headers": [str(value) if value is not None else None for value in headers],
                    }
                )
            workbook.close()
            tables.append({"member": name, "sheets": sheets})
    return {
        "workbooks": tables,
        "dimensions_are_xml_metadata": True,
        "matched_defect_count_established": False,
        "longitudinal_corrosion_rate_qualified": False,
    }


def elvhys_table(path, *, maximum_rows=None):
    """Read released relative clocks; preserve pressure-reference contradictions as unresolved units."""
    import re

    import numpy as np
    import pandas as pd

    if maximum_rows is not None and (type(maximum_rows) is not int or maximum_rows < 1):
        raise ValueError("maximum_rows must be a positive integer")
    table = pd.read_csv(path, sep=";", encoding="utf-8-sig", nrows=maximum_rows)
    time_name = table.columns[0]
    if time_name not in {"Time (seconds)", "Time (minutes)"}:
        raise ValueError("unreviewed source time column")
    times = pd.to_numeric(table.pop(time_name), errors="raise").to_numpy(float)
    times *= 60.0 if time_name == "Time (minutes)" else 1.0
    if not len(times) or not np.isfinite(times).all():
        raise ValueError("released relative source time must be finite and nonempty")
    values = table.apply(pd.to_numeric, errors="raise")
    units, conflicts, positive = {}, [], []
    scales = {
        "K": ("K", 1.0),
        "bar_a": ("Pa_abs", 1e5),
        "bar_g": ("Pa_gauge", 1e5),
        "mbar_g": ("Pa_gauge", 100.0),
        "bar": ("Pa_difference", 1e5),
    }
    for channel in values:
        match = re.fullmatch(r".+ \((K|bar_a|bar_g|mbar_g|bar)\)", channel)
        if match is None:
            raise ValueError(f"unreviewed source quantity header: {channel}")
        unit, scale = scales[match.group(1)]
        values[channel] *= scale
        token = channel.split(" (")[0].strip().lower()
        if token in {"p_1", "p_3"} or token.startswith("pw"):
            unit = "Pa_unresolved_reference"
            conflicts.append(channel)
        elif unit in {"K", "Pa_abs"}:
            positive.append(channel)
        units[channel] = unit
    valid = pd.DataFrame(np.isfinite(values), columns=values.columns, index=values.index)
    for channel in positive:
        valid[channel] &= values[channel] > 0
    spacing = np.diff(times)
    return {
        "time_relative_s": times,
        "values": values,
        "units": units,
        "valid": valid,
        "source_clock_is_signed_event_relative": True,
        "cross_instrument_alignment_qualified": False,
        "pressure_reference_conflict_channels": conflicts,
        "median_observed_interval_s": float(np.median(spacing)) if len(spacing) else None,
        "nominal_readme_sampling_rate_used": False,
        "clock_strictly_increasing": bool((spacing > 0).all()),
        "duplicate_adjacent_timestamps": int((spacing == 0).sum()),
        "backward_clock_steps": int((spacing < 0).sum()),
        "note": "Time knots are read from the file; source documentation has conflicting rate and pressure-reference descriptions.",
    }


def iter_elvhys_frames(path, *, event_time_offset_s, clock_id, maximum_rows=None):
    """Offset a signed event clock only with a caller-declared shared reference; never zero each sensor file separately."""
    import numpy as np

    if isinstance(event_time_offset_s, bool) or not math.isfinite(event_time_offset_s):
        raise ValueError("provide a finite explicit shared event-time offset")
    source = elvhys_table(path, maximum_rows=maximum_rows)
    if not source["clock_strictly_increasing"]:
        raise ValueError(
            "source timestamps repeat or go backward; review the instrument clock without silent deduplication"
        )
    times = source["time_relative_s"] + event_time_offset_s
    if np.any(times < 0):
        raise ValueError(
            "the declared common offset leaves negative elapsed time; review the shared clock origin"
        )
    for index, row in source["values"].iterrows():
        yield SignalFrame(
            stream_id=Path(path).stem,
            clock_id=clock_id,
            time_s=float(times[index]),
            values={name: float(value) if math.isfinite(value) else None for name, value in row.items()},
            units=source["units"],
            valid={name: bool(source["valid"].loc[index, name]) for name in row.index},
            origin="laboratory",
        )


def h2safe_table(archive_path, laboratory, experiment):
    """Read one source experiment, keeping missing observations and unresolved units/frame separate."""
    import numpy as np
    import pandas as pd

    if laboratory not in {"Lab1_Small", "Lab2_Large"} or experiment not in {
        "test1",
        "test2",
        "test3",
        "test4",
    }:
        raise ValueError("select declared source laboratory and experiment IDs")
    member = f"{laboratory}/{experiment}.csv"
    with zipfile.ZipFile(archive_path) as archive:
        with archive.open(member) as stream:
            table = pd.read_csv(stream, encoding="utf-8-sig", na_values=["#N/A"])
        with archive.open(f"{laboratory}/sensors.csv") as stream:
            coordinates = pd.read_csv(stream, encoding="utf-8-sig")
    time = pd.to_numeric(table.pop("time"), errors="raise").to_numpy(float)
    values = table.apply(pd.to_numeric, errors="raise")
    if not np.isfinite(time).all() or not len(time) or (np.diff(time) <= 0).any():
        raise ValueError("source experiment clock must strictly increase")
    identifiers = [str(value).casefold() for value in coordinates["id"]]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("case-folded sensor IDs are ambiguous")
    matched = {name: name.casefold() in identifiers for name in values}
    return {
        "time_source": time,
        "values": values,
        "valid": pd.DataFrame(np.isfinite(values), columns=values.columns),
        "coordinate_table": coordinates,
        "channels_with_coordinate_match": matched,
        "sensor_name_casefold_matching": True,
        "signal_unit": "source_unit_unresolved",
        "time_unit": "source_unit_unresolved",
        "coordinate_unit": "source_unit_unresolved",
        "coordinate_frame": "source_CAD_frame_unresolved",
        "cross_experiment_clock_shared": False,
        "physical_model_input_qualified": False,
        "note": "The acquired CSV headers do not declare units; the source-linked software endpoint was unavailable at review.",
    }
