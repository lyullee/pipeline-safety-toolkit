"""Authored two-leg static pose; the default is deliberately unreviewed."""

from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.lifting import (
    EffectiveLiftingCapacity,
    LiftingFrame,
    LiftingRequest,
    Sling,
    boundary_metadata,
)


def make_case(*, reviewed=False):
    evidence = EvidenceRef(
        id="authored",
        citation="Original symmetric two-leg rigid-payload equilibrium example; fictional force limits",
        origin="synthetic",
        limitations=(
            "No actual lifting plan, supplier approval, structural stability or field qualification",
        ),
    )
    slings = tuple(
        Sling(
            id=name,
            attachment_id=f"lug_{name}",
            attachment_m=(x, 0.0, 0.0),
            anchor_id="shared_hook",
            anchor_m=(0.0, 0.0, 1.0),
            geometry_evidence_id=evidence.id,
        )
        for name, x in (("left", -1.0), ("right", 1.0))
    )
    targets = (
        [("sling", s.id) for s in slings]
        + [("attachment", s.attachment_id) for s in slings]
        + [("anchor", "shared_hook")]
    )
    capacities = tuple(
        EffectiveLiftingCapacity(
            target_kind=kind,
            target_id=name,
            force_limit_n=20000.0,
            direction_policy="all_directions",
            applicability_id="authored_effective_limit_for_this_static_pose",
            evidence_id=evidence.id,
            reviewed=reviewed,
        )
        for kind, name in targets
    )
    request = LiftingRequest(
        id="symmetric_two_leg",
        asset_id="authored_payload",
        clock_id="authored_lifting_clock",
        time_s=0.0,
        frame=LiftingFrame(id="local_pose", definition_id="authored_local_xyz", evidence_id=evidence.id),
        center_of_mass_m=(0.0, 0.0, 0.0),
        mass_kg=1000.0,
        gravity_m_s2=(0.0, 0.0, -9.80665),
        slings=slings,
        capacities=capacities,
        load_basis_evidence_id=evidence.id,
        geometry_reviewed=reviewed,
        complete_free_body_reviewed=reviewed,
        moment_length_scale_m=1.0,
    )
    context = RunContext(
        asset_id=request.asset_id,
        scenario_id="authored_static_pose",
        clock_id=request.clock_id,
        origin="synthetic",
        evidence=(evidence,),
        metadata={"lifting_boundary": boundary_metadata(request)},
    )
    return request, context
