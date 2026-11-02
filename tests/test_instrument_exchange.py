"""Fault scenario ordering and timing policy; native filter mathematics is checked by the research runner."""

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from pipeline_integrity.contracts import EvidenceRef, RunContext, SignalFrame
from pipeline_integrity.control_exchange import ControlAxis
from pipeline_integrity.instrument_exchange import (
    FaultWindow,
    TransmitterScenario,
    packet_observation,
    replay_transmitter,
    transmitter_monitoring_input,
)

AXIS = ControlAxis(quantity="level", unit="m", reference_id="internal_bottom")
CTX = RunContext(
    asset_id="LT",
    scenario_id="injection",
    clock_id="elapsed",
    origin="synthetic",
    evidence=(
        EvidenceRef(id="source", citation="Authored truth", origin="synthetic"),
        EvidenceRef(id="parameters", citation="Authored fault scenario", origin="synthetic"),
    ),
)


@dataclass
class State:
    filtered_process_value: float


@dataclass
class Reading:
    indicated_value: float


class TransmitterSpy:
    calls = []

    def __init__(self, parameters):
        self.parameters = parameters

    def initialize(self, value):
        return State(value)

    def advance(self, state, target, dt):
        self.calls.append((state.filtered_process_value, target, dt))
        return State(target), Reading(target)

    def read(self, state):
        return Reading(state.filtered_process_value + self.parameters["bias"])


NATIVE = SimpleNamespace(FirstOrderTransmitter=TransmitterSpy, TransmitterParameters=lambda **p: p)


def frames(values=(1.0, 2.0, 3.0)):
    return tuple(
        SignalFrame(
            stream_id="truth",
            clock_id="elapsed",
            time_s=float(i),
            values={"level": v},
            units={"level": "m"},
            valid={"level": v is not None},
            origin="synthetic",
        )
        for i, v in enumerate(values)
    )


def spec(**changes):
    return TransmitterScenario(
        **(
            dict(
                instrument_id="LT",
                source_stream_id="truth",
                channel="level",
                axis=AXIS,
                lower_range=0.0,
                upper_range=5.0,
                initial_filtered_value=1.0,
                parameter_evidence_id="parameters",
                source_evidence_id="source",
            )
            | changes
        )
    )


def test_left_sample_hold_drift_and_delivery_do_not_use_future_values():
    TransmitterSpy.calls.clear()
    r = replay_transmitter(
        spec(time_constant_s=2.0, bias_drift_per_s=0.1, delivery_delay_s=0.5),
        frames(),
        CTX,
        native_module=NATIVE,
    )
    assert TransmitterSpy.calls == [(1.0, 1.0, 1.0), (1.0, 2.0, 1.0)]
    p = r["packets"]
    assert p[1]["reading"]["indicated_value"] == pytest.approx(1.1)
    assert p[2]["available_at_s"] == 2.5 and p[2]["frame"]["time_s"] == 2.0
    assert p[0]["source_origin"] == "synthetic" and r["output_origin"] == "simulation"


def test_zero_time_constant_reads_current_sample_without_an_artificial_scan_delay():
    r = replay_transmitter(spec(), frames(), CTX, native_module=NATIVE)
    assert [p["reading"]["indicated_value"] for p in r["packets"]] == [1.0, 2.0, 3.0]


def test_clock_faults_keep_physical_and_reported_time_and_negative_time_is_not_clipped():
    r = replay_transmitter(
        spec(
            reported_clock_offset_s=-0.5,
            reported_clock_rate_error=0.1,
            reported_clock_id="transmitter_clock",
        ),
        frames(),
        CTX,
        native_module=NATIVE,
    )
    a, b, c = r["packets"]
    assert a["frame"] is None and a["reported_time_s"] == -0.5
    assert c["physical_source_time_s"] == 2.0 and c["reported_time_s"] == pytest.approx(1.7)
    assert b["frame"]["clock_id"] == "transmitter_clock"
    assert packet_observation(a, axis=AXIS, evidence_id="source").frame is None
    assert b["frame"]["origin"] == "simulation"


def test_communication_dropout_does_not_destroy_known_physical_filter_state():
    r = replay_transmitter(
        spec(time_constant_s=2.0, dropouts=(FaultWindow(start_s=1.0, end_s=2.0),)),
        frames(),
        CTX,
        native_module=NATIVE,
    )
    assert not r["packets"][1]["frame"]["valid"]["level"]
    assert r["packets"][2]["frame"]["valid"]["level"] and not r["packets"][2]["input_unknown"]


@pytest.mark.parametrize("policy,valid", [("withhold", False), ("reset_on_next_valid", True)])
def test_unknown_physical_input_requires_a_declared_reset_instead_of_gap_interpolation(policy, valid):
    r = replay_transmitter(
        spec(time_constant_s=2.0, unknown_input_policy=policy),
        frames((1.0, None, 3.0)),
        CTX,
        native_module=NATIVE,
    )
    assert r["packets"][-1]["frame"]["valid"]["level"] == valid
    assert r["packets"][-1]["reset_on_valid_input"] == valid


def test_zero_lag_known_sample_can_recover_without_unknown_dynamic_history():
    r = replay_transmitter(spec(), frames((1.0, None, 3.0)), CTX, native_module=NATIVE)
    assert r["packets"][-1]["frame"]["valid"]["level"]


def test_source_clock_units_origin_and_evidence_are_checked_without_repair():
    source = frames()
    for bad in [
        source[::-1],
        (source[0], source[0]),
        (source[0].model_copy(update={"units": {"level": "cm"}}),),
        (source[0].model_copy(update={"clock_id": "foreign"}),),
    ]:
        with pytest.raises(ValueError):
            replay_transmitter(spec(), bad, CTX, native_module=NATIVE)
    with pytest.raises(ValueError):
        replay_transmitter(spec(source_evidence_id="missing"), source, CTX, native_module=NATIVE)
    with pytest.raises(ValueError, match="distinct, explicitly named reported clock"):
        replay_transmitter(spec(reported_clock_offset_s=0.25), source, CTX, native_module=NATIVE)
    with pytest.raises(ValueError):
        spec(reported_clock_rate_error=-1.0)


def test_packet_cannot_change_value_timing_or_datum_after_sealing():
    r = replay_transmitter(spec(), frames(), CTX, native_module=NATIVE)
    p = r["packets"][1]
    with pytest.raises(ValueError, match="content changed"):
        packet_observation(p | {"available_at_s": 99.0}, axis=AXIS, evidence_id="source")
    with pytest.raises(ValueError, match="datum differs"):
        packet_observation(
            p, axis=AXIS.model_copy(update={"reference_id": "different_bottom"}), evidence_id="source"
        )


def test_monitoring_bridge_preserves_report_clock_rows_and_dropouts():
    replay = replay_transmitter(
        spec(dropouts=(FaultWindow(start_s=1.0, end_s=2.0),)),
        frames(),
        CTX,
        native_module=NATIVE,
    )
    bridged = transmitter_monitoring_input(
        replay,
        role="training",
        split_id="train_1",
        physical_run_id="run_1",
        source_group_id="fault_scenario_1",
        source_evidence_id="source",
        reference_kind="caller_declared_reference",
    )
    assert len(bridged.split.frames) == len(replay["packets"]) == 3
    assert bridged.split.frames[1].valid["level"] is False
    assert bridged.preserved_invalid_samples == 1
    assert bridged.split.frames[0].clock_id == bridged.reported_clock_id == "elapsed"
    assert bridged.split.frames[0].origin == "simulation"
    assert bridged.all_selected_packets_preserved
    bridged.check_integrity()


def test_monitoring_bridge_refuses_unrepresented_delay_and_negative_time():
    delayed = replay_transmitter(
        spec(delivery_delay_s=0.5), frames(), CTX, native_module=NATIVE
    )
    with pytest.raises(ValueError, match="cannot represent packet availability delay"):
        transmitter_monitoring_input(
            delayed,
            role="training",
            split_id="train_1",
            physical_run_id="run_1",
            source_group_id="fault_scenario_1",
            source_evidence_id="source",
            reference_kind="caller_declared_reference",
        )
    negative = replay_transmitter(
        spec(reported_clock_offset_s=-0.5, reported_clock_id="transmitter_clock"),
        frames(),
        CTX,
        native_module=NATIVE,
    )
    with pytest.raises(ValueError, match="negative reported time"):
        transmitter_monitoring_input(
            negative,
            role="training",
            split_id="train_1",
            physical_run_id="run_1",
            source_group_id="fault_scenario_1",
            source_evidence_id="source",
            reference_kind="caller_declared_reference",
        )
    drifting = replay_transmitter(
        spec(reported_clock_rate_error=0.1, reported_clock_id="transmitter_clock"),
        frames(),
        CTX,
        native_module=NATIVE,
    )
    with pytest.raises(ValueError, match="cannot represent an unmapped reported clock"):
        transmitter_monitoring_input(
            drifting,
            role="training",
            split_id="train_1",
            physical_run_id="run_1",
            source_group_id="fault_scenario_1",
            source_evidence_id="source",
            reference_kind="caller_declared_reference",
        )
