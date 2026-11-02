"""Independent transition-history checks for alarm event replay and counting."""

import pytest

from pipeline_integrity import alarm_exchange as exchange
from pipeline_integrity.contracts import EvidenceRef, RunContext, SignalFrame


CONTEXT = RunContext(
    asset_id="synthetic_pipe",
    scenario_id="alarm_transition_oracle",
    clock_id="recorded_monotonic_clock",
    origin="synthetic",
    evidence=tuple(
        EvidenceRef(id=name, citation=f"Independently authored {name} fixture", origin="synthetic", license_id="MIT")
        for name in ("source", "policy", "reset")
    ),
)


def make_frames(values, times=None):
    if times is None:
        times = list(map(float, range(len(values))))
    return tuple(
        SignalFrame(
            stream_id="score-stream",
            clock_id=CONTEXT.clock_id,
            time_s=float(time),
            values={"score": value},
            units={"score": "1"},
            valid={"score": value is not None},
            origin="synthetic",
        )
        for time, value in zip(times, values, strict=True)
    )


def replay(values, times=None, **spec_updates):
    parameters = {
        "alarm_id": "pressure_score_alarm",
        "source_stream_id": "score-stream",
        "score_channel": "score",
        "score_unit": "1",
        "trigger": 10.0,
        "reset": 4.0,
        "persistence_s": 2.0,
        "recovery_s": 2.0,
        "max_gap_s": 3.0,
        "policy_reviewed": True,
        "policy_evidence_id": "policy",
        "source_evidence_id": "source",
    }
    parameters.update(spec_updates)
    specification = exchange.AlarmReplaySpec(**parameters)
    return exchange.replay_alarm(make_frames(values, times), specification, CONTEXT)


def test_strict_threshold_elapsed_persistence_and_recovery_emit_one_event_per_rising_edge():
    result = replay(
        [10.0, 10.1, 10.1, 3.0, 3.0, 11.0, 11.0],
        [0.0, 1.0, 3.0, 4.0, 6.0, 7.0, 9.0],
    )
    assert [row["state"] for row in result.records] == [
        "NORMAL", "PENDING", "ALARM", "ALARM", "NORMAL", "PENDING", "ALARM"
    ]
    assert [row["active"] for row in result.records] == [False, False, True, True, False, False, True]
    assert [event["time_s"] for event in result.activation_records] == [3.0, 9.0]
    assert len(result.activation_records) == 2
    assert all(event["id"].startswith("pressure_score_alarm:") for event in result.activation_records)
    assert result.activation_time_basis == "recorded_first_valid_alarm_transition"
    assert result.true_onset_estimated is False
    assert result.provider["id"] == "process_monitoring.events.AlarmPolicy"
    assert len(result.provider["source_sha256"]) == 64
    result.check_integrity()


def test_invalid_sample_breaks_pending_and_recovery_timers_without_losing_active_event():
    result = replay(
        [11.0, None, 11.0, 11.0, 3.0, None, 3.0, 3.0],
        [0.0, 1.0, 2.0, 4.0, 5.0, 6.0, 8.0, 10.0],
    )
    assert [row["state"] for row in result.records] == [
        "PENDING", "UNKNOWN", "PENDING", "ALARM", "ALARM", "UNKNOWN", "ALARM", "NORMAL"
    ]
    assert [row["active"] for row in result.records] == [False, False, False, True, True, True, True, False]
    assert [event["time_s"] for event in result.activation_records] == [4.0]
    assert "missing_or_invalid_score_observations" in result.issues


def test_long_gap_restarts_pending_and_recovery_elapsed_time_but_preserves_active_alarm():
    result = replay([11.0, 11.0, 11.0, 3.0, 3.0], [0.0, 4.0, 6.0, 10.0, 12.0])
    assert [row["state"] for row in result.records] == ["PENDING", "PENDING", "ALARM", "ALARM", "NORMAL"]
    assert [row["gap_before"] for row in result.records] == [False, True, False, True, False]
    assert [event["time_s"] for event in result.activation_records] == [6.0]
    assert "observation_gap_exceeded" in result.issues


def test_latched_alarm_recovery_needs_a_reviewed_reset_at_an_observed_timestamp():
    result = replay(
        [11.0, 3.0, 3.0, 3.0],
        [0.0, 1.0, 3.0, 4.0],
        persistence_s=0.0,
        recovery_s=1.0,
        latch=True,
        reset_requested_time_s=(4.0,),
        reset_evidence_id="reset",
    )
    assert [row["state"] for row in result.records] == ["ALARM", "ALARM", "ALARM", "NORMAL"]
    assert [row["reset_requested"] for row in result.records] == [False, False, False, True]
    assert [event["time_s"] for event in result.activation_records] == [0.0]


def test_gap_equal_to_limit_does_not_mark_a_gap_and_alarm_time_is_not_backdated():
    result = replay([11.0, 11.0], [5.0, 8.0])
    assert [row["gap_before"] for row in result.records] == [False, False]
    assert [row["state"] for row in result.records] == ["PENDING", "ALARM"]
    assert [event["time_s"] for event in result.activation_records] == [8.0]

