"""Authored water example, not a crude-oil surrogate or measured validation."""

from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.heated_transport import (
    REVIEWS,
    HeatedTransportCell,
    HeatedTransportRequest,
    boundary_metadata,
    evaluate_heated_transport,
    native_backend_metadata,
)
from pipeline_integrity.thermo import StateRequest


def make_case(*, reviewed=False, **changes):
    evidence = "authored_liquid_boundary"
    fields = dict(
        id="heated_transport",
        asset_id="line_01",
        event_id="heated_water",
        clock_id="plant_s",
        time_s=0.0,
        inlet_port_id="in",
        outlet_port_id="out",
        inlet=StateRequest(fluid="Water", inputs={"P": 1000000.0, "T": 300.0}),
        mass_flow_kg_s=1.0,
        property_provider_kind="coolprop",
        property_evidence_id=evidence,
        pressure_support_pa_abs=(100000.0, 2000000.0),
        temperature_support_K=(280.0, 350.0),
        gravity_m_s2=9.80665,
        maximum_cell_temperature_closure_error_K=0.1,
        maximum_omitted_kinetic_energy_rate_W=0.01,
        cells=tuple(
            HeatedTransportCell(
                id=f"cell_{i}",
                length_m=10.0,
                inner_diameter_m=0.05,
                roughness_m=1e-5,
                minor_loss_coefficient=0.0,
                outlet_minus_inlet_elevation_m=0.0,
                conductance_W_K=5.0,
                ambient_temperature_K=320.0,
                heater_into_fluid_W=100.0,
                evidence_id=evidence,
            )
            for i in range(3)
        ),
        evidence_ids=(evidence,),
        reviews=dict.fromkeys(REVIEWS, reviewed),
        review_evidence_ids=dict.fromkeys(REVIEWS, evidence) if reviewed else {},
    )
    fields.update(changes)
    q = HeatedTransportRequest(**fields)
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
                citation="Authored water heating boundary",
                origin="synthetic",
                limitations=("No independent validation; not an oil property model",),
            ),
        ),
        metadata={"heated_transport_boundary": boundary_metadata(q)},
    )
    return q, c


if __name__ == "__main__":
    import json

    print(json.dumps(evaluate_heated_transport(*make_case()), indent=2))
