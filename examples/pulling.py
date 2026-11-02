"""Authored flexible guide examples; supplier values here are fictional."""

import math

from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.pulling import PullingLimits, PullingRequest, PullingSegment, boundary_metadata


def make_case(*, reviewed=False, kind="horizontal"):
    if kind == "horizontal":
        segments = (
            PullingSegment(
                id="straight",
                start_m=(0.0, 0.0, 0.0),
                initial_tangent=(1.0, 0.0, 0.0),
                length_m=100.0,
                sliding_friction=0.2,
                immersed_fraction=0.0,
                external_fluid_density_kg_m3=0.0,
                axial_resistance_N_m=0.0,
                evidence_id="authored",
            ),
        )
        mass = 10.0
    elif kind == "capstan":
        segments = (
            PullingSegment(
                id="arc",
                start_m=(0.0, 0.0, 0.0),
                initial_tangent=(1.0, 0.0, 0.0),
                length_m=10 * math.pi / 2,
                bend_radius_m=10.0,
                initial_curve_normal=(0.0, 1.0, 0.0),
                sliding_friction=0.2,
                immersed_fraction=0.0,
                external_fluid_density_kg_m3=0.0,
                axial_resistance_N_m=0.0,
                evidence_id="authored",
            ),
        )
        mass = 0.0
    else:
        raise ValueError("choose the authored horizontal or massless capstan reference")
    request = PullingRequest(
        id=f"authored_{kind}",
        asset_id="authored_pull",
        clock_id="steady_case",
        time_s=0.0,
        frame_id="local_xyz",
        coordinate_definition_id="authored_right_handed_xyz_m",
        trailing_port_id="trailing",
        puller_port_id="puller",
        outer_diameter_m=0.1,
        pipe_mass_kg_m=mass,
        contents_mass_kg_m=0.0,
        displaced_area_m2=math.pi * 0.1**2 / 4,
        gravity_m_s2=(0.0, 0.0, -9.80665),
        trailing_tension_N=1000.0,
        pull_speed_m_s=0.2,
        segments=segments,
        operating_case_id="authored_steady_forward_sliding",
        geometry_evidence_id="authored",
        loading_evidence_id="authored",
        geometry_reviewed=reviewed,
        flexible_line_model_reviewed=reviewed,
        steady_sliding_and_guide_contact_reviewed=reviewed,
        load_and_buoyancy_basis_reviewed=reviewed,
        maximum_step_m=1.0,
        limits=PullingLimits(
            effective_axial_tension_limit_N=10000.0,
            minimum_bend_radius_m=5.0,
            maximum_geometric_outer_fiber_strain=0.02,
            applicability_id="fictional_case_limits",
            evidence_id="authored",
            reviewed=reviewed,
        ),
    )
    context = RunContext(
        asset_id=request.asset_id,
        scenario_id=f"authored_{kind}",
        clock_id=request.clock_id,
        origin="synthetic",
        evidence=(
            EvidenceRef(
                id="authored",
                citation="Original flexible-line reference and fictional capacity limits; not supplier or field evidence",
                origin="synthetic",
            ),
        ),
        metadata={"pulling_boundary": boundary_metadata(request)},
    )
    return request, context
