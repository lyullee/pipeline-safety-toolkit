"""Explicit pressure-loop configuration; requires the separately installed owner's PID engine."""

from pipeline_integrity.contracts import EvidenceRef, RunContext, SignalFrame
from pipeline_integrity.control_exchange import ControlAxis, ControlObservation, PIDGains, PIDLoop, PIDSession


def make_loop(*, quantity="pressure", reverse_acting=False, output_quantity="mass_flow"):
    measurement = {
        "pressure": ("Pa", "absolute"),
        "temperature": ("K", None),
        "mass_flow": ("kg/s", None),
        "level": ("m", None),
    }[quantity]
    output_unit = {"mass_flow": "kg/s", "power": "W", "fraction": "1"}[output_quantity]
    inp = measurement[0]
    axis = ControlAxis(
        quantity=quantity,
        unit=inp,
        pressure_reference=measurement[1],
        reference_id=f"authored:{quantity}:basis",
    )
    gain, integral, maximum = {
        "pressure": (1e-9, 1e-10, 1e-5),
        "temperature": (3.0, 0.15, 10.0),
        "mass_flow": (10000.0, 2000.0, 1.0),
        "level": (0.5, 0.01, 1.0),
    }[quantity]
    loop = PIDLoop(
        loop_id=f"{quantity}-loop",
        measurement_stream_id="instrument",
        measurement_channel="pv",
        actuator_id="authored-actuator",
        measurement_axis=axis,
        output_axis=ControlAxis(quantity=output_quantity, unit=output_unit, reference_id="authored:command"),
        gains=PIDGains(
            proportional=gain,
            integral=integral,
            derivative=0.0,
            proportional_unit=f"({output_unit})/({inp})",
            integral_unit=f"({output_unit})/({inp}*s)",
            derivative_unit=f"({output_unit})*s/({inp})",
        ),
        output_minimum=0.0,
        output_maximum=maximum,
        initial_output=0.0,
        maximum_measurement_age_s=1.0,
        maximum_scan_interval_s=1.0,
        reverse_acting=reverse_acting,
        parameter_evidence_id="authored:parameters",
    )
    context = RunContext(
        asset_id=loop.loop_id,
        scenario_id="authored-reference",
        clock_id="elapsed",
        origin="synthetic",
        evidence=(
            EvidenceRef(
                id="authored:parameters", citation="Explicit reference gains and bounds", origin="synthetic"
            ),
            EvidenceRef(
                id="authored:source", citation="Declared reference process state", origin="synthetic"
            ),
        ),
    )
    return loop, context


def observe(loop, context, time_s, value):
    frame = SignalFrame(
        stream_id=loop.measurement_stream_id,
        clock_id=context.clock_id,
        time_s=float(time_s),
        values={loop.measurement_channel: value},
        units={loop.measurement_channel: loop.measurement_axis.unit},
        valid={loop.measurement_channel: value is not None},
        origin="simulation",
    )
    return ControlObservation(
        frame=frame, axis=loop.measurement_axis, available_at_s=float(time_s), evidence_id="authored:source"
    )


if __name__ == "__main__":
    import json

    specification, context = make_loop()
    session = PIDSession(specification, context)
    print(json.dumps(session.step(0.5, 250000.0, observe(specification, context, 0.5, 200000.0)), indent=2))
