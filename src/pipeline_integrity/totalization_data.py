"""Preserve source counter-period means and unresolved timing/quantity covariance."""

from pathlib import Path

from .contracts import EvidenceRef, RunContext
from .datasets import file_digest
from .meter_data import verify_meter_source_record
from .metrology import MeasurementBasis, MeasurementInput
from .totalization import REVIEWS, TotalizationRequest, boundary_metadata


def vsl_counter_period_cases(record, *, source_root):
    """Map separate indicated counter periods; never join them into one inventory."""
    verify_meter_source_record(record, source_root=source_root)
    if record["kind"] != "vsl_liquid_meter_calibration":
        raise ValueError("the pinned VSL liquid-meter source is required")
    evidence = (
        EvidenceRef(
            id="workbook",
            citation=record["provenance"]["citation"],
            origin="laboratory",
            sha256=record["provenance"]["sha256"],
            license_id="CC-BY-4.0",
        ),
        EvidenceRef(
            id="method",
            citation=record["method_assets"][0]["citation"],
            origin="literature",
            sha256=record["method_assets"][0]["sha256"],
            license_id="CC-BY-4.0",
        ),
        EvidenceRef(
            id="period-definition",
            citation="Original mapping of indicated counter-period mean onto its supplied elapsed duration; no reconstructed calendar time",
            origin="user_declared",
            sha256=file_digest(Path(__file__)),
        ),
    )
    result = []
    for index, row in enumerate(record["runs"]):
        values = row["observations_si"]
        mass = values["indicated_mass_flow_kg_s"] is not None
        source_key = "indicated_mass_flow_kg_s" if mass else "indicated_local_volume_flow_m3_s"
        rate_unit, amount_unit = ("kg/s", "kg") if mass else ("m^3/s", "m^3")
        rate, duration = values[source_key], values["counter_duration_s"]
        clock_id = f"source-counter-period-{index}"
        basis_id = f"source-local-liquid:{row['source_sheet']}:{row['source_row']}"
        reading_basis = MeasurementBasis(
            quantity="indicated_counter_period_mean",
            unit=rate_unit,
            definition_id=basis_id,
            evidence_id="workbook",
        )
        output_basis = MeasurementBasis(
            quantity="counter_period_mass" if mass else "counter_period_local_liquid_volume",
            unit=amount_unit,
            definition_id=basis_id,
            evidence_id="workbook",
        )
        time_basis = MeasurementBasis(
            quantity="relative_counter_elapsed_time",
            unit="s",
            definition_id=clock_id,
            evidence_id="period-definition",
        )
        request = TotalizationRequest(
            id=f"source-period-{index}",
            asset_id="publisher-liquid-calibration-apparatus",
            meter_id=row["source_sheet"],
            clock_id=clock_id,
            event_id=f"row-{row['source_row']}",
            clock_offset_s=0.0,
            nominal_positive_source_port="source-MUT-upstream-role",
            nominal_positive_target_port="source-MUT-downstream-role",
            sample_meaning="interval_mean",
            sign_convention="nonnegative",
            readings=(
                MeasurementInput(
                    id="indicated-period-mean",
                    estimate=rate,
                    basis=reading_basis,
                    evidence_id="workbook",
                    valid=True if rate is not None else None,
                ),
            ),
            times=(
                MeasurementInput(
                    id="relative-origin",
                    estimate=0.0 if duration is not None else None,
                    basis=time_basis,
                    evidence_id="period-definition",
                    valid=True if duration is not None else None,
                ),
                MeasurementInput(
                    id="counter-duration",
                    estimate=duration,
                    basis=time_basis,
                    evidence_id="workbook",
                    valid=True if duration is not None else None,
                ),
            ),
            output_basis=output_basis,
            maximum_interval_s=duration,
            covariance=None,
            uncertainty_required=True,
            local_uncertainty_approximation_reviewed=True,
            evidence_id="period-definition",
            applicability_reviews=dict.fromkeys(REVIEWS, True),
        )
        context = RunContext(
            asset_id=request.asset_id,
            scenario_id="source-indicated-counter-period",
            clock_id=clock_id,
            origin="laboratory",
            evidence=evidence,
            metadata={
                "totalization_boundary": boundary_metadata(request),
                "source_record_digest": record["record_digest"],
                "source_sheet": row["source_sheet"],
                "source_row": row["source_row"],
                "source_calendar_clock": row["clock"],
                "time_definition": "relative0..supplied_counter_duration_only; no wall-clock/UTC conversion",
                "local_liquid_state": {
                    key: value
                    for key, value in values.items()
                    if any(label in key for label in ("pressure", "temperature", "density"))
                },
                "physical_plant_port_inventory": None,
                "reference_meter_window_equated_to_MUT_period": False,
                "normal_or_dry_gas_volume_inferred": False,
                "reading_time_covariance": None,
                "cross_period_inventory_aggregation_performed": False,
            },
        )
        result.append((request, context))
    return tuple(result)
