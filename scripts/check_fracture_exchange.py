"""Execute owned fracture engines, declared-only boundaries and primary crack-arrest readers."""

import argparse
import csv
import io
import json
import math
import sys
import zipfile
from pathlib import Path

from check_integrations import revision
from scipy.integrate import quad

from pipeline_integrity.adapters import MaterialClient, hra_signal_series, monitoring_batch
from pipeline_integrity.datasets import file_digest
from pipeline_integrity.fracture_exchange import (
    SectionOpeningMapping,
    assess_crack_growth,
    assess_failure_points,
    assess_mode_i,
    crack_arrest_frames,
    opening_stress_from_section,
)
from pipeline_integrity.material_exchange import (
    evaluate_material_job,
    evaluate_pressure_section,
    prepare_material_job,
)


def main():
    parser = argparse.ArgumentParser()
    for key in ("materials", "monitoring", "hra"):
        parser.add_argument("--" + key, type=Path, required=True)
    parser.add_argument("--raw-root", type=Path, default=Path("data/raw/crack_arrest"))
    args = parser.parse_args()
    roots = {key: getattr(args, key) for key in ("materials", "monitoring", "hra")}
    before = {key: revision(path) for key, path in roots.items()}
    sys.path[:0] = [str((p / "src").resolve()) for p in roots.values()] + [str(Path("examples").resolve())]
    from fracture_screen import context, failure_points, make_growth, make_mode_i
    from material_sections import pressure_case

    client = MaterialClient()
    ctx = context()
    private = Path("artifacts/fracture_exchange")
    private.mkdir(parents=True, exist_ok=True)
    summary = dict(
        schema="pipeline_integrity.fracture_exchange_verification.v1",
        mode_i={},
        growth={},
        declared_boundaries={},
    )
    for name, changes in dict(
        base={},
        zero_opening={"effective_opening_stress_pa": 0.0},
        above_resistance={"effective_opening_stress_pa": 1e8},
        unqualified_arrest={
            "resistance": make_mode_i().resistance.model_copy(update={"kind": "crack_arrest"})
        },
        unsupported_temperature={"temperature_k": 350.0},
    ).items():
        s = make_mode_i().model_copy(update=changes)
        result = assess_mode_i(s, ctx, client=client)
        if result["native"] is not None:
            native = result["native"]["native"]["payload"]["values"]
            expected = (
                s.geometry_factor * s.effective_opening_stress_pa * math.sqrt(math.pi * s.crack_depth_m)
            )
            assert math.isclose(native["stress_intensity_Pa_sqrt_m"], expected, rel_tol=1e-14, abs_tol=1e-10)
        else:
            native = {}
        summary["mode_i"][name] = dict(
            status=result["status"],
            values=native,
            qualified_values=result["values"],
            record_digest=result["record_digest"],
        )
        (private / (name + ".json")).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    assert (
        summary["mode_i"]["above_resistance"]["qualified_values"]["declared_resistance_outcome"]
        == "above_declared_resistance"
    )
    assert summary["mode_i"]["base"]["qualified_values"]["qualified_critical_depth_m"] is None
    assert summary["mode_i"]["unqualified_arrest"]["status"] == "needs_data"

    constant = {}
    for exponent in (2.0, 3.0):
        coefficient = 1e-16 if exponent == 2.0 else 1e-24
        parameters = dict(
            initial_depth_m=0.001,
            terminal_depth_m=0.004,
            stress_range_Pa=2e7,
            geometry_factor=1.0,
            coefficient_m_cycle_per_Pa_sqrt_m_power=coefficient,
            exponent=exponent,
        )
        receipt = evaluate_material_job(
            prepare_material_job(
                "fracture.paris_constant_amplitude",
                parameters,
                ctx,
                location_id="annulus",
                evidence_ids=("authored",),
                client=client,
            ),
            ctx,
            client=client,
        )
        cycles = receipt.native.payload["values"]["cycles_to_terminal_depth"]
        independent, error = quad(
            lambda a, coefficient_ref=coefficient, exponent_ref=exponent: (
                1 / (coefficient_ref * (2e7 * math.sqrt(math.pi * a)) ** exponent_ref)
            ),
            0.001,
            0.004,
            epsabs=1e-8,
            epsrel=1e-10,
        )
        assert math.isclose(cycles, independent, rel_tol=1e-10, abs_tol=1e-8)
        constant[str(exponent)] = dict(
            native_cycles=cycles,
            independent_quadrature_cycles=independent,
            absolute_difference=abs(cycles - independent),
            quadrature_error=error,
        )
        (private / ("constant_" + str(exponent) + ".json")).write_text(
            receipt.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
    summary["constant_paris_references"] = constant
    growth = make_growth()
    cases = dict(
        base=growth,
        terminal=growth.model_copy(
            update={"blocks": (growth.blocks[0].model_copy(update={"cycles": 2000.0}),)}
        ),
        zero_range=growth.model_copy(
            update={"blocks": (growth.blocks[0].model_copy(update={"minimum_opening_stress_pa": 4e7}),)}
        ),
        threshold=growth.model_copy(
            update={"calibration": growth.calibration.model_copy(update={"threshold_delta_k_pa_sqrt_m": 2e6})}
        ),
        ratio_outside=growth.model_copy(
            update={"calibration": growth.calibration.model_copy(update={"minimum_stress_ratio": 0.7})}
        ),
        calibration_limit=growth.model_copy(
            update={
                "calibration": growth.calibration.model_copy(update={"maximum_delta_k_pa_sqrt_m": 1.3e6}),
                "blocks": (growth.blocks[0].model_copy(update={"cycles": 2000.0}),),
            }
        ),
        tougher_load=growth.model_copy(
            update={
                "blocks": (
                    growth.blocks[0].model_copy(update={"maximum_opening_stress_pa": 8e7, "cycles": 2000.0}),
                )
            }
        ),
        geometry_outside=growth.model_copy(update={"geometry_factor": (1.0, 0.9)}),
    )
    for name, s in cases.items():
        result = assess_crack_growth(s, ctx, client=client)
        values = {} if result["native"] is None else result["native"]["native"]["payload"]["values"]
        summary["growth"][name] = dict(
            status=result["status"], values=values, record_digest=result["record_digest"]
        )
        (private / ("growth_" + name + ".json")).write_text(
            json.dumps(result, indent=2) + "\n", encoding="utf-8"
        )
    assert summary["growth"]["terminal"]["values"]["termination_reason"] == "declared_depth_limit"
    assert math.isclose(
        summary["growth"]["terminal"]["values"]["cycles_evaluated"],
        constant["3.0"]["native_cycles"],
        rel_tol=1e-8,
    )
    assert summary["growth"]["ratio_outside"]["status"] == "out_of_scope"
    assert summary["growth"]["tougher_load"]["values"]["termination_reason"] == "toughness_limit_reached"
    assert summary["growth"]["calibration_limit"]["status"] == "out_of_scope"

    section = evaluate_pressure_section(pressure_case(count=9, pi=2e6, pe=1e5), ctx, client=client)
    from pipeline_integrity.contracts import Pressure

    mapping = SectionOpeningMapping(
        radius_m=section["specification"]["evaluation_radii_m"][0],
        component="hoop_stress_Pa",
        crack_face_pressure=Pressure(value=1e5, unit="Pa", reference="absolute"),
        additional_opening_stress_pa=0.0,
        interpretation="Authored uniform scalar correction; not a general internal-face crack solution",
        evidence_id="authored",
        scalar_mapping_reviewed=True,
    )
    opening = opening_stress_from_section(section, mapping, ctx)
    s = make_mode_i().model_copy(
        update={
            "effective_opening_stress_pa": opening["value_pa"],
            "opening_stress_definition": mapping.interpretation,
            "opening_stress_receipt_digest": opening["record_digest"],
        }
    )
    result = assess_mode_i(s, ctx, client=client, opening_stress_receipt=opening)
    summary["pressure_opening_exchange"] = dict(
        status=result["status"],
        opening_stress_pa=opening["value_pa"],
        source_section_digest=section["record_digest"],
        mapping_receipt_digest=opening["record_digest"],
        general_crack_pressure_or_stress_gradient_validation=False,
    )
    (private / "pressure_binding.json").write_text(
        json.dumps(dict(section=section, opening=opening, result=result), indent=2) + "\n", encoding="utf-8"
    )

    for name, parameters in dict(
        inside=failure_points(),
        above=failure_points(kr=0.9, lr=1.0),
        outside_support=failure_points(lr=3.0),
        unreviewed=failure_points() | {"load_combination_reviewed": False},
    ).items():
        receipt = assess_failure_points(
            parameters, ctx, location_id="annulus", evidence_ids=("authored",), client=client
        )
        summary["declared_boundaries"][name] = dict(
            status=receipt.native.status,
            values=receipt.native.payload["values"],
            result_digest=receipt.result_digest,
            native_validation_mode=receipt.native.payload["context"]["metadata"][
                "pipeline_input_validation_mode"
            ],
        )
        (private / ("boundary_" + name + ".json")).write_text(
            receipt.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
    assert summary["declared_boundaries"]["above"]["values"]["declared_boundary_outcome"] == "not_satisfied"
    assert (
        summary["declared_boundaries"]["outside_support"]["values"]["declared_boundary_outcome"] == "unknown"
    )

    from matintegrity.fracture_data import list_crack_arrest_runs

    archive = args.raw_root / "g69875w65c-1.zip"
    runs = list_crack_arrest_runs(archive)
    reviews = []
    with zipfile.ZipFile(archive) as source:
        for run in runs.itertuples(index=False):
            frames, metadata = crack_arrest_frames(archive, run.specimen_id)
            text = source.read(metadata["source"]["source_member"]).decode("utf-8-sig")
            raw = list(csv.reader(io.StringIO("\n".join(text.splitlines()[1:])), delimiter=";"))
            assert len(raw) == len(frames)
            for f, row in zip(frames, raw, strict=True):
                t, u, force, extension, cycle = (float(v) for v in row)
                assert [f.time_s, *f.values.values()] == [t, u * 1e-3, force * 1e3, extension * 1e-3, cycle]
            reviews.append(
                dict(
                    specimen_id=run.specimen_id,
                    family=run.test_family,
                    rows=len(frames),
                    reported_temperature_k=run.reported_temperature_K,
                    header_representation_normalized=metadata["native_header_representation_normalized"],
                    negative_force_samples=sum(f.values["force_N"] < 0 for f in frames),
                    negative_machine_displacements=sum(
                        f.values["machine_displacement_m"] < 0 for f in frames
                    ),
                    negative_extensometer_displacements=sum(
                        f.values["extensometer_displacement_m"] < 0 for f in frames
                    ),
                    clock_id=frames[0].clock_id,
                    source_member_sha256=metadata["source"]["source_member_sha256"],
                )
            )
            (private / (run.specimen_id + "_metadata.json")).write_text(
                json.dumps(metadata, indent=2) + "\n", encoding="utf-8"
            )
    frames, metadata = crack_arrest_frames(archive, runs.iloc[0].specimen_id)
    batch = monitoring_batch(frames)
    series = hra_signal_series(frames, "force_N")
    assert batch.metadata["clock_id"] == series.clock_id == frames[0].clock_id
    summary["primary_data"] = dict(
        source="https://doi.org/10.17632/g69875w65c.1",
        creator="Puyi Gao",
        license_id="CC-BY-4.0",
        source_status="needs_data",
        archive_sha256=file_digest(archive),
        files=len(reviews),
        rows=sum(r["rows"] for r in reviews),
        header_normalization_count=sum(r["header_representation_normalized"] for r in reviews),
        negative_force_samples=sum(r["negative_force_samples"] for r in reviews),
        negative_machine_displacements=sum(r["negative_machine_displacements"] for r in reviews),
        negative_extensometer_displacements=sum(r["negative_extensometer_displacements"] for r in reviews),
        full_numeric_source_comparison_passed=True,
        monitoring_rows=len(frames),
        hra_samples=len(series.time_s),
        temperature_is_filename_metadata=True,
        geometry_crack_length_reduction_and_validity_qualified=False,
        initiation_toughness_or_failure_probability_inferred=False,
    )
    (private / "primary_data_review.json").write_text(json.dumps(reviews, indent=2) + "\n", encoding="utf-8")
    assert before == {key: revision(path) for key, path in roots.items()}
    summary["source_versions"] = before
    for key, path in [
        ("module_sha256", "src/pipeline_integrity/fracture_exchange.py"),
        ("material_job_module_sha256", "src/pipeline_integrity/material_exchange.py"),
        ("adapter_sha256", "src/pipeline_integrity/adapters.py"),
        ("example_sha256", "examples/fracture_screen.py"),
        ("script_sha256", __file__),
    ]:
        summary[key] = file_digest(Path(path))
    summary["field_or_standard_acceptance"] = False
    Path("research/fracture_exchange_results.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
