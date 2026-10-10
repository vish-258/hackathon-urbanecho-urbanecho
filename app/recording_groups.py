"""Real ten-second playback files assembled from immutable one-second originals.

Transport/measurement records stay one second for timely live alerts. A group
becomes downloadable only after all ten contiguous, compatible originals have
been verified. No silence or context is inserted, and no audio copy is stored.
"""
from __future__ import annotations

from collections import Counter
from datetime import timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import AwareDatetime
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import clock
from app.read_access import Read
from app.config import get_settings
from app.daily import source_kind
from app.db import get_db
from app.incident_audio import digest, prepare_playback, stamp, verified_original
from app.models import AudioChunk, Device, DeviceAssignment, RecordingGroup, RecordingGroupPart

PARTS = 10
RATE = 16000
FORMAT = "wav_pcm_s16le_mono"
TIMESTAMP_TOLERANCE = 1 / RATE
COLLECT_GRACE_SECONDS = 30
MAX_PART_ROWS = 21  # A group has ten sequences; excess/conflicts are not hidden.
router = APIRouter(tags=["saved recordings"])
DB = Annotated[Session, Depends(get_db)]


def group_sources(db, group_id):
    return db.scalars(select(AudioChunk).join(RecordingGroupPart, RecordingGroupPart.audio_chunk_id == AudioChunk.id)
        .where(RecordingGroupPart.group_id == group_id).order_by(AudioChunk.sequence, AudioChunk.id).limit(MAX_PART_ROWS)).all()


def source_fingerprint(rows):
    return digest([{name: getattr(row, name) for name in ("id", "checksum", "device_id", "location_id",
        "assignment_id", "session_id", "sequence", "captured_at", "duration_seconds", "sample_rate",
        "audio_format", "capture_interval_ms", "calibration", "location_snapshot")} for row in rows])


def build_group(group, rows, assignment, settings):
    codes, issues = [], []
    def issue(code, message):
        if code not in codes:
            codes.append(code)
            issues.append(message)
    expected = set(range(group.sequence_start, group.sequence_start + PARTS))
    sequences = Counter(row.sequence for row in rows)
    missing = sorted(expected - set(sequences))
    if missing:
        issue("missing_parts", "Some audio has not arrived; this is not yet a complete ten-second recording.")
    if len(rows) >= MAX_PART_ROWS or any(count != 1 for count in sequences.values()):
        issue("conflicting_sequences", "Conflicting recordings share a sequence number; a continuous file cannot be verified.")
    kinds = {source_kind(row) for row in rows}
    calibrations = {digest(row.calibration) for row in rows}
    calibration_present = bool(rows) and len(calibrations) == 1 and rows[0].calibration is not None
    if len(kinds) > 1 or len(calibrations) > 1:
        issue("incompatible_provenance", "The parts have different simulation or calibration profiles and cannot form one file.")
    assigned = bool(assignment and assignment.id == group.assignment_id and assignment.device_id == group.device_id
                    and assignment.location_id == group.location_id and assignment.location_snapshot == group.location_snapshot)
    if not assigned:
        issue("assignment_unverified", "The saved device-to-location assignment could not be verified.")
    spans, verified_sequences = [], set()
    discontinuities = 0
    for row in rows:
        if row.sequence not in expected or sequences[row.sequence] != 1:
            continue
        if (row.device_id != group.device_id or row.location_id != group.location_id
                or row.assignment_id != group.assignment_id or row.session_id != group.session_id
                or row.location_snapshot != group.location_snapshot):
            issue("identity_mismatch", "A part does not belong to this saved device, session and location.")
            continue
        end = row.captured_at + timedelta(seconds=row.duration_seconds)
        if not assigned or row.captured_at < assignment.effective_at or (assignment.ended_at and end > assignment.ended_at):
            issue("assignment_boundary", "A part crosses a saved location-assignment boundary.")
            continue
        if row.audio_format != FORMAT or row.sample_rate != RATE or row.duration_seconds != 1:
            issue("incompatible_format", "The saved audio uses an incompatible recording format.")
            continue
        if row.capture_interval_ms not in (None, 1000):
            issue("capture_gaps", "The device used intermittent capture; these parts are not continuous ten-second audio.")
        expected_time = group.captured_at + timedelta(seconds=row.sequence - group.sequence_start)
        if abs((row.captured_at - expected_time).total_seconds()) > TIMESTAMP_TOLERANCE:
            discontinuities += 1
            issue("timestamp_gap_or_overlap", "Capture timestamps contain a gap or overlap; missing time is not filled with silence.")
        try:
            _, info = verified_original(row, settings)
        except FileNotFoundError:
            issue("original_missing", "An original audio file is unavailable; restore it before playing this group.")
            continue
        except (OSError, ValueError):
            issue("original_invalid", "An original audio file failed its format or integrity check.")
            continue
        if info.sample_width != 2 or info.sample_rate != RATE or info.frame_count != RATE:
            issue("original_invalid", "An original audio file failed its format or integrity check.")
            continue
        verified_sequences.add(row.sequence)
        spans.append({"audio_id": str(row.id), "checksum": row.checksum, "start_frame": 0, "end_frame": RATE,
                      "started_at": stamp(row.captured_at), "ended_at": stamp(end)})
    complete = len(spans) == PARTS and not codes
    fatal = any(code in codes for code in ("conflicting_sequences", "incompatible_provenance", "assignment_unverified",
                 "identity_mismatch", "assignment_boundary", "incompatible_format", "original_invalid"))
    deadline = group.captured_at + timedelta(seconds=PARTS + COLLECT_GRACE_SECONDS)
    if complete:
        status = "ready"
    elif fatal:
        status = "failed"
    elif rows and not spans and "original_missing" in codes:
        status = "no_audio"
    elif missing and clock.now() < deadline and not any(code in codes for code in ("capture_gaps", "timestamp_gap_or_overlap", "original_missing")):
        status = "collecting"
    else:
        status = "partial"
    missing_runs = sum(sequence - 1 not in missing for sequence in missing)
    manifest = {"version": "continuous-pcm16-ten-seconds-v1", "device_id": str(group.device_id),
        "assignment_id": str(group.assignment_id), "location_id": str(group.location_id),
        "segments": spans, "sample_rate": RATE, "channels": 1, "bit_depth": 16,
        "duration_seconds": len(spans), "received_seconds": len(spans), "target_duration_seconds": PARTS,
        "recording_count": len(verified_sequences), "expected_recordings": PARTS,
        "missing_sequences": missing, "unusable_sequences": sorted(expected - verified_sequences - set(missing)),
        "issues": issues, "issue_codes": codes, "gap_count": missing_runs + discontinuities,
        "source_kind": next(iter(kinds)) if len(kinds) == 1 else "mixed" if kinds else "unknown",
        "calibration_present": calibration_present,
        "calibration_status": "mixed" if len(calibrations) > 1 else "profile_saved" if calibration_present else "uncalibrated",
        "file_available": complete, "status": status}
    return manifest


def group_dict(row, external_id=None):
    manifest = row.manifest or {}
    status = "collecting" if row.dirty else row.status
    available = status == "ready" and bool(row.revision) and bool(manifest.get("file_available"))
    return {"id": row.id, "kind": "recording_group", "device_id": row.device_id,
        "device_external_id": external_id, "location_id": row.location_id, "location_snapshot": row.location_snapshot,
        "session_id": row.session_id, "sequence_start": row.sequence_start,
        "captured_at": row.captured_at, "ended_at": row.captured_at + timedelta(seconds=PARTS),
        "received_at": row.received_at, "last_received_at": row.last_received_at, "created_at": row.created_at,
        "status": status, "file_available": available, "available": available, "revision": row.revision,
        "target_duration_seconds": PARTS, "duration_seconds": manifest.get("duration_seconds", 0),
        "received_seconds": manifest.get("received_seconds", 0), "recording_count": manifest.get("recording_count", 0),
        "expected_recordings": PARTS, "missing_sequences": manifest.get("missing_sequences", []),
        "unusable_sequences": manifest.get("unusable_sequences", []), "issues": manifest.get("issues", []),
        "issue_codes": manifest.get("issue_codes", []), "gap_count": manifest.get("gap_count", 0),
        "source_kind": manifest.get("source_kind", "unknown"),
        "calibration_present": manifest.get("calibration_present", False),
        "calibration_status": manifest.get("calibration_status", "uncalibrated"),
        "sample_rate": RATE, "channels": 1, "bit_depth": 16, "generated_at": row.generated_at,
        "error": row.error}


@router.get("/recordings")
def recordings(db: DB, access: Read, location_id: UUID | None = None, device_id: UUID | None = None,
               since: AwareDatetime | None = None, until: AwareDatetime | None = None,
               received_until: AwareDatetime | None = None,
               limit: Annotated[int, Query(ge=1, le=200)] = 100,
               offset: Annotated[int, Query(ge=0, le=10000000)] = 0):
    if since is not None and until is not None and since > until:
        raise HTTPException(422, "since must not be after until")
    location_id = access.location_filter(location_id)
    access.require_device(db, device_id)
    where = []
    if location_id is not None:
        where.append(RecordingGroup.location_id == location_id)
    if device_id is not None:
        where.append(RecordingGroup.device_id == device_id)
    if since is not None:
        where.append(RecordingGroup.captured_at >= since)
    if until is not None:
        where.append(RecordingGroup.captured_at <= until)
    if received_until is not None:
        # Freeze membership even while historical discovery creates older groups.
        # New parts may update an existing row without making it disappear.
        where.extend((RecordingGroup.received_at <= received_until, RecordingGroup.created_at <= received_until))
    total = db.scalar(select(func.count()).select_from(RecordingGroup).where(*where))
    rows = db.execute(select(RecordingGroup, Device.external_id).join(Device, Device.id == RecordingGroup.device_id)
        .where(*where).order_by(RecordingGroup.captured_at.desc(), RecordingGroup.id.desc()).offset(offset).limit(limit)).all()
    return {"items": [group_dict(row, code) for row, code in rows], "total": total, "limit": limit, "offset": offset}


def require_group(db, group_id):
    row = db.get(RecordingGroup, group_id)
    if row is None:
        raise HTTPException(404, "Saved recording group not found")
    return row


@router.get("/recordings/{group_id}")
def recording(group_id: UUID, db: DB, access: Read):
    row = require_group(db, group_id)
    access.require_location(row.location_id)
    access.require_manifest(db, row.manifest)
    return group_dict(row, db.scalar(select(Device.external_id).where(Device.id == row.device_id)))


@router.get("/recordings/{group_id}/file")
def recording_file(group_id: UUID, db: DB, access: Read,
                   revision: Annotated[str | None, Query(pattern=r"^[a-f0-9]{64}$")] = None):
    row = require_group(db, group_id)
    access.require_location(row.location_id)
    access.require_manifest(db, row.manifest)
    if row.status != "ready" or row.dirty or not row.manifest or not row.manifest.get("file_available"):
        raise HTTPException(409, "A complete, continuous ten-second recording is not available for this group")
    if revision is not None and row.revision != revision:
        raise HTTPException(409, "The saved recording changed; refresh its details before playing")
    assignment = db.get(DeviceAssignment, row.assignment_id)
    if (assignment is None or assignment.device_id != row.device_id or assignment.location_id != row.location_id
            or row.captured_at < assignment.effective_at
            or (assignment.ended_at is not None and row.captured_at + timedelta(seconds=PARTS) > assignment.ended_at)):
        raise HTTPException(409, "This recording crosses a saved location-assignment boundary and cannot be played as one file")
    try:
        if len(row.manifest["segments"]) != PARTS or any(span["start_frame"] != 0 or span["end_frame"] != RATE for span in row.manifest["segments"]):
            raise ValueError("Invalid ten-second manifest")
        stream, length = prepare_playback(db, row.manifest, get_settings())
        if length != PARTS * RATE * 2 + 44:
            raise ValueError("Invalid ten-second WAV length")
    except (OSError, ValueError):
        raise HTTPException(503, "An original audio file is unavailable or failed its integrity check") from None
    etag = row.revision
    db.rollback()
    return StreamingResponse(stream, media_type="audio/wav", headers={"Content-Length": str(length),
        "Content-Disposition": f'attachment; filename="urbanecho-recording-{group_id}.wav"',
        "Cache-Control": "private, no-store", "ETag": f'"{etag}"', "X-Content-Type-Options": "nosniff"})
