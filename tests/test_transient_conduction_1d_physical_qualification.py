"""Exact forced two-cell diffusion eigenmode and backward-Euler energy checks."""
import math
import pytest
from matintegrity.models.thermal_transient import (
    ThermalBoundary, ThermalMaterialProperties, TransientConduction1D,
    transient_conduction_1d,
)

AREA=1.0
LENGTH=1.0
K=10.0
CV=1000.0
FLUX=100.0
INITIAL=(400.0,300.0)
MEAN=sum(INITIAL)/2
INITIAL_GAP=INITIAL[0]-INITIAL[1]
CELL_CAPACITY=CV*AREA*LENGTH/2
INTERNAL_G=K*AREA/(LENGTH/2)
ALPHA=2*INTERNAL_G/CELL_CAPACITY

def solve(max_step):
    material=ThermalMaterialProperties(
        material_id='constant',temperature_K=[250.0,500.0],
        conductivity_W_m_K=[K,K],
        volumetric_heat_capacity_J_m3_K=[CV,CV],
    )
    model=TransientConduction1D(
        geometry='planar',cell_edges_m=[0.0,0.5,1.0],planar_area_m2=AREA,
        materials=[material],cell_material_ids=['constant','constant'],
        initial_temperature_K=list(INITIAL),time_s=[0.0,1.0],
        left_boundary=ThermalBoundary(boundary_id='left',kind='prescribed_heat_flux',
            heat_flux_into_solid_W_m2=[FLUX,FLUX]),
        right_boundary=ThermalBoundary(boundary_id='right',kind='adiabatic'),
        maximum_step_s=max_step,
        one_dimensional_geometry_reviewed=True,
        material_property_support_reviewed=True,
        boundary_conditions_reviewed=True,
        phase_change_absent_or_included_in_capacity_reviewed=True,
    )
    return transient_conduction_1d(model)

def expected_backward_euler(max_step):
    count=round(1.0/max_step)
    dt=1.0/count
    gap=INITIAL_GAP
    mean=MEAN
    for _ in range(count):
        gap=(gap+FLUX*AREA*dt/CELL_CAPACITY)/(1.0+ALPHA*dt)
        mean+=FLUX*AREA*dt/(2*CELL_CAPACITY)
    return [mean+gap/2,mean-gap/2]

def test_two_cell_forced_diffusion_matches_discrete_backward_euler_solution():
    result=solve(0.25)
    assert result.status=='computed',result.values
    assert result.values['temperature_history_K'][-1]==pytest.approx(expected_backward_euler(0.25),rel=2e-12)
    assert result.values['internal_steps']==4

def test_forced_diffusion_closes_global_energy_and_boundary_heat_input():
    result=solve(0.25)
    final=result.values['temperature_history_K'][-1]
    initial_energy=CELL_CAPACITY*sum(INITIAL)
    final_energy=CELL_CAPACITY*sum(final)
    assert final_energy-initial_energy==pytest.approx(FLUX*AREA*1.0,rel=2e-12)
    assert result.values['cumulative_boundary_energy_into_solid_J']==pytest.approx([FLUX*AREA,0.0],rel=2e-12)
    assert result.values['maximum_relative_discrete_energy_balance_error']<=1e-10

def test_halving_time_step_converges_toward_continuous_two_cell_solution():
    coarse=solve(0.25).values['temperature_history_K'][-1]
    fine=solve(0.125).values['temperature_history_K'][-1]
    continuous_gap=INITIAL_GAP*math.exp(-ALPHA)
    equilibrium_gap=FLUX*AREA/(2*INTERNAL_G)
    continuous_gap+=equilibrium_gap*(1-math.exp(-ALPHA))
    continuous_mean=MEAN+FLUX*AREA/(2*CELL_CAPACITY)
    exact=[continuous_mean+continuous_gap/2,continuous_mean-continuous_gap/2]
    coarse_error=max(abs(a-b) for a,b in zip(coarse,exact,strict=True))
    fine_error=max(abs(a-b) for a,b in zip(fine,exact,strict=True))
    assert fine_error<coarse_error
    assert fine==pytest.approx(expected_backward_euler(0.125),rel=2e-12)
