"""Unit coverage for the precisely documented audio transport and measurement."""
import hashlib
import io
import math
import struct
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.audio import AudioTooLargeError, AudioValidationError, digital_level_dbfs, validate_wav
from app.processing import calculate_measurement
from app.storage import cleanup_staged, finalize_audio, resolve_audio_path, stage_audio


def wav(samples, *, rate=16000, bits=24, channels=1, tag=1, fmt_extra=b"", extra=b""):
    width = bits // 8
    payload = b"".join(int(sample).to_bytes(width, "little", signed=True) for sample in samples)
    fmt = struct.pack("<HHIIHH", tag, channels, rate, rate * channels * width, channels * width, bits) + fmt_extra
    body = b"WAVEfmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(payload)) + payload
    if len(payload) & 1:
        body += b"\0"
    body += extra
    return b"RIFF" + struct.pack("<I", len(body)) + body


@pytest.fixture
def settings(tmp_path):
    return SimpleNamespace(audio_root=tmp_path, max_upload_bytes=1000000,
                           max_duration_seconds=60, allowed_sample_rates=[16000, 48000])


def write_audio(settings, data):
    path = settings.audio_root / "input.wav"
    path.write_bytes(data)
    return path


def test_exact_signed_pcm24_rms_and_dc_removal(settings):
    samples = [-(1 << 22), 1 << 22] * 100
    path = write_audio(settings, wav(samples))
    info = validate_wav(path, settings)
    assert (info.sample_rate, info.frame_count) == (16000, 200)
    assert info.duration_seconds == 200 / 16000
    assert digital_level_dbfs(path, info) == pytest.approx(20 * math.log10(0.5))
    path.write_bytes(wav([sample + 12345 for sample in samples]))
    assert digital_level_dbfs(path, validate_wav(path, settings)) == pytest.approx(20 * math.log10(0.5))


@pytest.mark.parametrize("level", [0, 5000000, -(1 << 23), (1 << 23) - 1])
def test_silence_and_constant_dc_are_json_safe_null(settings, level):
    path = write_audio(settings, wav([level] * 100))
    assert digital_level_dbfs(path, validate_wav(path, settings)) is None


@pytest.mark.parametrize("options", [
    {"bits": 16}, {"bits": 32}, {"channels": 2}, {"tag": 3},
    {"tag": 0xFFFE}, {"rate": 8000}, {"fmt_extra": b"\0\0"},
])
def test_rejects_other_wav_formats(settings, options):
    path = write_audio(settings, wav([1, 2], **options))
    with pytest.raises(AudioValidationError):
        validate_wav(path, settings)


def test_rejects_truncation_trailing_bytes_and_bad_data_length(settings):
    data = wav([1, 2])
    for invalid in [data[:-1], data + b"trailing", data[:40] + struct.pack("<I", 5) + data[44:]]:
        path = write_audio(settings, invalid)
        with pytest.raises(AudioValidationError):
            validate_wav(path, settings)


def test_handles_padded_odd_data_and_preserves_ancillary_chunks(settings):
    extra = b"LIST" + struct.pack("<I", 3) + b"abc\0"
    data = wav([1, -2, 3], extra=extra)
    path = write_audio(settings, data)
    assert validate_wav(path, settings).frame_count == 3
    assert path.read_bytes() == data


def test_size_duration_and_empty_audio_bounds(settings):
    path = write_audio(settings, wav([0] * 16000))
    settings.max_upload_bytes = 100
    with pytest.raises(AudioTooLargeError):
        validate_wav(path, settings)
    settings.max_upload_bytes = 1000000
    settings.max_duration_seconds = 0.5
    with pytest.raises(AudioValidationError):
        validate_wav(path, settings)
    path.write_bytes(wav([]))
    with pytest.raises(AudioValidationError):
        validate_wav(path, settings)


def test_storage_preserves_exact_bytes_and_generates_safe_unique_paths(settings):
    data = wav([-(1 << 23), (1 << 23) - 1] * 10)
    staged = stage_audio(io.BytesIO(data), settings)
    assert staged.checksum == hashlib.sha256(data).hexdigest()
    first = finalize_audio(staged, settings)
    second = finalize_audio(stage_audio(io.BytesIO(data), settings), settings)
    assert first != second
    assert resolve_audio_path(first, settings).read_bytes() == data
    cleanup_staged(staged)
    assert not staged.path.exists()
    with pytest.raises(ValueError):
        resolve_audio_path("../escape.wav", settings)
    settings.max_upload_bytes = 10
    with pytest.raises(AudioTooLargeError):
        stage_audio(io.BytesIO(data), settings)
    assert list((settings.audio_root / ".staging").iterdir()) == []


def fixture_chunk(settings):
    data = wav([-(1 << 22), 1 << 22] * 8000)
    staged = stage_audio(io.BytesIO(data), settings)
    reference = finalize_audio(staged, settings)
    return SimpleNamespace(file_path=reference, checksum=staged.checksum, sample_rate=16000,
                           duration_seconds=1, interval_seconds=1, threshold_type="spl_z_leq",
                           threshold_value=60, captured_at=datetime(2026, 1, 2, tzinfo=timezone.utc), calibration=None)


def test_uncalibrated_input_never_emits_physical_breach(settings):
    chunk = fixture_chunk(settings)
    result = calculate_measurement(chunk, settings)
    assert result.digital_dbfs == pytest.approx(-6.020599913)
    assert result.value_db is None
    assert result.calibration_status == "calibration_required"
    assert result.breach is False


def test_synthetic_calibration_and_interval_guard(settings):
    chunk = fixture_chunk(settings)
    # Explicitly synthetic fixture: this is NOT an INMP441 hardware offset.
    chunk.calibration = {"method": "spl_z_leq", "version": "synthetic-only-v1", "offset_db": 100,
                         "sample_rate": 16000, "calibrated_at": "2026-01-01T00:00:00Z",
                         "valid_until": "2027-01-01T00:00:00Z"}
    result = calculate_measurement(chunk, settings)
    assert result.value_db == pytest.approx(93.979400087)
    assert result.calibration_status == "calibrated"
    assert result.calibration_version == "synthetic-only-v1"
    assert result.breach is False
    chunk.calibration["sample_rate"] = 48000
    assert calculate_measurement(chunk, settings).calibration_status == "calibration_required"
    chunk.calibration["sample_rate"] = 16000
    chunk.calibration["valid_until"] = (chunk.captured_at + timedelta(seconds=0.5)).isoformat()
    assert calculate_measurement(chunk, settings).breach is False
    chunk.interval_seconds = 2
    assert calculate_measurement(chunk, settings).calibration_status == "interval_mismatch"


def test_digital_threshold_is_explicit_and_corruption_is_rejected(settings):
    chunk = fixture_chunk(settings)
    chunk.threshold_type = "dbfs_rms"
    chunk.threshold_value = -10
    result = calculate_measurement(chunk, settings)
    assert result.breach is False and result.calibration_status == "not_required"
    path = resolve_audio_path(chunk.file_path, settings)
    data = bytearray(path.read_bytes())
    data[-1] ^= 1
    path.write_bytes(data)
    with pytest.raises(ValueError, match="checksum"):
        calculate_measurement(chunk, settings)
