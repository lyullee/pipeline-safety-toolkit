"""Original high-temperature mathematical cases; no supplier material/design parameters."""

import math

from pipeline_integrity.contracts import EvidenceRef, RunContext, digest
from pipeline_integrity.creep_exchange import REVIEWS, CreepBlock, CreepDomain, CreepRequest, CreepSequence


def make_cases():
    context = RunContext(
        asset_id="authored_creep_component",
        scenario_id="authored_constant_conditions",
        clock_id="elapsed_s",
        origin="synthetic",
        evidence=(
            EvidenceRef(
                id="reference",
                citation="Original hypothetical constitutive/lifetime cases, explicit SI scales and fresh-material clock",
                origin="synthetic",
                license_id="MIT",
            ),
        ),
    )
    material = "authored_single_material_condition"
    domain = CreepDomain(
        material_condition_id=material,
        temperature_k_range=(850.0, 1050.0),
        stress_pa_range=(1e8, 3e8),
        maximum_exposure_s=1e7,
        evidence_id="reference",
        calibration_reviewed=True,
    )
    base = dict(
        asset_id=context.asset_id,
        location_id="declared_straight_span",
        clock_id=context.clock_id,
        material_condition_id=material,
        domain=domain,
        evaluation_temperature_k=923.15,
        evidence_ids=("reference",),
    )

    def request(method, parameters, **kwargs):
        return CreepRequest(
            method=method,
            parameters=parameters,
            applicability_reviews=dict.fromkeys(REVIEWS[method], True),
            **(base | kwargs),
        )

    cases = {
        "creep.norton": request(
            "creep.norton",
            dict(
                stress_Pa=2e8,
                reference_stress_Pa=2e8,
                rate_at_reference_stress_per_s=1e-8,
                stress_exponent=3.0,
                exposure_s=10000.0,
            ),
        ),
        "creep.larson_miller": request(
            "creep.larson_miller", dict(temperature_K=923.15, constant=20.0, rupture_time_s=36000.0)
        ),
        "creep.time_fraction": request(
            "creep.time_fraction",
            dict(exposure_s=[100000.0, 100000.0], rupture_life_s=[1000000.0, 2000000.0]),
        ),
        "kinetics.arrhenius": request(
            "kinetics.arrhenius",
            dict(
                reference_rate=1e-9,
                reference_temperature_K=900.0,
                temperature_K=1000.0,
                activation_energy_J_mol=100000.0,
            ),
            reference_rate_unit="1/s",
        ),
        "degradation.parabolic_oxidation": request(
            "degradation.parabolic_oxidation",
            dict(initial_oxide_thickness_m=1e-6, parabolic_constant_m2_s=1e-18, exposure_s=1000000.0),
        ),
    }
    stresses, times, ids = [], [], []
    for stress in (1e8, 1.5e8, 2e8, 2.5e8):
        for index, residual in enumerate((-0.2, -0.1, 0.0, 0.1, 0.2)):
            stresses.append(stress)
            times.append(1000000.0 * (2e8 / stress) ** 3 * math.exp(residual))
            ids.append(f"authored_{stress}_{index}")
    evidence = [
        dict(
            source_id="reference",
            origin="synthetic",
            citation="Original uncensored log-life regression reference",
            license_id="MIT",
        )
    ]
    cases["creep.lognormal_life_fit"] = request(
        "creep.lognormal_life_fit",
        dict(
            material_condition_id=material,
            stress_basis="initial_nominal_constant_load",
            temperature_basis="nominal_specified",
            family="stress_only",
            specimen_id=ids,
            initial_stress_Pa=stresses,
            temperature_K=[923.15] * len(ids),
            endpoint_time_s=times,
            right_censored=[False] * len(ids),
            temperature_tolerance_K=0.0,
            independent_specimens_confirmed=True,
            common_mechanism_and_condition_confirmed=True,
            evidence=evidence,
        ),
    )
    sequence = CreepSequence(
        **{
            k: base[k]
            for k in (
                "asset_id",
                "location_id",
                "clock_id",
                "material_condition_id",
                "domain",
                "evidence_ids",
            )
        },
        start_s=0.0,
        finish_s=200000.0,
        blocks=(
            CreepBlock(
                id="first",
                start_s=0.0,
                finish_s=100000.0,
                stress_pa=1.5e8,
                temperature_k=923.15,
                rupture_life_s=1000000.0,
                life_basis="qualified_reference",
                evidence_id="reference",
            ),
            CreepBlock(
                id="second",
                start_s=100000.0,
                finish_s=200000.0,
                stress_pa=2e8,
                temperature_k=973.15,
                rupture_life_s=2000000.0,
                life_basis="qualified_reference",
                evidence_id="reference",
            ),
        ),
        stress_basis="initial_nominal_constant_load",
        temperature_basis="nominal_specified",
        blocks_life_and_linear_accumulation_reviewed=True,
    )
    return cases, sequence, context


def prediction_request(
    fit, context, *, current_age_s=100000.0, horizon_s=200000.0, stress_pa=2e8, temperature_k=923.15
):
    model = fit["values"]["model"]
    return CreepRequest(
        method="creep.conditional_life",
        asset_id=context.asset_id,
        location_id=fit["request"]["location_id"],
        clock_id=context.clock_id,
        material_condition_id=model["material_condition_id"],
        parameters=dict(
            model=model,
            material_condition_id=model["material_condition_id"],
            stress_basis=model["stress_basis"],
            temperature_basis=model["temperature_basis"],
            initial_stress_Pa=stress_pa,
            temperature_K=temperature_k,
            current_age_s=current_age_s,
            horizon_s=horizon_s,
            maximum_time_extrapolation_factor=1.0,
            constant_conditions_since_fresh_material_confirmed=True,
            applicability_reviewed=True,
        ),
        applicability_reviews=dict.fromkeys(REVIEWS["creep.conditional_life"], True),
        model_reference_digest=digest(model),
        evidence_ids=tuple(e.id for e in context.evidence),
    )
