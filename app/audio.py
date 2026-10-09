"""Strict, lossless PCM WAV validation and versioned digital RMS calculation."""
from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class AudioValidationError(ValueError):
    """The uploaded bytes do not match the selected PCM transport format."""


class AudioTooLargeError(AudioValidationError):
    """The actual audio bytes exceed the configured size limit (HTTP 413)."""


@dataclass(frozen=True)
class WavInfo:
    duration_seconds: float
    sample_rate: int
    frame_count: int
    data_offset: int
    data_bytes: int
    sample_width: int = 3


def validate_wav(path: Path, settings: Any, *, allow_pcm16: bool = False) -> WavInfo:
    """Validate exact RIFF extent, chunk boundaries, PCM format and upload bounds.

    Ancillary RIFF chunks are preserved; there must be exactly one 16-byte fmt
    chunk and one nonempty data chunk. RF64, extensible WAV and 32-bit I2S slots
    are deliberately unsupported. PCM24 remains the default contract; PCM16
    must be explicitly enabled by the compatibility upload path.
    """
    size = path.stat().st_size
    if size > settings.max_upload_bytes:
        raise AudioTooLargeError("audio exceeds the configured upload-size limit")
    if size < 44:
        raise AudioValidationError("audio is not a complete RIFF WAV file")
    with path.open("rb") as stream:
        header = stream.read(12)
        if header[:4] != b"RIFF" or header[8:12] != b"WAVE":
            raise AudioValidationError("only conventional RIFF PCM WAV is supported")
        if struct.unpack_from("<I", header, 4)[0] + 8 != size:
            raise AudioValidationError("RIFF size must exactly match the uploaded file")
        sample_rate = None
        sample_width = None
        data_offset = None
        data_bytes = None
        position = 12
        while position < size:
            if size - position < 8:
                raise AudioValidationError("truncated RIFF chunk header")
            stream.seek(position)
            name, length = struct.unpack("<4sI", stream.read(8))
            payload = position + 8
            next_position = payload + length + (length & 1)
            if next_position > size:
                raise AudioValidationError("RIFF chunk extends beyond the file")
            if name == b"fmt ":
                if sample_rate is not None or length != 16:
                    raise AudioValidationError("exactly one conventional 16-byte PCM fmt chunk is required")
                tag, channels, rate, byte_rate, align, bits = struct.unpack("<HHIIHH", stream.read(16))
                allowed_formats = {(1, 1, 3, 24)}
                if allow_pcm16:
                    allowed_formats.add((1, 1, 2, 16))
                if (tag, channels, align, bits) not in allowed_formats:
                    depth = "16- or 24-bit" if allow_pcm16 else "24-bit"
                    raise AudioValidationError(f"audio must be mono, uncompressed, packed signed {depth} PCM")
                if rate not in settings.allowed_sample_rates:
                    raise AudioValidationError("unsupported audio sample rate")
                if byte_rate != rate * align:
                    raise AudioValidationError("invalid PCM byte rate")
                sample_rate = rate
                sample_width = align
            elif name == b"data":
                if data_offset is not None:
                    raise AudioValidationError("multiple PCM data chunks are not supported")
                if sample_rate is None:
                    raise AudioValidationError("fmt chunk must precede the data chunk")
                if length == 0 or length % sample_width:
                    raise AudioValidationError(f"PCM data must contain complete, nonempty {sample_width * 8}-bit frames")
                data_offset, data_bytes = payload, length
            position = next_position
        if sample_rate is None or sample_width is None or data_offset is None or data_bytes is None:
            raise AudioValidationError("WAV requires both fmt and data chunks")
    count = data_bytes // sample_width
    duration = count / sample_rate
    if duration > settings.max_duration_seconds:
        raise AudioValidationError("audio duration exceeds the configured limit")
    return WavInfo(duration, sample_rate, count, data_offset, data_bytes, sample_width)


@dataclass(frozen=True)
class AudioStatistics:
    digital_dbfs: float | None
    quality_status: str
    clipped_samples: int


def analyze_audio(path: Path, info: WavInfo) -> AudioStatistics:
    """Unweighted, DC-removed RMS relative to full scale; silence is null.

    Integer accumulation avoids precision loss in subtracting a large DC bias.
    Nothing is rewritten and original samples remain untouched.
    """
    total = 0
    squares = 0
    remaining = info.data_bytes
    clipped_samples = 0
    width = info.sample_width
    if width not in (2, 3):
        raise AudioValidationError("unsupported PCM sample width")
    full_scale = 1 << (width * 8 - 1)
    with path.open("rb") as stream:
        stream.seek(info.data_offset)
        while remaining:
            data = stream.read(min(width * 65536, remaining))
            if not data or len(data) % width:
                raise AudioValidationError("PCM data was truncated after validation")
            remaining -= len(data)
            for offset in range(0, len(data), width):
                sample = data[offset] | (data[offset + 1] << 8)
                if width == 3:
                    sample |= data[offset + 2] << 16
                if sample & full_scale:
                    sample -= full_scale * 2
                if sample <= -full_scale or sample >= full_scale - 1:
                    clipped_samples += 1
                total += sample
                squares += sample * sample
    # n*sum(x²)-sum(x)² is exact even for a nearly constant large DC signal.
    centered_energy = info.frame_count * squares - total * total
    if centered_energy <= 0:
        return AudioStatistics(None, "clipped" if clipped_samples else "silence", clipped_samples)
    rms = math.sqrt(centered_energy) / info.frame_count
    digital = 20.0 * math.log10(rms / full_scale)
    return AudioStatistics(digital, "clipped" if clipped_samples else "good", clipped_samples)


def digital_level_dbfs(path: Path, info: WavInfo) -> float | None:
    """Compatibility wrapper returning only the digital level; silence is null."""
    return analyze_audio(path, info).digital_dbfs
