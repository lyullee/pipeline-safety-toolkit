"""Equal-cell thickness inventory and connectivity checks through the owner adapter."""

import runpy

import pytest

from pipeline_integrity.inspection_statistics import evaluate_inspection_statistics


CASES, CONTEXT = runpy.run_path("examples/inspection_statistics.py")["make_cases"]()
BASE = CASES["geometry.thickness_grid"]


def evaluate(grid, *, dx=0.02, dy=0.03, reference=0.01, threshold=0.008):
    parameters = dict(BASE.parameters)
    parameters.update(
        cell_thickness_m=grid,
        cell_size_x_m=dx,
        cell_size_y_m=dy,
        reference_thickness_m=reference,
        reporting_threshold_m=threshold,
    )
    layout = BASE.grid_layout.model_copy(
        update={
            "row_ids": tuple(f"row-{i}" for i in range(len(grid))),
            "column_ids": tuple(f"column-{j}" for j in range(len(grid[0]))),
        }
    )
    request = BASE.model_copy(update={"parameters": parameters, "grid_layout": layout})
    return evaluate_inspection_statistics(request, CONTEXT)


def test_equal_cell_observed_missing_volume_and_mean_match_closed_form():
    grid = [[0.012, 0.006, None], [0.008, None, 0.004]]
    result = evaluate(grid)
    values = result["values"]
    area = 0.02 * 0.03
    observed = [0.012, 0.006, 0.008, 0.004]
    assert result["status"] == "computed"
    assert values["minimum_measured_thickness_m"] == pytest.approx(0.004)
    assert values["mean_measured_thickness_m"] == pytest.approx(sum(observed) / 4)
    assert values["observed_area_m2"] == pytest.approx(4 * area)
    assert values["missing_area_m2"] == pytest.approx(2 * area)
    assert values["observed_fraction"] == pytest.approx(4 / 6)
    expected_loss = sum(0.01 - x for x in observed) * area
    assert values["net_measured_loss_volume_m3"] == pytest.approx(expected_loss)
    assert result["native_receipt"]["native"]["payload"]["model_id"] == "geometry.thickness_grid"


def test_four_neighbour_components_do_not_join_diagonal_cells():
    result = evaluate([[0.006, 0.009], [0.009, 0.006]], dx=0.1, dy=0.2, threshold=0.008)
    assert result["values"]["connected_below_threshold_areas_m2"] == pytest.approx([0.02, 0.02])


def test_face_adjacent_cells_form_one_component_and_threshold_is_strict():
    result = evaluate([[0.008, 0.007, 0.006]], dx=0.1, dy=0.2, threshold=0.008)
    assert result["values"]["connected_below_threshold_areas_m2"] == pytest.approx([0.04])


def test_area_and_volume_scale_with_cell_dimensions():
    grid = [[0.006, 0.004], [0.008, 0.009]]
    base = evaluate(grid, dx=0.1, dy=0.2)
    scaled = evaluate(grid, dx=0.3, dy=0.4)
    # Cell area grows by six; the measured count and thicknesses stay fixed.
    assert scaled["values"]["observed_area_m2"] == pytest.approx(6 * base["values"]["observed_area_m2"])
    assert scaled["values"]["missing_area_m2"] == pytest.approx(6 * base["values"]["missing_area_m2"])
    assert scaled["values"]["net_measured_loss_volume_m3"] == pytest.approx(
        6 * base["values"]["net_measured_loss_volume_m3"]
    )


def test_reference_thickness_shift_changes_volume_by_observed_cell_count_times_area():
    grid = [[0.006, None], [0.009, 0.004]]
    base = evaluate(grid, reference=0.01)
    shifted = evaluate(grid, reference=0.011)
    area = 0.02 * 0.03
    assert shifted["values"]["net_measured_loss_volume_m3"] - base["values"]["net_measured_loss_volume_m3"] == pytest.approx(
        3 * area * 0.001
    )


def test_all_missing_cells_are_withheld_instead_of_assumed_sound():
    result = evaluate([[None, None], [None, None]])
    assert result["status"] == "needs_data"
    assert not result["values"]


def test_layout_row_and_column_identity_is_retained():
    result = evaluate([[0.006, 0.007, None], [0.009, None, 0.005]])
    assert result["request"]["grid_layout"]["row_ids"] == ["row-0", "row-1"]
    assert result["request"]["grid_layout"]["column_ids"] == ["column-0", "column-1", "column-2"]
