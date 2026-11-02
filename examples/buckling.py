"""Authored pinned elastic pipe-column, not a design allowance or collapse model."""

from pipeline_integrity.buckling import BucklingRequest, buckling_boundary_metadata
from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.structural import (
    FrameCombination,
    FrameMaterial,
    FrameMember,
    FrameNode,
    FrameNodeLoad,
    FrameRequest,
    FrameSupport,
    annular_frame_section,
)


def make_case(*, reviewed=False, elements=8, reference_force_N=-1000.0):
    if not isinstance(elements, int) or not 1 <= elements <= 30:
        raise ValueError("authored example supports 1 through 30 explicit elements")
    length = 4.0
    nodes = tuple(
        FrameNode(id=f"n{i}", position_m=(length * i / elements, 0, 0)) for i in range(elements + 1)
    )
    members = tuple(
        FrameMember(
            id=f"m{i}", i_node=f"n{i}", j_node=f"n{i + 1}", section_id="annulus", material_id="elastic"
        )
        for i in range(elements)
    )
    f = FrameRequest(
        asset_id="authored_column",
        clock_id="reference_load_snapshot",
        time_s=0,
        nodes=nodes,
        members=members,
        sections=(annular_frame_section("annulus", 0.1, 0.09, "authored"),),
        materials=(
            FrameMaterial(id="elastic", young_modulus_pa=200e9, poisson_ratio=0.3, evidence_id="authored"),
        ),
        supports=(
            FrameSupport(node_id="n0", fixed_dofs=("DX", "DY", "DZ", "RX"), evidence_id="authored"),
            FrameSupport(node_id=f"n{elements}", fixed_dofs=("DY", "DZ"), evidence_id="authored"),
        ),
        load_cases=("reference",),
        node_loads=(
            FrameNodeLoad(
                node_id=f"n{elements}",
                direction="FX",
                value=reference_force_N,
                case_id="reference",
                evidence_id="authored",
            ),
        ),
        combinations=(FrameCombination(id="pattern", factors={"reference": 1}, evidence_id="authored"),),
        geometry_evidence_id="authored",
        geometry_and_connectivity_reviewed=reviewed,
        stiffness_and_loads_reviewed=reviewed,
        supports_reviewed=reviewed,
        linear_small_displacement_applicability_reviewed=reviewed,
    )
    q = BucklingRequest(
        id="authored_eigen_column",
        frame_id="local_xyz",
        coordinate_definition_id="right_handed_xyz_m",
        source_port_id="base",
        target_port_id="loaded_end",
        reference_load_definition_id="authored_proportional_axial_pattern",
        frame=f,
        combination_id="pattern",
        translation_gauge_m=1,
        rotation_gauge_rad=1,
        conservative_proportional_reference_load_reviewed=reviewed,
        perfect_linear_elastic_frame_model_reviewed=reviewed,
        stiffness_provider_and_dof_basis_reviewed=reviewed,
        evidence_id="authored",
    )
    c = RunContext(
        asset_id=f.asset_id,
        scenario_id="authored_perfect_column",
        clock_id=f.clock_id,
        origin="synthetic",
        evidence=(
            EvidenceRef(
                id="authored",
                citation="Original fictional elastic column and Euler reference",
                origin="synthetic",
            ),
        ),
        metadata={"buckling_boundary": buckling_boundary_metadata(q)},
    )
    return q, c
