"""Read pinned copper tensile records without replacing reported gauge strain by travel."""

import hashlib
import math
from pathlib import PurePosixPath

from pydantic import Field

from .contracts import Contract, Name, digest
from .source_archive import pinned_source_zip

_COLUMNS = ("Zeit", "Weg", "Kraft", "Dehnung", "Spannung")
_UNITS = ("sec", "mm", "kN", "%", "MPa")


class CopperTensileRecord(Contract):
    source: dict
    member: Name
    member_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    specimen_id: Name
    material_label: Name
    metadata_rows: tuple[tuple[str, str | None, str], ...]
    columns_raw: tuple[str, ...]
    units_raw: tuple[str, ...]
    data_rows: tuple[tuple[float | None, ...], ...]
    source_line_numbers: tuple[int, ...]
    record_digest: str = Field(pattern=r"^[a-f0-9]{64}$")

    def check_integrity(self):
        if digest(self.model_dump(mode="json", exclude={"record_digest"})) != self.record_digest:
            raise ValueError("copper tensile source, metadata or measurement rows changed")

    def metadata(self, label):
        self.check_integrity()
        rows = [(unit, value) for name, unit, value in self.metadata_rows if name == label]
        if len(rows) != 1:
            raise ValueError(f"one explicit source metadata row required: {label}")
        return rows[0]

    def quantity(self, label, expected_unit, scale):
        unit, token = self.metadata(label)
        if unit != expected_unit:
            raise ValueError("source metadata unit differs; no dimensional guess is applied")
        value = _number(token)
        return None if value is None else value * scale

    def si_columns(self):
        """Retain all rows and signs; gauge strain and machine travel stay separate."""
        self.check_integrity()
        factors = (1.0, 1e-3, 1000.0, 0.01, 1e6)
        values = [
            tuple(None if row[j] is None else row[j] * factors[j] for row in self.data_rows) for j in range(5)
        ]
        return dict(
            zip(
                (
                    "time_s",
                    "machine_travel_m",
                    "force_N",
                    "reported_engineering_strain",
                    "reported_engineering_stress_Pa",
                ),
                values,
                strict=True,
            )
        )


def _number(token):
    if token in {"", "NN", "NaN", "nan", "N/A"}:
        return None
    if "." in token or token.count(",") > 1:
        raise ValueError("source numeric tokens must use the reviewed decimal-comma notation")
    value = float(token.replace(",", "."))
    return value if math.isfinite(value) else None


def parse_copper_tensile(payload: bytes, *, member: str, source: dict):
    """Parse one explicit LIS schema; loading, unloading and duplicate axes are retained."""
    if not isinstance(payload, bytes):
        raise TypeError("the original source member must be bytes")
    lines = payload.decode("cp1252", errors="strict").splitlines()
    markers = [i for i, line in enumerate(lines) if line == "[Daten]"]
    if len(markers) != 1:
        raise ValueError("one reviewed tensile data block is required")
    marker = markers[0]
    if tuple(lines[marker + 1].split("\t")) != _COLUMNS or tuple(lines[marker + 2].split("\t")) != _UNITS:
        raise ValueError("tensile source columns or units differ from the reviewed LIS schema")
    metadata = []
    for line in lines[:marker]:
        if "\t" not in line:
            continue
        cells = [c.strip() for c in line.split("\t") if c.strip()]
        if len(cells) == 2:
            metadata.append((cells[0], None, cells[1]))
        elif len(cells) == 3:
            metadata.append(tuple(cells))
        else:
            raise ValueError("unexpected source metadata columns")
    rows, indices = [], []
    for index, line in enumerate(lines[marker + 3 :], start=marker + 4):
        if not line.strip():
            raise ValueError("blank tensile data row cannot be silently removed")
        tokens = line.split("\t")
        if len(tokens) != 5:
            raise ValueError("tensile measurement row must preserve all five source columns")
        rows.append(tuple(_number(token.strip()) for token in tokens))
        indices.append(index)
    if len(rows) < 3:
        raise ValueError("tensile source requires at least three measurement rows")
    identity = {}
    for key in ("Probenbezeichnung", "Werkstoff"):
        found = [value for name, unit, value in metadata if name == key]
        if len(found) != 1:
            raise ValueError("source specimen/material metadata must occur exactly once")
        identity[key] = found[0]
    specimen = identity.get("Probenbezeichnung")
    if not specimen or specimen == "NN" or not identity.get("Werkstoff"):
        raise ValueError("explicit specimen and material labels are required")
    if PurePosixPath(member).name != f"Tensile_{specimen}.lis":
        raise ValueError("source member and reported physical specimen identifiers differ")
    body = dict(
        source=source,
        member=member,
        member_sha256=hashlib.sha256(payload).hexdigest(),
        specimen_id=specimen,
        material_label=identity["Werkstoff"],
        metadata_rows=tuple(metadata),
        columns_raw=_COLUMNS,
        units_raw=_UNITS,
        data_rows=tuple(rows),
        source_line_numbers=tuple(indices),
    )
    return CopperTensileRecord(**body, record_digest=digest(body))


def load_copper_tensile(archive, *, maximum_records=100, maximum_rows_per_record=100000):
    if any(type(v) is not int or v < 1 for v in (maximum_records, maximum_rows_per_record)):
        raise ValueError("positive integer tensile record/row budgets are required")
    with pinned_source_zip(archive, "copper_mechanical") as (source, provenance):
        names = [
            i.filename
            for i in source.infolist()
            if i.filename.startswith("Tensile test/Primary data/") and i.filename.endswith(".lis")
        ]
        if not names or len(names) > maximum_records:
            raise ValueError("tensile record selection or record budget differs")
        records = []
        for name in names:
            record = parse_copper_tensile(source.read(name), member=name, source=provenance)
            if len(record.data_rows) > maximum_rows_per_record:
                raise ValueError("tensile record row budget exceeded; no truncation")
            record.check_integrity()
            records.append(record)
    if len({r.specimen_id for r in records}) != len(records):
        raise ValueError("tensile specimen identifiers must remain distinct")
    return tuple(records)
