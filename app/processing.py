"""File-only audio calculation. Threshold decisions occur transactionally later."""
from __future__ import annotations

import hashlib
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any

from app.audio import analyze_audio, validate_wav
from app.models import AudioChunk
from app.storage import resolve_audio_path

PROCESSING_VERSION = "pcm24-dc-rms-quality-v2"
PCM16_PROCESSING_VERSION = "pcm16-dc-rms-quality-v1"


def processing_version_for(chunk: AudioChunk) -> str:
    """Definition for both processed measurements and pending-recording reports."""
    audio_format = getattr(chunk, "audio_format", "wav_pcm_s24le_mono")
    if audio_format == "wav_pcm_s16le_mono":
        return PCM16_PROCESSING_VERSION
    if audio_format in ("wav_pcm_s24le_mono", "pcm_s24le"):
        return PROCESSING_VERSION
    raise ValueError("unsupported stored audio format")


@dataclass(frozen=True)
class MeasurementResult:
    value_db: float | None
    digital_dbfs: float | None
    measurement_type: str
    calibration_status: str
    calibration_version: str | None
    processing_version: str
    breach: bool
    weighting: str = "none"
    channel_policy: str = "mono"
    quality_status: str = "good"

    def as_dict(self) -> dict:
        return asdict(self)


def _calibration_offset(chunk: AudioChunk) -> tuple[float, str] | None:
    configuration = chunk.calibration
    if not isinstance(configuration, dict):
        return None
    pcm_bits = configuration.get("pcm_bits")
    if getattr(chunk, "audio_format", "wav_pcm_s24le_mono") == "wav_pcm_s16le_mono":
        # A PCM24 capture-chain calibration cannot silently transfer to a
        # firmware path with different sample scaling or microphone gain.
        if type(pcm_bits) is not int or pcm_bits != 16:
            return None
    elif pcm_bits is not None and (type(pcm_bits) is not int or pcm_bits != 24):
        return None
    try:
        if (configuration["method"] != "spl_z_leq"
                or configuration.get("status", "valid") != "valid"
                or configuration.get("weighting", "Z") != "Z"
                or configuration.get("channel_policy", "mono") != "mono"):
            return None
        if (type(configuration["offset_db"]) not in (int, float)
                or type(configuration["sample_rate"]) is not int):
            return None
        offset = float(configuration["offset_db"])
        version = configuration["version"]
        valid_from = datetime.fromisoformat(configuration["calibrated_at"].replace("Z", "+00:00"))
        valid_until = datetime.fromisoformat(configuration["valid_until"].replace("Z", "+00:00"))
        if (not math.isfinite(offset) or not isinstance(version, str) or not version
                or configuration["sample_rate"] != chunk.sample_rate
                or valid_from.tzinfo is None or valid_until.tzinfo is None
                or valid_until <= valid_from
                or chunk.captured_at < valid_from
                or chunk.captured_at + timedelta(seconds=chunk.duration_seconds) > valid_until):
            return None
        return offset, version
    except (KeyError, TypeError, ValueError, OverflowError, AttributeError):
        return None


def calculate_measurement(chunk: AudioChunk, settings: Any) -> MeasurementResult:
    """CPU and file work only: call outside database transactions.

    spl_z_leq is unweighted DC-removed equivalent level over this complete chunk.
    The supplied offset must come from a real measurement/calibration of the full
    microphone chain. It is not a claim of IEC sound-level-meter certification.
    """
    path = resolve_audio_path(chunk.file_path, settings, checksum=chunk.checksum)
    with path.open("rb") as stream:
        checksum = hashlib.file_digest(stream, "sha256").hexdigest()
    if checksum != chunk.checksum:
        raise ValueError("preserved audio checksum does not match its ingestion record")
    audio_format = getattr(chunk, "audio_format", "wav_pcm_s24le_mono")
    expected_width = {"wav_pcm_s24le_mono": 3, "pcm_s24le": 3, "wav_pcm_s16le_mono": 2}.get(audio_format)
    if expected_width is None:
        raise ValueError("unsupported stored audio format")
    info = validate_wav(path, settings, allow_pcm16=expected_width == 2)
    if (info.sample_width != expected_width or info.sample_rate != chunk.sample_rate
            or abs(info.duration_seconds - chunk.duration_seconds) > 1e-9):
        raise ValueError("preserved audio format does not match its ingestion record")
    processing_version = processing_version_for(chunk)
    statistics = analyze_audio(path, info)
    digital = statistics.digital_dbfs
    weighting = "Z" if chunk.threshold_type == "spl_z_leq" else "none"
    extras = {"weighting": weighting, "channel_policy": "mono", "quality_status": statistics.quality_status}
    interval_matches = abs(info.duration_seconds - chunk.interval_seconds) <= max(1 / info.sample_rate, 0.001)
    if not interval_matches:
        return MeasurementResult(None, digital, chunk.threshold_type, "interval_mismatch", None,
                                 processing_version, False, **extras)
    if chunk.threshold_type == "dbfs_rms":
        return MeasurementResult(digital, digital, "dbfs_rms", "not_required", None,
                                 processing_version, False, **extras)
    calibration = _calibration_offset(chunk)
    if calibration is None:
        return MeasurementResult(None, digital, "spl_z_leq", "calibration_required", None,
                                 processing_version, False, **extras)
    offset, version = calibration
    value = digital + offset if digital is not None else None
    return MeasurementResult(value, digital, "spl_z_leq", "calibrated", version,
                             processing_version, False, **extras)
