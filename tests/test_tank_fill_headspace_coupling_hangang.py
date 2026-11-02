"""Synthetic tank-fill/headspace coupling checked against an adiabatic ideal-gas oracle.

The liquid rate is prescribed and the tank is rigid; these are not measured
facility data or a pump/pressure-feedback qualification.
"""
import math

import pytest

from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.gas_headspace import HeadspaceRequest, HeadspaceWindow
from pipeline_integrity.tank_filling import (
    FillHeadspaceRequest, FillWindow, TankFillRequest, evaluate_fill_headspace,
    verify_fill_headspace_record,
)
from pipeline_integrity.tanks import AxisymmetricTank


def _case(maximum_step_s):
    evidence = tuple(EvidenceRef(id=identity, citation="Authored synthetic fill/headspace reference",
                                 origin="synthetic")
                     for identity in ("geometry", "liquid-flow", "gas-state", "coupling-review"))
    context = RunContext(asset_id="rigid-tank", scenario_id="adiabatic-fill", clock_id="elapsed",
                         origin="synthetic", evidence=evidence)
    diameter, height = 1.0, 2.0
    area = math.pi * diameter**2 / 4.0
    capacity = area * height
    initial_liquid = area * 0.5
    duration, inflow = 10.0, 0.01
    window = FillWindow(id="fill-window", duration_s=duration, liquid_inflow_m3_s=inflow,
                        evidence_id="liquid-flow")
    filling = TankFillRequest(id="fill", asset_id="rigid-tank", clock_id="elapsed",
        geometry=AxisymmetricTank(inner_diameter_m=diameter, straight_length_m=height, horizontal=False,
                                  evidence_id="geometry"),
        initial_liquid_volume_m3=initial_liquid, liquid_density_kg_m3=1000.0,
        windows=(window,), level_limits_m=(0.55, 0.60), evidence_ids=("coupling-review",))
    gas_R, cv, mass, temperature = 287.0, 718.0, 1.0, 300.0
    gas_window = HeadspaceWindow(id="fill-window", duration_s=duration,
        inlet_temperature_K=temperature, ambient_temperature_K=temperature)
    gas = HeadspaceRequest(id="headspace", asset_id="rigid-tank", clock_id="elapsed",
        volume_m3=capacity-initial_liquid, gas_constant_J_kg_K=gas_R, cv_J_kg_K=cv,
        initial_mass_kg=mass, initial_temperature_K=temperature, maximum_step_s=maximum_step_s,
        windows=(gas_window,), evidence_ids=("gas-state", "coupling-review"))
    return FillHeadspaceRequest(filling=filling, headspace=gas), context, (area, capacity,
        initial_liquid, duration, inflow, gas_R, cv, mass, temperature)


def test_fill_displacement_matches_adiabatic_ideal_gas_and_exact_level_events():
    request, context, constants = _case(0.25)
    area, capacity, initial_liquid, duration, inflow, gas_R, cv, mass, temperature = constants
    record = evaluate_fill_headspace(request, context)
    liquid, gas = record["liquid"], record["gas"]
    final_liquid = initial_liquid + inflow * duration
    initial_gas = capacity - initial_liquid
    final_gas = capacity - final_liquid
    gamma = 1.0 + gas_R / cv
    p0 = mass * gas_R * temperature / initial_gas
    expected_pressure = p0 * (initial_gas / final_gas) ** gamma
    expected_temperature = temperature * (initial_gas / final_gas) ** (gamma - 1.0)

    assert record["status"] == "computed"
    assert liquid["states"][-1]["liquid_volume_m3"] == pytest.approx(final_liquid, abs=1e-14)
    assert liquid["states"][-1]["level_m"] == pytest.approx(final_liquid / area, abs=2e-12)
    assert liquid["volume_residual_m3"] == pytest.approx(0.0, abs=1e-14)
    assert gas["states"][-1]["volume_m3"] == pytest.approx(final_gas, abs=1e-14)
    assert gas["states"][-1]["pressure_pa_abs"] == pytest.approx(expected_pressure, rel=2e-8)
    assert gas["states"][-1]["temperature_K"] == pytest.approx(expected_temperature, rel=2e-8)
    assert gas["states"][-1]["mass_kg"] == pytest.approx(mass, abs=1e-14)
    assert abs(gas["energy_residual_J"]) < 1e-8
    assert abs(gas["ledger_energy_residual_J"]) < 1e-8

    events = {event["limit"]: event["time_s"] for event in liquid["level_events"]}
    assert events["lower"] == pytest.approx((area * 0.55 - initial_liquid) / inflow, abs=1e-12)
    assert events["upper"] == pytest.approx((area * 0.60 - initial_liquid) / inflow, abs=1e-12)
    assert verify_fill_headspace_record(record, request=request, context=context)


def test_adiabatic_headspace_pressure_converges_under_step_refinement():
    errors = []
    for maximum_step in (1.0, 0.5, 0.25):
        request, context, constants = _case(maximum_step)
        _, capacity, initial_liquid, duration, inflow, gas_R, cv, mass, temperature = constants
        gas = evaluate_fill_headspace(request, context)["gas"]
        initial_gas = capacity - initial_liquid
        final_gas = initial_gas - inflow * duration
        exact = (mass * gas_R * temperature / initial_gas) * (initial_gas / final_gas) ** (1 + gas_R / cv)
        errors.append(abs(gas["states"][-1]["pressure_pa_abs"] - exact))
    assert errors[2] < errors[1] < errors[0]
