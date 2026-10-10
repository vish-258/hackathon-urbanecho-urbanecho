"""Bounded, restart-safe recording-file assembly independent of ML and alerts."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta
import logging
import uuid

from sqlalchemy import or_, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from app import clock
from app.config import get_settings
from app.db import SessionLocal, wait_for_database
from app.incident_audio import digest
from app.models import AudioChunk, DeviceAssignment, RecordingGroup, RecordingGroupPart, RecordingGroupScanState
from app.recording_groups import COLLECT_GRACE_SECONDS, FORMAT, PARTS, RATE, build_group, group_sources, source_fingerprint

log = logging.getLogger(__name__)
BATCH_SIZE = 256
LEASE_SECONDS = 30


def discover_groups():
    """Visit recent new parts plus a persistent historical receipt cursor.

    Saved originals are only read. The scanner's own singleton prevents cursor
    races; no live event, device, ingestion or measurement lock is acquired.
    """
    now = clock.now()
    inserted_count = 0
    with SessionLocal() as db, db.begin():
        db.execute(insert(RecordingGroupScanState).values(id=1, history_available_at=now).on_conflict_do_nothing())
        state = db.scalar(select(RecordingGroupScanState).where(RecordingGroupScanState.id == 1).with_for_update())
        eligible = (AudioChunk.audio_format == FORMAT, AudioChunk.duration_seconds == 1,
                    AudioChunk.sample_rate == RATE, AudioChunk.assignment_id.is_not(None))
        already = select(RecordingGroupPart.audio_chunk_id).where(RecordingGroupPart.audio_chunk_id == AudioChunk.id).exists()
        recent = db.scalars(select(AudioChunk).where(*eligible, AudioChunk.received_at >= now - timedelta(minutes=5), ~already)
            .order_by(AudioChunk.received_at.desc(), AudioChunk.id.desc()).limit(BATCH_SIZE)).all()
        history = []
        if state.history_available_at <= now:
            query = select(AudioChunk).where(*eligible).order_by(AudioChunk.received_at, AudioChunk.id)
            if state.after_received_at is not None and state.after_audio_id is not None:
                query = query.where(tuple_(AudioChunk.received_at, AudioChunk.id) > tuple_(state.after_received_at, state.after_audio_id))
            history = db.scalars(query.limit(BATCH_SIZE)).all()
            if history:
                state.after_received_at, state.after_audio_id = history[-1].received_at, history[-1].id
            else:
                state.after_received_at, state.after_audio_id = None, None
                state.history_available_at = now + timedelta(seconds=60)
        sources = {row.id: row for row in recent + history}
        grouped = defaultdict(list)
        for chunk in sorted(sources.values(), key=lambda row: (row.received_at, row.id)):
            key = (chunk.device_id, chunk.session_id, chunk.assignment_id, chunk.sequence // PARTS * PARTS)
            grouped[key].append(chunk)
        for (device_id, session_id, assignment_id, sequence_start), chunks in grouped.items():
            first = chunks[0]
            db.execute(insert(RecordingGroup).values(id=uuid.uuid4(), device_id=device_id, session_id=session_id,
                assignment_id=assignment_id, sequence_start=sequence_start, location_id=first.location_id,
                location_snapshot=first.location_snapshot,
                captured_at=first.captured_at - timedelta(seconds=first.sequence - sequence_start),
                received_at=first.received_at, last_received_at=max(row.received_at for row in chunks),
                status="collecting", dirty=True, available_at=now, created_at=now, updated_at=now)
                .on_conflict_do_nothing(constraint="uq_recording_group_identity"))
            group = db.scalar(select(RecordingGroup).where(RecordingGroup.device_id == device_id,
                RecordingGroup.session_id == session_id, RecordingGroup.assignment_id == assignment_id,
                RecordingGroup.sequence_start == sequence_start).with_for_update())
            new_ids = db.scalars(insert(RecordingGroupPart).values([{"audio_chunk_id": row.id, "group_id": group.id,
                "sequence": row.sequence} for row in chunks]).on_conflict_do_nothing().returning(RecordingGroupPart.audio_chunk_id)).all()
            if new_ids:
                group.dirty, group.available_at, group.updated_at = True, now, now
                group.last_received_at = max(group.last_received_at, *(row.received_at for row in chunks))
                inserted_count += len(new_ids)
    return inserted_count


@dataclass(frozen=True)
class GroupClaim:
    group_id: uuid.UUID
    lease_token: uuid.UUID


def claim_group(*, prefer_oldest=False):
    now = clock.now()
    with SessionLocal() as db, db.begin():
        order = ((RecordingGroup.available_at, RecordingGroup.created_at, RecordingGroup.captured_at)
                 if prefer_oldest else (RecordingGroup.captured_at.desc(), RecordingGroup.available_at))
        group = db.scalar(select(RecordingGroup).where(
            or_(RecordingGroup.dirty.is_(True), RecordingGroup.next_check_at <= now),
            RecordingGroup.available_at <= now,
            or_(RecordingGroup.lease_until.is_(None), RecordingGroup.lease_until <= now))
            .order_by(*order, RecordingGroup.id)
            .with_for_update(skip_locked=True).limit(1))
        if group is None:
            return None
        group.dirty = True
        group.lease_token, group.lease_until = uuid.uuid4(), now + timedelta(seconds=LEASE_SECONDS)
        return GroupClaim(group.id, group.lease_token)


def owns(group, claim):
    return bool(group and group.lease_token == claim.lease_token and group.lease_until and group.lease_until > clock.now())


def process_group(claim, settings=None):
    settings = settings or get_settings()
    try:
        with SessionLocal() as db:
            group = db.get(RecordingGroup, claim.group_id)
            if not owns(group, claim):
                return False
            rows = group_sources(db, group.id)
            fingerprint = source_fingerprint(rows)
            assignment = db.get(DeviceAssignment, group.assignment_id)
            assignment_end = assignment.ended_at if assignment else None
        # At most twenty-one source rows and ten seconds of valid PCM are read.
        # Hashing, WAV validation and span construction hold no SQL locks.
        manifest = build_group(group, rows, assignment, settings)
        with SessionLocal() as db, db.begin():
            current = db.scalar(select(RecordingGroup).where(RecordingGroup.id == claim.group_id).with_for_update())
            if not owns(current, claim):
                return False
            current_assignment = db.get(DeviceAssignment, current.assignment_id)
            if (source_fingerprint(group_sources(db, current.id)) != fingerprint
                    or current_assignment is None or current_assignment.ended_at != assignment_end):
                current.lease_token, current.lease_until = None, None
                current.dirty, current.available_at = True, clock.now()
                return False
            current.manifest, current.status = manifest, manifest["status"]
            current.revision = digest({"sources": fingerprint, "manifest": manifest})
            current.dirty, current.error = False, None
            current.generated_at = current.updated_at = clock.now()
            current.lease_token, current.lease_until = None, None
            if current.status == "collecting":
                current.next_check_at = current.captured_at + timedelta(seconds=PARTS + COLLECT_GRACE_SECONDS)
            elif any(code in manifest["issue_codes"] for code in ("original_missing", "original_invalid")):
                current.next_check_at = clock.now() + timedelta(minutes=5)
            else:
                current.next_check_at = None
        return True
    except SQLAlchemyError:
        raise
    except Exception as error:
        log.warning("Recording group %s could not be prepared (%s)", claim.group_id, type(error).__name__)
        with SessionLocal() as db, db.begin():
            group = db.scalar(select(RecordingGroup).where(RecordingGroup.id == claim.group_id).with_for_update())
            if not owns(group, claim):
                return False
            group.status, group.dirty = "failed", False
            group.error = "The saved recording could not be assembled; original recordings and live readings are unchanged."
            group.next_check_at = clock.now() + timedelta(minutes=5)
            group.lease_token, group.lease_until = None, None
            group.updated_at = clock.now()
        return False


def run_once(settings=None, *, prefer_oldest=False):
    discover_groups()
    claim = claim_group(prefer_oldest=prefer_oldest)
    if claim is None:
        return False
    process_group(claim, settings)
    return True


def group_worker_loop(stop, settings):
    iteration = 0
    while not stop.is_set():
        try:
            wait_for_database()
            worked = run_once(settings, prefer_oldest=iteration % 5 == 4)
            iteration += 1
            if not worked:
                stop.wait(1)
        except SQLAlchemyError:
            log.warning("Recording group database unavailable; saved work will resume")
            stop.wait(1)
