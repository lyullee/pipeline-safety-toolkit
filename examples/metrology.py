"""Original meter indication/reference error budget; no field covariance is implied."""

from pathlib import Path

from pipeline_integrity.contracts import EvidenceRef, RunContext, digest
from pipeline_integrity.datasets import file_digest
from pipeline_integrity.metrology import (
    CoverageDeclaration,
    MeasurementBasis,
    MeasurementBudgetRequest,
    MeasurementDecisionRule,
    MeasurementInput,
    MeasurementLinearization,
    MeasurementOutput,
    StandardCovariance,
    propagate_measurement_uncertainty,
    verify_measurement_uncertainty,
)


def example():
    evidence = EvidenceRef(
        id="authored",
        citation="Original synthetic meter relative-error and correlated covariance example",
        origin="synthetic",
        limitations=("Not a calibration certificate or a field distribution",),
    )
    context = RunContext(
        asset_id="meter_a",
        scenario_id="analytical_budget",
        clock_id="steady_run_clock",
        origin="synthetic",
        evidence=(evidence,),
    )
    flow_basis = MeasurementBasis(
        quantity="mass_flow",
        unit="kg/s",
        definition_id="same_meter_steady_run_mass_basis",
        evidence_id="authored",
    )
    inputs = tuple(
        MeasurementInput(id=name, estimate=value, basis=flow_basis, evidence_id="authored", valid=True)
        for name, value in (("reference", 10.0), ("indication", 11.0))
    )
    reference, indication = (v.estimate for v in inputs)
    model = MeasurementLinearization(
        model_id="meter_relative_error",
        model_version="1",
        provider_source_sha256=file_digest(Path(__file__)),
        configuration_sha256=digest({"definition": "indication/reference - 1"}),
        inputs_sha256=digest(inputs),
        outputs=(
            MeasurementOutput(
                id="relative_error",
                estimate=indication / reference - 1,
                basis=MeasurementBasis(
                    quantity="relative_mass_flow_error",
                    unit="1",
                    definition_id="ratio_minus_one",
                    evidence_id="authored",
                ),
            ),
        ),
        jacobian=((-indication / reference**2, 1 / reference),),
        evidence_id="authored",
        local_approximation_reviewed=True,
    )
    request = MeasurementBudgetRequest(
        id="relative_error_budget",
        asset_id=context.asset_id,
        clock_id=context.clock_id,
        event_id="steady_run_1",
        time_s=120.0,
        inputs=inputs,
        linearization=model,
        covariance=StandardCovariance(
            input_ids=tuple(v.id for v in inputs),
            values=((0.04, 0.01), (0.01, 0.09)),
            evidence_id="authored",
            definition_reviewed=True,
        ),
        coverage=CoverageDeclaration(factor=2.0, evidence_id="authored", reviewed=True),
        decision_rules=(
            MeasurementDecisionRule(
                output_id="relative_error",
                lower_limit=-0.15,
                upper_limit=0.15,
                comparison="expanded_interval",
                evidence_id="authored",
                reviewed=True,
            ),
        ),
    )
    result = propagate_measurement_uncertainty(request, context)
    assert verify_measurement_uncertainty(result)
    return request, context, result


if __name__ == "__main__":
    import json

    print(json.dumps(example()[2], ensure_ascii=False, indent=2))
