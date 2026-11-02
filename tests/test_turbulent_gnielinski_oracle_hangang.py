"""Hangang candidate: independent smooth/rough turbulent Gnielinski oracle."""
import math
import pytest
from pipeline_integrity.models.library_models import InternalConvection, internal_convection

def _colebrook_darcy_factor(reynolds, relative_roughness):
    """Solve Colebrook by bisection independently from delegated correlation."""
    low, high = 0.005, 0.12
    for _ in range(180):
        f = 0.5 * (low + high)
        residual = 1.0 / math.sqrt(f) + 2.0 * math.log10(
            relative_roughness / 3.7 + 2.51 / (reynolds * math.sqrt(f))
        )
        if residual > 0:
            low = f
        else:
            high = f
    return 0.5 * (low + high)

@pytest.mark.parametrize(
    "re,pr,roughness,diameter,conductivity",
    [
        (12000.0, 0.72, 0.0, 0.025, 0.026),
        (85000.0, 6.2, 1.5e-4, 0.08, 0.61),
        (1.2e6, 120.0, 8.0e-4, 0.5, 16.0),
    ],
)
def test_turbulent_gnielinski_matches_independent_colebrook_oracle(re, pr, roughness, diameter, conductivity):
    e_d = roughness / diameter
    f_darcy = _colebrook_darcy_factor(re, e_d)
    expected_nu = (f_darcy / 8.0) * (re - 1000.0) * pr / (
        1.0 + 12.7 * math.sqrt(f_darcy / 8.0) * (pr ** (2.0 / 3.0) - 1.0)
    )
    result = internal_convection(
        InternalConvection(
            reynolds=re, prandtl=pr, diameter_m=diameter,
            conductivity_w_m_k=conductivity, relative_roughness=e_d,
            method="Gnielinski",
        )
    )
    assert result.status == "computed"
    assert result.values["nusselt"] == pytest.approx(expected_nu, rel=2e-8)
    assert result.values["coefficient_w_m2_k"] == pytest.approx(expected_nu * conductivity / diameter, rel=2e-8)

def test_gnielinski_coefficient_recovers_dimensionless_nusselt_across_geometry():
    for diameter, conductivity in ((0.02, 0.03), (0.2, 0.6), (0.9, 25.0)):
        result = internal_convection(
            InternalConvection(
                reynolds=50000.0, prandtl=4.0, diameter_m=diameter,
                conductivity_w_m_k=conductivity, relative_roughness=0.0,
                method="Gnielinski",
            )
        )
        recovered = result.values["coefficient_w_m2_k"] * diameter / conductivity
        assert recovered == pytest.approx(result.values["nusselt"], rel=2e-14)
