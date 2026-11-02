"""Original fictional REF-currency keep/replace inventories; no market quotes or prescribed factors."""

import json

from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.lifecycle import (
    CostEvent,
    EmissionEvent,
    LifecycleAlternative,
    LifecycleStudy,
    Money,
    MoneyBasis,
    RecurringCost,
    evaluate_lifecycle,
)

YEAR = 31557600.0


def make_case():
    context = RunContext(
        asset_id="example_pipe",
        scenario_id="authored_lifecycle",
        clock_id="valuation_elapsed_s",
        origin="synthetic",
        metadata={
            "base_inventory_scope": "Authored capital, routine maintenance, replacement and residual values.",
            "source_linked_demo_scope": "Additional energy, one-off planned work and unplanned rework are excluded from base quotes and applied equally to both alternatives.",
        },
        evidence=(
            EvidenceRef(
                id="reference",
                citation="Original fictional service/cost/emission comparison",
                origin="synthetic",
                license_id="MIT",
            ),
        ),
    )
    basis = MoneyBasis(currency="REF", base_year=2026, prices="constant")

    def money(value):
        return Money(amount=value, basis=basis)

    def event(id, year, category, value):
        return CostEvent(
            id=id, time_s=year * YEAR, category=category, money=money(value), evidence_id="reference"
        )

    def alternative(id, capital, annual, replacement, salvage, embodied, operating_emissions):
        costs = (event("capital", 0, "capital", capital), event("residual", 5, "salvage", -salvage))
        if replacement:
            costs += (event("replacement", 3, "replacement", replacement),)
        return LifecycleAlternative(
            id=id,
            service_definition_id="same_declared_five_year_service",
            cost_boundary_id="declared_lifecycle_cost_inventory",
            emissions_boundary_id="declared_embodied_and_operations",
            cost_events=costs,
            recurring_costs=(
                RecurringCost(
                    id="maintenance",
                    first_time_s=YEAR,
                    interval_s=YEAR,
                    occurrences=5,
                    category="maintenance",
                    base_quote=money(annual),
                    escalation_per_year=0.0,
                    escalation_basis="real",
                    evidence_id="reference",
                ),
            ),
            emission_events=tuple(
                EmissionEvent(
                    id=f"emission_{year}",
                    time_s=year * YEAR,
                    mass_kg_co2e=embodied if year == 0 else operating_emissions,
                    metric_id="declared_CO2e_metric",
                    evidence_id="reference",
                )
                for year in range(6)
            ),
            evidence_id="reference",
            cost_inventory_reviewed=True,
            emissions_inventory_reviewed=True,
        )

    study = LifecycleStudy(
        asset_id=context.asset_id,
        clock_id=context.clock_id,
        horizon_s=5 * YEAR,
        year_duration_s=YEAR,
        money_basis=basis,
        annual_discount_rate=0.05,
        discount_rate_basis="real",
        service_definition_id="same_declared_five_year_service",
        cost_boundary_id="declared_lifecycle_cost_inventory",
        emissions_boundary_id="declared_embodied_and_operations",
        emission_metric_id="declared_CO2e_metric",
        alternatives=(
            alternative("keep", 1000.0, 100.0, 600.0, 100.0, 500.0, 100.0),
            alternative("replace", 1800.0, 30.0, 0.0, 200.0, 1200.0, 20.0),
        ),
        baseline_alternative_id="keep",
        finance_evidence_id="reference",
        functional_equivalence_reviewed=True,
    )
    return study, context


if __name__ == "__main__":
    study, context = make_case()
    print(json.dumps(evaluate_lifecycle(study, context), indent=2))
