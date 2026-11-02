"""Original synthetic positive interval quantities and a predeclared ordered holdout."""

import json

import numpy as np

from pipeline_integrity.contracts import EvidenceRef, RunContext, digest
from pipeline_integrity.inverse import ResidualScale
from pipeline_integrity.uncertainty import UncertainVariable
from pipeline_integrity.uncertainty_fit import (
    DistributionAssessment,
    DistributionFitRequest,
    DistributionUse,
    JointObservation,
    JointObservations,
    assess_joint_distribution,
    distribution_design,
    fit_joint_distribution,
)


def make_case():
    rng = np.random.default_rng(2026)
    z = rng.multivariate_normal([0.0, 0.0], [[1.0, 0.65], [0.65, 1.0]], size=128)
    energy = np.exp(np.log(120000.0) + 0.05 * z[:, 0])
    volume = np.exp(np.log(0.005) + 0.05 * z[:, 1])
    context = RunContext(
        asset_id="authored_rig",
        scenario_id="synthetic_joint_quantities",
        clock_id="source_order_not_physical_time",
        origin="synthetic",
        evidence=(
            EvidenceRef(
                id="reference",
                citation="Original synthetic quantities, split and unit scales",
                origin="synthetic",
                license_id="MIT",
            ),
        ),
    )
    variables = tuple(
        UncertainVariable(id=id, unit=unit, layer="measurement", evidence_id="reference")
        for id, unit in (("energy_j", "J"), ("volume_m3", "m3"))
    )
    rows = tuple(
        JointObservation(
            id=f"row_{i}",
            order=i,
            group_id="authored_series",
            condition_id="authored_condition",
            values=dict(energy_j=float(energy[i]), volume_m3=float(volume[i])),
            valid=dict(energy_j=True, volume_m3=True),
            evidence_id="reference",
        )
        for i in range(128)
    )
    observations = JointObservations(
        id="synthetic_observations",
        variables=variables,
        rows=rows,
        order_basis="user_declared_order",
        source_digests={
            "generation_definition": digest(
                dict(
                    generator="numpy PCG64",
                    seed=2026,
                    latent_correlation=0.65,
                    law="bivariate_log_normal",
                    generated_values=z.tolist(),
                )
            )
        },
        context=context,
    )
    request = DistributionFitRequest(
        id="fixed_synthetic_protocol",
        condition_id="authored_condition",
        training_ids=tuple(r.id for r in rows[:80]),
        validation_ids=tuple(r.id for r in rows[96:]),
        families=dict(energy_j="lognormal", volume_m3="lognormal"),
        scales=dict(
            energy_j=ResidualScale(value=100000.0, unit="J", source_id="reference"),
            volume_m3=ResidualScale(value=0.005, unit="m3", source_id="reference"),
        ),
        split_policy="ordered_gap",
        minimum_order_gap=17,
        definition_reviewed=True,
        evidence_id="reference",
    )
    use = DistributionUse(
        mode="normal_score_copula",
        samples=512,
        seed=881,
        interpretation="scenario",
        target_condition_id="authored_condition",
        applicability_reviewed=True,
        evidence_id="reference",
    )
    assessment = DistributionAssessment(evidence_id="reference")
    return observations, request, use, assessment


if __name__ == "__main__":
    observations, request, use, assessment = make_case()
    fit = fit_joint_distribution(observations, request)
    prediction = distribution_design(fit, use, observations.context)
    result = assess_joint_distribution(fit, observations, prediction, assessment)
    print(
        json.dumps(
            {
                k: result[k]
                for k in ("status", "scores", "marginal_intervals", "declared_gate", "field_qualified")
            },
            indent=2,
        )
    )
