"""Authored two-cell nitrogen case; default execution remains unreviewed."""

from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.gas_thermal_transient import (
    REVIEWS,
    GasThermalCell,
    GasThermalTransientRequest,
    GasThermalWindow,
    boundary_metadata,
    evaluate_gas_thermal_transient,
    native_backend_metadata,
)
from pipeline_integrity.thermo import StateRequest


def make_case(*, reviewed=False, **changes):
    evidence = "authored_gas_thermal"

    def state(pressure=1000000.0, temperature=300.0):
        return StateRequest(fluid="Nitrogen", inputs={"P": pressure, "T": temperature})

    cells = tuple(
        GasThermalCell(
            id=f"cell_{i}",
            inlet_port_id=f"cell_{i}_in",
            outlet_port_id=f"cell_{i}_out",
            length_m=10.0,
            inner_diameter_m=0.05,
            roughness_m=1e-5,
            local_loss_coefficient=0.0,
            wall_heat_capacity_J_K=10000.0,
            fluid_wall_UA_W_K=1.0,
            ambient_UA_W_K=1.0,
            ambient_temperature_K=310.0,
            initial_wall_temperature_K=300.0,
            initial_fluid_state=state(),
            evidence_id=evidence,
        )
        for i in range(2)
    )
    fields = dict(
        id="gas_thermal",
        asset_id="gas_line",
        event_id="authored_heat",
        clock_id="plant_s",
        start_time_s=0.0,
        fluid="Nitrogen",
        property_basis="HEOS pure Nitrogen common runtime H/S marker",
        left_boundary_id="supply",
        right_boundary_id="delivery",
        cells=cells,
        windows=(
            GasThermalWindow(
                id="heat",
                duration_s=0.002,
                timestep_s=0.001,
                maximum_reviewed_timestep_s=0.001,
                left_state=state(),
                right_state=state(),
                evidence_id=evidence,
            ),
        ),
        pressure_support_pa_abs=(100000.0, 2000000.0),
        temperature_support_K=(250.0, 350.0),
        maximum_omitted_kinetic_energy_transport_W=0.01,
        maximum_link_momentum_residual_Pa=1.0,
        evidence_ids=(evidence,),
        reviews=dict.fromkeys(REVIEWS, reviewed),
        review_evidence_ids=dict.fromkeys(REVIEWS, evidence) if reviewed else {},
    )
    fields.update(changes)
    q = GasThermalTransientRequest(**fields)
    if reviewed and not q.owner_pins:
        q = q.model_copy(update={"owner_pins": native_backend_metadata(q)})
    c = RunContext(
        asset_id=q.asset_id,
        scenario_id=q.event_id,
        clock_id=q.clock_id,
        origin="synthetic",
        evidence=(
            EvidenceRef(
                id=evidence,
                citation="Authored two-cell gas/wall boundary",
                origin="synthetic",
                limitations=("Not measured validation or an acoustic wave benchmark",),
            ),
        ),
        metadata={"gas_thermal_transient_boundary": boundary_metadata(q)},
    )
    return q, c


if __name__ == "__main__":
    import json

    print(json.dumps(evaluate_gas_thermal_transient(*make_case()), indent=2))
