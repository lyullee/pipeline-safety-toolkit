"""Closed-loop recycle command oracle for a flat-map synthetic compressor.

The compressor map and PID parameters are authored fixtures. The compressor
train thermodynamics still run through the pinned native property owner.
"""

import runpy

import pytest

from pipeline_integrity.compressor_control import (
    ControlledCompressorRequest,
    evaluate_controlled_compressor,
)
from pipeline_integrity.control_exchange import ControlAxis, PIDGains, PIDSession
from pipeline_integrity.contracts import RunContext


COMPRESSOR_CASES = runpy.run_path("tests/test_compressor_cavity_validation.py")
CONTROL_EXAMPLE = runpy.run_path("examples/control_loop.py")


def test_pressure_pid_command_is_held_as_recycle_and_energy_balance_closes():
    plant, context = COMPRESSOR_CASES["compressor_case"](0.05, 0.0)
    base_loop, _ = CONTROL_EXAMPLE["make_loop"](quantity="pressure", output_quantity="fraction")
    loop = base_loop.model_copy(
        update={
            "actuator_id": "recycle-valve",
            "measurement_axis": ControlAxis(
                quantity="pressure",
                unit="Pa",
                pressure_reference="absolute",
                reference_id=plant.base.target_port_id,
            ),
            "output_axis": ControlAxis(
                quantity="fraction", unit="1", reference_id="recycle-valve"
            ),
            "gains": PIDGains(
                proportional=1e-6,
                integral=0.0,
                derivative=0.0,
                proportional_unit="(1)/(Pa)",
                integral_unit="(1)/(Pa*s)",
                derivative_unit="(1)*s/(Pa)",
            ),
            "output_minimum": 0.0,
            "output_maximum": 1.0,
            "initial_output": 0.0,
            "parameter_evidence_id": "review",
            "maximum_scan_interval_s": 0.05,
        }
    )
    pid_context = RunContext(
        asset_id=loop.loop_id,
        scenario_id=context.scenario_id,
        clock_id=context.clock_id,
        origin=context.origin,
        evidence=context.evidence,
    )
    provider_identity = PIDSession(loop, pid_context).provider
    request = ControlledCompressorRequest(
        id="synthetic-pressure-recycle-loop",
        plant=plant,
        controller=loop,
        pid_provider_identity=provider_identity,
        measurement="outlet_total_pressure_pa_abs",
        setpoint=130_000.0,
        scan_interval_s=0.05,
        maximum_recycle_mass_flow_kg_s=0.005,
        mapping_evidence_id="review",
        ideal_instantaneous_recycle_flow_actuator_reviewed=True,
        measurement_datum_and_control_direction_reviewed=True,
    )

    record = evaluate_controlled_compressor(request, context)
    assert record["status"] == "computed_diagnostic"
    assert len(record["intervals"]) == 2
    assert record["intervals"][0]["held_command_fraction"] == 0.0
    first_decision = record["decisions"][0]
    # The flat synthetic map holds outlet total pressure at 120 kPa. With zero
    # I/D gains, Kp=1e-6 fraction/Pa and a 130 kPa setpoint, the native PID's
    # first command is the independently calculated proportional response.
    assert first_decision["output"] == pytest.approx(0.01, abs=1e-10)
    assert record["intervals"][1]["held_command_fraction"] == pytest.approx(0.01, abs=1e-10)
    assert record["intervals"][1]["actual_recycle_mass_flow_kg_s"] == pytest.approx(
        0.005 * 0.01, abs=1e-12
    )
    assert all(0.0 <= decision["output"] <= 1.0 for decision in record["decisions"])
    ledger = record["energy_ledger_J"]
    # Sum the rotor balance and the gas enthalpy balance. Shaft power exits the
    # rotor while gas power enters the fluid; subtracting mechanical loss again
    # would double-count the difference between those two boundaries.
    residual = (
        ledger["initial_rotor"]
        + ledger["drive"]
        - ledger["final_rotor"]
        - ledger["shaft_load"]
        + ledger["gas"]
        - ledger["drag"]
        - ledger["train_cooling"]
        - ledger["recycle_conditioning"]
        - ledger["net_enthalpy"]
    )
    assert residual == pytest.approx(ledger["overall_residual"], abs=1e-10)
    assert abs(residual) <= record["energy_tolerance_J"]
    assert record["actuator_model"] == "ideal_instantaneous_recycle_flow; held_between_samples"
