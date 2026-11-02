"""Free thermal-gradient cantilever: exact curvature and mesh invariance.

The analytical beam reference uses the local frame convention documented by
the thermal-mesh transform: kappa_z=-alpha*dT/dy and d(rotation_z)/dx=kappa_z.
All values and provenance evidence in this test are synthetic.
"""

import runpy

import pytest

from pipeline_integrity.contracts import EvidenceRef
from pipeline_integrity.structural import FrameRequest
from pipeline_integrity.structural_thermal_mesh import (
    ThermalBeamPiece,
    ThermalMemberMesh,
    ThermalMeshRequest,
    evaluate_thermal_mesh_frame,
)


LENGTH_M = 4.0
ALPHA_PER_K = 12.0e-6
GRADIENT_Y_K_PER_M = 50.0
CURVATURE_Z_PER_M = -ALPHA_PER_K * GRADIENT_Y_K_PER_M


def _context():
    context = runpy.run_path("examples/pipe_frame.py")["context"]()
    return context.model_copy(update={
        "evidence": context.evidence + (
            EvidenceRef(id="thermal-field", citation="Authored uniform through-section temperature gradient", origin="synthetic", license_id="MIT"),
            EvidenceRef(id="thermal-property", citation="Authored constant expansion-property support", origin="synthetic", license_id="MIT"),
        )
    })


def _request(partitions: int):
    make_case = runpy.run_path("examples/pipe_frame.py")["make_case"]
    frame = make_case().model_copy(update={"node_loads": (), "line_loads": ()})
    pieces = tuple(
        ThermalBeamPiece(
            start_fraction=index / partitions,
            end_fraction=(index + 1) / partitions,
            centroid_temperature_K=300.0,
            stress_free_temperature_K=300.0,
            temperature_gradient_y_K_m=GRADIENT_Y_K_PER_M,
            temperature_gradient_z_K_m=0.0,
            expansion_coefficient_per_K=ALPHA_PER_K,
            coefficient_temperature_support_K=(290.0, 310.0),
            evidence_id="thermal-field",
        )
        for index in range(partitions)
    )
    mesh = ThermalMemberMesh(
        member_id="pipe",
        case_id="point",
        section_y_extent_m=(-0.1, 0.1),
        section_z_extent_m=(-0.1, 0.1),
        pieces=pieces,
        piecewise_uniform_axial_and_linear_section_temperature_reviewed=True,
        frozen_elastic_properties_and_constant_expansion_per_piece_reviewed=True,
    )
    return ThermalMeshRequest(id=f"thermal-gradient-{partitions}-pieces", frame=frame, members=(mesh,))


@pytest.mark.parametrize("partitions", (1, 2, 4))
def test_free_cantilever_linear_section_gradient_matches_exact_rotation_and_deflection(partitions):
    context = _context()
    record = evaluate_thermal_mesh_frame(_request(partitions), context)

    assert record["status"] == "computed"
    tip = record["result"]["combinations"]["service"]["nodes"]["tip"]
    expected_rotation_z = CURVATURE_Z_PER_M * LENGTH_M
    expected_deflection_y = 0.5 * CURVATURE_Z_PER_M * LENGTH_M**2
    assert tip["rotation_rad"][2] == pytest.approx(expected_rotation_z, rel=2e-10, abs=1e-12)
    assert tip["translation_m"][1] == pytest.approx(expected_deflection_y, rel=2e-10, abs=1e-12)
    assert tip["translation_m"][0] == pytest.approx(0.0, abs=1e-12)
    assert record["result"]["combinations"]["service"]["static_balance"]["passed"]
    assert all(
        row["stress_free_curvature_z_per_m"] == pytest.approx(CURVATURE_Z_PER_M)
        for row in record["preparation"]["member_mapping"]
    )
