"""Laminar vertical-cylinder drainage has an independent exponential solution.

Poiseuille flow gives Q=pi*d**4*rho*g*h/(128*mu*L). With V=A*h,
the liquid inventory decays exponentially and the stop-volume event time
is logarithmic. This checks the midpoint integration and its shortened final
step against a continuous physical reference, without calling the flow owner
to build the oracle. Applicability is laminar, incompressible quasi-steady flow.
"""
import math

import pytest

from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.tank_drainage import DrainageRequest, evaluate_tank_drainage
from pipeline_integrity.tanks import AxisymmetricTank


def test_laminar_drainage_stop_time_has_second_order_refinement_and_conserved_inventory():
    area = math.pi / 4.0
    pipe_diameter, viscosity, density, gravity, length = 0.02, 1.0, 1000.0, 9.80665, 1.0
    conductance = math.pi * pipe_diameter**4 / (128.0 * viscosity * length)
    rate = conductance * density * gravity / area
    initial_volume, exact_elapsed, start_time = 1.0, 10000.0, 101.0
    stop_volume = initial_volume * math.exp(-rate * exact_elapsed)
    context = RunContext(asset_id='laminar_tank', scenario_id='stop', clock_id='seconds',
        origin='simulation', evidence=(EvidenceRef(id='reference', origin='synthetic',
        citation='Authored vertical-cylinder Poiseuille drainage reference'),))
    errors = []
    for step in (1000.0, 500.0, 250.0):
        request = DrainageRequest(id='drain', asset_id='laminar_tank', clock_id='seconds',
            geometry=AxisymmetricTank(inner_diameter_m=1.0, straight_length_m=2.0,
                horizontal=False, evidence_id='reference'),
            initial_volume_m3=initial_volume, stop_volume_m3=stop_volume,
            start_time_s=start_time, duration_s=20000.0, maximum_step_s=step,
            tank_gas_pressure_pa_abs=100000.0, downstream_pressure_pa_abs=100000.0,
            tank_bottom_minus_outlet_elevation_m=0.0, pipe_length_m=length,
            pipe_diameter_m=pipe_diameter, roughness_m=0.0, minor_loss_coefficient=0.0,
            density_kg_m3=density, viscosity_pa_s=viscosity,
            maximum_volume_flow_m3_s=0.0001, gravity_m_s2=gravity,
            flow_absolute_tolerance_m3_s=1e-15, volume_absolute_tolerance_m3=1e-12,
            transition_policy='withhold', quasi_steady_nonflashing_single_phase_reviewed=True,
            evidence_ids=('reference',))
        record = evaluate_tank_drainage(request, context)
        assert record['status'] == 'computed'
        assert record['stop_volume_reached'] is True
        for row in record['states']:
            expected_flow = conductance * density * gravity * row['volume_m3'] / area
            assert row['outflow_m3_s'] == pytest.approx(expected_flow, rel=1e-8)
            reynolds = 4.0 * density * expected_flow / (math.pi * pipe_diameter * viscosity)
            assert 0.0 < reynolds < 100.0
            exact_volume = initial_volume * math.exp(-rate * (row['time_s'] - start_time))
            assert row['volume_m3'] == pytest.approx(exact_volume, rel=0.0003)
        final = record['states'][-1]
        assert final['volume_m3'] == stop_volume
        assert record['discharged_volume_m3'] == pytest.approx(initial_volume - stop_volume, abs=1e-10)
        assert abs(record['volume_residual_m3']) < 1e-10
        errors.append(abs(final['time_s'] - start_time - exact_elapsed))
    assert errors[0] > errors[1] > errors[2] > 0.0
    for coarse, fine in zip(errors, errors[1:]):
        assert 3.5 < coarse / fine < 4.5
