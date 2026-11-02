"""Circular-tube Saint-Venant torsion oracle through the native frame solver.

All geometry, stiffness and load values are synthetic and authored here.
"""

import math
import runpy

import pytest

from pipeline_integrity.structural import annular_frame_section, solve_frame


CASES = (
    (0.20, 0.18, 4.0, 2.0e11, 0.30, 1000.0),
    (0.20, 0.18, 4.0, 2.0e11, 0.30, 500.0),
    (0.20, 0.18, 4.0, 2.0e11, 0.30, 2000.0),
    (0.16, 0.12, 2.5, 1.8e11, 0.27, 750.0),
    (0.24, 0.20, 5.0, 2.1e11, 0.42, 1250.0),
)


@pytest.mark.parametrize("outer_d,inner_d,length,young,poisson,torque", CASES)
def test_annular_pipe_torsion_matches_closed_form_twist_and_reaction(outer_d, inner_d, length, young, poisson, torque):
    make_case = runpy.run_path("examples/pipe_frame.py")
    request = make_case["make_case"]()
    context = make_case["context"]()
    tip = request.nodes[1].model_copy(update={"position_m": (length, 0.0, 0.0)})
    section = annular_frame_section("annulus", outer_d, inner_d, "reference")
    material = request.materials[0].model_copy(update={"young_modulus_pa": young, "poisson_ratio": poisson})
    load = request.node_loads[0].model_copy(update={"direction": "MX", "value": torque})
    request = request.model_copy(update={"nodes": (request.nodes[0], tip), "sections": (section,),
                                       "materials": (material,), "node_loads": (load,)})

    record = solve_frame(request, context)

    assert record["status"] == "computed"
    result = record["combinations"]["service"]
    polar_j = math.pi * (outer_d**4 - inner_d**4) / 32.0
    shear_modulus = young / (2.0 * (1.0 + poisson))
    expected_twist = torque * length / (shear_modulus * polar_j)
    assert result["nodes"]["tip"]["rotation_rad"][0] == pytest.approx(expected_twist, rel=2e-11, abs=1e-12)
    assert result["nodes"]["anchor"]["reaction_moment_nm"][0] == pytest.approx(-torque, rel=2e-11, abs=1e-8)
    assert result["static_balance"]["passed"]
