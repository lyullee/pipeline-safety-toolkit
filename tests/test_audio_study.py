"""Authored analytic/source-boundary scenarios; deferred to NURION, never run locally."""

import io
import wave

import pytest

from pipeline_integrity import audio_study as au
from pipeline_integrity.audio_data import parse_pcm_wave
from pipeline_integrity.contracts import EvidenceRef, RunContext, digest


def make_case(monkeypatch, *, reviewed=True, codes=None):
    samples = codes if codes is not None else [8192 if i % 2 == 0 else -8192 for i in range(64)]
    raw = io.BytesIO()
    with wave.open(raw, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(64)
        stream.writeframes(b"".join(int(value).to_bytes(2, "little", signed=True) for value in samples))
    source = {
        "schema": "authored_source_double",
        "archive_filename": "leak acoustic data.rar",
        "archive_sha256": "a" * 64,
        "member": "leak acoustic data/authored.wav",
        "source_origin": "measured_process",
        "citation": "Authored PCM fixture; not the field dataset",
        "license_id": "CC0-1.0",
        "observations": parse_pcm_wave(raw.getvalue()),
    }
    source["record_digest"] = digest(source)
    monkeypatch.setattr(au, "_source", lambda *a, **k: source)
    q = au.AudioStudyRequest(
        id="authored_clip",
        asset_id="audio_collection",
        clock_id="file_relative_s",
        archive_filename=source["archive_filename"],
        member=source["member"],
        expected_source_record_digest=source["record_digest"],
        start_frame=0,
        frame_count=len(samples),
        window="boxcar",
        nperseg=64,
        noverlap=0,
        detrend="none",
        bands=(
            au.AudioBand(id="low", lower_Hz=0.0, upper_Hz=16.0),
            au.AudioBand(id="high", lower_Hz=16.0, upper_Hz=32.0),
        ),
        evidence_id="authored",
        reviews=dict.fromkeys(au.REVIEWS, reviewed),
        review_evidence_ids=dict.fromkeys(au.REVIEWS, "authored") if reviewed else {},
        runtime_pins=au.runtime_metadata() if reviewed else {},
    )
    c = RunContext(
        asset_id=q.asset_id,
        scenario_id="authored_pcm",
        clock_id=q.clock_id,
        origin="measured",
        evidence=(EvidenceRef(id="authored", citation="Authored PCM test fixture", origin="synthetic"),),
        metadata={"audio_study_boundary": au.boundary_metadata(q)},
    )
    return q, c, source


def evaluate(q, c):
    return au.evaluate_audio_clip(q, c, source_root="unused_source_double", tar_executable="unused")


def test_unreviewed_never_reads_archive_or_runs_signal(monkeypatch):
    q, c, _ = make_case(monkeypatch, reviewed=False)
    monkeypatch.setattr(au, "_source", lambda *a: pytest.fail("unreviewed source read"))
    monkeypatch.setattr(au, "runtime_metadata", lambda: pytest.fail("unreviewed signal owner"))
    assert evaluate(q, c)["status"] == "needs_data"


def test_actual_welch_nyquist_and_digital_full_scale_analytic(monkeypatch):
    q, c, _ = make_case(monkeypatch)
    r = evaluate(q, c)
    assert r["status"] == "computed_diagnostic"
    assert r["values"]["rms_full_scale"] == pytest.approx(0.25)
    assert r["values"]["welch_total_bin_power_full_scale_squared"] == pytest.approx(0.0625)
    assert r["values"]["band_powers"][0]["power_full_scale_squared"] == pytest.approx(0.0, abs=1e-15)
    assert r["values"]["band_powers"][1]["power_full_scale_squared"] == pytest.approx(0.0625)
    assert r["physical_amplitude_unit"] is None and r["probability_of_detection"] is None
    assert au.verify_audio_study_record(r, source_root="unused", tar_executable="unused")


def test_zero_signal_undefined_ratios_are_unknown_not_infinite(monkeypatch):
    q, c, _ = make_case(monkeypatch, codes=[0] * 64)
    r = evaluate(q, c)
    assert r["values"]["rms_full_scale"] == 0.0
    assert r["values"]["rms_dBFS"] is None
    assert r["values"]["crest_factor"] is None and r["values"]["spectral_centroid_Hz"] is None


def test_source_pin_and_context_tampering_rejected(monkeypatch):
    q, c, source = make_case(monkeypatch)
    with pytest.raises(ValueError):
        evaluate(q, c.model_copy(update={"clock_id": "other"}))
    source["record_digest"] = "f" * 64
    with pytest.raises(ValueError, match="source receipt"):
        evaluate(q, c)


def test_edge_codes_are_diagnostics_not_physical_calibration(monkeypatch):
    q, c, _ = make_case(monkeypatch, codes=[-32768, 32767] * 32)
    r = evaluate(q, c)
    assert r["values"]["edge_code_fraction"] == 1.0
    assert r["physical_amplitude_calibrated"] is False
    assert r["values"]["peak_absolute_full_scale"] == 1.0


def test_unknown_groups_do_not_generate_training_splits(monkeypatch):
    q, c, _ = make_case(monkeypatch)
    r = evaluate(q, c)
    binding = au.AudioMonitoringBinding(
        study_record_digest=r["record_digest"],
        analysis_clock_id=c.clock_id,
        clip_start_time_s=None,
        physical_event_id=None,
        device_id=None,
        site_id=None,
        source_group_id=None,
        calibration_basis_id=None,
        evidence_id="authored",
    )
    p = au.prepare_audio_monitoring_split(
        [r],
        [binding],
        c,
        role="training",
        split_id="explicit",
        reference_kind="unresolved",
        source_root="unused",
        tar_executable="unused",
    )
    assert p["status"] == "needs_data" and p["split"] is None
    assert p["trained"] is False and p["independent_split_generated"] is False


def test_explicit_grouped_diagnostic_handoff_reuses_monitoring_contract(monkeypatch):
    q, c, _ = make_case(monkeypatch)
    r = evaluate(q, c)
    binding = au.AudioMonitoringBinding(
        study_record_digest=r["record_digest"],
        analysis_clock_id=c.clock_id,
        clip_start_time_s=0.0,
        physical_event_id="event_1",
        device_id="device_1",
        site_id="site_1",
        source_group_id="event_site_device_group",
        calibration_basis_id="reviewed_same_digitizer_gain",
        event_device_site_clock_and_calibration_reviewed=True,
        evidence_id="authored",
    )
    c = c.model_copy(
        update={
            "metadata": {
                **c.metadata,
                "audio_monitoring_boundary": au.monitoring_boundary_metadata(
                    [r], [binding], role="evaluation", split_id="explicit", reference_kind="not_used"
                ),
            }
        }
    )
    p = au.prepare_audio_monitoring_split(
        [r],
        [binding],
        c,
        role="evaluation",
        split_id="explicit",
        reference_kind="not_used",
        source_root="unused",
        tar_executable="unused",
    )
    assert p["status"] == "prepared" and p["split"]["frames"][0]["time_s"] == 1.0
    assert p["split"]["physical_run_id"] == "event_1"
    assert p["trained"] is False


def test_tampered_diagnostic_requires_actual_replay(monkeypatch):
    q, c, _ = make_case(monkeypatch)
    r = evaluate(q, c)
    r["values"]["rms_full_scale"] = 0.9
    r["record_digest"] = digest({k: v for k, v in r.items() if k != "record_digest"})
    with pytest.raises(ValueError, match="replay differs"):
        au.verify_audio_study_record(r, source_root="unused", tar_executable="unused")
