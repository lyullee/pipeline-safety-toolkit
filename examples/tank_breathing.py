"""Authored synthetic tank boundary. Default execution is deliberately unreviewed."""

from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.tank_breathing import (
    REVIEWS,
    TankBreathingClosures,
    TankBreathingPorts,
    TankBreathingRequest,
    TankBreathingVent,
    TankBreathingWindow,
    boundary_metadata,
    evaluate_tank_breathing,
    native_backend_metadata,
)
from pipeline_integrity.tanks import AxisymmetricTank


def make_case(*, reviewed=False, mode="closed"):
    evidence = "authored_boundary"
    q = TankBreathingRequest(
        id="tank_history",
        asset_id="tank_01",
        event_id="synthetic_hold",
        clock_id="plant_s",
        start_time_s=0.0,
        fluid="ParaHydrogen",
        shape=AxisymmetricTank(
            inner_diameter_m=1.0, straight_length_m=2.0, horizontal=True, evidence_id=evidence
        ),
        shape_evidence_id=evidence,
        closures=TankBreathingClosures(
            lower_wall_heat_capacity_J_K=10000.0,
            upper_wall_heat_capacity_J_K=10000.0,
            lower_ambient_UA_W_K=0.1,
            upper_ambient_UA_W_K=0.1,
            wall_liquid_U_W_m2K=1.0,
            wall_vapor_U_W_m2K=1.0,
            liquid_interface_UA_W_K=1.0,
            vapor_interface_UA_W_K=1.0,
            wall_axial_UA_W_K=0.1,
            wall_zone_split_height_m=0.5,
            minimum_pressure_Pa=10000.0,
            maximum_pressure_Pa=1000000.0,
            evidence_id=evidence,
        ),
        initial_pressure_pa_abs=150000.0,
        initial_liquid_volume_fraction=0.5,
        initial_lower_wall_temperature_K=22.0,
        initial_upper_wall_temperature_K=22.0,
        ports=TankBreathingPorts(
            liquid_inlet_id="li", liquid_outlet_id="lo", vapor_inlet_id="vi", vapor_outlet_id="vo"
        ),
        vent=TankBreathingVent(
            mode=mode,
            tank_port_id="vent",
            downstream_boundary_id="sink",
            downstream_state=None,
            full_open_area_m2=None,
            discharge_coefficient=None,
            closed_leakage_CdA_m2=None,
            evidence_id=evidence,
        ),
        windows=(
            TankBreathingWindow(
                id="hold",
                duration_s=0.01,
                timestep_s=0.005,
                maximum_reviewed_step_s=0.005,
                ambient_temperature_K=300.0,
                direct_lower_wall_heat_W=0.0,
                direct_upper_wall_heat_W=0.0,
                direct_liquid_heat_W=0.0,
                direct_vapor_heat_W=0.0,
                liquid_inflow_kg_s=0.0,
                liquid_inlet_state=None,
                liquid_process_outflow_kg_s=0.0,
                vapor_inflow_kg_s=0.0,
                vapor_inlet_state=None,
                vapor_process_outflow_kg_s=0.0,
                vent_opening=0.0,
                evidence_id=evidence,
            ),
        ),
        evidence_ids=(evidence,),
        reviews=dict.fromkeys(REVIEWS, reviewed),
        review_evidence_ids=dict.fromkeys(REVIEWS, evidence) if reviewed else {},
        owner_pins=native_backend_metadata() if reviewed and mode == "closed" else {},
    )
    c = RunContext(
        asset_id=q.asset_id,
        scenario_id=q.event_id,
        clock_id=q.clock_id,
        origin="synthetic",
        evidence=(
            EvidenceRef(
                id=evidence,
                citation="Authored synthetic example",
                origin="synthetic",
                limitations=("Not a measured validation dataset",),
            ),
        ),
        metadata={"tank_breathing_boundary": boundary_metadata(q)},
    )
    return q, c


if __name__ == "__main__":
    import json

    print(json.dumps(evaluate_tank_breathing(*make_case()), indent=2))
