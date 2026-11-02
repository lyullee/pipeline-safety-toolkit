"""Authored synthetic boundary; default invocation does not run the solver."""

from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.pig_motion import (
    REVIEWS,
    PigBoundaryWindow,
    PigMotionRequest,
    boundary_metadata,
    evaluate_pig_motion,
    runtime_metadata,
)


def make_case(*, reviewed=False, windows=None, **changes):
    evidence = "authored_pig_boundary"
    fields = dict(
        id="pig_motion",
        asset_id="line_01",
        event_id="authored_drive",
        clock_id="plant_s",
        start_time_s=0.0,
        upstream_port_id="behind_pig",
        downstream_port_id="ahead_pig",
        mass_kg=10.0,
        effective_pressure_area_m2=0.01,
        length_m=100.0,
        initial_position_m=1.0,
        initial_velocity_m_s=0.0,
        inclination_rad=0.0,
        gravity_m_s2=9.80665,
        static_friction_N=5.0,
        kinetic_friction_N=2.0,
        quadratic_drag_N_s2_m2=0.0,
        geometry_friction_evidence_id=evidence,
        windows=windows
        or (
            PigBoundaryWindow(
                id="drive",
                duration_s=1.0,
                upstream_pressure_pa_abs=110000.0,
                downstream_pressure_pa_abs=100000.0,
                external_axial_force_N=0.0,
                provider_id="authored_constant_pressure",
                provider_version="1",
                evidence_id=evidence,
            ),
        ),
        evidence_ids=(evidence,),
        reviews=dict.fromkeys(REVIEWS, reviewed),
        review_evidence_ids=dict.fromkeys(REVIEWS, evidence) if reviewed else {},
        runtime_pins=runtime_metadata() if reviewed else {},
        maximum_step_s=0.01,
    )
    fields.update(changes)
    q = PigMotionRequest(**fields)
    c = RunContext(
        asset_id=q.asset_id,
        scenario_id=q.event_id,
        clock_id=q.clock_id,
        origin="synthetic",
        evidence=(
            EvidenceRef(
                id=evidence,
                citation="Authored synthetic mechanics boundary",
                origin="synthetic",
                limitations=("No measured validation or hydraulic feedback",),
            ),
        ),
        metadata={"pig_motion_boundary": boundary_metadata(q)},
    )
    return q, c


if __name__ == "__main__":
    import json

    print(json.dumps(evaluate_pig_motion(*make_case()), indent=2))
