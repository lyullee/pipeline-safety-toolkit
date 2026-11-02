"""Quantitative manual/interlock/automatic PID transition reference.

All values are authored synthetic inputs.  The native PID remains the owner;
the closed-form proportional outputs provide independent decision-time checks.
"""

import pytest

from pipeline_integrity.control_exchange import (
    ControlAxis,
    ControlObservation,
    PIDGains,
    PIDLoop,
    PIDSession,
)
from pipeline_integrity.contracts import EvidenceRef, RunContext, SignalFrame
from pipeline_integrity.control_modes import (
    RegulationModeScan,
    RegulationModesRequest,
    evaluate_regulation_modes,
    verify_regulation_modes_record,
)


def _evidence(identifier):
    return EvidenceRef(
        id=identifier,
        citation="Authored synthetic controller reference input",
        origin="synthetic",
        sha256="a" * 64,
    )


def _observation(*, stream, channel, axis, clock, time_s, value, evidence_id):
    frame = SignalFrame(
        stream_id=stream,
        clock_id=clock,
        time_s=float(time_s),
        values={channel: float(value)},
        units={channel: axis.unit},
        valid={channel: True},
        origin="simulation",
    )
    return ControlObservation(
        frame=frame,
        axis=axis,
        available_at_s=float(time_s),
        evidence_id=evidence_id,
    )


def test_manual_interlock_auto_reentry_matches_actual_position_then_tracks_analytic_response():
    pressure = ControlAxis(
        quantity="pressure",
        unit="Pa",
        pressure_reference="absolute",
        reference_id="synthetic:pressure-datum",
    )
    output = ControlAxis(quantity="fraction", unit="1", reference_id="synthetic:valve")
    loop = PIDLoop(
        loop_id="synthetic-pressure-valve-loop",
        measurement_stream_id="synthetic-process",
        measurement_channel="pressure_pa_abs",
        actuator_id="synthetic-valve",
        measurement_axis=pressure,
        output_axis=output,
        gains=PIDGains(
            proportional=1e-6,
            integral=0.0,
            derivative=0.0,
            proportional_unit="(1)/(Pa)",
            integral_unit="(1)/(Pa*s)",
            derivative_unit="(1)*s/(Pa)",
        ),
        output_minimum=0.0,
        output_maximum=1.0,
        initial_output=0.0,
        maximum_measurement_age_s=0.0,
        maximum_scan_interval_s=1.0,
        parameter_evidence_id="synthetic:pid-parameters",
    )
    context = RunContext(
        asset_id=loop.loop_id,
        scenario_id="synthetic-mode-transition",
        clock_id="synthetic-elapsed",
        origin="synthetic",
        evidence=tuple(
            _evidence(identifier)
            for identifier in (
                "synthetic:pid-parameters",
                "synthetic:mode-priority",
                "synthetic:mode-manual",
                "synthetic:mode-interlock",
                "synthetic:mode-auto-entry",
                "synthetic:mode-auto-followup",
                "synthetic:process-manual",
                "synthetic:process-interlock",
                "synthetic:process-auto-entry",
                "synthetic:process-auto-followup",
                "synthetic:actuator-manual",
                "synthetic:actuator-interlock",
                "synthetic:actuator-auto-entry",
                "synthetic:actuator-auto-followup",
            )
        ),
    )

    def scan(identifier, time_s, mode, *, setpoint, actual_position, interlock=False, manual=None):
        return RegulationModeScan(
            id=identifier,
            decision_time_s=float(time_s),
            setpoint=float(setpoint),
            requested_mode=mode,
            manual_output=manual,
            interlock_active=interlock,
            process_observation=_observation(
                stream=loop.measurement_stream_id,
                channel=loop.measurement_channel,
                axis=pressure,
                clock=context.clock_id,
                time_s=time_s,
                value=100_000.0,
                evidence_id=f"synthetic:process-{identifier.removeprefix('scan-')}",
            ),
            actual_actuator_observation=_observation(
                stream="synthetic-actuator-position",
                channel="position_fraction",
                axis=output,
                clock=context.clock_id,
                time_s=time_s,
                value=actual_position,
                evidence_id=f"synthetic:actuator-{identifier.removeprefix('scan-')}",
            ),
            mode_state_evidence_id=f"synthetic:mode-{identifier.removeprefix('scan-')}",
        )

    request = RegulationModesRequest(
        id="synthetic-manual-interlock-auto-sequence",
        loop=loop,
        initial_mode="manual",
        scans=(
            scan("scan-manual", 1.0, "manual", setpoint=130_000.0, actual_position=0.2, manual=0.2),
            scan("scan-interlock", 2.0, "auto", setpoint=130_000.0, actual_position=0.1, interlock=True),
            scan("scan-auto-entry", 3.0, "auto", setpoint=140_000.0, actual_position=0.04),
            scan("scan-auto-followup", 4.0, "auto", setpoint=150_000.0, actual_position=0.05),
        ),
        actuator_stream_id="synthetic-actuator-position",
        actuator_channel="position_fraction",
        maximum_actual_actuator_age_s=0.0,
        interlock_output=0.1,
        tracking_integral_bounds=(-1.0, 1.0),
        native_pid_identity=PIDSession(loop, context).provider,
        tracking_and_mode_priority_evidence_id="synthetic:mode-priority",
        manual_interlock_auto_priority_and_integral_state_tracking_reviewed=True,
    )

    record = evaluate_regulation_modes(request, context)

    rows = record["states"]
    assert [row["effective_mode"] for row in rows] == ["manual", "interlock", "auto", "auto"]
    assert [row["output_command"] for row in rows[:3]] == [0.2, 0.1, 0.04]
    assert rows[3]["output_command"] == pytest.approx(0.05, rel=0.0, abs=1e-15)
    assert rows[2]["tracking"]["committed"] is True
    assert rows[2]["tracking"]["target_actual_output"] == 0.04
    assert rows[2]["native_decision"]["output"] == 0.04
    # Native PID proportional law: Kp * (setpoint - measured pressure).
    # The 10 kPa increase after auto re-entry therefore adds exactly 0.01.
    expected_followup = 1e-6 * (150_000.0 - 100_000.0)
    assert rows[3]["native_decision"]["output"] == expected_followup
    assert rows[3]["output_command"] - rows[2]["output_command"] == pytest.approx(
        0.01, rel=0.0, abs=1e-15
    )
    assert all(row["status"] == "computed_diagnostic" for row in rows)
    assert verify_regulation_modes_record(record, request=request, context=context)
