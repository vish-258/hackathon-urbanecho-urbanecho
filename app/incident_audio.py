"""Lossless incident evidence manifests and bounded streaming WAV playback.

Timeline gaps are described, never filled with invented silence. The playback
timeline concatenates the available spans; classification receives only spans
inside the incident's half-open interval and resets at every real gap.
"""
from __future__ import annotations

from dataclasses import dataclass
from collections import Counter
from datetime import datetime, timedelta
import hashlib
import json
import math
import struct
from types import SimpleNamespace
import uuid

from sqlalchemy import select, text

from app.audio import validate_wav
from app.daily import source_kind
from app.models import AudioChunk, DeviceAssignment, Measurement, MeasurementEvaluation, StreamState
from app.object_storage import AudioReadLimitError
from app.storage import resolve_audio_path

MAX_RECORDINGS = 10_000
MAX_RECORDED_SECONDS = 7_200
MAX_SOURCE_BYTES = 512 * 1024 * 1024
MANIFEST_VERSION = "incident-original-spans-v1"
# Existing originals remain readable even if today's upload limit is smaller.
WAV_LIMITS = SimpleNamespace(max_upload_bytes=100_000_000, max_duration_seconds=600,
                            allowed_sample_rates=(16000, 32000, 44100, 48000))


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def stamp(value):
    return value.isoformat() if value is not None else None


def parse(value):
    return datetime.fromisoformat(value)


@dataclass
class Sources:
    definition: dict
    chunks: list
    fingerprint: str


def collect_sources(session, incident, snapshot_at, settings):
    """Read only historical identity and bounded indexed capture metadata."""
    start = incident.started_at
    end = incident.ended_at if incident.ended_at is not None else max(start, snapshot_at)
    before, after = settings.incident_context_before_seconds, settings.incident_context_after_seconds
    wanted_end = end + timedelta(seconds=after) if incident.ended_at else end
    playback_start = start - timedelta(seconds=before)
    playback_end = max(playback_start, min(wanted_end, snapshot_at))
    stream = session.get(StreamState, incident.stream_id) if incident.stream_id else None
    assignment = session.get(DeviceAssignment, stream.assignment_id) if stream else None
    valid = bool(stream and assignment and stream.device_id == incident.device_id
                 and stream.location_id == incident.location_id and assignment.device_id == incident.device_id
                 and assignment.location_id == incident.location_id)
    definition = {
        "manifest_version": MANIFEST_VERSION, "incident_id": str(incident.id),
        "device_id": str(incident.device_id), "location_id": str(incident.location_id),
        "assignment_id": str(assignment.id) if valid else None,
        "stream_id": str(incident.stream_id) if incident.stream_id else None,
        "stream_key": stream.stream_key if valid else None,
        "incident_started_at": stamp(start), "incident_ended_at": stamp(end),
        "incident_closed_at": stamp(incident.ended_at),
        "started_at": stamp(playback_start), "ended_at": stamp(playback_end),
        "context_before_seconds": before, "context_after_seconds": after,
        "provisional": incident.ended_at is None or snapshot_at < wanted_end + timedelta(seconds=settings.incident_analysis_settle_seconds),
        "association_verified": valid,
        "assignment_started_at": stamp(assignment.effective_at) if valid else None,
        "assignment_ended_at": stamp(assignment.ended_at) if valid else None,
    }
    chunks = []
    if valid and playback_end > playback_start:
        # The format belongs to the live reading that actually opened this
        # incident. A late overlapping upload must not replace that anchor.
        opening = session.scalar(select(AudioChunk).join(Measurement, Measurement.audio_chunk_id == AudioChunk.id)
            .join(MeasurementEvaluation, MeasurementEvaluation.measurement_id == Measurement.id).where(
                AudioChunk.device_id == incident.device_id, AudioChunk.location_id == incident.location_id,
                AudioChunk.assignment_id == assignment.id, AudioChunk.captured_at == incident.started_at,
                MeasurementEvaluation.stream_id == stream.id, MeasurementEvaluation.live.is_(True),
                MeasurementEvaluation.breach.is_(True))
            .order_by(Measurement.result_order).limit(1))
        if opening is not None:
            definition["opening_audio_id"] = str(opening.id)
            definition["format_sample_rate"] = opening.sample_rate
            definition["format_bit_depth"] = 16 if opening.audio_format == "wav_pcm_s16le_mono" else 24
            definition["source_kind"] = source_kind(opening)
        # The DB enforces a 600-second maximum source duration. This lookback
        # includes crossing captures while retaining the device/capture index.
        candidates = session.scalars(select(AudioChunk).where(
            AudioChunk.device_id == incident.device_id, AudioChunk.location_id == incident.location_id,
            AudioChunk.assignment_id == assignment.id,
            AudioChunk.captured_at >= playback_start - timedelta(seconds=600),
            AudioChunk.captured_at < playback_end,
            AudioChunk.captured_at + AudioChunk.duration_seconds * text("interval '1 second'") > playback_start,
        ).order_by(AudioChunk.captured_at, AudioChunk.id).limit(MAX_RECORDINGS + 1)).all()
        chunks = candidates
        definition["source_limit_reached"] = len(candidates) > MAX_RECORDINGS
    else:
        definition["source_limit_reached"] = False
    signatures = [{"id": str(row.id), "checksum": row.checksum, "captured_at": stamp(row.captured_at),
        "duration_seconds": row.duration_seconds, "sample_rate": row.sample_rate,
        "audio_format": row.audio_format, "threshold_type": row.threshold_type,
        "interval_seconds": row.interval_seconds, "source_kind": source_kind(row)} for row in chunks]
    return Sources(definition, chunks[:MAX_RECORDINGS], digest({"definition": definition, "sources": signatures}))


def verified_original(chunk, settings):
    path = resolve_audio_path(chunk.file_path, settings, checksum=chunk.checksum)
    if path.stat().st_size > WAV_LIMITS.max_upload_bytes:
        raise ValueError("Original audio exceeds the supported size")
    with path.open("rb") as source:
        if hashlib.file_digest(source, "sha256").hexdigest() != chunk.checksum:
            raise ValueError("Original audio checksum differs")
    info = validate_wav(path, WAV_LIMITS, allow_pcm16=True)
    if info.sample_rate != chunk.sample_rate or abs(info.duration_seconds - chunk.duration_seconds) > 1 / info.sample_rate:
        raise ValueError("Original audio metadata differs")
    return path, info


def _frames(start, end, chunk, info):
    first = max(0, math.ceil((start - chunk.captured_at).total_seconds() * info.sample_rate - 1e-6))
    last = min(info.frame_count, math.floor((end - chunk.captured_at).total_seconds() * info.sample_rate + 1e-6))
    return first, max(first, last)


def _gap(start, end):
    return {"started_at": stamp(start), "ended_at": stamp(end), "duration_seconds": (end - start).total_seconds()}


def build_manifest(sources, settings):
    """Verify sources, trim exact frames, deduplicate overlaps deterministically."""
    definition, chunks = sources.definition, sources.chunks
    start, end = parse(definition["started_at"]), parse(definition["ended_at"])
    core_start, core_end = parse(definition["incident_started_at"]), parse(definition["incident_ended_at"])
    assignment_start = parse(definition["assignment_started_at"]) if definition.get("assignment_started_at") else start
    assignment_end = parse(definition["assignment_ended_at"]) if definition.get("assignment_ended_at") else end
    excluded, verified = [], {}
    # Choose the incident's actual format/provenance before pre-roll. Never
    # allow a different context format to exclude all the core incident audio.
    ordered = sorted(chunks, key=lambda row: (not (row.captured_at < core_end and
                     row.captured_at + timedelta(seconds=row.duration_seconds) > core_start), row.captured_at, row.id))
    compatible = ((definition["format_sample_rate"], definition["format_bit_depth"] // 8, definition["source_kind"])
                  if definition.get("opening_audio_id") else None)
    source_bytes, read_limit_reached = 0, False
    stream_parts = (definition.get("stream_key") or "").split("|")
    for chunk in ordered:
        if len(stream_parts) != 4 or chunk.threshold_type != stream_parts[0] or str(chunk.interval_seconds) != stream_parts[3]:
            excluded.append({"audio_id": str(chunk.id), "reason": "incompatible_measurement_stream"})
            continue
        expected_width = 2 if chunk.audio_format == "wav_pcm_s16le_mono" else 3
        if compatible is not None and (chunk.sample_rate, expected_width, source_kind(chunk)) != compatible:
            excluded.append({"audio_id": str(chunk.id), "reason": "incompatible_audio_format_or_source"})
            continue
        remaining_bytes = MAX_SOURCE_BYTES - source_bytes
        if read_limit_reached or remaining_bytes <= 0:
            read_limit_reached = True
            excluded.append({"audio_id": str(chunk.id), "reason": "source_read_limit"})
            continue
        try:
            size = resolve_audio_path(chunk.file_path, settings, checksum=chunk.checksum,
                                      max_bytes=remaining_bytes).stat().st_size
            if size + source_bytes > MAX_SOURCE_BYTES:
                excluded.append({"audio_id": str(chunk.id), "reason": "source_read_limit"})
                read_limit_reached = True
                continue
            source_bytes += size
            path, info = verified_original(chunk, settings)
        except AudioReadLimitError:
            excluded.append({"audio_id": str(chunk.id), "reason": "source_read_limit"})
            read_limit_reached = True
            continue
        except (OSError, ValueError):
            excluded.append({"audio_id": str(chunk.id), "reason": "original_unavailable_or_invalid"})
            continue
        key = (info.sample_rate, info.sample_width, source_kind(chunk))
        if compatible is None:
            compatible = key
        if key != compatible:
            excluded.append({"audio_id": str(chunk.id), "reason": "incompatible_audio_format_or_source"})
            continue
        verified[chunk.id] = (path, info)
    spans, segments, gaps = [], [], []
    cursor, core_cursor = start, None
    frame_total, core_seconds = 0, 0.0
    truncated = definition["source_limit_reached"]
    reason = "Recording count exceeds the 10,000-source incident limit." if truncated else None
    if read_limit_reached:
        truncated, reason = True, "Original audio exceeds the 512 MiB source-read limit."
    rate, width, kind = compatible or (None, None, "unknown")
    for chunk in chunks:
        if chunk.id not in verified:
            continue
        path, info = verified[chunk.id]
        first, last = _frames(max(start, cursor, assignment_start), min(end, assignment_end), chunk, info)
        remaining = int(MAX_RECORDED_SECONDS * info.sample_rate) - frame_total
        if last - first > remaining:
            last = first + max(0, remaining)
            truncated, reason = True, "Playable audio exceeds the two-hour recorded-audio limit."
        if last <= first:
            continue
        span_start = chunk.captured_at + timedelta(seconds=first / info.sample_rate)
        span_end = chunk.captured_at + timedelta(seconds=last / info.sample_rate)
        if span_start > cursor:
            gaps.append(_gap(cursor, span_start))
        spans.append({"audio_id": str(chunk.id), "checksum": chunk.checksum,
            "start_frame": first, "end_frame": last, "started_at": stamp(span_start), "ended_at": stamp(span_end)})
        frame_total += last - first
        cursor = span_end
        core_first, core_last = _frames(max(core_start, span_start), min(core_end, span_end), chunk, info)
        if core_last > core_first:
            actual_start = chunk.captured_at + timedelta(seconds=core_first / info.sample_rate)
            actual_end = chunk.captured_at + timedelta(seconds=core_last / info.sample_rate)
            segments.append({"path": path, "start_frame": core_first, "end_frame": core_last,
                             "break_before": core_cursor is None or actual_start > core_cursor})
            core_seconds += (core_last - core_first) / info.sample_rate
            core_cursor = actual_end
    if cursor < end:
        gaps.append(_gap(cursor, end))
    duration = frame_total / rate if rate else 0
    window = max(0, (end - start).total_seconds())
    manifest = {**definition, "segments": spans, "gaps": gaps, "gap_count": len(gaps),
        "excluded": excluded, "excluded_count": len(excluded),
        "exclusion_reasons": dict(Counter(item["reason"] for item in excluded)),
        "truncated": truncated, "truncation_reason": reason,
        "duration_seconds": duration, "coverage_seconds": duration,
        "coverage_percent": 100 * duration / window if window else 0,
        "window_duration_seconds": window, "core_coverage_seconds": core_seconds,
        "core_window_duration_seconds": max(0, (core_end - core_start).total_seconds()),
        "recording_count": len(spans), "sample_rate": rate, "bit_depth": width * 8 if width else None,
        "source_kind": kind, "available": bool(spans), "channels": 1,
        "continuity_note": "Missing intervals are omitted from playback; they are not silence. Classification never joins across those gaps."}
    return manifest, segments


def public_manifest(manifest, revision=None):
    if not manifest:
        return {"available": False, "revision": None, "duration_seconds": 0, "recording_count": 0,
                "gaps": [], "gap_count": 0, "excluded_count": 0, "truncated": False}
    # Original file locations and internal spans are not public API data.
    result = {key: value for key, value in manifest.items() if key not in {"segments", "excluded", "stream_key"}}
    result["gaps"] = manifest["gaps"][:100]
    result["gaps_list_truncated"] = len(manifest["gaps"]) > 100
    result["revision"] = revision
    return result


def prepare_playback(session, manifest, settings):
    """Check originals before emitting HTTP headers, then stream fixed spans."""
    spans = manifest.get("segments", [])
    if not spans or len(spans) > MAX_RECORDINGS:
        raise ValueError("Incident audio is unavailable")
    identifiers = [uuid.UUID(span["audio_id"]) for span in spans]
    rows = {row.id: row for row in session.scalars(select(AudioChunk).where(AudioChunk.id.in_(identifiers)))}
    prepared, total = [], 0
    for span in spans:
        chunk = rows.get(uuid.UUID(span["audio_id"]))
        if (chunk is None or str(chunk.device_id) != manifest["device_id"]
                or str(chunk.location_id) != manifest["location_id"] or str(chunk.assignment_id) != manifest["assignment_id"]
                or chunk.checksum != span["checksum"]):
            raise ValueError("Incident source identity differs")
        path, info = verified_original(chunk, settings)
        first, last = span["start_frame"], span["end_frame"]
        if (type(first) is not int or type(last) is not int or not 0 <= first < last <= info.frame_count
                or info.sample_rate != manifest["sample_rate"] or info.sample_width * 8 != manifest["bit_depth"]):
            raise ValueError("Incident source format differs")
        length = (last - first) * info.sample_width
        total += length
        prepared.append((path, info.data_offset + first * info.sample_width, length))
    rate, width = manifest["sample_rate"], manifest["bit_depth"] // 8
    if total > MAX_RECORDED_SECONDS * rate * width:
        raise ValueError("Incident playback exceeds its limit")
    padding = total & 1
    header = struct.pack("<4sI4s4sIHHIIHH4sI", b"RIFF", 36 + total + padding, b"WAVE", b"fmt ", 16,
                         1, 1, rate, rate * width, width, width * 8, b"data", total)
    def stream():
        yield header
        for path, offset, length in prepared:
            with path.open("rb") as source:
                source.seek(offset)
                remaining = length
                while remaining:
                    data = source.read(min(65536, remaining))
                    if not data:
                        raise OSError("Original audio changed during playback")
                    remaining -= len(data)
                    yield data
        if padding:
            yield b"\0"
    return stream(), 44 + total + padding
