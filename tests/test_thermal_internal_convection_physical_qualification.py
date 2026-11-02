"""Catalog-path qualification against fully developed laminar tube identities."""

import pytest

from pipeline_integrity import evaluate
from pipeline_integrity.contracts import RunContext

CASES = (
    ("Laminar - constant T", 3.66, 0.025, 0.015),
    ("Laminar - constant T", 3.66, 0.1, 0.6),
    ("Laminar - constant T", 3.66, 0.4, 20.0),
    ("Laminar - constant Q", 48.0 / 11.0, 0.025, 0.015),
    ("Laminar - constant Q", 48.0 / 11.0, 0.1, 0.6),
    ("Laminar - constant Q", 48.0 / 11.0, 0.4, 20.0),
)


def context():
    return RunContext(
        asset_id="internal-convection-reference",
        scenario_id="fully-developed-laminar-tube",
        clock_id="steady-state",
        origin="synthetic",
    )


@pytest.mark.parametrize("method,expected_nu,diameter,conductivity", CASES)
def test_catalog_correlation_matches_laminar_nusselt_and_heat_transfer_scale(
    method, expected_nu, diameter, conductivity
):
    result = evaluate(
        "thermal.internal_convection",
        {
            "reynolds": 1000.0,
            "prandtl": 7.0,
            "diameter_m": diameter,
            "conductivity_w_m_k": conductivity,
            "relative_roughness": 0.0,
            "method": method,
        },
        context(),
    )
    assert result.status == "computed"
    assert result.model_id == "thermal.internal_convection"
    assert result.values["nusselt"] == pytest.approx(expected_nu, rel=1e-10, abs=1e-12)
    assert result.values["coefficient_w_m2_k"] == pytest.approx(
        expected_nu * conductivity / diameter, rel=1e-10, abs=1e-12
    )
    assert result.values["coefficient_w_m2_k"] * diameter / conductivity == pytest.approx(
        expected_nu, rel=1e-10, abs=1e-12
    )


def test_transition_reynolds_band_is_withheld_from_laminar_correlation():
    result = evaluate(
        "thermal.internal_convection",
        {
            "reynolds": 3000.0,
            "prandtl": 7.0,
            "diameter_m": 0.1,
            "conductivity_w_m_k": 0.6,
            "relative_roughness": 0.0,
            "method": "Laminar - constant T",
        },
        context(),
    )
    assert result.status == "out_of_scope"
    assert result.values == {}
