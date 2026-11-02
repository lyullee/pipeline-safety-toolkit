"""Ideal-gas compressor closed form via an independent analytic property provider."""
import math
import pytest

from pipeline_integrity.models.equipment import Compression, compression
from pipeline_integrity.thermo import StateRequest, ThermodynamicState

R = 287.05
CP = 1005.0


class AnalyticIdealGas:
    def resolve(self, request: StateRequest):
        values = request.inputs
        pressure = values["P"]
        if "T" in values:
            temperature = values["T"]
        elif "Smass" in values:
            temperature = 300.0 * math.exp((values["Smass"] + R * math.log(pressure / 1e5)) / CP)
        else:
            temperature = values["Hmass"] / CP
        return ThermodynamicState(
            fluid=request.fluid, backend=request.backend, pressure_pa_abs=pressure,
            temperature_k=temperature, density_kg_m3=pressure/(R*temperature),
            enthalpy_j_kg=CP*temperature,
            entropy_j_kg_k=CP*math.log(temperature/300.0)-R*math.log(pressure/1e5),
            phase="gas", viscosity_pa_s=1.8e-5, cp_j_kg_k=CP, cv_j_kg_k=CP-R,
            conductivity_w_m_k=0.026, speed_of_sound_m_s=math.sqrt((CP/(CP-R))*R*temperature),
            provider_version="analytic-reference", provider_revision="constant-cp-ideal-gas-v1",
        )


@pytest.mark.parametrize("ratio,efficiency,flow", [(2.0,1.0,0.0),(5.0,0.82,0.2),(12.0,0.67,1.4)])
def test_compression_matches_constant_cp_ideal_gas_closed_form(ratio, efficiency, flow):
    p1, t1 = 100000.0, 300.0
    p2 = ratio*p1
    k = CP/(CP-R)
    t2s = t1*ratio**((k-1.0)/k)
    t2 = t1+(t2s-t1)/efficiency
    expected_specific_work = CP*(t2s-t1)/efficiency
    result = compression(Compression(
        fluid="Air", inlet_pressure_pa_abs=p1, inlet_temperature_k=t1,
        outlet_pressure_pa_abs=p2, mass_flow_kg_s=flow,
        isentropic_efficiency=efficiency,
    ), property_provider=AnalyticIdealGas())
    assert result.status == "computed"
    assert result.values["specific_work_j_kg"] == pytest.approx(expected_specific_work, rel=2e-13)
    assert result.values["shaft_power_w"] == pytest.approx(flow*expected_specific_work, rel=2e-13, abs=1e-12)
    assert result.values["outlet_temperature_k"] == pytest.approx(t2, rel=2e-13)
    assert result.values["outlet_enthalpy_j_kg"] == pytest.approx(CP*t2, rel=2e-13)
    assert result.values["outlet_temperature_k"] >= t2s
