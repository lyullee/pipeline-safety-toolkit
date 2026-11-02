"""Synthetic analytical oracle for compressor transient state-to-signal export.

This checks the public signal adapter against the source model states and a
flat-map constant-power rotor reference. It is not measured sensor evidence.
"""
import math
import runpy

import pytest

from pipeline_integrity.compressor_dynamics import (
    compressor_dynamics_signal_frames,
    evaluate_compressor_dynamics,
)


_FIXTURE = runpy.run_path("tests/test_compressor_cavity_validation.py")


def test_compressor_signal_frames_preserve_analytic_coastdown_state_and_clock():
    request, context = _FIXTURE["compressor_case"](0.025, 0.0)
    record = evaluate_compressor_dynamics(request, context)
    assert record["status"] == "computed_diagnostic", record.get("failure")

    # Flat map gives constant gas power; this is an independent rotor-energy
    # solution, E(t)=E0-Pt and omega=sqrt(2E/I).
    import CoolProp.CoolProp as CP

    state = CP.AbstractState("HEOS", "Hydrogen")
    state.update(CP.PT_INPUTS, 1e5, 300.0)
    h0, s0 = state.hmass(), state.smass()
    state.update(CP.PSmass_INPUTS, 1.2e5, s0)
    power_W = 0.01 * (state.hmass() - h0) / 0.8

    frames, receipt = compressor_dynamics_signal_frames(record, stream_id="compressor-rotor")
    assert receipt["frame_count"] == len(record["states"]) == len(frames)
    assert receipt["clock_id"] == request.base.clock_id
    for row, frame in zip(record["states"], frames):
        assert frame.time_s == pytest.approx(row["time_s"], abs=1e-12)
        assert frame.clock_id == request.base.clock_id
        assert frame.stream_id == "compressor-rotor"
        assert frame.operating_mode == row["window_id"]
        assert frame.units == {
            "speed_rad_s": "rad/s",
            "rotor_energy_J": "J",
            "compressor_mass_flow_kg_s": "kg/s",
            "fresh_mass_flow_kg_s": "kg/s",
            "recycle_mass_flow_kg_s": "kg/s",
        }
        assert all(frame.valid.values())
        for key in frame.units:
            assert frame.values[key] == pytest.approx(row[key], rel=1e-12, abs=1e-12)
        expected_E = 0.5 * request.rotor_inertia_kg_m2 * request.initial_speed_rad_s**2 - power_W * row["time_s"]
        assert frame.values["rotor_energy_J"] == pytest.approx(expected_E, abs=1e-3)
        assert frame.values["speed_rad_s"] == pytest.approx(
            math.sqrt(2.0 * expected_E / request.rotor_inertia_kg_m2), abs=1e-6
        )
