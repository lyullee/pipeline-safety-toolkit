"""Forward normal/mass rate against a separate engineering-unit gas-capacity law."""
import math

import pytest

from pipeline_integrity.gas_valve_forward_capacity import GasValveForwardCapacityRequest as Request
from pipeline_integrity.gas_valve_forward_capacity import evaluate_gas_valve_forward_capacity as evaluate
from pipeline_integrity.models.library_models import GasControlValve

BASE=dict(temperature_k=330.,molar_mass_kg_mol=.028,viscosity_pa_s=1.6e-5,heat_capacity_ratio=1.4,
    compressibility=.95,inlet_pressure_pa_abs=8e5,outlet_pressure_pa_abs=4e5,standard_volume_flow_m3_s=.2,
    reference_temperature_k=273.15,reference_pressure_pa_abs=101325.,inlet_diameter_m=.1,
    outlet_diameter_m=.1,valve_diameter_m=.1,pressure_recovery_factor=.85,valve_style_factor=.7,
    terminal_pressure_ratio_factor=.5)


def calc(state_changes=None,**changes):
    values=dict(coefficient_scale='Kv',sizing_reference_case=GasControlValve(**(BASE|(state_changes or {}))),
        supplied_coefficient=16.,normal_reference_density_kg_m3=1.25,normal_density_basis_id='authored_normal_gas_density',
        equal_bore_turbulent_and_reference_density_reviewed=True)
    values.update(changes)
    return evaluate(Request(**values))


def reference_normal_flow(p,kv):
    terminal=p['heat_capacity_ratio']/1.4*p['terminal_pressure_ratio_factor']
    x=min((p['inlet_pressure_pa_abs']-p['outlet_pressure_pa_abs'])/p['inlet_pressure_pa_abs'],terminal)
    expansion=1-x/(3*terminal)
    q_hour=24.6*kv*(p['inlet_pressure_pa_abs']/1000)*expansion
    q_hour*=math.sqrt(x/(p['molar_mass_kg_mol']*1000*p['temperature_k']*p['compressibility']))
    return q_hour/3600


@pytest.mark.parametrize('gamma',[1.2,1.4,1.6])
@pytest.mark.parametrize('ratio_to_terminal',[.5,1.1])
def test_forward_mass_and_normal_flow_match_separate_engineering_unit_expression(gamma,ratio_to_terminal):
    terminal=gamma/1.4*BASE['terminal_pressure_ratio_factor']
    changes=dict(heat_capacity_ratio=gamma,outlet_pressure_pa_abs=BASE['inlet_pressure_pa_abs']*(1-terminal*ratio_to_terminal))
    result=calc(changes)
    assert result.status=='computed',result.issues
    expected=reference_normal_flow(BASE|changes,16.)
    assert result.normal_volume_flow_m3_s==pytest.approx(expected,rel=5e-12)
    assert result.mass_flow_kg_s==pytest.approx(expected*1.25,rel=5e-12)
    assert result.kv==pytest.approx(16.,rel=5e-12)
    assert result.diagnostics['choked'] is (ratio_to_terminal>=1)


def test_conventional_cv_input_recovers_the_same_physical_capacity_after_exact_unit_conversion():
    cv=18.
    psi=.45359237*9.80665/.0254**2
    kv=cv*.003785411784*60*math.sqrt(1e5/psi)
    result=calc(coefficient_scale='Cv',supplied_coefficient=cv)
    assert result.status=='computed'
    assert result.normal_volume_flow_m3_s==pytest.approx(reference_normal_flow(BASE,kv),rel=5e-12)
    assert result.cv==pytest.approx(cv,rel=5e-12)


def test_forward_choked_mass_capacity_is_a_backpressure_plateau():
    a=calc(dict(outlet_pressure_pa_abs=.3*BASE['inlet_pressure_pa_abs']))
    b=calc(dict(outlet_pressure_pa_abs=.1*BASE['inlet_pressure_pa_abs']))
    assert a.status==b.status=='computed'
    assert a.mass_flow_kg_s==b.mass_flow_kg_s
    assert a.diagnostics['choked'] is b.diagnostics['choked'] is True


def test_unknown_reference_density_does_not_become_an_ideal_gas_default():
    result=calc(normal_reference_density_kg_m3=None)
    assert result.status=='needs_data'
    assert result.mass_flow_kg_s is None


def test_unequal_bores_are_not_silently_treated_as_neutral_geometry():
    assert calc(dict(inlet_diameter_m=.12)).status=='out_of_scope'


def test_unreviewed_reference_convention_is_withheld():
    assert calc(equal_bore_turbulent_and_reference_density_reviewed=False).status=='needs_data'


def test_laminar_reference_is_not_extrapolated_using_a_turbulent_linear_inverse():
    assert calc(dict(standard_volume_flow_m3_s=1e-9)).status=='out_of_scope'


def test_zero_declared_coefficient_has_zero_model_flow_without_inventing_a_choking_state():
    result=calc(supplied_coefficient=0.)
    assert result.status=='computed'
    assert result.mass_flow_kg_s==result.normal_volume_flow_m3_s==0
    assert result.diagnostics['choked'] is None
    assert result.manufacturer_or_device_qualification is False
