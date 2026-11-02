"""Physical and integration checks for the declared composition-index gateway."""

from copy import deepcopy
from pathlib import Path
import os
import runpy

import pytest
import matintegrity

from pipeline_integrity import material_quality as quality
from pipeline_integrity.material_quality import verify_material_quality_record


# Historical execution pin is retained as provenance, not relabelled as current.
HISTORICAL_OWNER_TREE_SHA256 = "3fb426306c5b38b2386c389471fda9d7a2d9ebd031dc2af8edbd79b17b138b29"
SELECTED_OWNER_FIXTURE_TREE_SHA256 = "b9be6226ac879a44dd9420019f5f12232ec5af34c81be1ba99a09716bca0289c"
REPO_ROOT = Path(__file__).resolve().parents[1]
selected = os.environ.get("PIPELINE_SELECTED_MATERIAL_SOURCE_ROOT")
selected_path = Path(selected).resolve() if selected else Path(matintegrity.__file__).resolve().parents[1]
OWNER_SOURCE_ROOT = selected_path.parent if (selected_path / "matintegrity").is_dir() else selected_path


def fixture():
    assert Path(matintegrity.__file__).resolve().is_relative_to(OWNER_SOURCE_ROOT / "src"), "loaded material owner differs from selected source"
    requests, context = runpy.run_path(str(REPO_ROOT / "examples/material_quality.py"))["make_cases"]()
    request = requests[quality.PITTING].model_copy(update={"owner_source_tree_sha256": SELECTED_OWNER_FIXTURE_TREE_SHA256})
    return request, context


def evaluate(request, context):
    return quality.evaluate_material_quality(
        request,
        context,
        owner_source_root=OWNER_SOURCE_ROOT,
    )


def test_gateway_result_matches_independent_declared_coefficient_formula_and_receipt_verifies():
    request, context = fixture()
    result = evaluate(request, context)
    parameters = request.parameters
    expected = (
        parameters["chromium_mass_percent"]
        + parameters["molybdenum_coefficient"] * parameters["molybdenum_mass_percent"]
        + parameters["nitrogen_coefficient"] * parameters["nitrogen_mass_percent"]
    )
    assert result["status"] == "computed"
    assert result["values"]["composition_index"] == pytest.approx(expected, rel=0, abs=0)
    assert result["composition_index_is_corrosion_rate"] is False
    assert result["field_or_standard_qualification"] is False
    assert result["source_pin"]["source_unchanged"] is True
    verify_material_quality_record(result)


@pytest.mark.parametrize(
    ("element", "increment", "coefficient_key"),
    [
        ("chromium_mass_percent", 0.25, None),
        ("molybdenum_mass_percent", 0.25, "molybdenum_coefficient"),
        ("nitrogen_mass_percent", 0.25, "nitrogen_coefficient"),
    ],
)
def test_each_mass_percent_term_changes_index_by_its_declared_linear_sensitivity(
    element, increment, coefficient_key
):
    request, context = fixture()
    base = evaluate(request, context)["values"]["composition_index"]
    params = deepcopy(request.parameters)
    params[element] += increment
    varied = evaluate(request.model_copy(update={"parameters": params}), context)["values"]["composition_index"]
    expected_slope = 1.0 if coefficient_key is None else params[coefficient_key]
    assert varied - base == pytest.approx(increment * expected_slope, rel=1e-12)


def test_same_assay_identity_gate_withholds_value_before_native_calculation():
    request, context = fixture()
    request = request.model_copy(update={"element_assay_ids": {"Cr": "assay-A", "Mo": "assay-B", "N": "assay-A"}})
    result = quality.evaluate_material_quality(request, context)
    assert result["status"] == "needs_data"
    assert result["values"] == {}
    assert result["native_receipt"] is None
    verify_material_quality_record(result)


def test_mass_percent_total_above_physical_composition_limit_is_rejected():
    request, context = fixture()
    params = deepcopy(request.parameters)
    params.update(chromium_mass_percent=98.0, molybdenum_mass_percent=2.0, nitrogen_mass_percent=1.0)
    with pytest.raises(ValueError):
        evaluate(request.model_copy(update={"parameters": params}), context)


def test_source_tree_pin_mismatch_fails_closed():
    request, context = fixture()
    request = request.model_copy(update={"owner_source_tree_sha256": "0" * 64})
    with pytest.raises(ValueError, match="source tree differs"):
        evaluate(request, context)
