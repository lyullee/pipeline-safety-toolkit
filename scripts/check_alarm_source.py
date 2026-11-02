"""Replay a declared alarm policy on existing simulated SCADA; infer no detection or response accuracy."""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from check_integrations import revision

from pipeline_integrity.alarm_exchange import AlarmReplaySpec, replay_alarm
from pipeline_integrity.contracts import EvidenceRef, RunContext, SignalFrame, digest
from pipeline_integrity.datasets import catalog, file_digest
from pipeline_integrity.loaders import iter_battledim_frames

# These are independently authored research scenario parameters, not fitted source statistics.
PRESSURE_CHANNEL = "pressure_head:n1"
FLOW_CHANNEL = "volume_flow:PUMP_1"
SCORE_CHANNEL = "declared_low_pressure_head_score"
REFERENCE_HEAD_M = 35.0
HEAD_SCALE_M = 5.0
POLICY = dict(trigger=1.0, reset=0.0, persistence_s=600.0, recovery_s=600.0, max_gap_s=300.0, latch=False)
SOURCE_FILES = ("2018_SCADA_Pressures.csv", "2018_SCADA_Flows.csv", "2018_SCADA_Levels.csv", "README.txt")


def _source_files(root, source):
    assets = {a.filename: a for a in source.assets}
    files = {}
    for name in SOURCE_FILES:
        asset = assets[name]
        path = root / name
        algorithm, expected = asset.checksum.split(":", 1)
        if algorithm != "md5" or asset.checksum_origin != "publisher_metadata":
            raise ValueError("alarm source proof requires the catalogued publisher MD5")
        if path.stat().st_size != asset.bytes or file_digest(path, algorithm) != expected:
            raise ValueError("alarm source size or publisher checksum mismatch: " + name)
        files[name] = dict(
            bytes=path.stat().st_size,
            supplier_checksum=asset.checksum,
            checksum_origin=asset.checksum_origin,
            supplier_checksum_verified=True,
            current_sha256=file_digest(path),
        )
    return files


def _aggregate(result):
    result.check_integrity()
    return dict(
        status=result.status,
        source_frames_digest=result.source_frames_digest,
        result_digest=result.result_digest,
        provider=result.provider,
        observations=len(result.records),
        states=dict(Counter(r["state"] for r in result.records)),
        active_observations=sum(r["active"] for r in result.records),
        invalid_observations=sum(not r["valid"] for r in result.records),
        gaps_before_observations=sum(r["gap_before"] for r in result.records),
        observed_activation_count=len(result.activation_records),
        issues=list(result.issues),
        score_is_probability=result.score_is_probability,
        field_qualified=result.field_qualified,
        activation_time_basis=result.activation_time_basis,
        true_onset_estimated=result.true_onset_estimated,
    )


def _synthetic_frames(times, scores, name):
    return tuple(
        SignalFrame(
            stream_id=name,
            clock_id=name + ":elapsed_s",
            time_s=float(t),
            values={SCORE_CHANNEL: score},
            units={SCORE_CHANNEL: "1"},
            valid={SCORE_CHANNEL: score is not None},
            origin="synthetic",
        )
        for t, score in zip(times, scores, strict=True)
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--monitoring", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path("data/raw/battledim"))
    args = parser.parse_args()
    before = revision(args.monitoring)
    sys.dont_write_bytecode = True
    sys.path.insert(0, str((args.monitoring / "src").resolve()))
    import process_monitoring
    import process_monitoring.events
    from process_monitoring.events import AlarmPolicy

    source = next(s for s in catalog() if s.id == "battledim")
    source_files = _source_files(args.root, source)
    source_manifest = digest(source_files)
    output = Path("artifacts/alarm_source")
    output.mkdir(parents=True, exist_ok=True)

    def save(name, value):
        (output / (name + ".json")).write_text(
            json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8"
        )

    observations = tuple(
        iter_battledim_frames(
            args.root, 2018, selected_channels=(PRESSURE_CHANNEL, FLOW_CHANNEL), maximum_rows=864
        )
    )
    assert len(observations) == 864
    assert all(f.origin == "simulation" for f in observations)
    assert all(f.units == {PRESSURE_CHANNEL: "m", FLOW_CHANNEL: "m3/s"} for f in observations)
    assert all(b.time_s - a.time_s == 300 for a, b in zip(observations, observations[1:], strict=False))
    frames = tuple(
        SignalFrame(
            stream_id="battledim_2018:declared_low_pressure_head_score",
            clock_id=f.clock_id,
            time_s=f.time_s,
            values={
                SCORE_CHANNEL: None
                if f.values[PRESSURE_CHANNEL] is None
                else (REFERENCE_HEAD_M - f.values[PRESSURE_CHANNEL]) / HEAD_SCALE_M
            },
            units={SCORE_CHANNEL: "1"},
            valid={SCORE_CHANNEL: f.valid[PRESSURE_CHANNEL]},
            origin=f.origin,
        )
        for f in observations
    )
    transform = dict(
        pressure_channel=PRESSURE_CHANNEL,
        pressure_quantity="source pressure head in metres, not absolute pressure in Pa",
        contextual_flow_channel=FLOW_CHANNEL,
        contextual_flow_used_in_score=False,
        flow_source_unit="m3/hour",
        flow_model_boundary_unit="m3/s",
        flow_scale=1 / 3600,
        score_channel=SCORE_CHANNEL,
        score_unit="1",
        rule="score = (reference_head_m - pressure_head_m) / head_scale_m",
        reference_head_m=REFERENCE_HEAD_M,
        head_scale_m=HEAD_SCALE_M,
        negative_scores_clipped_or_rectified=False,
        missing_pressure_head_yields_missing_invalid_score=True,
        parameters_fitted_or_tuned=False,
        parameter_basis="independent authored research scenario; no plant alarm setting or learned baseline",
        hydraulic_pressure_or_flow_model_fitted=False,
    )
    evidence = (
        EvidenceRef(
            id="pressure_source",
            citation=source.citation,
            origin="simulation",
            locator=source.source,
            license_id=source.license_id,
            sha256=source_files["2018_SCADA_Pressures.csv"]["current_sha256"],
        ),
        EvidenceRef(
            id="contextual_flow_source",
            citation=source.citation + "; contextual PUMP_1 flow, not alarm input",
            origin="simulation",
            locator=source.source,
            license_id=source.license_id,
            sha256=source_files["2018_SCADA_Flows.csv"]["current_sha256"],
        ),
        EvidenceRef(
            id="source_units",
            citation="BattLeDIM released README: pressure head m and flow m3/hour",
            origin="literature",
            locator=source.source,
            license_id=source.license_id,
            sha256=source_files["README.txt"]["current_sha256"],
        ),
        EvidenceRef(
            id="declared_policy",
            origin="user_declared",
            license_id="MIT",
            citation=(
                "Independent research rule: (35 m - n1 pressure head)/(5 m), trigger >1, reset <=0, "
                "600 s persistence/recovery, maximum gap 300 s, no latch. This is policy replay only; "
                "no leak truth, false-alarm or detection performance, human reaction or delivery time is inferred."
            ),
            sha256=digest(dict(transform=transform, policy=POLICY)),
        ),
    )
    context = RunContext(
        asset_id="battledim_ltown_n1",
        scenario_id="first_864_rows_declared_low_pressure_head_replay",
        clock_id=frames[0].clock_id,
        origin="simulation",
        evidence=evidence,
        metadata=dict(
            source_checksum_manifest=source_manifest,
            source_frames_digest=digest(observations),
            score_transform=transform,
            independent_physical_sources=1,
        ),
    )
    spec = AlarmReplaySpec(
        alarm_id="declared_n1_low_pressure_head",
        source_stream_id=frames[0].stream_id,
        score_channel=SCORE_CHANNEL,
        score_unit="1",
        **POLICY,
        policy_evidence_id="declared_policy",
        source_evidence_id="pressure_source",
    )
    unreviewed = replay_alarm(frames, spec, context)
    assert unreviewed.status == "needs_data" and not unreviewed.records and unreviewed.provider is None
    reviewed = spec.model_copy(update={"policy_reviewed": True})
    result = replay_alarm(frames, reviewed, context)
    result.check_integrity()
    assert result.status == "exchange_checked" and len(result.records) == len(frames)
    # Compare the facade with a fresh instance of the same owner API; this is an interface check.
    direct = AlarmPolicy(**POLICY)
    for frame, record in zip(frames, result.records, strict=True):
        value = frame.values[SCORE_CHANNEL]
        expected = direct.update(
            frame.time_s, float("nan") if value is None else value, valid=frame.valid[SCORE_CHANNEL]
        )
        assert {key: record[key] for key in ("time_s", "state", "active")} == expected
    save("source_observations", [f.model_dump(mode="json") for f in observations])
    save("source_scores", [f.model_dump(mode="json") for f in frames])
    save("source_transform", transform)
    save("source_unreviewed", unreviewed.model_dump(mode="json"))
    save("source_replay", result.model_dump(mode="json"))

    synthetic = {}

    def synthetic_case(name, times, scores, *, latch=False, resets=()):
        values = _synthetic_frames(times, scores, name)
        ctx = RunContext(
            asset_id=name,
            scenario_id="authored_policy_contract_check",
            clock_id=values[0].clock_id,
            origin="synthetic",
            evidence=(
                EvidenceRef(
                    id="authored",
                    citation="Authored synthetic score, quality, gaps and reset requests; no human observations",
                    origin="synthetic",
                    license_id="MIT",
                ),
            ),
        )
        request = AlarmReplaySpec(
            alarm_id=name,
            source_stream_id=name,
            score_channel=SCORE_CHANNEL,
            score_unit="1",
            **(POLICY | dict(latch=latch)),
            policy_reviewed=True,
            policy_evidence_id="authored",
            source_evidence_id="authored",
            reset_requested_time_s=resets,
            reset_evidence_id="authored" if resets else None,
        )
        replay = replay_alarm(values, request, ctx)
        replay.check_integrity()
        assert replay.status == "exchange_checked"
        save(
            name,
            dict(frames=[f.model_dump(mode="json") for f in values], result=replay.model_dump(mode="json")),
        )
        synthetic[name] = _aggregate(replay)
        return replay

    missing = synthetic_case(
        "synthetic_missing", range(0, 2700, 300), [2.0, 2.0, 2.0, None, -1.0, -1.0, -1.0, 0.5, 2.0]
    )
    assert missing.records[3]["state"] == "UNKNOWN" and missing.records[3]["active"]
    assert not missing.records[6]["active"] and len(missing.activation_records) == 1
    gap = synthetic_case("synthetic_gap", [0, 300, 900, 1200, 1500], [2.0] * 5)
    assert gap.records[2]["gap_before"] and gap.records[2]["state"] == "PENDING"
    assert [r["time_s"] for r in gap.activation_records] == [1500.0]
    latched = synthetic_case(
        "synthetic_latch",
        range(0, 2100, 300),
        [2.0, 2.0, 2.0, -1.0, -1.0, -1.0, -1.0],
        latch=True,
        resets=(1800.0,),
    )
    assert latched.records[5]["active"] and not latched.records[6]["active"]
    assert latched.records[6]["reset_requested"]
    after = revision(args.monitoring)
    assert before == after and source_files == _source_files(args.root, source)
    provider = result.provider
    assert provider["version"] == process_monitoring.__version__
    assert provider["source_sha256"] == file_digest(Path(process_monitoring.events.__file__))
    summary = dict(
        schema="pipeline_integrity.alarm_source_verification.v1",
        status="passed",
        primary_data=dict(
            dataset_id=source.id,
            source=source.source,
            citation=source.citation,
            creators=list(source.creators),
            version=source.version,
            license_id=source.license_id,
            rights_evidence_url=source.rights_evidence_url,
            origin=source.origin,
            files=source_files,
            source_checksum_manifest=source_manifest,
            publisher_md5_verified_files=len(source_files),
            first_source_rows_used=len(observations),
            selected_channels=[PRESSURE_CHANNEL, FLOW_CHANNEL],
            independent_physical_sources=1,
            same_network_time_rows_are_independent_experiments=False,
        ),
        source_quality=dict(
            source_frame_count=len(observations),
            source_frames_digest=digest(observations),
            source_clock_id=context.clock_id,
            source_clock_is_elapsed=True,
            absolute_UTC_alignment_inferred=False,
            observed_span_s=observations[-1].time_s - observations[0].time_s,
            sample_interval_s=300.0,
            samples_sorted_resampled_or_filled=False,
            missing_or_invalid_pressure_head_samples=sum(not f.valid[PRESSURE_CHANNEL] for f in observations),
            missing_or_invalid_contextual_flow_samples=sum(not f.valid[FLOW_CHANNEL] for f in observations),
        ),
        score_transform=transform,
        declared_policy=POLICY,
        unreviewed_policy_status=unreviewed.status,
        source_replay=_aggregate(result),
        same_owner_API_sequence_comparison=dict(
            observations_compared=len(frames),
            matches=True,
            interpretation="fresh same-owner AlarmPolicy comparison; not independent physical validation",
        ),
        synthetic_contract_checks=synthetic,
        synthetic_unknown_retains_active_and_resets_timers=True,
        synthetic_gap_restarts_persistence_without_filling=True,
        synthetic_latch_requires_authored_reset_request=True,
        synthetic_checks_are_source_or_human_observations=False,
        external_monitoring_source_before=before,
        external_monitoring_source_after=after,
        external_monitoring_source_unchanged=True,
        public_summary_contains_source_or_transformed_sample_rows=False,
        source_labels_or_human_reactions_read=False,
        threshold_fit_or_tuning_performed=False,
        held_out_performance_claimed=False,
        physical_leak_false_alarm_rate=None,
        physical_leak_detection_rate=None,
        human_error_probability=None,
        observed_delivery_delay_s=None,
        activation_count_is_leak_incidence_or_HRA_demand_rate=False,
        alarm_policy_delivery_human_response_or_field_qualification=False,
        loader_sha256=file_digest(Path("src/pipeline_integrity/loaders.py")),
        alarm_exchange_sha256=file_digest(Path("src/pipeline_integrity/alarm_exchange.py")),
        script_sha256=file_digest(Path(__file__)),
    )
    Path("research/alarm_source_results.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            dict(
                status="passed",
                source_frames=len(frames),
                source_activations=len(result.activation_records),
                source_states=summary["source_replay"]["states"],
                provider=provider,
                publisher_md5_files=len(source_files),
                synthetic_contract_cases=len(synthetic),
                source_and_external_code_unchanged=True,
                physical_detection_or_human_performance_claimed=False,
            )
        )
    )


if __name__ == "__main__":
    main()
