"""Profile already acquired rig labels without converting condition grades into actuation or HEP data."""

import argparse
import hashlib
import json
import zipfile
from collections import Counter
from pathlib import Path

from pipeline_integrity.datasets import catalog, file_digest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, default=Path("data/raw/hydraulic/source.zip"))
    args = parser.parse_args()
    spec = next(item for item in catalog() if item.id == "hydraulic")
    checksum = file_digest(args.archive)
    assert spec.assets[0].checksum == f"sha256:{checksum}", (
        "raw observations differ from the retained acquisition"
    )
    with zipfile.ZipFile(args.archive) as archive:
        raw = archive.read("profile.txt")
        rows = [
            tuple(int(x) for x in line.split()) for line in raw.decode("ascii").splitlines() if line.strip()
        ]
        assert rows and all(len(row) == 5 for row in rows)
        assert set(row[1] for row in rows) <= {73, 80, 90, 100}
        assert set(row[4] for row in rows) <= {0, 1}
        notes = {}
        for member in ("description.txt", "documentation.txt"):
            data = archive.read(member)
            try:
                data.decode("utf-8")
                encoding = "utf-8"
            except UnicodeDecodeError:
                data.decode("cp1252")
                encoding = "cp1252"
            notes[member] = {"sha256": hashlib.sha256(data).hexdigest(), "text_encoding": encoding}
        members = archive.namelist()
    result = {
        "schema": "pipeline_integrity.valve_data_review.v1",
        "dataset_id": spec.id,
        "source": spec.source,
        "citation": spec.citation,
        "creators": spec.creators,
        "license_id": spec.license_id,
        "archive_sha256": checksum,
        "checksum_basis": spec.assets[0].checksum_origin,
        "profile_member_sha256": hashlib.sha256(raw).hexdigest(),
        "source_notes": notes,
        "cycles": len(rows),
        "profile_columns": 5,
        "valve_grade_counts": dict(sorted(Counter(row[1] for row in rows).items())),
        "stable_flag_counts": dict(sorted(Counter(row[4] for row in rows).items())),
        "valve_grade_counts_when_other_components_nominal_and_stable": dict(
            sorted(
                Counter(
                    row[1] for row in rows if row[0] == 100 and row[2] == 0 and row[3] == 130 and row[4] == 0
                ).items()
            )
        ),
        "sensor_member_count": len(
            [
                m
                for m in members
                if m.endswith(".txt") and m not in {"profile.txt", "description.txt", "documentation.txt"}
            ]
        ),
        "label_meaning": "cycle-level component-condition category; not an opening fraction, duration, or failure probability",
        "physical_valve_transient_validation_status": "needs_corresponding_geometry_command_travel_and_liquid_boundary_evidence",
        "human_reliability_validation": False,
        "independent_cycles_established": False,
        "independent_field_validation": False,
        "raw_observations_in_code_release": False,
    }
    Path("research/valve_data_review.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                k: result[k]
                for k in (
                    "cycles",
                    "valve_grade_counts",
                    "stable_flag_counts",
                    "physical_valve_transient_validation_status",
                )
            }
        )
    )


if __name__ == "__main__":
    main()
