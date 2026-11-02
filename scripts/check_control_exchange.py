"""Run actual native PID, transmitter, gas-tank and stroke-limited valve feedback references."""

import argparse
import json
import math
import sys
from dataclasses import asdict
from pathlib import Path

import numpy as np
from check_integrations import revision

from pipeline_integrity.adapters import hra_signal_series, monitoring_batch
from pipeline_integrity.contracts import EvidenceRef, RunContext, SignalFrame, digest
from pipeline_integrity.control_exchange import ControlAxis, ControlObservation, PIDSession
from pipeline_integrity.datasets import file_digest
from pipeline_integrity.instrument_exchange import (
    FaultWindow,
    TransmitterScenario,
    packet_observation,
    replay_transmitter,
)
from pipeline_integrity.tank_data import CoupledTankReadRequest, coupled_tank_frames


def tank_feedback(quantity, dt, *, missing=False):
    from control_loop import make_loop, observe
    from lh2dt import HomogeneousTank, HydrogenProperties, MassEnergyFlow, TankGeometry, TankState

    loop, context = make_loop(
        quantity=quantity, output_quantity="mass_flow" if quantity == "pressure" else "power"
    )
    controller = PIDSession(loop, context)
    properties = HydrogenProperties("Hydrogen")
    volume = 0.02
    thermo = properties.from_pT(200000.0, 300.0)
    mass = thermo.density_kg_m3 * volume
    initial = TankState(mass, mass * thermo.specific_internal_energy_J_kg, 300.0)
    tank = HomogeneousTank(TankGeometry(volume, 20.0, 0.05, 2.0), properties)
    state = initial
    inlet = properties.from_pT(300000.0, 300.0)
    command, boundary_mass, boundary_energy = 0.0, 0.0, 0.0
    rows = []
    target = 250000.0 if quantity == "pressure" else 305.0
    for i in range(round(120.0 / dt)):
        time = (i + 1) * dt
        disturbance = 1e-6 if quantity == "pressure" and i * dt >= 40.0 else 0.0
        flows = (
            (
                MassEnergyFlow(command, inlet.specific_enthalpy_J_kg, "controlled_inlet"),
                MassEnergyFlow(-disturbance, 0.0, "declared_outlet_disturbance"),
            )
            if quantity == "pressure"
            else ()
        )
        result = tank.simulate(
            state,
            dt,
            dt / 4,
            300.0,
            lambda _t, _s, held_flows=flows: held_flows,
            wall_heat_callback=lambda _t, _s, _w, held_command=command: (
                held_command if quantity == "temperature" else 0.0
            ),
        )[-1]
        state = result.state
        boundary_mass += result.cumulative_boundary_mass_kg
        boundary_energy += result.cumulative_boundary_energy_J
        pv = result.thermo.pressure_Pa if quantity == "pressure" else result.thermo.temperature_K
        gap = missing and 30.0 <= time < 32.0
        decision = controller.step(time, target, observe(loop, context, time, None if gap else pv))
        # This reference explicitly applies zero command when the adapter withholds a result.
        # That plant policy is separate from the controller's needs_data status.
        command = 0.0 if decision["output"] is None else decision["output"]
        energy_residual = (
            state.hydrogen_internal_energy_J
            + 20.0 * state.wall_temperature_K
            - initial.hydrogen_internal_energy_J
            - 20.0 * initial.wall_temperature_K
            - boundary_energy
        )
        mass_residual = state.hydrogen_mass_kg - initial.hydrogen_mass_kg - boundary_mass
        rows.append(
            dict(
                time_s=time,
                process_value=pv,
                applied_command_for_next_interval=command,
                decision=decision,
                mass_residual_kg=mass_residual,
                energy_residual_j=energy_residual,
                native_step=asdict(result),
            )
        )
    max_mass = max(abs(r["mass_residual_kg"]) for r in rows)
    max_energy = max(abs(r["energy_residual_j"]) for r in rows)
    assert max_mass < 1e-12 and max_energy < 1e-7, (max_mass, max_energy)
    return rows, dict(
        final_value=rows[-1]["process_value"],
        target=target,
        final_error=rows[-1]["process_value"] - target,
        maximum_mass_residual_kg=max_mass,
        maximum_energy_residual_j=max_energy,
        sample_count=len(rows),
        withheld_decisions=sum(r["decision"]["output"] is None for r in rows),
        controller_and_plant_are_closed_loop=True,
        field_validation=False,
    )


def valve_feedback():
    from control_loop import make_loop, observe
    from lh2dt import HydrogenProperties, StrokeLimitedCommandedValve, Valve

    loop, context = make_loop(quantity="mass_flow", output_quantity="fraction")
    controller = PIDSession(loop, context)
    properties = HydrogenProperties("Hydrogen")
    left, right = properties.from_pT(300000.0, 300.0), properties.from_pT(100000.0, 300.0)
    actor = StrokeLimitedCommandedValve(Valve(5e-7, 0.8, properties), stroke_time_s=3.0, initial_opening=0.0)
    rows = []
    target = 3e-5
    for i in range(150):
        t = (i + 1) * 0.2
        actor.advance(0.2)
        flow = actor.evaluate(left, right).mass_flow_kg_s
        decision = controller.step(t, target, observe(loop, context, t, flow))
        before = actor.opening
        actor.set_target_opening(decision["output"])
        assert actor.opening == before
        rows.append(
            dict(
                time_s=t,
                measured_mass_flow_kg_s=flow,
                actual_opening=actor.opening,
                target_opening=actor.target_opening,
                decision=decision,
            )
        )
    return rows, dict(
        target_mass_flow_kg_s=target,
        final_mass_flow_kg_s=rows[-1]["measured_mass_flow_kg_s"],
        final_error_kg_s=rows[-1]["measured_mass_flow_kg_s"] - target,
        command_does_not_teleport_position=True,
        sample_count=len(rows),
        native_property_fluid="Hydrogen",
        field_validation=False,
    )


def instrument_checks():
    from control_loop import make_loop

    axis = ControlAxis(quantity="level", unit="m", reference_id="authored:bottom")
    ctx = RunContext(
        asset_id="LT",
        scenario_id="authored-step",
        clock_id="elapsed",
        origin="synthetic",
        evidence=(
            EvidenceRef(id="source", citation="Authored step truth", origin="synthetic"),
            EvidenceRef(id="parameters", citation="Authored fault parameters", origin="synthetic"),
        ),
    )
    source = tuple(
        SignalFrame(
            stream_id="truth",
            clock_id="elapsed",
            time_s=float(t),
            values={"level": 0.0 if t == 0 else 1.0},
            units={"level": "m"},
            valid={"level": True},
            origin="synthetic",
        )
        for t in range(11)
    )
    base = TransmitterScenario(
        instrument_id="LT",
        source_stream_id="truth",
        channel="level",
        axis=axis,
        lower_range=0.0,
        upper_range=1.0,
        initial_filtered_value=0.0,
        time_constant_s=2.0,
        parameter_evidence_id="parameters",
        source_evidence_id="source",
    )
    outputs = {}
    parameters = dict(
        lag={},
        drift={"bias_drift_per_s": 0.02},
        quantized={"resolution": 0.1},
        delayed={"delivery_delay_s": 2.0},
        dropout={"dropouts": (FaultWindow(start_s=2.0, end_s=4.0),)},
        negative_clock={"reported_clock_offset_s": -0.5, "reported_clock_id": "reported_clock"},
        clock_rate={"reported_clock_rate_error": 0.1, "reported_clock_id": "reported_clock"},
        saturation={"bias": 2.0},
        zero_lag={"time_constant_s": 0.0},
    )
    for name, changes in parameters.items():
        outputs[name] = replay_transmitter(base.model_copy(update=changes), source, ctx)
    exact = [0.0 if t <= 1 else 1 - math.exp(-(t - 1) / 2) for t in range(11)]
    error = max(
        abs(p["reading"]["filtered_process_value"] - ref)
        for p, ref in zip(outputs["lag"]["packets"], exact, strict=True)
    )
    assert error < 1e-14
    assert outputs["negative_clock"]["packets"][0]["frame"] is None
    assert all(p["reading"]["saturated"] for p in outputs["saturation"]["packets"])
    assert outputs["zero_lag"]["packets"][1]["reading"]["indicated_value"] == 1.0
    summary = {
        name: dict(
            samples=len(r["packets"]),
            invalid_frames=sum(p["frame"] is None or not p["frame"]["valid"]["level"] for p in r["packets"]),
            reported_final_time_s=r["packets"][-1]["reported_time_s"],
            record_digest=r["record_digest"],
        )
        for name, r in outputs.items()
    }
    for name, r in outputs.items():
        loop, _ = make_loop(quantity="level", output_quantity="fraction")
        loop = loop.model_copy(
            update={
                "measurement_stream_id": "LT",
                "measurement_channel": "level",
                "measurement_axis": axis,
                "parameter_evidence_id": "parameters",
            }
        )
        controller = PIDSession(loop, ctx.model_copy(update={"asset_id": loop.loop_id}))
        if any(p["reported_clock_id"] != loop.clock_id for p in r["packets"][1:]):
            decisions = [
                {"status": "needs_data", "reason": "reported_clock_requires_reviewed_mapping"}
                for _ in r["packets"][1:]
            ]
        else:
            decisions = [
                controller.step(
                    p["physical_source_time_s"],
                    0.5,
                    packet_observation(p, axis=axis, evidence_id="source"),
                )
                for p in r["packets"][1:]
            ]
        outputs[name] = {"replay": r, "controller_decisions": decisions}
        summary[name]["withheld_decisions"] = sum(d["status"] != "computed" for d in decisions)
        summary[name]["withheld_reasons"] = sorted(
            {d["reason"] for d in decisions if d["reason"] is not None}
        )
    assert summary["delayed"]["withheld_reasons"] == ["not_yet_available"]
    assert summary["clock_rate"]["withheld_reasons"] == ["reported_clock_requires_reviewed_mapping"]
    assert summary["dropout"]["withheld_decisions"] == 2
    return outputs, dict(
        cases=summary, maximum_exact_zoh_lag_error=error, noise_or_fault_probability_inferred=False
    )


def public_data(args):
    from control_loop import make_loop

    loop, template = make_loop(quantity="level", output_quantity="fraction")
    records, all_frames, counts = {}, None, {}
    for run in range(1, 11):
        read_request = CoupledTankReadRequest(
            run_number=run,
            level_unit="cm",
            level_zero_offset_m=0.0,
            unit_mapping_evidence_id="assumed:cm",
            datum_evidence_id="assumed:zero",
            source_origin="laboratory",
        )
        frames, mapping = coupled_tank_frames(args.raw_root, read_request)
        ctx = template.model_copy(
            update={
                "clock_id": frames[0].clock_id,
                "origin": "laboratory",
                "evidence": template.evidence
                + (
                    EvidenceRef(
                        id="assumed:cm",
                        citation="Declared cm interpretation for shadow commands",
                        origin="user_declared",
                    ),
                    EvidenceRef(
                        id="assumed:zero",
                        citation="Declared zero offset; not established internal bottom",
                        origin="user_declared",
                    ),
                    EvidenceRef(
                        id="coupled:source",
                        citation="Silva, Coelho and Leite, v1.0.0",
                        locator="https://doi.org/10.5281/zenodo.19856119",
                        sha256=file_digest(args.raw_root / "h1.csv"),
                        license_id="CC-BY-4.0",
                        origin="laboratory",
                    ),
                ),
            }
        )
        s = loop.model_copy(
            update={
                "measurement_stream_id": frames[0].stream_id,
                "measurement_channel": "tank1_level_m",
                "maximum_scan_interval_s": 5.0,
                "maximum_measurement_age_s": 0.5,
            }
        )
        c = PIDSession(s, ctx, initial_time_s=frames[0].time_s)
        # Source is already measured; this is a shadow command on the preserved history, not a new experiment.
        decisions = [
            c.step(
                f.time_s,
                0.474,
                ControlObservation(
                    frame=f,
                    axis=s.measurement_axis,
                    available_at_s=f.time_s,
                    evidence_id="coupled:source",
                    receipt={"unit_mapping_digest": digest(mapping)},
                ),
            )
            for f in frames[1:]
        ]
        records[str(run)] = dict(mapping=mapping, decisions=decisions)
        counts[str(run)] = len(decisions)
        if run == 1:
            all_frames = frames
    batch = monitoring_batch(all_frames)
    series = hra_signal_series(all_frames, "tank1_level_m")
    assert batch.metadata["clock_id"] == series.clock_id == all_frames[0].clock_id
    assert int(np.sum(~batch.valid[:, -1])) == 1
    return records, dict(
        experiments=10,
        shadow_decisions=sum(counts.values()),
        first_sample_used_as_initial_clock=True,
        source_clock_and_unit_mapping_preserved=True,
        source_origin="laboratory",
        output_origin="simulation",
        source_status="needs_data",
        setpoint_gains_and_datum_are_assumptions=True,
        observed_pump_commands_replaced=False,
        closed_loop_or_field_accuracy_validated=False,
        monitoring_rows=len(all_frames),
        hra_series_samples=len(series.time_s),
        monitoring_missing_final_command_preserved=True,
        native_monitoring_class=type(batch).__module__ + "." + type(batch).__name__,
        native_hra_class=type(series).__module__ + "." + type(series).__name__,
    )


def native_pid_checks():
    from control_loop import make_loop, observe

    loop, context = make_loop()
    saturated = PIDSession(loop, context)
    for t in range(1, 21):
        decision = saturated.step(float(t), 1e6, observe(loop, context, t, 200000.0))
        assert decision["output"] == loop.output_maximum
    assert saturated.checkpoint().memory.integral_error_s == 0.0
    derivative_loop = loop.model_copy(
        update={
            "gains": loop.gains.model_copy(update={"proportional": 0.0, "integral": 0.0, "derivative": 1e-9})
        }
    )
    derivative = PIDSession(derivative_loop, context)
    derivative.step(1.0, 250000.0, observe(loop, context, 1.0, 200000.0))
    jump = derivative.step(2.0, 500000.0, observe(loop, context, 2.0, 200000.0))
    assert jump["native"]["derivative_term"] == 0.0
    a, b = PIDSession(loop, context), PIDSession(loop, context)
    a.step(1.0, 250000.0, observe(loop, context, 1.0, 200000.0))
    b.restore(a.checkpoint())
    assert a.step(2.0, 250000.0, observe(loop, context, 2.0, 200100.0)) == b.step(
        2.0, 250000.0, observe(loop, context, 2.0, 200100.0)
    )
    reverse_loop = loop.model_copy(update={"reverse_acting": True})
    reverse = PIDSession(reverse_loop, context).step(1.0, 250000.0, observe(loop, context, 1.0, 200000.0))
    assert reverse["output"] == 0.0
    return dict(
        saturation_samples=20,
        conditional_integral_windup_prevented=True,
        setpoint_jump_has_zero_derivative_kick=True,
        reverse_action_preserved=True,
        checkpoint_resume_decision_exactly_equal=True,
    )


def main():
    parser = argparse.ArgumentParser()
    for key in ("lh2dt", "monitoring", "hra"):
        parser.add_argument("--" + key, type=Path, required=True)
    parser.add_argument("--raw-root", type=Path, default=Path("data/raw/coupled_tank"))
    args = parser.parse_args()
    roots = {key: getattr(args, key) for key in ("lh2dt", "monitoring", "hra")}
    before = {key: revision(path) for key, path in roots.items()}
    sys.path[:0] = [str((p / "src").resolve()) for p in roots.values()] + [str(Path("examples").resolve())]
    private = Path("artifacts/control_exchange")
    private.mkdir(parents=True, exist_ok=True)
    summary = dict(schema="pipeline_integrity.control_exchange_verification.v1", feedback={})
    summary["native_pid_contracts"] = native_pid_checks()
    histories = {}
    for quantity in ("pressure", "temperature"):
        for label, dt, missing in [("base", 0.5, False), ("refined", 0.25, False), ("data_gap", 0.5, True)]:
            name = quantity + "_" + label
            histories[name], summary["feedback"][name] = tank_feedback(quantity, dt, missing=missing)
            (private / (name + ".json")).write_text(
                json.dumps(histories[name], indent=2) + "\n", encoding="utf-8"
            )
            print(json.dumps({"case": name, **summary["feedback"][name]}), flush=True)
        summary["feedback"][quantity + "_refinement_difference"] = max(
            abs(a["process_value"] - b["process_value"])
            for a, b in zip(
                histories[quantity + "_base"], histories[quantity + "_refined"][1::2], strict=True
            )
        )
    rows, summary["valve_flow_feedback"] = valve_feedback()
    (private / "valve_flow.json").write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    rows, summary["instrumentation"] = instrument_checks()
    (private / "instruments.json").write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    rows, summary["public_data"] = public_data(args)
    (private / "public_data_shadow.json").write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    after = {key: revision(path) for key, path in roots.items()}
    assert before == after, "external engine sources changed during verification"
    summary["source_versions"] = after
    summary["module_sha256"] = file_digest(Path("src/pipeline_integrity/control_exchange.py"))
    summary["instrument_module_sha256"] = file_digest(Path("src/pipeline_integrity/instrument_exchange.py"))
    summary["script_sha256"] = file_digest(Path(__file__))
    summary["field_validated"] = False
    summary["tracking_performance_qualified"] = False
    summary["operating_acceptance_criteria_supplied"] = False
    summary["example_sha256"] = file_digest(Path("examples/control_loop.py"))
    Path("research/control_exchange_results.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
