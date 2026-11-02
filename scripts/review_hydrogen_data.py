"""Publish source-schema and suitability findings without pointwise measurements or copied figures."""

import json
import zipfile
from pathlib import Path

from pipeline_integrity.datasets import file_digest
from pipeline_integrity.loaders import elvhys_table, h2safe_table


def main():
    folder = Path("data/raw/elvhys_bleve")
    inspected = []
    for name in (
        "ELE431KIT011PRESF20260727.csv",
        "ELE431KIT011PRESS20260727.csv",
        "ELE431KIT011TEMP20260727.csv",
    ):
        row = elvhys_table(folder / name)
        inspected.append(
            {
                "filename": name,
                "sha256": file_digest(folder / name),
                "rows": len(row["time_relative_s"]),
                "channels": len(row["values"].columns),
                "observed_median_interval_s": row["median_observed_interval_s"],
                "signed_time_span_s": [float(row["time_relative_s"][0]), float(row["time_relative_s"][-1])],
                "invalid_samples_by_channel": {
                    name: int(value) for name, value in (~row["valid"]).sum().items()
                },
                "pressure_reference_conflict_channels": row["pressure_reference_conflict_channels"],
                "clock_strictly_increasing": row["clock_strictly_increasing"],
                "duplicate_adjacent_timestamps": row["duplicate_adjacent_timestamps"],
                "backward_clock_steps": row["backward_clock_steps"],
            }
        )
    experiments = []
    with zipfile.ZipFile("data/raw/h2safe/dataset.zip") as archive:
        selected = [name for name in archive.namelist() if name.endswith(".csv") and "/test" in name]
    for name in selected:
        lab, test = Path(name).parts
        row = h2safe_table("data/raw/h2safe/dataset.zip", lab, Path(test).stem)
        experiments.append(
            {
                "laboratory": lab,
                "experiment": Path(test).stem,
                "rows": len(row["time_source"]),
                "channels": len(row["values"].columns),
                "missing_samples": int((~row["valid"]).sum().sum()),
                "channels_entirely_missing": row["values"].columns[row["values"].isna().all()].tolist(),
                "coordinate_matches": sum(row["channels_with_coordinate_match"].values()),
                "signal_unit": row["signal_unit"],
                "time_unit": row["time_unit"],
                "physical_model_input_qualified": False,
            }
        )
    result = {
        "schema": "pipeline_integrity.hydrogen_data_review.v1",
        "elvhys": {
            "source": "https://doi.org/10.18710/M5LOLM",
            "license": "CC0-1.0",
            "inspected": inspected,
            "readme_rate_conflict": "The released high-rate file has a 10-microsecond median interval; the source text also states 10 kHz. Source time knots are used.",
            "pressure_reference_review": "p_1, p_3 and external PW channels retain unresolved references because source descriptions conflict or labeled absolute values are negative.",
            "cross_instrument_alignment_qualified": False,
            "ordinary_pipeline_transient_validation_claim": False,
        },
        "h2safe": {
            "source": "https://data.openei.org/submissions/8799",
            "license": "CC-BY-4.0",
            "archive_sha256": file_digest(Path("data/raw/h2safe/dataset.zip")),
            "experiments": experiments,
            "supporting_metadata_sha256": file_digest(Path("data/raw/h2safe/hvac_information.docx")),
            "supporting_metadata_review": {
                "leak_coordinates_declared_unit": "m",
                "sensor_coordinates_same_frame_as_leak": True,
                "sensor_coordinate_unit_inferred_from_supporting_frame": True,
                "enu_transform_qualified": False,
                "pressure_values_reference_unspecified": True,
                "standard_litre_reference_conditions_unspecified": True,
                "signal_amplitude_unit_confirmed": False,
                "source_linked_software_http_status": 404,
            },
            "signal_and_time_units_not_guessed": True,
            "hep_estimated": False,
        },
    }
    Path("research/hydrogen_data_review.json").write_text(
        json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "elvhys_tables": len(inspected),
                "h2safe_experiments": len(experiments),
                "physical_concentration_units_qualified": False,
            }
        )
    )


if __name__ == "__main__":
    main()
