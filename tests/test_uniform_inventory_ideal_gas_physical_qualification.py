"""Uniform inventory closure against the ideal-gas equation of state."""
import math
import pytest
from pipeline_integrity.models.thermal import Inventory, inventory
from pipeline_integrity.thermo import StateRequest, ThermodynamicState

R=287.05
CP=1005.0
class AnalyticGas:
    def resolve(self, request: StateRequest):
        p=request.inputs['P']; t=request.inputs['T']
        return ThermodynamicState(fluid=request.fluid,backend=request.backend,
            pressure_pa_abs=p,temperature_k=t,density_kg_m3=p/(R*t),
            enthalpy_j_kg=CP*t,entropy_j_kg_k=CP*math.log(t/300)-R*math.log(p/1e5),
            phase='gas',viscosity_pa_s=1.8e-5,cp_j_kg_k=CP,cv_j_kg_k=CP-R,
            conductivity_w_m_k=.026,speed_of_sound_m_s=math.sqrt(CP/(CP-R)*R*t),
            provider_version='analytic-reference',provider_revision='ideal-gas-v1')

@pytest.mark.parametrize('pressure,temperature,volume,reference_pressure,reference_temperature',[
 (1e5,300.0,2.0,1e5,300.0),
 (4e5,360.0,0.75,1.01325e5,273.15),
 (8e5,420.0,3.25,2e5,320.0),
])
def test_uniform_inventory_matches_ideal_gas_mass_and_reference_volume(pressure,temperature,volume,reference_pressure,reference_temperature):
    result=inventory(Inventory(fluid='Air',pressure_pa_abs=pressure,temperature_k=temperature,
        internal_volume_m3=volume,reference_pressure_pa_abs=reference_pressure,
        reference_temperature_k=reference_temperature),property_provider=AnalyticGas()).values
    expected_mass=pressure*volume/(R*temperature)
    expected_reference_volume=expected_mass*R*reference_temperature/reference_pressure
    assert result['density_kg_m3']==pytest.approx(pressure/(R*temperature),rel=2e-14)
    assert result['mass_kg']==pytest.approx(expected_mass,rel=2e-14)
    assert result['reference_volume_m3']==pytest.approx(expected_reference_volume,rel=2e-14)
    assert result['specific_enthalpy_j_kg']==pytest.approx(CP*temperature,rel=2e-14)


def test_uniform_inventory_is_linear_in_vessel_volume():
    args=dict(fluid='Air',pressure_pa_abs=250000.,temperature_k=330.,
        reference_pressure_pa_abs=101325.,reference_temperature_k=273.15)
    a=inventory(Inventory(internal_volume_m3=1.0,**args),property_provider=AnalyticGas()).values
    b=inventory(Inventory(internal_volume_m3=3.0,**args),property_provider=AnalyticGas()).values
    assert b['mass_kg']==pytest.approx(3*a['mass_kg'],rel=2e-14)
    assert b['reference_volume_m3']==pytest.approx(3*a['reference_volume_m3'],rel=2e-14)
