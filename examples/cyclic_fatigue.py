"""Authored material quantities; values are not empirical material recommendations."""

from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.cyclic_fatigue import PARAMETER_UNITS, REVIEWS, CyclicFatigueRequest


def make_cases():
    context = RunContext(
        asset_id="authored_cyclic_material",
        scenario_id="cyclic_reference",
        clock_id="source_cycle_record",
        origin="synthetic",
        evidence=(
            EvidenceRef(
                id="authored",
                citation="Independently authored cyclic reference inputs",
                origin="synthetic",
                license_id="MIT",
            ),
        ),
    )
    parameters = {
        "fatigue.goodman": {
            "alternating_stress_Pa": 1.2e8,
            "mean_stress_Pa": 2e8,
            "ultimate_tensile_strength_Pa": 8e8,
        },
        "fatigue.hysteresis_path": {
            "stress_Pa": [0.0, 2e8, 2e8, 0.0, 0.0],
            "strain_fraction": [0.0, 0.0, 0.002, 0.002, 0.0],
            "cycle_boundary_declared": True,
            "linear_closure_declared": False,
            "maximum_endpoint_stress_difference_Pa": 0.0,
            "maximum_endpoint_strain_difference": 0.0,
        },
        # Equal -1/2 exponents make an independent closed-form reference at N=5000.
        "fatigue.strain_life": {
            "strain_amplitude": 0.00105,
            "elastic_modulus_Pa": 2e11,
            "fatigue_strength_coefficient_Pa": 1e9,
            "fatigue_strength_exponent": -0.5,
            "fatigue_ductility_coefficient": 0.1,
            "fatigue_ductility_exponent": -0.5,
            "minimum_valid_cycles": 50.0,
            "maximum_valid_cycles": 5e5,
        },
    }
    requests = {}
    for method, p in parameters.items():
        path = method == "fatigue.hysteresis_path"
        requests[method] = CyclicFatigueRequest(
            method=method,
            asset_id=context.asset_id,
            specimen_id="authored_specimen",
            material_condition_id="authored_condition",
            population_id="authored_population",
            parameter_support_id="authored_support",
            load_history_id="authored_load",
            location_id="uniaxial_material_point",
            clock_id=context.clock_id,
            stress_measure="engineering",
            strain_measure="not_used" if method == "fatigue.goodman" else "engineering",
            response_regime="cyclic",
            stress_sign_convention="tension_positive",
            amplitude_definition="half_range",
            cycle_definition="two_reversals",
            parameters=p,
            input_units=PARAMETER_UNITS[method],
            applicability_reviews=dict.fromkeys(REVIEWS[method], True),
            review_evidence_ids=dict.fromkeys(REVIEWS[method], "authored"),
            evidence_ids=("authored",),
            path_sample_ids=tuple(f"row-{i}" for i in range(5)) if path else (),
            sample_time_s=(0.0, 0.1, 0.2, 0.3, 0.4) if path else None,
            observation_period_s=(0.0, 0.4) if path else None,
        )
    return requests, context
