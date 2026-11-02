"""Alarm boundaries use scripted providers; owner integration checks native time/latch behavior."""

import importlib
import importlib.util
import math
from pathlib import Path

import pytest

from pipeline_integrity import alarm_exchange as ae
from pipeline_integrity.contracts import EvidenceRef, RunContext, SignalFrame, digest

CONTEXT = RunContext(
    asset_id="pipe_A",
    scenario_id="authored_alarm_case",
    clock_id="recorded_clock",
    origin="synthetic",
    evidence=tuple(
        EvidenceRef(id=name, citation=f"Authored {name}", origin="synthetic")
        for name in ("source", "policy", "reset")
    ),
)
PROVIDER = {"id": "authored_contract_spy", "version": "test"}


def specification(**updates):
    return ae.AlarmReplaySpec(
        **(
            {
                "alarm_id": "pressure_alarm",
                "source_stream_id": "scores",
                "score_channel": "score",
                "score_unit": "1",
                "trigger": 2.0,
                "reset": 1.0,
                "max_gap_s": 3.0,
                "policy_reviewed": True,
                "policy_evidence_id": "policy",
                "source_evidence_id": "source",
            }
            | updates
        )
    )


def frames(values=(0.0, 3.0, 0.0), *, times=None, validity=None):
    if times is None:
        times = tuple(float(i) for i in range(len(values)))
    if validity is None:
        validity = tuple(value is not None for value in values)
    return tuple(
        SignalFrame(
            stream_id="scores",
            clock_id=CONTEXT.clock_id,
            time_s=float(time),
            values={"score": value, "raw": 10.0},
            units={"score": "1", "raw": "W"},
            valid={"score": valid, "raw": True},
            origin="synthetic",
        )
        for value, time, valid in zip(values, times, validity, strict=True)
    )


def scripted(*states):
    """Return declared output records without implementing any threshold logic."""
    instances = []

    class Policy:
        def __init__(self, **parameters):
            self.__dict__.update(parameters)
            self.active = False
            self.last = self.since = self.clear_since = None
            self.calls = []
            instances.append(self)

        def update(self, time_s, score, *, valid, reset_requested):
            state, self.active = states[len(self.calls)]
            self.calls.append((time_s, score, valid, reset_requested))
            self.last = time_s
            return {"time_s": time_s, "state": state, "active": self.active}

    return Policy, instances


@pytest.fixture
def native(monkeypatch):
    native_root = Path(__file__).resolve().parents[2] / "tmp/analysis/repos/process-monitoring-toolkit/src"
    if native_root.is_dir():
        monkeypatch.syspath_prepend(str(native_root))
    if importlib.util.find_spec("process_monitoring") is None:
        pytest.skip("optional process-monitoring package is unavailable")
    return importlib.import_module("process_monitoring.events").AlarmPolicy


def test_unreviewed_policy_needs_no_optional_dependency_or_factory(monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("unreviewed policy imported or called an external dependency")

    monkeypatch.setattr(ae.importlib, "import_module", unexpected)
    result = ae.replay_alarm(frames(), specification(policy_reviewed=False), CONTEXT, factory=unexpected)
    assert result.status == "needs_data" and result.provider is None
    assert result.issues == ("alarm_policy_not_reviewed",)
    assert result.records == result.activation_records == ()
    assert result.source_frames_digest == digest(frames())
    result.check_integrity()


def test_provider_receives_original_score_units_clocks_and_invalidity():
    factory, instances = scripted(("NORMAL", False), ("UNKNOWN", False), ("ALARM", True))
    source = frames((0.0, None, 5.0), times=(100.0, 101.0, 103.0))
    result = ae.replay_alarm(source, specification(), CONTEXT, factory=factory, provider=PROVIDER)
    calls = instances[0].calls
    assert calls[0] == (100.0, 0.0, True, False)
    assert calls[1][0] == 101.0 and math.isnan(calls[1][1]) and calls[1][2] is False
    assert calls[2] == (103.0, 5.0, True, False)
    assert [row["time_s"] for row in result.records] == [100.0, 101.0, 103.0]
    assert result.activation_records[0]["time_s"] == 103.0
    assert (
        result.status == "exchange_checked" and not result.score_is_probability and not result.field_qualified
    )
    assert "missing_or_invalid_score_observations" in result.issues
    assert result.source_frames_digest == digest(source)


@pytest.mark.parametrize("value", [None, 7.0])
def test_missing_or_invalid_values_are_unknown_without_false_activation(value):
    source = frames((3.0, value), validity=(True, False))
    factory, _ = scripted(("ALARM", True), ("UNKNOWN", True))
    result = ae.replay_alarm(source, specification(), CONTEXT, factory=factory, provider=PROVIDER)
    assert result.records[-1]["state"] == "UNKNOWN" and result.records[-1]["active"]
    assert len(result.activation_records) == 1


def test_native_persistence_uses_elapsed_time_and_strict_threshold(native):
    source = frames((2.0, 3.0, 3.0, 3.0), times=(0.0, 1.0, 2.0, 3.0))
    result = ae.replay_alarm(
        source, specification(persistence_s=2.0), CONTEXT, factory=native, provider=PROVIDER
    )
    assert [row["state"] for row in result.records] == ["NORMAL", "PENDING", "PENDING", "ALARM"]
    assert [row["time_s"] for row in result.activation_records] == [3.0]


def test_native_missing_breaks_persistence_without_dropping_rows(native):
    source = frames((3.0, None, 3.0, 3.0), times=(0.0, 1.0, 2.0, 4.0))
    result = ae.replay_alarm(
        source, specification(persistence_s=2.0), CONTEXT, factory=native, provider=PROVIDER
    )
    assert [row["state"] for row in result.records] == ["PENDING", "UNKNOWN", "PENDING", "ALARM"]
    assert len(result.records) == len(source)
    assert [row["time_s"] for row in result.activation_records] == [4.0]


def test_native_long_gap_restarts_pending_and_preserves_active_through_unknown(native):
    source = frames((3.0, 3.0, 3.0, None, 3.0), times=(0.0, 4.0, 6.0, 7.0, 12.0))
    result = ae.replay_alarm(
        source, specification(persistence_s=2.0), CONTEXT, factory=native, provider=PROVIDER
    )
    assert [row["state"] for row in result.records] == ["PENDING", "PENDING", "ALARM", "UNKNOWN", "ALARM"]
    assert [row["gap_before"] for row in result.records] == [False, True, False, False, True]
    assert [row["time_s"] for row in result.activation_records] == [6.0]
    assert result.records[3]["active"] and result.records[4]["active"]
    assert "observation_gap_exceeded" in result.issues


def test_native_gap_equal_to_limit_is_retained_and_recovery_is_elapsed(native):
    source = frames((3.0, 0.0, 0.0, 3.0), times=(0.0, 1.0, 4.0, 5.0))
    result = ae.replay_alarm(
        source, specification(recovery_s=3.0), CONTEXT, factory=native, provider=PROVIDER
    )
    assert [row["active"] for row in result.records] == [True, True, False, True]
    assert not any(row["gap_before"] for row in result.records)
    assert [row["time_s"] for row in result.activation_records] == [0.0, 5.0]
    assert len({row["id"] for row in result.activation_records}) == 2


def test_native_recovery_does_not_bridge_missing_or_long_gap(native):
    source = frames((3.0, 0.0, None, 0.0, 0.0, 0.0), times=(0.0, 1.0, 2.0, 3.0, 7.0, 9.0))
    result = ae.replay_alarm(
        source, specification(recovery_s=2.0), CONTEXT, factory=native, provider=PROVIDER
    )
    assert [row["active"] for row in result.records] == [True, True, True, True, True, False]
    assert len(result.activation_records) == 1


def test_native_latch_reset_requires_recovery_and_exact_recorded_request(native):
    source = frames((3.0, 0.0, 0.0, 0.0, 0.0, 3.0), times=(0.0, 1.0, 2.0, 3.0, 4.0, 5.0))
    spec = specification(
        latch=True, recovery_s=2.0, reset_requested_time_s=(2.0, 4.0), reset_evidence_id="reset"
    )
    result = ae.replay_alarm(source, spec, CONTEXT, factory=native, provider=PROVIDER)
    assert [row["active"] for row in result.records] == [True, True, True, True, False, True]
    assert [row["reset_requested"] for row in result.records] == [False, False, True, False, True, False]
    assert [row["time_s"] for row in result.activation_records] == [0.0, 5.0]


def test_default_provider_directly_loads_owner_policy_with_source_identity(native):
    result = ae.replay_alarm(frames(), specification(), CONTEXT)
    assert result.provider["id"] == "process_monitoring.events.AlarmPolicy"
    assert result.provider["version"] == "0.1.0"
    assert len(result.provider["source_sha256"]) == 64
    result.check_integrity()


@pytest.mark.parametrize(
    "change",
    [
        {"clock_id": "shifted_clock"},
        {"stream_id": "foreign"},
        {"origin": "simulation"},
        {"units": {"score": "percent", "raw": "W"}},
        {"values": {"raw": 1.0}, "units": {"raw": "W"}, "valid": {"raw": True}},
        {"values": {"score": float("nan"), "raw": 10.0}},
        {"values": {"score": None, "raw": 10.0}},
    ],
)
def test_frame_clones_context_units_and_quality_are_strictly_revalidated(change):
    source = (frames()[0].model_copy(update=change),)
    with pytest.raises(ValueError):
        ae.replay_alarm(source, specification(policy_reviewed=False), CONTEXT)


@pytest.mark.parametrize("source", [(), frames()[::-1], (frames()[0], frames()[0])])
def test_empty_reversed_or_repeated_clocks_are_rejected_without_sorting(source):
    with pytest.raises(ValueError):
        ae.replay_alarm(source, specification(policy_reviewed=False), CONTEXT)


@pytest.mark.parametrize(
    "updates",
    [
        {"max_gap_s": 0.0},
        {"max_gap_s": -1.0},
        {"max_gap_s": float("inf")},
        {"max_gap_s": True},
        {"trigger": float("nan")},
        {"reset": 3.0},
        {"persistence_s": -1.0},
        {"recovery_s": -1.0},
        {"maximum_samples": True},
        {"maximum_samples": 0},
        {"reset_requested_time_s": (1.0,)},
        {"reset_evidence_id": "reset"},
        {"reset_requested_time_s": (2.0, 1.0), "reset_evidence_id": "reset"},
    ],
)
def test_policy_domains_and_required_positive_gap(updates):
    with pytest.raises(ValueError):
        specification(**updates)


def test_cloned_policy_is_revalidated_before_provider_use():
    with pytest.raises(ValueError):
        ae.replay_alarm(frames(), specification().model_copy(update={"max_gap_s": 0.0}), CONTEXT)


def test_missing_duplicate_or_wrong_reset_evidence_is_rejected():
    for context, spec in [
        (CONTEXT, specification(source_evidence_id="absent")),
        (CONTEXT.model_copy(update={"evidence": CONTEXT.evidence + (CONTEXT.evidence[0],)}), specification()),
        (CONTEXT, specification(reset_requested_time_s=(1.0,), reset_evidence_id="absent")),
        (CONTEXT, specification(reset_requested_time_s=(1.5,), reset_evidence_id="reset")),
    ]:
        with pytest.raises(ValueError):
            ae.replay_alarm(frames(), spec, context)


def test_budget_stops_a_generator_before_materializing_the_whole_source():
    seen = []

    def source():
        for frame in frames():
            seen.append(frame.time_s)
            yield frame
        pytest.fail("source iterator exceeded the bounded lookahead")

    with pytest.raises(ValueError, match="maximum_samples"):
        ae.replay_alarm(source(), specification(maximum_samples=2), CONTEXT)
    assert seen == [0.0, 1.0, 2.0]


@pytest.mark.parametrize(
    "provider", [None, {}, {"id": "spy"}, {"id": "spy", "version": ""}, {"id": "spy", "source_sha256": "bad"}]
)
def test_injected_factory_requires_provider_provenance(provider):
    factory, _ = scripted(("NORMAL", False))
    with pytest.raises(ValueError, match="provider"):
        ae.replay_alarm(frames((0.0,)), specification(), CONTEXT, factory=factory, provider=provider)


def test_factory_cannot_change_parameters_or_reuse_active_memory():
    for field, value in [("trigger", 99.0), ("active", True), ("last", 0.0), ("since", 0.0)]:
        parent, _ = scripted(("NORMAL", False))

        def altered(_parent=parent, _field=field, _value=value, **parameters):
            policy = _parent(**parameters)
            setattr(policy, _field, _value)
            return policy

        with pytest.raises(ValueError, match="provider|fresh"):
            ae.replay_alarm(frames((0.0,)), specification(), CONTEXT, factory=altered, provider=PROVIDER)


@pytest.mark.parametrize(
    "state,active,invalid",
    [("UNKNOWN", False, False), ("ALARM", False, False), ("NORMAL", True, False), ("ALARM", True, True)],
)
def test_provider_cannot_invent_validity_or_active_semantics(state, active, invalid):
    factory, _ = scripted((state, active))
    with pytest.raises(ValueError, match="semantics"):
        ae.replay_alarm(
            frames((None if invalid else 0.0,)), specification(), CONTEXT, factory=factory, provider=PROVIDER
        )


@pytest.mark.parametrize(
    "field",
    [
        "records",
        "activation_records",
        "context",
        "specification",
        "provider",
        "source_frames_digest",
        "status",
        "issues",
    ],
)
def test_result_clones_and_nested_records_cannot_change_the_binding(field):
    factory, _ = scripted(("ALARM", True))
    result = ae.replay_alarm(frames((3.0,)), specification(), CONTEXT, factory=factory, provider=PROVIDER)
    replacements = {
        "records": (result.records[0] | {"time_s": 10.0},),
        "activation_records": (result.activation_records[0] | {"time_s": 10.0},),
        "context": result.context.model_copy(update={"clock_id": "shifted"}),
        "specification": result.specification.model_copy(update={"trigger": 4.0}),
        "provider": PROVIDER | {"version": "different"},
        "source_frames_digest": "a" * 64,
        "status": "needs_data",
        "issues": ("different",),
    }
    with pytest.raises(ValueError, match="changed after replay"):
        result.model_copy(update={field: replacements[field]}).check_integrity()


def test_private_source_snapshot_retains_missing_units_and_is_independent():
    factory, _ = scripted(("UNKNOWN", False))
    original = frames((None,))
    result = ae.replay_alarm(original, specification(), CONTEXT, factory=factory, provider=PROVIDER)
    assert result.source_frames_digest == digest(original)
    assert result._source_frames[0].values["score"] is None
    assert result._source_frames[0].units == {"score": "1", "raw": "W"}
    original[0].values["raw"] = 200.0
    result.check_integrity()
    clone = result.model_copy(deep=True)
    clone.check_integrity()
    clone._source_frames[0].units["raw"] = "kW"
    with pytest.raises(ValueError, match="source frames changed"):
        clone.check_integrity()
    result.check_integrity()


def test_public_receipt_excludes_raw_frames_and_does_not_claim_source_rehydration():
    factory, _ = scripted(("ALARM", True))
    result = ae.replay_alarm(frames((3.0,)), specification(), CONTEXT, factory=factory, provider=PROVIDER)
    public = result.model_dump(mode="json")
    assert "_source_frames" not in public and "values" not in public
    reconstructed = ae.AlarmReplayResult.model_validate_json(result.model_dump_json())
    with pytest.raises(ValueError, match="local source-integrity"):
        reconstructed.check_integrity()
    # Recomputing a public checksum cannot silently rebind a local clone.
    changed = result.model_copy(update={"issues": ("changed",)})
    changed = changed.model_copy(
        update={"result_digest": digest(changed.model_dump(mode="json", exclude={"result_digest"}))}
    )
    with pytest.raises(ValueError, match="changed after replay"):
        changed.check_integrity()
