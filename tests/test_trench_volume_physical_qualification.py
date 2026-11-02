"""Closed-form polygon geometry checks for declared uniform trench sections."""
import pytest
from pipeline_integrity.models.hydraulics import TrenchVolume, trench_volume


@pytest.mark.parametrize("bottom,depth,slope,length,loose", [
    (0.6, 0.8, 0.0, 3.0, 1.0),
    (0.9, 1.2, 1.0, 7.5, 1.18),
    (1.4, 2.0, 1.6, 12.0, 1.32),
])
def test_uniform_trench_volume_matches_independent_polygon_and_loose_factor(bottom, depth, slope, length, loose):
    # The side slope is horizontal run per vertical rise for each of two symmetric sides.
    side_spread = slope*depth
    vertices = [(0.0, 0.0), (bottom, 0.0), (bottom+side_spread, depth),
                (-side_spread, depth)]
    shoelace_twice_area = abs(sum(
        x1*y2-y1*x2
        for (x1,y1),(x2,y2) in zip(vertices, vertices[1:]+vertices[:1])
    ))
    section_area = shoelace_twice_area/2.0
    expected_in_place = section_area*length
    result = trench_volume(TrenchVolume(
        bottom_width_m=bottom, depth_m=depth,
        side_slope_horizontal_per_vertical=slope,
        length_m=length, loose_volume_ratio=loose,
    ))
    assert result.values["cross_section_m2"] == pytest.approx(section_area, rel=1e-14)
    assert result.values["in_place_volume_m3"] == pytest.approx(expected_in_place, rel=1e-14)
    assert result.values["loose_volume_m3"] == pytest.approx(expected_in_place*loose, rel=1e-14)
    assert result.values["loose_volume_m3"]/result.values["in_place_volume_m3"] == pytest.approx(loose)

