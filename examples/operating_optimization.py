"""Authored fixed-batch investment plans; not equipment predictions or market quotes."""

from pipeline_integrity.contracts import EvidenceRef, RunContext, digest
from pipeline_integrity.lifecycle import CostEvent, LifecycleAlternative, LifecycleStudy, Money, MoneyBasis
from pipeline_integrity.operating_optimization import (
    OperatingOptimizationRequest,
    OperatingPlan,
    SuppliedOperatingBatch,
    TransportWindow,
    operating_batch_binding,
    operating_boundary_metadata,
)
from pipeline_integrity.scheduling import PlanningResource


def make_case(*, reviewed=False):
    basis = MoneyBasis(currency="REF", base_year=2026, prices="constant")
    alternatives = []
    plans = []
    for name, cost, energy in (("existing", 200, 800), ("investment", 150, 500)):
        alternatives.append(
            LifecycleAlternative(
                id=name,
                service_definition_id="same_delivered_service",
                cost_boundary_id="complete_fixed_plan_cashflows",
                emissions_boundary_id="not_an_objective",
                cost_events=(
                    CostEvent(
                        id="all_supplied_cashflows",
                        time_s=0,
                        category="capital",
                        money=Money(amount=cost, basis=basis),
                        evidence_id="authored",
                    ),
                ),
                recurring_costs=(),
                emission_events=(),
                evidence_id="authored",
                cost_inventory_reviewed=reviewed,
                emissions_inventory_reviewed=reviewed,
            )
        )
        plans.append(
            OperatingPlan(
                id=name,
                lifecycle_alternative_id=name,
                batches=(
                    SuppliedOperatingBatch(
                        id="batch",
                        window_id="delivery",
                        duration_s=10,
                        delivered_mass_kg=100,
                        electrical_energy_J=energy,
                        resource_demands={"station": 1},
                        performance_source_id="authored_performance_snapshot",
                        performance_payload={},
                        performance_digest=digest({}),
                        evidence_id="authored",
                        reviewed=reviewed,
                    ),
                ),
                evidence_id="authored",
                fixed_batch_cost_and_service_mapping_reviewed=reviewed,
            )
        )
    study = LifecycleStudy(
        asset_id="authored_station",
        clock_id="valuation_elapsed_s",
        horizon_s=20,
        year_duration_s=31557600,
        money_basis=basis,
        annual_discount_rate=0,
        discount_rate_basis="real",
        service_definition_id="same_delivered_service",
        cost_boundary_id="complete_fixed_plan_cashflows",
        emissions_boundary_id="not_an_objective",
        emission_metric_id="not_an_objective",
        alternatives=tuple(alternatives),
        baseline_alternative_id="existing",
        finance_evidence_id="authored",
        functional_equivalence_reviewed=reviewed,
    )
    q = OperatingOptimizationRequest(
        id="authored_operation",
        asset_id=study.asset_id,
        clock_id=study.clock_id,
        frame_id="authored_service_frame",
        source_port_id="supply",
        delivery_port_id="delivery",
        transport_service_definition_id=study.service_definition_id,
        deterministic_case_id="authored_forecast",
        windows=(
            TransportWindow(
                id="delivery",
                start_s=0,
                finish_s=20,
                required_delivered_mass_kg=100,
                forecast_id="authored_demand",
                forecast_sha256=digest({"required_kg": 100}),
                evidence_id="authored",
                reviewed=reviewed,
            ),
        ),
        plans=tuple(plans),
        financial_study=study,
        resources=(PlanningResource(id="station", capacity=1, evidence_id="authored"),),
        electrical_energy_budget_J=1000,
        energy_quantum_J=1,
        time_quantum_s=1,
        case_forecast_cost_timing_and_equivalence_reviewed=reviewed,
        evidence_id="authored",
    )
    rebound = []
    for plan in q.plans:
        batches = []
        for b in plan.batches:
            payload = {
                "source": "original fictional batch",
                "operating_batch_binding": operating_batch_binding(q, b),
            }
            batches.append(
                b.model_copy(update={"performance_payload": payload, "performance_digest": digest(payload)})
            )
        rebound.append(plan.model_copy(update={"batches": tuple(batches)}))
    q = q.model_copy(update={"plans": tuple(rebound)})
    c = RunContext(
        asset_id=q.asset_id,
        scenario_id="authored_finite_plans",
        clock_id=q.clock_id,
        origin="synthetic",
        evidence=(
            EvidenceRef(
                id="authored",
                citation="Original fictional costs, forecast and batch snapshots",
                origin="synthetic",
            ),
        ),
        metadata={"operating_boundary": operating_boundary_metadata(q)},
    )
    return q, c
