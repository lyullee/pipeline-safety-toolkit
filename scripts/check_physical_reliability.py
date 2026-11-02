"""Actual owner calls and authored arithmetic, without observed accident or calibration claims."""

import argparse
import json
import math
import runpy
import sys
from pathlib import Path

from check_integrations import revision

from pipeline_integrity.datasets import file_digest
from pipeline_integrity.reliability_exchange import (
    evaluate_physical_reliability,
    physical_frequency,
    physical_period_probability,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--materials", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("research/physical_reliability_results.json"))
    args = parser.parse_args()
    before = revision(args.materials)
    # Keep even Python's import cache outside the separately owned source tree.
    sys.dont_write_bytecode = True
    sys.path.insert(0, str((args.materials / "src").resolve()))
    cases, frequency_mapping, period_mapping, context = runpy.run_path("examples/physical_reliability.py")[
        "make_cases"
    ]()
    records, references = {}, {}

    def evaluate(name, method, **changes):
        request = cases[method]
        parameters = request.parameters | changes
        record = evaluate_physical_reliability(request.model_copy(update={"parameters": parameters}), context)
        assert record["status"] == "computed", (name, record["issues"])
        records[name] = record
        return record

    def check(name, actual, expected):
        assert math.isclose(actual, expected, rel_tol=1e-12, abs_tol=0.0), (name, actual, expected)
        references[name] = dict(actual=actual, expected=expected, absolute_error=abs(actual - expected))

    gamma_method, weibull_method = "reliability.gamma_poisson", "reliability.weibull_interval"
    gamma = evaluate("physical_count", gamma_method)
    g = gamma["values"]
    year = 31557600.0
    check("posterior_shape_with_explicit_prior", g["posterior_shape"], 5.0)
    check("posterior_total_exposure_seconds", g["posterior_exposure_s"], 11 * year)
    check("posterior_mean_events_per_second", g["mean_frequency_per_s"], 5 / (11 * year))
    check("posterior_intensity_variance", g["variance_frequency_per_s2"], 5 / (11 * year) ** 2)
    frequency_mapping = frequency_mapping.model_copy(
        update={"expected_source_digest": gamma["record_digest"]}
    )
    annual = physical_frequency(gamma, frequency_mapping)
    assert annual["status"] == "computed"
    check("declared_Julian_year_mean_event_count", annual["frequency"]["annual_frequency_per_year"], 5 / 11)
    alternative_year = physical_frequency(
        gamma,
        frequency_mapping.model_copy(
            update={"seconds_per_year": 31536000.0, "year_basis_id": "declared_365_day_year"}
        ),
    )
    check(
        "declared_365_day_year_mean_event_count",
        alternative_year["frequency"]["annual_frequency_per_year"],
        5 / 11 * 365 / 365.25,
    )

    zero = evaluate("zero_observed_events", gamma_method, confirmed_events=0)
    check("zero_events_preserve_prior_shape", zero["values"]["posterior_shape"], 2.0)
    check("zero_events_do_not_imply_zero_intensity", zero["values"]["mean_frequency_per_s"], 2 / (11 * year))
    first = evaluate("first_disjoint_exposure", gamma_method, confirmed_events=1, exposure_s=4 * year)
    second = evaluate(
        "second_disjoint_exposure",
        gamma_method,
        prior_shape=first["values"]["posterior_shape"],
        prior_exposure_s=first["values"]["posterior_exposure_s"],
        confirmed_events=2,
        exposure_s=6 * year,
    )
    check("split_exposure_preserves_posterior_shape", second["values"]["posterior_shape"], 5.0)
    check(
        "split_exposure_preserves_posterior_mean",
        second["values"]["mean_frequency_per_s"],
        5 / (11 * year),
    )

    weibull = evaluate("aged_first_failure", weibull_method)
    w = weibull["values"]
    check("aged_interval_integrated_hazard", w["integrated_hazard"], 3 / 4)
    check("aged_interval_conditional_probability", w["probability_given_survival"], 1 - math.exp(-3 / 4))
    check("equivalent_hazard_is_not_event_intensity", w["equivalent_hazard_per_s"], 3 / 200)
    fresh = evaluate("fresh_first_failure", weibull_method, current_age_s=0.0)
    check(
        "fresh_interval_conditional_probability",
        fresh["values"]["probability_given_survival"],
        1 - math.exp(-1 / 4),
    )
    left = evaluate("first_conditional_subinterval", weibull_method, horizon_s=25.0)
    right = evaluate("second_conditional_subinterval", weibull_method, current_age_s=75.0, horizon_s=25.0)
    check(
        "contiguous_conditional_survival_product",
        (1 - left["values"]["probability_given_survival"])
        * (1 - right["values"]["probability_given_survival"]),
        math.exp(-3 / 4),
    )
    memoryless = evaluate(
        "exponential_aged_interval", weibull_method, shape=1.0, current_age_s=5000.0, horizon_s=10.0
    )
    check(
        "shape_one_memoryless_reference",
        memoryless["values"]["probability_given_survival"],
        1 - math.exp(-0.1),
    )
    period_mapping = period_mapping.model_copy(update={"expected_source_digest": weibull["record_digest"]})
    period = physical_period_probability(weibull, period_mapping)
    assert period["status"] == "computed" and period["probability"]["measure"] == "mission_probability"
    check(
        "mapped_period_preserves_native_probability",
        period["probability"]["probability"],
        1 - math.exp(-3 / 4),
    )
    shifted = physical_period_probability(
        weibull,
        period_mapping.model_copy(
            update={"period_start_s": 200.0, "period_finish_s": 250.0, "age_origin_s": 150.0}
        ),
    )
    check(
        "clock_translation_preserves_survival_probability",
        shifted["probability"]["probability"],
        1 - math.exp(-3 / 4),
    )

    withheld = {}
    for key, changes in {
        "incomplete_event_ascertainment": dict(complete_ascertainment=False),
        "unknown_event_count": dict(confirmed_events=None),
        "unknown_exposure": dict(exposure_s=None),
        "unknown_prior": dict(prior_shape=None),
    }.items():
        request = cases[gamma_method]
        record = evaluate_physical_reliability(
            request.model_copy(update={"parameters": request.parameters | changes}), context
        )
        assert record["status"] == "needs_data" and record["native_receipt"] is None
        withheld[key] = record["status"]
    withheld["unreviewed_year_basis"] = physical_frequency(
        gamma, frequency_mapping.model_copy(update={"year_basis_reviewed": False})
    )["status"]
    withheld["unreviewed_survival"] = physical_period_probability(
        weibull, period_mapping.model_copy(update={"survival_and_age_origin_reviewed": False})
    )["status"]
    assert set(withheld.values()) == {"needs_data"}

    invalid_rejected = 0
    for method, changes in (
        (gamma_method, dict(confirmed_events=-1)),
        (gamma_method, dict(confirmed_events=True)),
        (gamma_method, dict(exposure_s=0.0)),
        (gamma_method, dict(prior_shape=0.0)),
        (weibull_method, dict(current_age_s=-1.0)),
        (weibull_method, dict(horizon_s=0.0)),
        (weibull_method, dict(shape=0.0)),
    ):
        request = cases[method]
        try:
            evaluate_physical_reliability(
                request.model_copy(update={"parameters": request.parameters | changes}), context
            )
        except ValueError:
            invalid_rejected += 1
        else:
            raise AssertionError((method, changes, "invalid native input was accepted"))

    # A posterior mixture occurrence probability differs from plugging its mean into exp(-rate*t).
    # Neither is supplied by this adapter: gamma_poisson returns intensity moments only.
    assert not math.isclose(1 - (1 / 2) ** 5, 1 - math.exp(-5), rel_tol=1e-3)
    assert not gamma["posterior_predictive_occurrence_probability_computed"]
    after = revision(args.materials)
    assert before == after, "the separately owned material source changed during verification"
    result = dict(
        schema="pipeline_integrity.physical_reliability_verification.v1",
        status="passed",
        native_models=sorted({r["request"]["method"] for r in records.values()}),
        native_computed_case_count=len(records),
        native_model_versions={
            r["request"]["method"]: r["native_receipt"]["job"]["model_version"] for r in records.values()
        },
        independent_reference_count=len(references),
        independent_references=references,
        withheld_cases=withheld,
        invalid_native_inputs_rejected=invalid_rejected,
        conditional_survival_and_age_preserved=True,
        seconds_per_year_explicit=True,
        physical_mean_intensity_is_not_demand_HEP=True,
        posterior_predictive_occurrence_probability_inferred=False,
        gates_or_exclusive_path_engine_added=False,
        observed_accident_data_used=False,
        field_calibration_or_validation_claim=False,
        original_authored_inputs_not_observations=True,
        source_record_digests={key: record["record_digest"] for key, record in records.items()},
        external_material_source_before=before,
        external_material_source_after=after,
        external_source_unchanged=before == after,
        implementation_sha256=file_digest(Path("src/pipeline_integrity/reliability_exchange.py")),
        script_sha256=file_digest(Path(__file__)),
        example_sha256=file_digest(Path("examples/physical_reliability.py")),
    )
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            dict(
                status=result["status"],
                native_models=len(result["native_models"]),
                native_cases=len(records),
                independent_references=len(references),
                withheld_cases=len(withheld),
                invalid_inputs_rejected=invalid_rejected,
            )
        )
    )


if __name__ == "__main__":
    main()
