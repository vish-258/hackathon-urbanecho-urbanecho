"""Compatibility audio stays native PCM16 and retains the normal quality gates."""
import hashlib
import math
import struct
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.audio import AudioValidationError, WavInfo, analyze_audio, validate_wav
from app.processing import PCM16_PROCESSING_VERSION, PROCESSING_VERSION, calculate_measurement, processing_version_for
from app.schemas import Calibration


def wav(samples, *, width=2):
    payload = b"".join(sample.to_bytes(width, "little", signed=True) for sample in samples)
    fmt = struct.pack("<HHIIHH", 1, 1, 16000, 16000 * width, width, width * 8)
    body = b"WAVEfmt " + struct.pack("<I", len(fmt)) + fmt
    body += b"data" + struct.pack("<I", len(payload)) + payload
    body += b"\0" if len(payload) % 2 else b""
    return b"RIFF" + struct.pack("<I", len(body)) + body


@pytest.fixture
def settings(tmp_path):
    return SimpleNamespace(audio_root=tmp_path, max_upload_bytes=1000000,
                           max_duration_seconds=60, allowed_sample_rates=[16000, 48000])


def save(settings, samples, *, width=2):
    path = settings.audio_root / "audio.wav"
    path.write_bytes(wav(samples, width=width))
    return path


def test_pcm16_requires_explicit_opt_in(settings):
    path = save(settings, [-16384, 16384] * 10)
    with pytest.raises(AudioValidationError, match="24-bit"):
        validate_wav(path, settings)
    info = validate_wav(path, settings, allow_pcm16=True)
    assert (info.sample_width, info.frame_count, info.duration_seconds) == (2, 20, 20 / 16000)
    # Existing callers constructing WavInfo positionally remain PCM24.
    assert WavInfo(1, 16000, 16000, 44, 48000).sample_width == 3


def test_native_pcm16_has_same_normalized_level_as_pcm24(settings):
    samples = [-12000, -5000, 0, 9000, 16000] * 100
    path = save(settings, samples)
    original = path.read_bytes()
    info = validate_wav(path, settings, allow_pcm16=True)
    result16 = analyze_audio(path, info)
    assert path.read_bytes() == original
    path = save(settings, [sample * 256 for sample in samples], width=3)
    result24 = analyze_audio(path, validate_wav(path, settings))
    assert result16.digital_dbfs == pytest.approx(result24.digital_dbfs, abs=1e-12)
    assert result16.quality_status == result24.quality_status == "good"


def test_pcm16_signed_little_endian_and_dc_removal(settings):
    path = save(settings, [-16000, 16000] * 40000)
    initial = analyze_audio(path, validate_wav(path, settings, allow_pcm16=True))
    assert initial.digital_dbfs == pytest.approx(20 * math.log10(16000 / 32768))
    path = save(settings, [-16000 + 1234, 16000 + 1234] * 40000)
    shifted = analyze_audio(path, validate_wav(path, settings, allow_pcm16=True))
    assert shifted.digital_dbfs == pytest.approx(initial.digital_dbfs, abs=1e-12)
    assert shifted.clipped_samples == 0


@pytest.mark.parametrize("samples,clipped,level", [
    ([-32768, 32767], 2, 20 * math.log10(32767.5 / 32768)),
    ([-32768, 0], 1, 20 * math.log10(0.5)),
    ([0, 32767], 1, 20 * math.log10(32767 / 2 / 32768)),
    ([-32767, 32766], 0, 20 * math.log10(32766.5 / 32768)),
    ([32767, 32767], 2, None),
    ([-32768, -32768], 2, None),
    ([12345, 12345], 0, None),
    ([0, 0], 0, None),
])
def test_pcm16_extremes_clipping_and_silence(settings, samples, clipped, level):
    path = save(settings, samples)
    result = analyze_audio(path, validate_wav(path, settings, allow_pcm16=True))
    assert result.clipped_samples == clipped
    assert result.quality_status == ("clipped" if clipped else "silence" if level is None else "good")
    if level is None:
        assert result.digital_dbfs is None
    else:
        assert result.digital_dbfs == pytest.approx(level)


def test_pcm16_rejects_odd_data_even_with_valid_riff_padding(settings):
    data = bytearray(wav([1, 2]))
    # Three declared PCM bytes plus one RIFF pad byte still make a valid RIFF
    # extent, but cannot represent complete two-byte samples.
    struct.pack_into("<I", data, 40, 3)
    path = settings.audio_root / "odd.wav"
    path.write_bytes(data)
    with pytest.raises(AudioValidationError, match="complete, nonempty 16-bit frames"):
        validate_wav(path, settings, allow_pcm16=True)


@pytest.mark.parametrize("offset,encoding,value", [(28, "<I", 48000), (32, "<H", 3), (22, "<H", 2)])
def test_pcm16_rejects_inconsistent_format_fields(settings, offset, encoding, value):
    data = bytearray(wav([1, -2]))
    struct.pack_into(encoding, data, offset, value)
    path = settings.audio_root / "bad-format.wav"
    path.write_bytes(data)
    with pytest.raises(AudioValidationError):
        validate_wav(path, settings, allow_pcm16=True)


def fixture_chunk(settings, *, width=2, audio_format="wav_pcm_s16le_mono"):
    path = save(settings, [-16384, 16384] * 8000, width=width)
    return SimpleNamespace(file_path=path.name, checksum=hashlib.sha256(path.read_bytes()).hexdigest(),
                           audio_format=audio_format, sample_rate=16000, duration_seconds=1,
                           interval_seconds=1, threshold_type="spl_z_leq", threshold_value=60,
                           captured_at=datetime(2026, 10, 9, tzinfo=timezone.utc), calibration=None)


def test_pcm16_worker_requires_physical_calibration_and_preserves_bytes(settings):
    chunk = fixture_chunk(settings)
    path = settings.audio_root / chunk.file_path
    original = path.read_bytes()
    result = calculate_measurement(chunk, settings)
    assert result.processing_version == PCM16_PROCESSING_VERSION
    assert result.digital_dbfs == pytest.approx(20 * math.log10(0.5))
    assert result.value_db is None
    assert result.calibration_status == "calibration_required"
    assert result.quality_status == "good"
    assert result.breach is False
    assert path.read_bytes() == original


def test_pcm16_worker_digital_interval_and_calibration_paths(settings):
    chunk = fixture_chunk(settings)
    chunk.threshold_type = "dbfs_rms"
    digital = calculate_measurement(chunk, settings)
    assert digital.value_db == digital.digital_dbfs
    assert digital.calibration_status == "not_required"
    assert digital.processing_version == PCM16_PROCESSING_VERSION
    assert digital.breach is False
    chunk.interval_seconds = 2
    mismatch = calculate_measurement(chunk, settings)
    assert mismatch.value_db is None and mismatch.calibration_status == "interval_mismatch"
    assert mismatch.processing_version == PCM16_PROCESSING_VERSION
    chunk.interval_seconds = 1
    chunk.threshold_type = "spl_z_leq"
    # Arithmetic fixture only: this offset is not a physical calibration.
    chunk.calibration = {"method": "spl_z_leq", "version": "synthetic-unit-test",
                         "offset_db": 100, "sample_rate": 16000,
                         "calibrated_at": "2026-10-01T00:00:00Z", "valid_until": "2026-11-01T00:00:00Z"}
    old_calibration = calculate_measurement(chunk, settings)
    assert old_calibration.value_db is None and old_calibration.calibration_status == "calibration_required"
    chunk.calibration["pcm_bits"] = 24
    wrong_calibration = calculate_measurement(chunk, settings)
    assert wrong_calibration.value_db is None and wrong_calibration.calibration_status == "calibration_required"
    chunk.calibration["pcm_bits"] = 16
    calibrated = calculate_measurement(chunk, settings)
    assert calibrated.value_db == pytest.approx(100 + 20 * math.log10(0.5))
    assert calibrated.calibration_status == "calibrated"
    assert calibrated.processing_version == PCM16_PROCESSING_VERSION
    assert calibrated.breach is False  # transactional evaluation remains separate


@pytest.mark.parametrize("width,audio_format", [
    (3, "wav_pcm_s16le_mono"), (2, "wav_pcm_s24le_mono"), (2, "unknown"),
])
def test_worker_rejects_recorded_format_mismatch(settings, width, audio_format):
    chunk = fixture_chunk(settings, width=width, audio_format=audio_format)
    with pytest.raises(ValueError):
        calculate_measurement(chunk, settings)


@pytest.mark.parametrize("audio_format", ["wav_pcm_s24le_mono", "pcm_s24le"])
def test_existing_pcm24_worker_version_is_unchanged(settings, audio_format):
    chunk = fixture_chunk(settings, width=3, audio_format=audio_format)
    result = calculate_measurement(chunk, settings)
    assert result.processing_version == PROCESSING_VERSION == "pcm24-dc-rms-quality-v2"


@pytest.mark.parametrize("bits", [None, 24, 16])
def test_pcm24_calibration_legacy_compatibility_and_bit_depth_guard(settings, bits):
    chunk = fixture_chunk(settings, width=3, audio_format="wav_pcm_s24le_mono")
    chunk.calibration = {"method": "spl_z_leq", "version": "synthetic-unit-test",
                         "offset_db": 100, "sample_rate": 16000, "pcm_bits": bits,
                         "calibrated_at": "2026-10-01T00:00:00Z", "valid_until": "2026-11-01T00:00:00Z"}
    result = calculate_measurement(chunk, settings)
    assert result.calibration_status == ("calibration_required" if bits == 16 else "calibrated")


def test_processing_version_available_before_measurement_exists():
    assert processing_version_for(SimpleNamespace(audio_format="wav_pcm_s16le_mono")) == PCM16_PROCESSING_VERSION
    assert processing_version_for(SimpleNamespace(audio_format="wav_pcm_s24le_mono")) == PROCESSING_VERSION
    assert processing_version_for(SimpleNamespace(audio_format="pcm_s24le")) == PROCESSING_VERSION


def test_calibration_schema_supports_explicit_pcm_depth_and_legacy_omission():
    values = {"method": "spl_z_leq", "version": "synthetic-unit-test", "offset_db": 100,
              "sample_rate": 16000, "calibrated_at": "2026-10-01T00:00:00Z", "valid_until": "2026-11-01T00:00:00Z"}
    assert Calibration(**values).pcm_bits is None
    assert Calibration(**values, pcm_bits=16).model_dump()["pcm_bits"] == 16
    assert Calibration(**values, pcm_bits=24).model_dump()["pcm_bits"] == 24
    with pytest.raises(ValueError):
        Calibration(**values, pcm_bits=32)
