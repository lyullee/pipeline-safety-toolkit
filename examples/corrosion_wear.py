"""Original hypothetical inputs, not fitted material properties or field predictions."""

import math

from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.corrosion_wear import INPUT_UNITS, REVIEWS, CorrosionWearRequest, LossDomain


def make_cases():
    context = RunContext(
        asset_id="authored_surface",
        scenario_id="loss_reference",
        clock_id="declared_physical_exposure",
        origin="synthetic",
        evidence=(
            EvidenceRef(
                id="authored",
                citation="Original numerical reference inputs",
                origin="synthetic",
                license_id="MIT",
            ),
        ),
    )
    parameters = {
        "corrosion.power_law": {
            "coefficient_m": 0.0002,
            "exponent": 0.5,
            "exposure_s": 400.0,
            "reference_time_s": 100.0,
        },
        "degradation.archard_wear": {
            "dimensionless_wear_coefficient": 1e-5,
            "normal_force_N": 100.0,
            "sliding_distance_m": 10.0,
            "hardness_Pa": 1e9,
            "distributed_area_m2": 0.001,
        },
        "corrosion.gumbel_area_maximum": {
            "population_id": "authored_population",
            "exposure_condition_id": "one_exposure",
            "block_ids": [f"block-{i}" for i in range(32)],
            "maximum_depth_m": [0.003 + 0.0005 * (-math.log(-math.log((i + 0.5) / 32))) for i in range(32)],
            "block_area_m2": 0.01,
            "target_block_count": 64,
            "independent_equivalent_blocks": True,
            "same_exposure_and_surface_population": True,
            "complete_uncensored_block_maxima": True,
            "maximum_target_to_sampled_area_ratio": 2.5,
            "depth_threshold_m": 0.006,
            "quantile_probability": 0.9,
            "confidence_level": 0.95,
            "diagnostic_resamples": 99,
            "parameter_resamples": 99,
            "seed": 21,
            "minimum_goodness_of_fit_pvalue": 0.05,
            "maximum_negative_depth_probability": 1e-10,
        },
    }
    requests = {}
    for method, values in parameters.items():
        quantity = {
            "corrosion.power_law": "mean_penetration",
            "degradation.archard_wear": "wear_volume",
            "corrosion.gumbel_area_maximum": "maximum_depth",
        }[method]
        domain = (
            None
            if method == "corrosion.gumbel_area_maximum"
            else LossDomain(
                material_condition_id="authored_material",
                environment_or_contact_id="authored_condition",
                population_id="authored_population",
                quantity_meaning=quantity,
                parameter_support_id="authored_support",
                evidence_id="authored",
                calibration_reviewed=True,
                exposure_origin_id="physical_origin",
                exposure_range_s=(0.0, 1000.0),
                normal_force_range_N=(0.0, 1000.0),
                sliding_distance_range_m=(0.0, 100.0),
                hardness_range_Pa=(1e8, 2e9),
            )
        )
        requests[method] = CorrosionWearRequest(
            method=method,
            asset_id=context.asset_id,
            location_id="declared_surface",
            clock_id=context.clock_id,
            material_condition_id="authored_material",
            environment_or_contact_id="authored_condition",
            population_id="authored_population",
            quantity_meaning=quantity,
            exposure_origin_id="physical_origin",
            exposure_condition_id="one_exposure",
            parameters=values,
            input_units=INPUT_UNITS[method],
            domain=domain,
            applicability_reviews=dict.fromkeys(REVIEWS[method], True),
            review_evidence_ids=dict.fromkeys(REVIEWS[method], "authored"),
            evidence_ids=("authored",),
        )
    return requests, context
