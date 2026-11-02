"""Independent arithmetic checks for a caller-provided piecewise FAD boundary.

The synthetic polyline is authored in this test. It is not a standards curve and
does not qualify the supplied K, reference stress, toughness, strength or method.
"""

import runpy

import pytest

from pipeline_integrity.fracture_exchange import assess_failure_points


EXAMPLE = runpy.run_path("examples/fracture_screen.py")


def _assess(*, kr: float, lr: float, material_condition: str = "authored-material"):
    parameters = EXAMPLE["failure_points"](kr=kr, lr=lr)
    parameters["points"][0]["material_condition_id"] = material_condition
    return assess_failure_points(
        parameters,
        EXAMPLE["context"](),
        location_id="annulus",
        evidence_ids=("authored",),
    ).native.payload


@pytest.mark.parametrize(
    "load_ratio,expected_allowable",
    [(0.0, 1.0), (0.25, 0.95), (1.0, 0.8), (1.5, 0.4), (2.0, 0.0)],
)
def test_piecewise_linear_allowable_boundary_matches_independent_interpolation(
    load_ratio, expected_allowable
):
    kr = expected_allowable / 2.0
    payload = _assess(kr=kr, lr=load_ratio)
    values = payload["values"]
    point = values["points"][0]

    assert payload["status"] == "computed"
    assert point["load_ratio_Lr"] == pytest.approx(load_ratio, abs=1e-14)
    assert point["toughness_ratio_Kr"] == pytest.approx(kr, abs=1e-14)
    assert point["allowable_toughness_ratio"] == pytest.approx(expected_allowable, abs=1e-14)
    assert point["vertical_toughness_ratio_margin"] == pytest.approx(expected_allowable - kr, abs=1e-14)
    assert point["point_outcome"] == "inside_or_on_declared_boundary"
    assert values["declared_boundary_outcome"] == "satisfied"


def test_point_above_boundary_is_reported_with_signed_margin():
    payload = _assess(kr=0.5, lr=1.5)
    values = payload["values"]

    assert payload["status"] == "computed"
    assert values["points"][0]["allowable_toughness_ratio"] == pytest.approx(0.4)
    assert values["points"][0]["vertical_toughness_ratio_margin"] == pytest.approx(-0.1)
    assert values["points"][0]["point_outcome"] == "outside_declared_boundary"
    assert values["declared_boundary_outcome"] == "not_satisfied"


def test_load_ratio_beyond_last_knot_is_withheld_without_extrapolation():
    payload = _assess(kr=0.1, lr=2.01)

    assert payload["status"] == "out_of_scope"
    assert payload["values"]["points"][0]["allowable_toughness_ratio"] is None
    assert payload["values"]["points"][0]["vertical_toughness_ratio_margin"] is None
    assert payload["values"]["points"][0]["point_outcome"] == "outside_declared_curve_support"
    assert payload["values"]["declared_boundary_outcome"] == "unknown"


def test_material_condition_mismatch_withholds_assessment():
    payload = _assess(kr=0.2, lr=0.5, material_condition="different-condition")

    assert payload["status"] == "needs_data"
    assert payload["values"] == {}
