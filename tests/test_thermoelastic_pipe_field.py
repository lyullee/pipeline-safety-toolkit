import numpy as np
import pytest

from pipeline_integrity.thermoelastic_pipe_field import (
    ThermoelasticWall, ThermoelasticPipeFieldRequest, _physical, _recover,
    solve_thermoelastic_pipe_field,
)


def request(**updates):
    wall = ThermoelasticWall(reference_area_m2=.01, reference_temperature_K=300.,
        area_compliance_m2_pa=1e-10, natural_area_temperature_derivative_m2_K=2e-7,
        heat_capacity_per_length_J_m_K=2000., exterior_pressure_pa_abs=100000.,
        minimum_pressure_pa_abs=110000., maximum_pressure_pa_abs=1000000.,
        minimum_temperature_K=250., maximum_temperature_K=400.,
        maximum_reference_area_fractional_change=.05, constitutive_evidence_id='elastic-wall')
    values = dict(id='field', length_m=10., gas_constant_J_kg_K=287., heat_capacity_ratio=1.4,
        wall=wall, fluid_wall_conductance_W_m_K=5.,
        initial_pressure_pa_abs=(200000.,)*5, initial_fluid_temperature_K=(300.,)*5,
        initial_wall_temperature_K=(300.,)*5, initial_velocity_m_s=(0.,)*5,
        end_time_s=.001, maximum_step_s=.0001, cfl=.2,
        fixed_axial_length_quasistatic_wall_and_ideal_gas_reviewed=True)
    values.update(updates)
    return ThermoelasticPipeFieldRequest(**values)


def test_state_recovery_and_thermodynamic_wall_identity():
    q = request()
    state, physical = _physical(200000., 310., 320., 2., q)
    recovered = _recover(state, q)
    assert recovered == pytest.approx(physical, rel=2e-10)
    # Coupled wave speed must lie below ideal-fluid sound speed for Ceff>0.
    assert 0 < recovered[5] < np.sqrt(q.heat_capacity_ratio*q.gas_constant_J_kg_K*310.)


def test_uniform_closed_equilibrium_retains_fields_and_budgets():
    result = solve_thermoelastic_pipe_field(request())
    assert result.final_pressure_pa_abs == pytest.approx((200000.,)*5, rel=1e-9)
    assert result.final_wall_temperature_K == pytest.approx((300.,)*5, rel=1e-9)
    assert abs(result.energy_balance_residual_J) < 1e-7
    assert abs(result.mass_balance_residual_kg) < 1e-12


def test_wall_heating_expands_area_without_creating_combined_energy():
    q = request(initial_wall_temperature_K=(320.,)*5)
    result = solve_thermoelastic_pipe_field(q)
    assert max(result.final_wall_temperature_K) < 320.
    assert min(result.final_fluid_temperature_K) > 300.
    assert abs(result.energy_balance_residual_J) < 1e-7
    assert abs(result.mass_balance_residual_kg) < 1e-12
    assert result.numerical_qualification_complete is False
