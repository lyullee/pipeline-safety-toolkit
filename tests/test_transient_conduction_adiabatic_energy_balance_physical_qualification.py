"""Adiabatic two-cell diffusion exactness and roundoff-safe energy closure."""
import pytest
from matintegrity.models.thermal_transient import ThermalBoundary,ThermalMaterialProperties,TransientConduction1D,transient_conduction_1d


def solve():
    area=1.0; length=1.0; conductivity=10.0; volumetric_capacity=1000.0
    initial=[400.0,300.0]; dt=0.25
    material=ThermalMaterialProperties(material_id='constant',temperature_K=[250.,500.],
        conductivity_W_m_K=[conductivity,conductivity],
        volumetric_heat_capacity_J_m3_K=[volumetric_capacity,volumetric_capacity])
    model=TransientConduction1D(geometry='planar',cell_edges_m=[0.,.5,1.],planar_area_m2=area,
        materials=[material],cell_material_ids=['constant','constant'],initial_temperature_K=initial,
        time_s=[0.,1.],left_boundary=ThermalBoundary(boundary_id='left',kind='adiabatic'),
        right_boundary=ThermalBoundary(boundary_id='right',kind='adiabatic'),maximum_step_s=dt,
        one_dimensional_geometry_reviewed=True,material_property_support_reviewed=True,
        boundary_conditions_reviewed=True,phase_change_absent_or_included_in_capacity_reviewed=True)
    return transient_conduction_1d(model),initial,conductivity,volumetric_capacity,area,length,dt


def test_adiabatic_two_cell_diffusion_preserves_energy_and_exact_implicit_solution():
    result,initial,k,cv,area,length,dt=solve()
    capacity=cv*area*length/2
    conductance=k*area/(length/2)
    alpha=2*conductance/capacity
    gap=initial[0]-initial[1]
    mean=sum(initial)/2
    for _ in range(round(1/dt)):
        gap=gap/(1+alpha*dt)
    expected=[mean+gap/2,mean-gap/2]
    assert result.status=='computed',result.values
    final=result.values['temperature_history_K'][-1]
    assert final==pytest.approx(expected,rel=2e-12)
    assert capacity*sum(final)==pytest.approx(capacity*sum(initial),abs=1e-9)
    assert result.values['maximum_absolute_discrete_energy_balance_error_J']<1e-8
    assert result.values['maximum_relative_discrete_energy_balance_error']<1e-7
