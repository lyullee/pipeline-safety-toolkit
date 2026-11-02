"""Catalog-path closed-form checks for circular pipe geometry and inventories."""

import math
import pytest

from pipeline_integrity import evaluate
from pipeline_integrity.contracts import RunContext

CTX = RunContext(asset_id="pipe-geometry-reference", scenario_id="uniform-tube", clock_id="steady", origin="synthetic")
CASES = ((0.10, 0.12, 10.0, 7850.0), (0.50, 0.70, 100.0, 2700.0), (0.025, 0.035, 0.5, 8000.0))


@pytest.mark.parametrize("inner,outer,length,density", CASES)
def test_catalog_pipe_geometry_matches_independent_circular_integrals(inner, outer, length, density):
    result = evaluate(
        "geometry.pipe",
        {
            "inner_diameter_m": inner,
            "outer_diameter_m": outer,
            "length_m": length,
            "wall_density_kg_m3": density,
        },
        CTX,
    )
    flow_area = math.pi * inner**2 / 4
    wall_area = math.pi * (outer**2 - inner**2) / 4
    assert result.status == "computed"
    assert result.model_id == "geometry.pipe"
    assert result.values["flow_area_m2"] == pytest.approx(flow_area, rel=2e-15)
    assert result.values["internal_volume_m3"] == pytest.approx(flow_area * length, rel=2e-15)
    assert result.values["wall_volume_m3"] == pytest.approx(wall_area * length, rel=2e-15)
    assert result.values["wall_mass_kg"] == pytest.approx(wall_area * length * density, rel=2e-15)
    assert result.values["external_area_m2"] == pytest.approx(math.pi * outer * length, rel=2e-15)
