"""Mass-loss coupon conversion to average penetration, through the owner adapter."""

import runpy

import pytest

from pipeline_integrity.electrochemical_exchange import evaluate_electrochemical

SECONDS_PER_YEAR = 365.25 * 24.0 * 3600.0
CASES = (
    (0.001, 0.01, 7850.0, 1000.0),
    (0.025, 0.20, 8000.0, 86400.0),
    (0.0, 0.05, 2700.0, 100000.0),
)


@pytest.mark.parametrize("mass_loss,area,density,duration", CASES)
def test_coupon_mass_loss_matches_uniform_volume_and_time_balance(mass_loss, area, density, duration):
    cases, context = runpy.run_path("examples/electrochemical_methods.py")["make_cases"]()
    request = cases["corrosion.coupon_loss"].model_copy(
        update={"parameters": {"mass_loss_kg": mass_loss, "exposed_area_m2": area,
                               "density_kg_m3": density, "duration_s": duration}}
    )
    result = evaluate_electrochemical(request, context)
    penetration = mass_loss / (area * density)
    rate = penetration / duration
    assert result["status"] == "computed"
    assert result["values"]["mean_penetration_m"] == pytest.approx(penetration, rel=1e-14, abs=1e-30)
    assert result["values"]["rate_m_s"] == pytest.approx(rate, rel=1e-14, abs=1e-30)
    assert result["values"]["rate_mm_year"] == pytest.approx(rate * 1000 * SECONDS_PER_YEAR, rel=1e-14, abs=1e-30)
