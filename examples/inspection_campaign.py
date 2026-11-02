"""Original finite-menu inspection reference; probabilities and appointments are hypothetical."""

from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.inspection_planning import (
    InspectionCampaign,
    InspectionHypothesis,
    InspectionOption,
    InspectionTarget,
    LogisticInspectionPOD,
    plan_inspections,
)
from pipeline_integrity.scheduling import PlanningResource


def make_case():
    context = RunContext(
        asset_id="authored_line",
        scenario_id="finite_inspection_menu",
        clock_id="planning_elapsed",
        origin="synthetic",
        evidence=(
            EvidenceRef(
                id="reference",
                citation="Original hypothetical campaign inputs",
                origin="synthetic",
                license_id="MIT",
            ),
        ),
    )
    curve = LogisticInspectionPOD(
        midpoint_size_m=0.001,
        log_size_scale=0.5,
        supported_minimum_size_m=0.0001,
        supported_maximum_size_m=0.01,
        evidence_id="reference",
    )
    targets = []
    for key, weight, starts in [("A", 3.0, [0.0]), ("B", 2.0, [0.0, 2.0]), ("C", 1.0, [4.0])]:
        targets.append(
            InspectionTarget(
                id=key,
                location_id=key,
                hypotheses=(
                    InspectionHypothesis(
                        id="absent", prior_mass=0.5, unrevealed_weight=0.0, evidence_id="reference"
                    ),
                    InspectionHypothesis(
                        id="flaw", prior_mass=0.5, unrevealed_weight=weight, evidence_id="reference"
                    ),
                ),
                options=tuple(
                    InspectionOption(
                        id=f"method_at_{start:g}",
                        method_id="hypothetical_log_size_curve",
                        start_s=start,
                        duration_s=2.0,
                        coverage_fraction=1.0,
                        sizes_m={"absent": 0.0, "flaw": 0.002},
                        detection=curve,
                        resources={"crew": 1},
                        budget_use={"campaign_units": 1},
                        evidence_id="reference",
                    )
                    for start in starts
                ),
            )
        )
    campaign = InspectionCampaign(
        horizon_s=8.0,
        targets=tuple(targets),
        resources=(PlanningResource(id="crew", capacity=1, evidence_id="reference"),),
        budgets={"campaign_units": 2},
        score_quantum=0.001,
        criterion_unit="authored_unrevealed_weight",
        evidence_id="reference",
    )
    return campaign, context


if __name__ == "__main__":
    print(plan_inspections(*make_case()).model_dump_json(indent=2))
