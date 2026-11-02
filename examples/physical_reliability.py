"""Authored physical-event exposure and survival examples, without field-calibration data."""

from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.reliability_exchange import (
    REVIEWS,
    PhysicalFrequencyMapping,
    PhysicalReliabilityRequest,
    WeibullPeriodMapping,
)


def make_cases():
    context = RunContext(
        asset_id="authored_component",
        scenario_id="authored_physical_reliability",
        clock_id="scenario_elapsed_s",
        origin="synthetic",
        evidence=(
            EvidenceRef(
                id="authored",
                citation="Independently authored illustrative physical population, count, prior and survival inputs",
                origin="synthetic",
                license_id="MIT",
            ),
        ),
    )
    common = dict(
        asset_id=context.asset_id,
        location_id="authored_segment",
        clock_id=context.clock_id,
        physical_scope_id="authored_component_population",
        event_definition="First physical loss of the declared component function",
        population_definition="Authored homogeneous physical components under unchanged conditions",
        evidence_ids=("authored",),
    )
    parameters = {
        "reliability.gamma_poisson": dict(
            prior_shape=2.0,
            prior_exposure_s=31557600.0,
            confirmed_events=3,
            exposure_s=315576000.0,
            complete_ascertainment=True,
        ),
        "reliability.weibull_interval": dict(shape=2.0, scale_s=100.0, current_age_s=50.0, horizon_s=50.0),
    }
    cases = {
        method: PhysicalReliabilityRequest(
            method=method,
            parameters=p,
            applicability_reviews=dict.fromkeys(REVIEWS[method], True),
            **common,
        )
        for method, p in parameters.items()
    }
    frequency = PhysicalFrequencyMapping(
        event_id="authored_physical_occurrence",
        physical_scope_id=common["physical_scope_id"],
        condition_id="unchanged_physical_population",
        conditioning="The declared physical population remains homogeneous at constant event intensity",
        endpoint="event_occurrence",
        seconds_per_year=31557600.0,
        year_basis_id="declared_Julian_year_365.25_days",
        event_scope_endpoint_and_condition_reviewed=True,
        constant_intensity_projection_reviewed=True,
        year_basis_reviewed=True,
        evidence_ids=("authored",),
    )
    period = WeibullPeriodMapping(
        event_id="authored_first_failure",
        physical_scope_id=common["physical_scope_id"],
        endpoint="first_physical_failure",
        condition_id="survived_to_period_start",
        conditioning="The declared physical component has survived without first failure to the period start",
        period_id="authored_survival_period",
        period_definition="One declared physical first-failure interval",
        period_start_s=100.0,
        period_finish_s=150.0,
        age_origin_s=50.0,
        event_scope_and_period_reviewed=True,
        survival_and_age_origin_reviewed=True,
        evidence_ids=("authored",),
    )
    return cases, frequency, period, context


if __name__ == "__main__":
    from pipeline_integrity.reliability_exchange import evaluate_physical_reliability

    requests, _, _, ctx = make_cases()
    for name, request in requests.items():
        result = evaluate_physical_reliability(request, ctx)
        print(name, result["status"], result["values"])
