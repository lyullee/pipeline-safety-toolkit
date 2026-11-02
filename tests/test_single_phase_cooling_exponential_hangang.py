"""Independent exponential temperature and heat-budget checks for single-phase cooling."""

import math

import pytest

from pipeline_integrity.models.thermal import Cooling, cooling


CASES = (
    (420.0, 300.0, 0.2, 2_400.0, 60.0),
    (280.0, 330.0, 0.8, 1_900.0, 420.0),
    (360.0, 300.0, 0.05, 1_100.0, 7.5),
    (315.0, 315.0, 0.4, 4_000.0, 1_000.0),
    (450.0, 290.0, 0.15, 2_200.0, 0.0),
)


@pytest.mark.parametrize("inlet,ambient,mass_flow,cp,ua", CASES)
def test_lumped_single_phase_temperature_matches_exponential_and_energy_balance(
    inlet, ambient, mass_flow, cp, ua
):
    result = cooling(
        Cooling(
            inlet_temperature_k=inlet,
            ambient_temperature_k=ambient,
            mass_flow_kg_s=mass_flow,
            specific_heat_j_kg_k=cp,
            conductance_w_k=ua,
        )
    )
    capacity_rate = mass_flow * cp
    attenuation = math.exp(-ua / capacity_rate)
    expected_outlet = ambient + (inlet - ambient) * attenuation
    expected_heat_loss = capacity_rate * (inlet - expected_outlet)

    assert result.status == "computed"
    assert result.values["outlet_temperature_k"] == pytest.approx(expected_outlet, rel=2e-14, abs=1e-12)
    assert result.values["heat_loss_w"] == pytest.approx(expected_heat_loss, rel=2e-14, abs=1e-10)
    assert result.values["heat_loss_w"] == pytest.approx(
        capacity_rate * (inlet - result.values["outlet_temperature_k"]), abs=1e-10
    )
    if inlet != ambient:
        if ua == 0.0:
            assert result.values["outlet_temperature_k"] == pytest.approx(inlet, abs=1e-12)
            assert result.values["heat_loss_w"] == pytest.approx(0.0, abs=1e-12)
        else:
            ratio = (result.values["outlet_temperature_k"] - ambient) / (inlet - ambient)
            assert 0.0 < ratio <= 1.0
            assert math.log(ratio) == pytest.approx(-ua / capacity_rate, abs=2e-13)
