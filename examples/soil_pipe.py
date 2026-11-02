"""Authored cantilever and discrete bilateral ground spring, not field soil parameters."""

from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.soil_pipe import (
    SoilNodeSupport,
    SoilPipeBodyState,
    SoilPipeRequest,
    soil_pipe_boundary_metadata,
)
from pipeline_integrity.structural import (
    FrameCombination,
    FrameMaterial,
    FrameMember,
    FrameNode,
    FrameRequest,
    FrameSupport,
    annular_frame_section,
)


def make_case(*, reviewed=False, ground_m=-0.001):
    frame = FrameRequest(
        asset_id="authored_pipe",
        clock_id="ground_snapshot",
        time_s=0,
        nodes=(FrameNode(id="anchor", position_m=(0, 0, 0)), FrameNode(id="tip", position_m=(2, 0, 0))),
        members=(
            FrameMember(
                id="pipe", i_node="anchor", j_node="tip", section_id="annulus", material_id="elastic"
            ),
        ),
        sections=(annular_frame_section("annulus", 0.1, 0.09, "authored"),),
        materials=(
            FrameMaterial(id="elastic", young_modulus_pa=200e9, poisson_ratio=0.3, evidence_id="authored"),
        ),
        supports=(
            FrameSupport(
                node_id="anchor", fixed_dofs=("DX", "DY", "DZ", "RX", "RY", "RZ"), evidence_id="authored"
            ),
        ),
        load_cases=("physical",),
        combinations=(FrameCombination(id="snapshot", factors={"physical": 1}, evidence_id="authored"),),
        geometry_evidence_id="authored",
        geometry_and_connectivity_reviewed=reviewed,
        stiffness_and_loads_reviewed=reviewed,
        supports_reviewed=reviewed,
        linear_small_displacement_applicability_reviewed=reviewed,
    )
    q = SoilPipeRequest(
        id="authored_soil_pipe",
        frame_id="local_xyz",
        coordinate_definition_id="right_handed_xyz_m",
        source_port_id="anchor_port",
        target_port_id="tip_port",
        ground_event_id="authored_settlement",
        frame=frame,
        soil_supports=(
            SoilNodeSupport(
                node_id="tip",
                stiffness_N_m={"DY": 100000},
                ground_displacement_m=(0, ground_m, 0),
                behavior="bilateral" if reviewed else "unreviewed",
                stiffness_definition_id="discrete_tip_N_per_m",
                evidence_id="authored",
                reviewed=reviewed,
            ),
        ),
        body_states=(
            SoilPipeBodyState(
                member_id="pipe",
                outer_diameter_m=0.1,
                inner_diameter_m=0.09,
                pipe_and_attached_mass_kg_m=0,
                contents_mass_kg_m=0,
                displaced_area_m2=0,
                immersed_fraction=0,
                external_density_kg_m3=0,
                evidence_id="authored",
                reviewed=reviewed,
            ),
        ),
        gravity_m_s2=(0, -9.80665, 0),
        bilateral_discrete_soil_model_reviewed=reviewed,
        geometry_material_and_buoyancy_basis_reviewed=reviewed,
        evidence_id="authored",
    )
    c = RunContext(
        asset_id=frame.asset_id,
        scenario_id="authored_bilateral_snapshot",
        clock_id=frame.clock_id,
        origin="synthetic",
        evidence=(
            EvidenceRef(
                id="authored",
                citation="Original fictional linear pipe/ground spring case",
                origin="synthetic",
            ),
        ),
        metadata={"soil_pipe_boundary": soil_pipe_boundary_metadata(q)},
    )
    return q, c
