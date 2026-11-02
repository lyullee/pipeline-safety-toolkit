"""Original material-test and cyclic reference inputs, without proprietary tables."""

from pipeline_integrity.characterization import PARAMETER_UNITS, REVIEWS, CharacterizationRequest
from pipeline_integrity.contracts import EvidenceRef, RunContext


def make_cases():
    context = RunContext(
        asset_id="authored_material",
        scenario_id="specimen_kinematics_reference",
        clock_id="declared_specimen_record_not_a_fabricated_time_axis",
        origin="synthetic",
        evidence=(
            EvidenceRef(
                id="authored",
                citation="Original specimen geometry, force, branch and cyclic-amplitude reference values",
                origin="synthetic",
                license_id="MIT",
            ),
        ),
    )
    parameters = {
        "materials.tensile_point": {
            "force_N": 20000.0,
            "initial_area_m2": 1e-4,
            "extension_m": 0.005,
            "gauge_length_m": 0.05,
            "uniform_deformation_declared": True,
        },
        "materials.ductility_retention": {
            "baseline_elongation_fraction": 0.2,
            "exposed_elongation_fraction": 0.25,
        },
        "materials.offset_yield": {
            "engineering_strain": [0.0, 0.001, 0.003, 0.006],
            "engineering_stress_Pa": [0.0, 2e8, 3e8, 4e8],
            "elastic_modulus_Pa": 2e11,
            "offset_strain": 0.002,
        },
        "materials.ramberg_osgood": {
            "stress_amplitude_Pa": 2e8,
            "elastic_modulus_Pa": 2e11,
            "cyclic_strength_coefficient_Pa": 1e9,
            "cyclic_strain_hardening_exponent": 0.2,
        },
    }
    cases = {
        method: CharacterizationRequest(
            method=method,
            asset_id=context.asset_id,
            specimen_id="authored_specimen",
            material_condition_id="authored_condition",
            location_id="gauge_or_material_point",
            clock_id=context.clock_id,
            parameters=p,
            input_units=PARAMETER_UNITS[method],
            applicability_reviews=dict.fromkeys(REVIEWS[method], True),
            review_evidence_ids=dict.fromkeys(REVIEWS[method], "authored"),
            evidence_ids=("authored",),
        )
        for method, p in parameters.items()
    }
    return cases, context
