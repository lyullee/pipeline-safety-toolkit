"""Original elastic reference with declared pipe geometry; no standard or vendor coefficients."""

import json

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
    solve_frame,
)


def context():
    return RunContext(
        asset_id="authored-frame",
        scenario_id="linear-reference",
        clock_id="declared-snapshot",
        origin="synthetic",
        evidence=(
            EvidenceRef(
                id="reference",
                citation="Original declared elastic frame reference",
                origin="synthetic",
                license_id="MIT",
            ),
        ),
    )


def make_case():
    return FrameRequest(
        asset_id="authored-frame",
        clock_id="declared-snapshot",
        time_s=0.0,
        nodes=(
            FrameNode(id="anchor", position_m=(0.0, 0.0, 0.0)),
            FrameNode(id="tip", position_m=(4.0, 0.0, 0.0)),
        ),
        members=(
            FrameMember(
                id="pipe", i_node="anchor", j_node="tip", section_id="annulus", material_id="declared-elastic"
            ),
        ),
        sections=(annular_frame_section("annulus", 0.2, 0.18, "reference"),),
        materials=(
            FrameMaterial(
                id="declared-elastic", young_modulus_pa=2e11, poisson_ratio=0.3, evidence_id="reference"
            ),
        ),
        supports=(
            FrameSupport(
                node_id="anchor", fixed_dofs=("DX", "DY", "DZ", "RX", "RY", "RZ"), evidence_id="reference"
            ),
        ),
        load_cases=("point",),
        node_loads=(
            FrameNodeLoad(
                node_id="tip", direction="FY", value=-1000.0, case_id="point", evidence_id="reference"
            ),
        ),
        combinations=(FrameCombination(id="service", factors={"point": 1.0}, evidence_id="reference"),),
        geometry_evidence_id="reference",
        geometry_and_connectivity_reviewed=True,
        stiffness_and_loads_reviewed=True,
        supports_reviewed=True,
        linear_small_displacement_applicability_reviewed=True,
    )


if __name__ == "__main__":
    print(json.dumps(solve_frame(make_case(), context()), indent=2))
