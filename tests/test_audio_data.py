"""Raw audio interpretation and resource limits, pending Nurion execution."""

import io
import wave

import pytest

from pipeline_integrity.audio_data import parse_pcm_wave, read_training_audio, verify_training_audio_record
from pipeline_integrity.contracts import digest


def wav(codes, width=2, channels=1):
    payload = b"".join(
        bytes([v + 128]) if width == 1 else int(v).to_bytes(width, "little", signed=True) for v in codes
    )
    out = io.BytesIO()
    with wave.open(out, "wb") as stream:
        stream.setnchannels(channels)
        stream.setsampwidth(width)
        stream.setframerate(8000)
        stream.writeframes(payload)
    return out.getvalue()


@pytest.mark.parametrize("width", [1, 2, 3, 4])
def test_integer_codes_are_preserved_without_physical_calibration(width):
    values = [-100, -1, 0, 1, 100, 2, -2, 0]
    record = parse_pcm_wave(wav(values, width))
    assert record["pcm_codes"] == values
    assert record["sample_frames"] == 8
    assert record["physical_amplitude_unit"] is None
    assert record["physical_clock_id"] is None
    assert record["amplitude_calibration"] is None


def test_frame_budget_rejects_whole_record_without_truncation():
    with pytest.raises(ValueError, match="bounded mono"):
        parse_pcm_wave(wav([0] * 16), maximum_frames=8)


def test_truncated_payload_is_not_adopted():
    with pytest.raises(ValueError, match="truncated"):
        parse_pcm_wave(wav([0] * 8)[:-2])


def test_multichannel_requires_separate_review():
    with pytest.raises(ValueError, match="bounded mono"):
        parse_pcm_wave(wav([0] * 16, channels=2))


def test_corrupt_archive_is_rejected_before_tool_access(tmp_path):
    archive = tmp_path / "leak acoustic data.rar"
    archive.write_bytes(b"not a source archive")
    with pytest.raises(ValueError, match="source size"):
        read_training_audio(archive, "leak acoustic data/x.wav", tar_executable=tmp_path / "must-not-run")


def test_symlink_archive_alias_is_rejected_before_hash_or_tool_access(tmp_path):
    target = tmp_path / "original.rar"
    target.write_bytes(b"underlying archive placeholder")
    alias = tmp_path / "leak acoustic data.rar"
    try:
        alias.symlink_to(target)
    except OSError as exc:
        if getattr(exc, "winerror", None) == 1314:
            pytest.skip("Windows account lacks symbolic-link creation privilege (WinError1314); rejection requires an actual symlink")
        raise
    with pytest.raises(ValueError, match="regular non-symlink"):
        read_training_audio(
            alias,
            "leak acoustic data/recording.wav",
            tar_executable=tmp_path / "must-not-run",
        )


@pytest.mark.parametrize(
    "filename",
    [
        "../leak acoustic data.rar",
        "/leak acoustic data.rar",
        "C:/leak acoustic data.rar",
        "a\\leak acoustic data.rar",
    ],
)
def test_rehashed_audio_replay_cannot_access_outside_source_root(tmp_path, monkeypatch, filename):
    body = {"archive_filename": filename}
    record = dict(body, record_digest=digest(body))
    monkeypatch.setattr(
        "pipeline_integrity.audio_data.read_training_audio",
        lambda *a, **kw: pytest.fail("unsafe source was accessed"),
    )
    with pytest.raises(ValueError, match="source-root basename"):
        verify_training_audio_record(record, source_root=tmp_path, tar_executable=tmp_path / "must-not-run")


def test_pcm_direct_parser_retains_reader_identity():
    record = parse_pcm_wave(wav([0] * 8))
    assert len(record["module_sha256"]) == len(record["wave_reader_sha256"]) == 64
    assert record["python_version"] and record["physical_amplitude_unit"] is None
