"""Finite-time relay opening from an independently inverted real-EOS energy path.

Both phase inventories are fixed before opening, all interfacial/ambient heat
exchange is disabled, and only the vapor receives heat. At the target pressure,
solve liquid T from its fixed specific U, then gas T from the remaining tank
volume. The required gas energy divided by imposed heating gives event time
without invoking the tank integrator or its event search.
"""
import hashlib
import importlib.util
import math
from pathlib import Path

import pytest

from pipeline_integrity import tank_breathing as tb
from pipeline_integrity.contracts import EvidenceRef
from pipeline_integrity.hysteretic_tank import (
    REVIEWS, HystereticTankRequest, bind_hysteretic_tank_context,
    evaluate_hysteretic_tank, hysteretic_tank_metadata,
)
from pipeline_integrity.thermo import StateRequest

_spec = importlib.util.spec_from_file_location('relay_reference_example',
    Path(__file__).resolve().parents[1] / 'examples' / 'tank_breathing.py')
_example = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_example)


@pytest.mark.parametrize('scan_divisions', [2.0, 4.0])
def test_real_eos_heating_opens_relay_at_independent_energy_threshold_and_preserves_ledgers(scan_divisions):
    import CoolProp.CoolProp as CP
    from scipy.optimize import brentq

    fluid, initial_pressure, opening_pressure, heating = 'ParaHydrogen', 150000.0, 151000.0, 100.0
    saturation = CP.PropsSI('T', 'P', initial_pressure, 'Q', 0.0, fluid)
    liquid_temperature, vapor_temperature = saturation - 0.5, saturation + 0.5
    capacity = math.pi / 4.0 * 2.0
    liquid_mass = capacity / 2.0 * CP.PropsSI('Dmass', 'P', initial_pressure, 'T', liquid_temperature, fluid)
    vapor_mass = capacity / 2.0 * CP.PropsSI('Dmass', 'P', initial_pressure, 'T', vapor_temperature, fluid)
    liquid_u = CP.PropsSI('Umass', 'P', initial_pressure, 'T', liquid_temperature, fluid)
    vapor_u = CP.PropsSI('Umass', 'P', initial_pressure, 'T', vapor_temperature, fluid)
    target_saturation = CP.PropsSI('T', 'P', opening_pressure, 'Q', 0.0, fluid)
    target_liquid_temperature = brentq(
        lambda t: CP.PropsSI('Umass', 'P', opening_pressure, 'T', t, fluid) - liquid_u,
        target_saturation - 2.0, target_saturation - 0.001, xtol=1e-11)
    target_liquid_density = CP.PropsSI('Dmass', 'P', opening_pressure, 'T', target_liquid_temperature, fluid)
    target_vapor_density = vapor_mass / (capacity - liquid_mass / target_liquid_density)
    target_vapor_temperature = brentq(
        lambda t: CP.PropsSI('Dmass', 'P', opening_pressure, 'T', t, fluid) - target_vapor_density,
        target_saturation + 0.001, target_saturation + 5.0, xtol=1e-11)
    target_vapor_u = CP.PropsSI('Umass', 'P', opening_pressure, 'T', target_vapor_temperature, fluid)
    opening_time = vapor_mass * (target_vapor_u - vapor_u) / heating
    assert opening_time > 0.0
    base, context = _example.make_case(mode='pressure_relief_hysteresis')
    initial = tb.TankBreathingState(liquid_mass_kg=liquid_mass, liquid_internal_energy_J=liquid_mass * liquid_u,
        vapor_mass_kg=vapor_mass, vapor_internal_energy_J=vapor_mass * vapor_u,
        lower_wall_temperature_K=25.0, upper_wall_temperature_K=25.0)
    closures = base.closures.model_copy(update=dict(lower_ambient_UA_W_K=0.0, upper_ambient_UA_W_K=0.0,
        wall_liquid_U_W_m2K=0.0, wall_vapor_U_W_m2K=0.0, liquid_interface_UA_W_K=0.0,
        vapor_interface_UA_W_K=0.0, wall_axial_UA_W_K=0.0))
    window = base.windows[0].model_copy(update=dict(duration_s=opening_time * 1.02,
        timestep_s=opening_time / scan_divisions, maximum_reviewed_step_s=opening_time / scan_divisions,
        direct_vapor_heat_W=heating, direct_lower_wall_heat_W=20.0, direct_upper_wall_heat_W=-10.0))
    vent = base.vent.model_copy(update=dict(downstream_state=StateRequest(fluid=fluid,
        inputs={'P': 100000.0, 'T': 300.0}), full_open_area_m2=1e-8,
        discharge_coefficient=0.8, closed_leakage_CdA_m2=0.0))
    base = base.model_copy(update=dict(initial_state=initial, initial_pressure_pa_abs=None,
        initial_liquid_volume_fraction=None, initial_lower_wall_temperature_K=None,
        initial_upper_wall_temperature_K=None, closures=closures, windows=(window,), vent=vent,
        reviews=dict.fromkeys(tb.REVIEWS, True), review_evidence_ids=dict.fromkeys(tb.REVIEWS, 'authored_boundary'),
        owner_pins=tb.native_backend_metadata()))
    evidence = EvidenceRef(id='authored_boundary', origin='simulation',
        citation='Authored real-EOS fixed-inventory heating and relay mapping fixture',
        sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    context = context.model_copy(update={'origin': 'simulation', 'evidence': (evidence,)})
    request = HystereticTankRequest(id='energy_threshold', base=base,
        opening_pressure_pa_abs=opening_pressure, reseat_pressure_pa_abs=149000.0,
        initial_relay_state='closed', relay_mapping_evidence_id=evidence.id, evidence_ids=(evidence.id,),
        reviews=dict.fromkeys(REVIEWS, True), review_evidence_ids=dict.fromkeys(REVIEWS, evidence.id),
        controller_owner_pins=hysteretic_tank_metadata(), event_time_absolute_tolerance_s=1e-7,
        event_pressure_absolute_tolerance_Pa=0.01)
    context = bind_hysteretic_tank_context(request, context)
    record = evaluate_hysteretic_tank(request, context)
    assert record['status'] == 'computed_diagnostic', record['issues']
    assert len(record['relay_events']) == 1
    event = record['relay_events'][0]
    assert event['kind'] == 'opening'
    assert event['time_s'] == pytest.approx(opening_time, rel=2e-4, abs=2e-6)
    lower, upper = event['time_bracket_s']
    assert upper - lower <= 1.01e-7
    assert event['pressure_pa_abs'] == pytest.approx(opening_pressure, abs=0.012)
    initial_total_energy = liquid_mass * liquid_u + vapor_mass * vapor_u + 2.0 * 10000.0 * 25.0
    for row in record['history']:
        state = row['state']
        assert state['liquid_mass_kg'] == pytest.approx(liquid_mass, abs=1e-9)
        assert state['liquid_internal_energy_J'] == pytest.approx(liquid_mass * liquid_u, abs=1e-4)
        total_mass = state['liquid_mass_kg'] + state['vapor_mass_kg']
        assert total_mass - liquid_mass - vapor_mass == pytest.approx(row['cumulative_boundary_mass_kg'], abs=1e-8)
        total_energy = (state['liquid_internal_energy_J'] + state['vapor_internal_energy_J']
            + 10000.0 * (state['lower_wall_temperature_K'] + state['upper_wall_temperature_K']))
        assert total_energy - initial_total_energy == pytest.approx(row['cumulative_boundary_energy_J'], abs=0.002)
        assert state['lower_wall_temperature_K'] == pytest.approx(25.0 + 20.0 * row['time_s'] / 10000.0, abs=1e-8)
        assert state['upper_wall_temperature_K'] == pytest.approx(25.0 - 10.0 * row['time_s'] / 10000.0, abs=1e-8)
        if row['time_s'] < opening_time * (1.0 - 2e-4):
            assert state['vapor_mass_kg'] == pytest.approx(vapor_mass, abs=1e-9)
            assert state['vapor_internal_energy_J'] == pytest.approx(vapor_mass * vapor_u + heating * row['time_s'], abs=1e-4)
    assert record['values']['final_relay_state'] == 'open'
    assert abs(record['values']['global_mass_residual_kg']) <= record['values']['global_mass_balance_limit_kg']
    assert abs(record['values']['global_energy_residual_J']) <= record['values']['global_energy_balance_limit_J']
