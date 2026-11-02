"""Faraday-law penetration checks through the electrochemical material adapter."""

import runpy

import pytest

from pipeline_integrity.electrochemical_exchange import evaluate_electrochemical

CASES = (
    (0.2, 0.055845, 2.0, 7850.0, 0.9, 100000.0),
    (1.5, 0.06538, 2.0, 7140.0, 1.0, 3600.0),
    (0.0, 0.0269815, 3.0, 2700.0, 0.8, 50000.0),
)
FARADAY_C_MOL = 96485.33212331001


@pytest.mark.parametrize("current_density,molar_mass,electrons,density,efficiency,duration", CASES)
def test_faraday_penetration_matches_charge_mass_and_volume_balance(
    current_density, molar_mass, electrons, density, efficiency, duration
):
    cases, context = runpy.run_path("examples/electrochemical_methods.py")["make_cases"]()
    request = cases["corrosion.faraday"].model_copy(
        update={
            "parameters": {
                "current_density_A_m2": current_density,
                "molar_mass_kg_mol": molar_mass,
                "electrons_per_atom": electrons,
                "density_kg_m3": density,
                "current_efficiency": efficiency,
                "duration_s": duration,
            }
        }
    )
    result = evaluate_electrochemical(request, context)
    expected_rate = efficiency * current_density * molar_mass / (electrons * FARADAY_C_MOL * density)
    assert result["status"] == "computed"
    assert result["values"]["rate_m_s"] == pytest.approx(expected_rate, rel=2e-13, abs=1e-30)
    assert result["values"]["penetration_m"] == pytest.approx(expected_rate * duration, rel=2e-13, abs=1e-30)
