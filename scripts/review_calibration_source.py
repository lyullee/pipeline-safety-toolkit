"""Review actual calibration source values without publishing observations."""

import argparse
import json
import re
import runpy
from copy import deepcopy
from importlib.metadata import version
from pathlib import Path

import numpy as np

from pipeline_integrity.calibration_data import (
    ARCHIVE_NAME,
    EMUE_ID,
    emue_calibration_inputs,
    read_emue_calibration,
    verify_calibration_source_record,
)
from pipeline_integrity.contracts import digest
from pipeline_integrity.datasets import DatasetSpec, catalog, file_digest


def save(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, ensure_ascii=False, allow_nan=False, indent=2) + "\n", encoding="utf-8")


def review(raw_root, spec, artifacts):
    path = raw_root / EMUE_ID / ARCHIVE_NAME
    before = file_digest(path)
    record = read_emue_calibration(path, source_spec=spec)
    save(artifacts / "source_record.json", record)
    selected = emue_calibration_inputs(record, source_root=raw_root, source_spec=spec)
    save(artifacts / "calibration_inputs.json", selected)
    intermediate = record["intermediate"]
    covariance = np.asarray(selected["standard_joint_covariance"])
    deviations = np.sqrt(np.diag(covariance))
    correlation = covariance / deviations[:, None] / deviations[None, :]
    eigenvalues = np.linalg.eigvalsh(correlation)
    checks = dict(
        original_source_receipt=verify_calibration_source_record(
            record, source_root=raw_root, source_spec=spec
        ),
        supplier_md5_and_1406894_bytes=record["provenance"]["bytes"] == 1406894
        and record["provenance"]["expected_checksum"] == "md5:ffc39a245e2e4361a3337be63a0b21db"
        and record["provenance"]["supplier_checksum_verified"],
        three_reviewed_method_assets=all(
            name in record["selected_members"]
            for name in ("Example_A223.pdf", "Example_A223.Rmd", "Example_A223.tex")
        ),
        source_primitive_rows30=len(record["primitive"]["observations"]) == 30,
        source_intermediate_rows30=len(intermediate["x"]) == len(intermediate["y"]) == 30,
        publisher_wrong_declared_dimensions_preserved=all(
            s["declared_dimension"] == "A1:A1" and not s["source_bytes_changed"]
            for s in record["worksheets"].values()
        ),
        actual_xml_dimensions_preserved=[s["observed_dimension"] for s in record["worksheets"].values()]
        == ["A1:B31", "A1:AD31", "A1:AD31", "A1:AD31"],
        joint_covariance_60_by_60=covariance.shape == (60, 60),
        exact_source_covariance_symmetry=bool(np.array_equal(covariance, covariance.T)),
        strictly_positive_source_standard_variances=bool(np.all(np.diag(covariance) > 0)),
        all_3540_offdiagonal_values_retained=intermediate["offdiagonal_nonzero_entries"] == 3540,
        cross_block_transpose_preserved=bool(np.array_equal(covariance[:30, 30:], covariance[30:, :30].T)),
        source_joint_covariance_order=selected["covariance_order"]
        == tuple([f"x:{i}" for i in range(1, 31)] + [f"y:{i}" for i in range(1, 31)]),
        scaled_source_covariance_positive_definite=bool(eigenvalues.min() > 0),
        scaled_source_covariance_full_rank=np.linalg.matrix_rank(correlation) == 60,
        covariance_not_repaired_or_diagonalized=not intermediate["covariance_repaired"]
        and not intermediate["covariance_diagonalized"],
        primitive_type_A_not_total=all(
            not r["type_A_is_total_uncertainty"]
            and r["coverage_factor"] is None
            and r["degrees_of_freedom"] is None
            for r in record["primitive"]["observations"]
        ),
        source_numeric_domains_available=record["status"] == "source_values_available"
        and all(r["numeric_domain_valid"] for r in record["primitive"]["observations"]),
        publisher_MC_origin_retained=intermediate["source_origin"]
        == "publisher_Monte_Carlo_propagation_of_laboratory_estimates",
        no_MC_samples_or_rerun=not intermediate["publisher_MC_samples_acquired"]
        and not intermediate["MC_rerun"],
        no_independent_validation_or_fabricated_clock=selected["independent_validation"] is None
        and selected["observation_time_s"] is None,
        no_assumed_gas_state=all(
            record["primitive"][key] is None
            for key in ("upstream_pressure_pa_abs", "upstream_temperature_K", "gas_mole_fractions")
        ),
        point_order_not_sorted=any(
            b < a for a, b in zip(intermediate["x"], intermediate["x"][1:], strict=False)
        ),
        no_supplier_code_executed=not record["claims"]["source_code_executed"],
        units_are_dimensionless=selected["x_unit"]
        == selected["y_unit"]
        == intermediate["covariance_unit"]
        == "1",
    )
    attacks = {
        "primitive_component_becomes_total": lambda r: r["primitive"].update(uncertainty_kind="total"),
        "MC_estimates_become_raw_observations": lambda r: r["intermediate"].update(
            source_origin="raw_reference_instrument_pairs"
        ),
        "mixed_source_becomes_field": lambda r: r.update(origin="measured_process"),
        "invented_measurement_times": lambda r: r["intermediate"].update(observation_time_s=list(range(30))),
        "invented_independent_validation": lambda r: r["intermediate"].update(
            independent_validation={"approved": True}
        ),
        "cross_point_covariance_zeroed": lambda r: r["intermediate"]["standard_joint_covariance"][
            0
        ].__setitem__(1, 0),
        "covariance_order_reversed": lambda r: r["intermediate"]["covariance_order"].reverse(),
        "dimensionless_x_becomes_mass_flow": lambda r: r["intermediate"].update(x_unit="kg/s"),
        "covariance_becomes_expanded": lambda r: r["intermediate"].update(covariance_unit="expanded_k2"),
        "invented_source_MC_samples": lambda r: r["intermediate"].update(publisher_MC_samples_acquired=True),
        "method_source_pin_changed": lambda r: r["selected_members"]["Example_A223.pdf"].update(
            sha256="0" * 64
        ),
        "source_cells_silently_sorted": lambda r: r["intermediate"]["x"].sort(),
    }
    refusals = {}
    for name, mutation in attacks.items():
        corrupted = deepcopy(record)
        mutation(corrupted)
        corrupted["record_digest"] = digest({k: v for k, v in corrupted.items() if k != "record_digest"})
        try:
            verify_calibration_source_record(corrupted, source_root=raw_root, source_spec=spec)
        except ValueError:
            refusals[name] = True
        else:
            raise AssertionError("rehashed source-meaning mutation accepted: " + name)
    checks["original_archive_unchanged"] = file_digest(path) == before
    checks = {name: bool(value) for name, value in checks.items()}
    if not all(checks.values()):
        raise AssertionError([k for k, v in checks.items() if not v])
    return dict(
        schema="pipeline_integrity.calibration_source_review.v1",
        status="source_reader_full_covariance_verified_single_campaign",
        acquired_groups=1,
        acquired_files=1,
        acquired_bytes=record["provenance"]["bytes"],
        assets=[record["provenance"]],
        selected_member_pins=record["selected_members"],
        source_record_digest=record["record_digest"],
        primitive_rows=30,
        publisher_MC_intermediate_rows=30,
        supplied_joint_covariance_shape=[60, 60],
        supplier_reader_warnings=record["supplier_reader_warnings"],
        source_dimensions={
            k: {
                field: s[field]
                for field in ("declared_dimension", "observed_dimension", "source_bytes_changed")
            }
            for k, s in record["worksheets"].items()
        },
        covariance=dict(
            exactly_symmetric=True,
            scaled_correlation_minimum_eigenvalue=float(eigenvalues.min()),
            scaled_correlation_maximum_eigenvalue=float(eigenvalues.max()),
            scaled_correlation_rank=int(np.linalg.matrix_rank(correlation)),
            scaled_correlation_condition=float(np.linalg.cond(correlation)),
            nonzero_offdiagonal_entries=3540,
            repaired=False,
            diagonalized=False,
        ),
        checks=checks,
        source_contract_criteria=sum(checks.values()),
        actual_rehashed_source_corruption_refusals=refusals,
        supplier_programs_executed=0,
        source_MC_trials_rerun=0,
        native_calibration_fit_calls=0,
        independent_validation_campaigns=0,
        rows_and_detailed_receipts_in_release=False,
        field_or_thermodynamic_model_validation=False,
        runtime={name: version(name) for name in ("numpy", "openpyxl")},
    )


def apply_native_source(raw_root, spec, artifacts, root):
    from pipeline_integrity.calibration import boundary_metadata, fit_calibration, verify_calibration_record

    record = read_emue_calibration(raw_root / EMUE_ID / ARCHIVE_NAME, source_spec=spec)
    inputs = emue_calibration_inputs(record, source_root=raw_root, source_spec=spec)
    factory = runpy.run_path(str(root / "examples/calibration.py"))["make_case"]

    def case(reviewed):
        request, context, provider = factory(reviewed=reviewed, source_inputs=inputs)
        evidence = context.evidence[0].model_copy(
            update={
                "citation": inputs["source_citation"],
                "locator": inputs["source_url"],
                "license_id": inputs["source_license_id"],
            }
        )
        context = context.model_copy(update={"evidence": (evidence,)})
        return request, context, provider

    q, c, provider = case(False)
    withheld = fit_calibration(q, c, provider)
    q, c, provider = case(True)
    fitted = fit_calibration(q, c, provider)
    if fitted["status"] != "computed":
        raise AssertionError("actual full-covariance source fit failed: " + str(fitted["notes"]))
    values = fitted["values"]
    parameters, uncertainty = values["parameters"], values["parameter_standard_uncertainty"]
    required = q.model_copy(update={"require_independent_validation": True, "split_policy": "separate_runs"})
    required_context = c.model_copy(
        update={"metadata": dict(c.metadata, calibration_boundary=boundary_metadata(required))}
    )
    validation_missing = fit_calibration(required, required_context, provider)
    checks = dict(
        source_fit_original_full_matrix_preserved=q.covariance.values == inputs["standard_joint_covariance"],
        source_published_rounded_offset=abs(parameters["offset"] - 0.99663) <= 0.000005,
        source_published_rounded_slope=abs(parameters["slope"] - (-3.5267)) <= 0.00005,
        source_published_rounded_offset_standard_uncertainty=abs(uncertainty[0] - 0.000961) <= 0.0000005,
        source_published_rounded_slope_standard_uncertainty=abs(uncertainty[1] - 0.04827) <= 0.000005,
        source_published_rounded_parameter_covariance=abs(
            values["parameter_standard_covariance"][0][1] - (-2.53e-5)
        )
        <= 5e-8,
        supplied_covariance_not_residual_scaled=fitted["audits"]["covariance_residual_scaled"] is False,
        single_campaign_has_no_independent_validation=values["validation"] is None
        and all(
            o.run_id == "publisher-single-campaign" and o.observation_time_s is None for o in q.observations
        ),
        unreviewed_case_held_before_native=withheld["status"] == "needs_data"
        and not withheld["native_called"],
        independently_required_case_refused=validation_missing["status"] == "out_of_scope"
        and not validation_missing["native_called"],
        fitted_source_receipt_replayed=verify_calibration_record(fitted, provider, request=q, context=c)[
            "valid"
        ],
    )
    checks = {name: bool(value) for name, value in checks.items()}
    if not all(checks.values()):
        raise AssertionError([name for name, passed in checks.items() if not passed])
    save(
        artifacts / "actual_source_native_calibration_records.json",
        dict(unreviewed=withheld, fit_only=fitted, independently_required=validation_missing),
    )
    return dict(
        status="published_numerical_reference_reproduced_no_independent_campaign",
        actual_requests=3,
        native_fit_calls=1,
        computed_record_native_replays=1,
        source_points=30,
        source_model_calls=fitted["model_calls"],
        source_status_counts={"computed": 1, "needs_data": 1, "out_of_scope": 1},
        parameters=parameters,
        parameter_standard_uncertainty=uncertainty,
        parameter_standard_covariance=values["parameter_standard_covariance"],
        covariance_residual_scaled=False,
        source_joint_covariance_diagonalized=False,
        independent_validation_campaigns=0,
        prediction_or_field_acceptance_claimed=False,
        checks=checks,
        numerical_reference_and_contract_criteria=sum(checks.values()),
        adapter_sha256=fitted["adapter_sha256"],
        factory_sha256=file_digest(root / "examples/calibration.py"),
        backend=fitted["backend"],
        source_record_digest=record["record_digest"],
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-root", type=Path, default=Path("data/raw"))
    parser.add_argument("--catalog-file", type=Path)
    parser.add_argument(
        "--fit-source", action="store_true", help="Explicitly apply the reviewed single-campaign research fit"
    )
    parser.add_argument("--artifacts", type=Path, default=Path("artifacts/calibration_data"))
    parser.add_argument("--output", type=Path, default=Path("research/calibration_source_results.json"))
    args = parser.parse_args()
    specs = (
        catalog()
        if args.catalog_file is None
        else tuple(
            DatasetSpec.model_validate_json(json.dumps(row))
            for row in json.loads(args.catalog_file.read_text(encoding="utf-8"))["datasets"]
        )
    )
    selected = [s for s in specs if s.id == EMUE_ID]
    if len(selected) != 1:
        raise ValueError("one reviewed EMUE calibration source specification required")
    result = review(args.raw_root, selected[0], args.artifacts)
    root = Path(__file__).resolve().parents[1]
    if args.fit_source:
        result["native_source_application"] = apply_native_source(
            args.raw_root, selected[0], args.artifacts, root
        )
        result["native_calibration_fit_calls"] = result["native_source_application"]["native_fit_calls"]
    blocks = re.findall(
        r"```python\s*\n(.*?)\n```",
        (root / "docs/CALIBRATION_DATA_KO.md").read_text(encoding="utf-8"),
        flags=re.S,
    )
    for block in blocks:
        exec(
            compile(block, "CALIBRATION_DATA_KO.md", "exec"),
            {"CALIBRATION_RAW_ROOT": args.raw_root, "CALIBRATION_SOURCE_SPEC": selected[0]},
        )
    result["doc_python_blocks_executed"] = len(blocks)
    result["project_file_sha256"] = {
        name: file_digest(root / name)
        for name in (
            "src/pipeline_integrity/calibration_data.py",
            "tests/test_calibration_data.py",
            "scripts/review_calibration_source.py",
            "docs/CALIBRATION_DATA_KO.md",
        )
    }
    save(args.output, result)
    print(
        json.dumps(
            {
                k: result[k]
                for k in (
                    "acquired_groups",
                    "acquired_files",
                    "acquired_bytes",
                    "source_contract_criteria",
                    "doc_python_blocks_executed",
                )
            }
        )
    )


if __name__ == "__main__":
    main()
