"""Independent volume-balance checks for coating material coverage."""
import math
import runpy

import pytest

from pipeline_integrity.electrochemical_exchange import evaluate_electrochemical


CASES, CTX = runpy.run_path("examples/electrochemical_methods.py")["make_cases"]()
BASE = CASES["protection.coating_coverage"]


def evaluate(area, dry_thickness, solids, transfer):
    request = BASE.model_copy(
        update={
            "parameters": {
                "area_m2": area,
                "dry_thickness_m": dry_thickness,
                "volume_solids_fraction": solids,
                "transfer_efficiency": transfer,
            }
        }
    )
    return evaluate_electrochemical(request, CTX)


@pytest.mark.parametrize(
    "area,thickness,solids,transfer",
    [(1.0, 1e-3, 1.0, 1.0), (100.0, 2e-4, 0.5, 0.7), (42.5, 8e-5, 0.63, 0.82)],
)
def test_required_wet_volume_matches_independent_volume_balance(area, thickness, solids, transfer):
    result = evaluate(area, thickness, solids, transfer)
    assert result["status"] == "computed"
    values = result["values"]
    dry_volume = area * thickness
    wet_film_volume = dry_volume / solids
    applied_volume = wet_film_volume / transfer
    assert values["dry_volume_m3"] == pytest.approx(dry_volume, rel=2e-13)
    assert values["wet_thickness_m"] == pytest.approx(thickness / solids, rel=2e-13)
    assert values["required_wet_volume_m3"] == pytest.approx(applied_volume, rel=2e-13)


def test_area_and_film_thickness_scale_all_material_volumes_linearly():
    base = evaluate(10.0, 1e-4, 0.5, 0.8)["values"]
    double_area = evaluate(20.0, 1e-4, 0.5, 0.8)["values"]
    double_thickness = evaluate(10.0, 2e-4, 0.5, 0.8)["values"]
    for key in ("dry_volume_m3", "required_wet_volume_m3"):
        assert double_area[key] == pytest.approx(2 * base[key], rel=2e-13)
        assert double_thickness[key] == pytest.approx(2 * base[key], rel=2e-13)


def test_solids_fraction_and_transfer_efficiency_have_separate_inverse_effects():
    base = evaluate(10.0, 1e-4, 0.5, 0.8)["values"]
    half_solids = evaluate(10.0, 1e-4, 0.25, 0.8)["values"]
    half_transfer = evaluate(10.0, 1e-4, 0.5, 0.4)["values"]
    assert half_solids["required_wet_volume_m3"] == pytest.approx(2 * base["required_wet_volume_m3"])
    assert half_transfer["required_wet_volume_m3"] == pytest.approx(2 * base["required_wet_volume_m3"])
    assert half_transfer["wet_thickness_m"] == pytest.approx(base["wet_thickness_m"])


@pytest.mark.parametrize(
    "field,value",
    [("volume_solids_fraction", 0.0), ("volume_solids_fraction", 1.01), ("transfer_efficiency", 0.0), ("transfer_efficiency", 1.01)],
)
def test_efficiency_inputs_outside_physical_fraction_are_rejected(field, value):
    parameters = {"area_m2": 1.0, "dry_thickness_m": 1e-4, "volume_solids_fraction": 0.5, "transfer_efficiency": 0.8}
    parameters[field] = value
    request = BASE.model_copy(update={"parameters": parameters})
    with pytest.raises(ValueError):
        evaluate_electrochemical(request, CTX)
