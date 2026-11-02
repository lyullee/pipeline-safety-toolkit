"""Read-only analytic covariance and actual counter-period source probes."""

import json
import math
import runpy
from copy import deepcopy
from pathlib import Path

from pipeline_integrity.contracts import EvidenceRef, RunContext, digest
from pipeline_integrity.datasets import file_digest
from pipeline_integrity.meter_data import read_vsl_calibration, verify_meter_source_record
from pipeline_integrity.metrology import MeasurementBasis, MeasurementInput, StandardCovariance
from pipeline_integrity.totalization import (
    REVIEWS,
    TotalizationRequest,
    boundary_metadata,
    totalize_measurements,
    verify_totalization_record,
)

ROOT = Path(__file__).resolve().parents[1]


def save(name, value):
    private = ROOT / "artifacts/verification/totalization_review"
    private.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
    (private / name).write_text(encoded, encoding="utf-8")
    if name == "results.json":
        value["review_driver_sha256"] = file_digest(Path(__file__))
        (ROOT / "research/totalization_independent_review.json").write_text(
            json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n", encoding="utf-8"
        )


def recontext(q, context):
    return context.model_copy(
        update={"metadata": dict(context.metadata, totalization_boundary=boundary_metadata(q))}
    )


def main():
    make = runpy.run_path("examples/totalization.py")["make_case"]
    checks = {}
    observations = {}
    pins = {
        name: file_digest(Path("src/pipeline_integrity") / name)
        for name in ("totalization.py", "quadrature.py", "contracts.py", "metrology.py")
    }
    q, c = make(reviewed=True)
    readings = tuple(
        v.model_copy(update={"estimate": p}) for v, p in zip(q.readings, (1.0, 4.0, 2.0), strict=True)
    )
    times = tuple(v.model_copy(update={"estimate": p}) for v, p in zip(q.times, (0.0, 2.0, 5.0), strict=True))
    # A factored covariance gives an independent scalar error-budget oracle.
    # Last two factors are common clock error and a mixed reading/clock error.
    factors = [
        [0.1, 0, 0, 0, 0, 0, 0, 0.05],
        [0, 0.2, 0, 0, 0, 0, 0, 0],
        [0, 0, 0.15, 0, 0, 0, 0, 0.02],
        [0, 0, 0, 0.01, 0, 0, 0.25, 0.03],
        [0, 0, 0, 0, 0.02, 0, 0.25, 0],
        [0, 0, 0, 0, 0, 0.03, 0.25, -0.01],
    ]
    matrix = tuple(
        tuple(math.fsum(a * b for a, b in zip(first, last, strict=True)) for last in factors)
        for first in factors
    )
    inputs = (*readings, *times)
    covariance = StandardCovariance(
        input_ids=tuple(v.id for v in inputs), values=matrix, evidence_id="authored", definition_reviewed=True
    )
    q = q.model_copy(
        update={"readings": readings, "times": times, "covariance": covariance, "maximum_interval_s": 5.0}
    )
    c = recontext(q, c)
    result = totalize_measurements(q, c)
    gradient = (1.0, 2.5, 1.5, -2.5, -0.5, 3.0)
    duration_gradient = (0.0, 0.0, 0.0, -1.0, 0.0, 1.0)
    projected = [math.fsum(gradient[i] * factors[i][j] for i in range(6)) for j in range(8)]
    duration_projected = [math.fsum(duration_gradient[i] * factors[i][j] for i in range(6)) for j in range(8)]
    expected = math.fsum(v * v for v in projected)
    duration_variance = math.fsum(v * v for v in duration_projected)
    cross = math.fsum(a * b for a, b in zip(projected, duration_projected, strict=True))
    actual = result["uncertainty"]["output_covariance"]
    checks["independent_14kg_point_integral"] = (
        result["status"] == "computed" and result["values"]["complete_net_amount"] == 14.0
    )
    checks["full_joint_factored_covariance"] = math.isclose(
        actual[0][0], expected, rel_tol=1e-12, abs_tol=1e-14
    )
    checks["duration_full_clock_covariance"] = math.isclose(
        actual[1][1], duration_variance, rel_tol=1e-12, abs_tol=1e-14
    )
    checks["amount_duration_cross_covariance"] = math.isclose(
        actual[0][1], cross, rel_tol=1e-12, abs_tol=1e-14
    )
    checks["common_clock_factor_cancels"] = projected[6] == duration_projected[6] == 0
    checks["exact_record_replay"] = verify_totalization_record(result, q, c)["valid"]
    shifted = q.model_copy(update={"clock_offset_s": 37.0})
    shifted_result = totalize_measurements(shifted, recontext(shifted, c))
    checks["representable_offset_preserves_total_and_covariance"] = (
        shifted_result["values"] == result["values"]
        and shifted_result["uncertainty"]["output_covariance"] == actual
        and shifted_result["intervals"][0]["start_s"] == 37.0
    )
    gap = q.model_copy(update={"maximum_interval_s": 2.5})
    gap_result = totalize_measurements(gap, c)
    checks["gap_not_filled"] = (
        gap_result["status"] == "needs_data"
        and gap_result["values"]["known_interval_net_amount"] == 5
        and gap_result["values"]["complete_net_amount"] is None
        and gap_result["skipped_interval_indices"] == [1]
    )
    invalid = q.model_copy(
        update={"readings": (readings[0].model_copy(update={"valid": False}), *readings[1:])}
    )
    invalid_result = totalize_measurements(invalid, c)
    checks["invalid_not_omitted_or_zero"] = (
        invalid_result["status"] == "out_of_scope" and invalid_result["values"] is None
    )
    bad = deepcopy(result)
    bad["uncertainty"]["output_covariance"][0][1] = 0.0
    bad["record_digest"] = digest({k: v for k, v in bad.items() if k != "record_digest"})
    checks["rehashed_cross_covariance_change_refused"] = not verify_totalization_record(bad, q, c)["valid"]
    observations["covariance"] = dict(
        expected_amount_variance=expected,
        expected_duration_variance=duration_variance,
        expected_cross_covariance=cross,
        actual=actual,
        common_clock_factor=projected[6],
    )

    source = read_vsl_calibration("data/raw/vsl_lng_water_calibration")
    assert verify_meter_source_record(source, source_root="data/raw")
    eligible = [r for r in source["runs"] if r["observations_si"]["counter_duration_s"] is not None]
    absent = [r for r in source["runs"] if r["observations_si"]["counter_duration_s"] is None]
    count_mass = sum(r["observations_si"]["indicated_mass_flow_kg_s"] is not None for r in eligible)
    count_volume = sum(r["observations_si"]["indicated_local_volume_flow_m3_s"] is not None for r in eligible)
    checks["VSL_224_counter_periods_not_wall_windows"] = (
        len(eligible) == 224 and count_mass == 160 and count_volume == 64
    )
    checks["VSL_50_turbine_durations_unknown"] = len(absent) == 50 and all(
        "Trigas" in r["source_sheet"] for r in absent
    )
    source_evidence = EvidenceRef(
        id="workbook",
        citation=source["provenance"]["citation"],
        origin="laboratory",
        sha256=source["provenance"]["sha256"],
        license_id="CC-BY-4.0",
    )
    method = source["method_assets"][0]
    method_evidence = EvidenceRef(
        id="method",
        citation=method["citation"],
        origin="literature",
        sha256=method["sha256"],
        license_id="CC-BY-4.0",
    )
    analysis_evidence = EvidenceRef(
        id="period-definition",
        citation="Original relative-counter-clock and indicated-period-mean mapping; no calendar-clock interpolation",
        origin="user_declared",
        sha256=file_digest(Path(__file__)),
    )
    selected = [
        eligible[0],
        next(r for r in eligible if r["observations_si"]["indicated_local_volume_flow_m3_s"] is not None),
        absent[0],
    ]
    actual_cases = []
    for index, run in enumerate(selected):
        mass = run["observations_si"]["indicated_mass_flow_kg_s"] is not None
        rate_unit, amount_unit = ("kg/s", "kg") if mass else ("m^3/s", "m^3")
        field = "indicated_mass_flow_kg_s" if mass else "indicated_local_volume_flow_m3_s"
        rate, tau = run["observations_si"][field], run["observations_si"]["counter_duration_s"]
        clock = f"VSL-counter-relative-period-{index}"
        basis_id = f"publisher-local-liquid-{run['source_sheet']}-row-{run['source_row']}"
        rate_basis = MeasurementBasis(
            quantity="counter_period_mean_mass_flow"
            if mass
            else "counter_period_mean_local_liquid_volume_flow",
            unit=rate_unit,
            definition_id=basis_id,
            evidence_id="workbook",
        )
        amount_basis = MeasurementBasis(
            quantity="local_counter_period_mass" if mass else "local_liquid_counter_period_volume",
            unit=amount_unit,
            definition_id=basis_id,
            evidence_id="workbook",
        )
        time_basis = MeasurementBasis(
            quantity="relative_counter_elapsed_time",
            unit="s",
            definition_id=clock,
            evidence_id="period-definition",
        )
        request = TotalizationRequest(
            id=f"VSL-counter-period-{index}",
            asset_id="publisher-MUT-calibration-apparatus",
            meter_id=run["source_sheet"],
            clock_id=clock,
            event_id=f"source-row-{run['source_row']}",
            clock_offset_s=0.0,
            nominal_positive_source_port="source-MUT-upstream-role",
            nominal_positive_target_port="source-MUT-downstream-role",
            sample_meaning="interval_mean",
            sign_convention="nonnegative",
            readings=(
                MeasurementInput(
                    id="period-mean-indication",
                    estimate=rate,
                    basis=rate_basis,
                    evidence_id="workbook",
                    valid=True if rate is not None else None,
                ),
            ),
            times=(
                MeasurementInput(
                    id="defined-relative-origin",
                    estimate=0.0,
                    basis=time_basis,
                    evidence_id="period-definition",
                    valid=True,
                ),
                MeasurementInput(
                    id="counter-duration",
                    estimate=tau,
                    basis=time_basis,
                    evidence_id="workbook",
                    valid=True if tau is not None else None,
                ),
            ),
            output_basis=amount_basis,
            maximum_interval_s=tau,
            covariance=None,
            uncertainty_required=True,
            local_uncertainty_approximation_reviewed=True,
            evidence_id="period-definition",
            applicability_reviews=dict.fromkeys(REVIEWS, True),
        )
        context = RunContext(
            asset_id=request.asset_id,
            scenario_id="source-counter-period-no-calendar-reconstruction",
            clock_id=clock,
            origin="laboratory",
            evidence=(source_evidence, method_evidence, analysis_evidence),
            metadata={
                "totalization_boundary": boundary_metadata(request),
                "source_record_digest": source["record_digest"],
                "source_sheet": run["source_sheet"],
                "source_row": run["source_row"],
                "source_calendar_clock": run["clock"],
                "counter_time_origin_meaning": "declared relative elapsed origin0, not UTC or source wall-clock interval",
                "local_liquid_state": {
                    k: v
                    for k, v in run["observations_si"].items()
                    if any(word in k for word in ("pressure", "temperature", "density"))
                },
                "gas_or_normal_volume_basis": False,
                "q_time_covariance": None,
                "physical_plant_port_inventory": None,
            },
        )
        answer = totalize_measurements(request, context)
        checks[f"VSL-selected-{index}-uncertainty-or-time-needs-data"] = answer["status"] == "needs_data"
        if tau is not None:
            checks[f"VSL-selected-{index}-period-q-times-tau"] = math.isclose(
                answer["values"]["complete_net_amount"], rate * tau, rel_tol=1e-14
            )
            checks[f"VSL-selected-{index}-no-false-standard-uncertainty"] = (
                answer["uncertainty"]["status"] == "needs_data" and answer["uncertainty"]["outputs"] == {}
            )
        else:
            checks["VSL-turbine-no-wall-duration-filled"] = answer["values"] is None
        actual_cases.append(
            dict(
                request=request.model_dump(mode="json"),
                context=context.model_dump(mode="json"),
                result=answer,
            )
        )
    save("VSL_actual_source_cases.json", actual_cases)
    proposal = dict(
        actual_periods=len(source["runs"]),
        available_counter_periods=224,
        mass_periods=160,
        local_liquid_volume_periods=64,
        turbine_counter_duration_unknown=50,
        selected_actual_requests=3,
        selected_actual_statuses=[c["result"]["status"] for c in actual_cases],
        correct_reading="MUT indicated counter-period mean, not a point sample and not the reference meter's possibly different averaging window",
        correct_clock="declared relative counter elapsed coordinate0..tau; never source wall-start/end, UTC, or cross-period interpolation",
        covariance="unknown full joint reading/time covariance; no combined error U decomposition, no zero offdiagonal assumption",
        multiple_periods_summed_as_one_inventory=False,
        independent_calibration_or_human_error_validation=False,
        source_model_means_only=True,
        source_record_digest=source["record_digest"],
    )
    save("VSL_contract_proposal.json", proposal)
    checks["source_files_unchanged"] = verify_meter_source_record(source, source_root="data/raw")
    after = {name: file_digest(Path("src/pipeline_integrity") / name) for name in pins}
    save(
        "results.json",
        dict(
            checks=checks,
            passed=sum(checks.values()),
            total=len(checks),
            observations=observations,
            source_proposal=proposal,
            producer_pins_before=pins,
            producer_pins_after=after,
            shared_source_changed_during_review=pins != after,
        ),
    )
    print(
        json.dumps(
            dict(
                passed=sum(checks.values()),
                total=len(checks),
                failed=[k for k, v in checks.items() if not v],
                producer_changed=pins != after,
            )
        )
    )


if __name__ == "__main__":
    main()
