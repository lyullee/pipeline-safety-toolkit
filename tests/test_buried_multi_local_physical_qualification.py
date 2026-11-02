"""Bounded analytic qualification of the polygonal buried-soil heat solver."""

from pathlib import Path
import runpy

import pytest

from pipeline_integrity.soil_fem import evaluate_soil_fem, verify_soil_fem_record


ROOT = Path(__file__).resolve().parents[1]
MAKE_CASE = runpy.run_path(str(ROOT / "examples" / "soil_fem.py"))["make_case"]


@pytest.mark.parametrize("shear,refinement", [(0.0, 1), (0.3, 2), (0.75, 4)])
def test_linear_harmonic_field_is_exact_and_boundary_flux_is_conservative(shear, refinement):
    request, context = MAKE_CASE(
        mode="linear", shear=shear, refinement=refinement, reviewed=True,
        triangulation="local_delaunay",
    )
    result = evaluate_soil_fem(request, context)

    assert result["status"] == "computed"
    assert result["native"]["geometry"]["cavity_count"] == 2
    assert sum(result["native"]["geometry"]["triangle_areas_m2"]) == pytest.approx(22.0, abs=1e-10)

    row = result["native"]["rows"][0]
    exact = [300.0 + 2.0 * y for _, y in request.mesh.vertices_m]
    assert row["node_temperature_K"] == pytest.approx(exact, abs=1e-10)

    # T=300+2y is harmonic for constant isotropic conductivity. The declared
    # ground boundary has 12 m length, k=1 W/(m K), and unit extrusion depth,
    # so its independent normal-gradient heat rate is 24 W.
    physical = row["boundary_physical_gradient_power_into_soil_W"]
    assert physical["ground"] == pytest.approx(24.0, abs=1e-9)
    assert sum(physical.values()) == pytest.approx(0.0, abs=1e-8)
    assert verify_soil_fem_record(result, request, context)["valid"]
