"""Original point, interval-average and counter examples on one observed window."""

from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.metrology import MeasurementBasis, MeasurementInput, StandardCovariance
from pipeline_integrity.totalization import (
    REVIEWS,
    TotalizationRequest,
    boundary_metadata,
    totalize_measurements,
)


def make_case(*, meaning="point_linear", reviewed=False):
    reading_unit = "kg" if meaning == "cumulative_counter" else "kg/s"
    reading_basis = MeasurementBasis(
        quantity="cumulative_mass" if reading_unit == "kg" else "mass_flow",
        unit=reading_unit,
        definition_id="same-meter-mass-basis",
        evidence_id="authored",
    )
    time_basis = MeasurementBasis(
        quantity="elapsed_time", unit="s", definition_id="observation-clock", evidence_id="authored"
    )
    output_basis = MeasurementBasis(
        quantity="net_mass",
        unit="kg",
        definition_id="same-meter-observed-window-net-mass",
        evidence_id="authored",
    )
    values = {
        "point_linear": (1.0, 3.0, 5.0),
        "interval_mean": (2.0, 4.0),
        "cumulative_counter": (10.0, 11.0, 15.0),
    }[meaning]
    readings = tuple(
        MeasurementInput(
            id=f"reading_{i}", estimate=v, basis=reading_basis, evidence_id="authored", valid=True
        )
        for i, v in enumerate(values)
    )
    times = tuple(
        MeasurementInput(id=f"time_{i}", estimate=v, basis=time_basis, evidence_id="authored", valid=True)
        for i, v in enumerate((0.0, 1.0, 3.0))
    )
    inputs = (*readings, *times)
    covariance = tuple(
        tuple((0.01 if i < len(readings) else 0.0001) if i == j else 0.0 for j in range(len(inputs)))
        for i in range(len(inputs))
    )
    request = TotalizationRequest(
        id="authored-window",
        asset_id="meter-a",
        meter_id="mass-meter",
        clock_id="observation-clock",
        event_id="window-a",
        clock_offset_s=20.0,
        nominal_positive_source_port="inlet",
        nominal_positive_target_port="outlet",
        sample_meaning=meaning,
        sign_convention="signed_nominal_ports",
        readings=readings,
        times=times,
        output_basis=output_basis,
        maximum_interval_s=3.0,
        covariance=StandardCovariance(
            input_ids=tuple(v.id for v in inputs),
            values=covariance,
            evidence_id="authored",
            definition_reviewed=True,
        ),
        uncertainty_required=True,
        local_uncertainty_approximation_reviewed=reviewed,
        evidence_id="authored",
        applicability_reviews=dict.fromkeys(REVIEWS, True) if reviewed else {},
    )
    context = RunContext(
        asset_id=request.asset_id,
        scenario_id="authored-measurement-model",
        clock_id=request.clock_id,
        origin="synthetic",
        evidence=(
            EvidenceRef(
                id="authored",
                citation="Original observed-window and complete joint covariance example",
                origin="synthetic",
                license_id="MIT",
            ),
        ),
        metadata={"totalization_boundary": boundary_metadata(request)},
    )
    return request, context


if __name__ == "__main__":
    import json

    request, context = make_case()
    print(json.dumps(totalize_measurements(request, context), indent=2))
