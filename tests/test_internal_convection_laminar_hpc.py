"""Exact fully developed laminar tube Nusselt and coefficient references."""


import pytest

from pipeline_integrity.models.library_models import InternalConvection, internal_convection


@pytest.mark.parametrize(
    "method,expected_nusselt",
    [("Laminar - constant T", 3.66), ("Laminar - constant Q", 48.0/11.0)],
)
@pytest.mark.parametrize(
    "diameter_m,conductivity_w_m_k",
    [(0.025, 0.015), (0.1, 0.6), (0.4, 20.0)],
)
# The ht 1.2.0 docstring says 4.354, while its executable constant-Q formula is 48/11.
def test_laminar_fully_developed_nusselt_and_heat_transfer_coefficient(
    method, expected_nusselt, diameter_m, conductivity_w_m_k
):
    """Independent canonical tube references; same Re/Pr isolates geometry/property scaling."""
    result = internal_convection(
        InternalConvection(
            reynolds=1000.0,
            prandtl=7.0,
            diameter_m=diameter_m,
            conductivity_w_m_k=conductivity_w_m_k,
            relative_roughness=0.0,
            method=method,
        )
    )

    assert result.status == "computed"
    assert result.values["nusselt"] == pytest.approx(expected_nusselt, rel=1e-10, abs=1e-12)
    assert result.values["coefficient_w_m2_k"] == pytest.approx(
        expected_nusselt * conductivity_w_m_k / diameter_m, rel=1e-10, abs=1e-12
    )


def test_laminar_nusselt_is_independent_of_diameter_and_conductivity():
    """For the same dimensionless state, h D / k must recover Nu exactly."""
    for diameter_m, conductivity_w_m_k in ((0.02, 0.1), (0.2, 2.0), (1.0, 50.0)):
        result = internal_convection(
            InternalConvection(
                reynolds=800.0,
                prandtl=1.0,
                diameter_m=diameter_m,
                conductivity_w_m_k=conductivity_w_m_k,
                method="Laminar - constant T",
            )
        )
        assert result.status == "computed"
        recovered = result.values["coefficient_w_m2_k"] * diameter_m / conductivity_w_m_k
        assert recovered == pytest.approx(3.66, rel=1e-10, abs=1e-12)
