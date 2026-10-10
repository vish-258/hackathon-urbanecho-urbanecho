"""Durable optional classification, isolated from measurements and live events."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import hashlib
import logging
import math
import uuid

from sqlalchemy import and_, literal, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from app import clock
from app.classification_contract import CATEGORIES, MAPPING_VERSION, MODEL_SHA256, MODEL_VERSION, SCORE_THRESHOLD
from app.config import get_settings
from app.db import SessionLocal
from app.models import AudioChunk, ClassificationScanState, RecordingClassification
from app.storage import resolve_audio_path

log = logging.getLogger(__name__)
VERSION = (MODEL_VERSION, MAPPING_VERSION)


def _version(model):
    return (model.model_version == MODEL_VERSION, model.mapping_version == MAPPING_VERSION)


def _state(session, now):
    session.execute(insert(ClassificationScanState).values(
        model_version=MODEL_VERSION, mapping_version=MAPPING_VERSION,
        scan_available_at=now, worker_status="starting",
    ).on_conflict_do_nothing())
    return session.scalar(select(ClassificationScanState).where(*_version(ClassificationScanState)).with_for_update())


def record_worker_status(status: str, error: str | None = None):
    """Only fixed caller messages are accepted here; never raw exception text."""
    with SessionLocal() as session, session.begin():
        state = _state(session, clock.now())
        state.worker_status, state.worker_error, state.heartbeat_at = status, error, clock.now()


def worker_status(session, settings=None):
    return _worker_status_dict(session.get(ClassificationScanState, VERSION), settings)


def _worker_status_dict(row, settings=None):
    settings = settings or get_settings()
    status = row.worker_status if row else "waiting"
    error = row.worker_error if row else None
    if not settings.classification_enabled:
        status, error = "disabled", "Automatic sound classification is disabled."
    elif row and row.heartbeat_at and (clock.now() - row.heartbeat_at).total_seconds() > max(300, settings.classification_lease_seconds * 2):
        status, error = "unavailable", "The classification worker has not checked in recently."
    return {"worker_status": status, "worker_error": error,
            "worker_heartbeat_at": row.heartbeat_at if row else None,
            "model_version": MODEL_VERSION, "mapping_version": MAPPING_VERSION,
            "enabled": settings.classification_enabled, "classification_scope": settings.classification_scope}


def classification_dict(row, service):
    result = row.result if row and row.status == "completed" else {}
    status = row.status if row else "pending"
    if service.get("classification_scope") == "incidents" and status != "completed":
        status = "not_requested"
    return {"status": status, "queued": row is not None and status != "not_requested",
            "scope": "recording", "automatic_scope": service.get("classification_scope", "recordings"),
            "primary_category": row.primary_category if row else None,
            "category_scores": {}, "top_labels": [], "confidence_status": None,
            "uncertainty_reason": None, "score_threshold": SCORE_THRESHOLD,
            **(result or {}), "model_version": MODEL_VERSION, "mapping_version": MAPPING_VERSION,
            "classified_at": row.classified_at if row else None,
            "error": row.error if row else None, "attempts": row.attempts if row else 0,
            "worker_status": service["worker_status"], "worker_error": service["worker_error"]}


def classifications_for(session, audio_ids):
    """One bounded query for results and global readiness per UI history page."""
    identifiers = set(audio_ids)
    if not identifiers:
        return {}
    # A singleton anchor retains readiness even when none of these recordings
    # has a job yet, and retains jobs if no worker heartbeat has been stored.
    anchor = select(literal(1).label("singleton")).subquery()
    matches = session.execute(select(RecordingClassification, ClassificationScanState).select_from(anchor)
        .outerjoin(ClassificationScanState, and_(*_version(ClassificationScanState)))
        .outerjoin(RecordingClassification, and_(RecordingClassification.audio_chunk_id.in_(identifiers),
                                                *_version(RecordingClassification)))).all()
    rows = {row.audio_chunk_id: row for row, _ in matches if row is not None}
    service = _worker_status_dict(matches[0][1])
    return {identifier: classification_dict(rows.get(identifier), service) for identifier in identifiers}


def _queue_values(chunk, now):
    return dict(id=uuid.uuid4(), audio_chunk_id=chunk.id, model_version=MODEL_VERSION,
                mapping_version=MAPPING_VERSION, status="pending", source_received_at=chunk.received_at,
                attempts=0, available_at=now, created_at=now, updated_at=now)


def enqueue_recording(session, chunk, *, retry=False):
    now = clock.now()
    session.execute(insert(RecordingClassification).values(**_queue_values(chunk, now)).on_conflict_do_nothing(
        constraint="uq_classification_version"))
    row = session.scalar(select(RecordingClassification).where(
        RecordingClassification.audio_chunk_id == chunk.id, *_version(RecordingClassification)).with_for_update())
    if retry and row.status == "failed":
        row.status, row.attempts, row.error = "pending", 0, None
        row.available_at, row.updated_at = now, now
    session.flush()
    return row


def enqueue_missing(settings=None):
    """Recent indexed discovery plus a bounded persistent history cursor.

    Recent discovery covers five minutes of receipt time, including late captures.
    History visits a fixed-size batch, never rescans the whole excluded prefix.
    Cycling after a quiet minute catches any commits older than the cursor.
    """
    settings = settings or get_settings()
    now = clock.now()
    count = 0
    with SessionLocal() as session, session.begin():
        state = _state(session, now)
        exists = select(RecordingClassification.id).where(
            RecordingClassification.audio_chunk_id == AudioChunk.id,
            *_version(RecordingClassification)).exists()
        recent = session.execute(select(AudioChunk.id, AudioChunk.received_at).where(
            AudioChunk.received_at >= now - timedelta(minutes=5), ~exists)
            .order_by(AudioChunk.received_at.desc(), AudioChunk.id.desc())
            .limit(settings.classification_scan_batch_size)).all()
        history = []
        if state.scan_available_at <= now:
            query = select(AudioChunk.id, AudioChunk.received_at).order_by(AudioChunk.received_at, AudioChunk.id)
            if state.after_received_at is not None and state.after_audio_id is not None:
                query = query.where(tuple_(AudioChunk.received_at, AudioChunk.id) >
                                    tuple_(state.after_received_at, state.after_audio_id))
            history = session.execute(query.limit(settings.classification_scan_batch_size)).all()
            if history:
                state.after_received_at, state.after_audio_id = history[-1].received_at, history[-1].id
            else:
                state.after_received_at, state.after_audio_id = None, None
                state.scan_available_at = now + timedelta(seconds=60)
        chunks = {chunk.id: chunk for chunk in recent + history}
        if chunks:
            inserted = session.execute(insert(RecordingClassification).values(
                [_queue_values(chunk, now) for chunk in chunks.values()]).on_conflict_do_nothing(
                    constraint="uq_classification_version").returning(RecordingClassification.id)).all()
            count = len(inserted)
    return count


@dataclass(frozen=True)
class ClassificationClaim:
    job_id: uuid.UUID
    audio_chunk_id: uuid.UUID
    lease_token: uuid.UUID


def claim_job(settings=None, *, prefer_oldest=False):
    settings = settings or get_settings()
    now = clock.now()
    with SessionLocal() as session, session.begin():
        expired = session.scalars(select(RecordingClassification).where(
            *_version(RecordingClassification), RecordingClassification.status == "processing",
            RecordingClassification.lease_until <= now).order_by(RecordingClassification.lease_until)
            .with_for_update(skip_locked=True).limit(100)).all()
        for row in expired:
            row.status = "failed" if row.attempts >= settings.classification_max_attempts else "pending"
            row.error = "Classification worker stopped before finishing; retrying is safe."
            row.available_at, row.updated_at = now, now
            row.lease_until, row.lease_token = None, None
        session.flush()
        columns = (RecordingClassification.source_received_at, RecordingClassification.created_at, RecordingClassification.id)
        order = [column.asc() if prefer_oldest else column.desc() for column in columns]
        row = session.scalar(select(RecordingClassification).where(
            *_version(RecordingClassification), RecordingClassification.status == "pending",
            RecordingClassification.available_at <= now,
            RecordingClassification.attempts < settings.classification_max_attempts)
            .order_by(*order)
            .with_for_update(skip_locked=True).limit(1))
        if row is None:
            return None
        row.status, row.attempts = "processing", row.attempts + 1
        row.lease_token, row.lease_until = uuid.uuid4(), now + timedelta(seconds=settings.classification_lease_seconds)
        row.updated_at = now
        return ClassificationClaim(row.id, row.audio_chunk_id, row.lease_token)


def _owns(row, claim):
    return bool(row and row.status == "processing" and row.lease_token == claim.lease_token
                and row.lease_until is not None and row.lease_until > clock.now())


def _validated_result(raw):
    """Persist a bounded, JSON-safe model contract rather than arbitrary output."""
    if not isinstance(raw, dict) or raw.get("model_version") != MODEL_VERSION or raw.get("mapping_version") != MAPPING_VERSION:
        raise ValueError("Classification version mismatch")
    if raw.get("primary_category") not in CATEGORIES or raw.get("confidence_status") not in {"classified", "uncertain", "no_usable_audio"}:
        raise ValueError("Invalid classification result")
    scores = raw.get("category_scores")
    if not isinstance(scores, dict) or set(scores) != set(CATEGORIES):
        raise ValueError("Invalid classification scores")
    def number(value):
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    if any(not number(value) or not 0 <= value <= 1 for value in scores.values()):
        raise ValueError("Invalid classification score")
    labels = raw.get("top_labels")
    if not isinstance(labels, list) or len(labels) > 20:
        raise ValueError("Invalid classification labels")
    for item in labels:
        if (not isinstance(item, dict) or not isinstance(item.get("label"), str) or len(item["label"]) > 200
                or not number(item.get("score")) or not 0 <= item["score"] <= 1
                or type(item.get("class_index")) is not int or not 0 <= item["class_index"] < 521):
            raise ValueError("Invalid classification label")
    reason = raw.get("uncertainty_reason")
    if reason is not None and (not isinstance(reason, str) or len(reason) > 300):
        raise ValueError("Invalid classification explanation")
    for name in ("score_threshold", "analyzed_duration_seconds", "input_sample_rate", "model_sample_rate"):
        if not number(raw.get(name)) or raw[name] < 0:
            raise ValueError("Invalid classification metadata")
    result = {key: raw[key] for key in ("primary_category", "category_scores", "top_labels", "model_version",
            "mapping_version", "confidence_status", "uncertainty_reason", "score_threshold",
            "analyzed_duration_seconds", "input_sample_rate", "model_sample_rate")}
    result["top_labels"] = [{key: item[key] for key in ("label", "score", "class_index")} for item in labels]
    if "model_sha256" in raw:
        if raw["model_sha256"] != MODEL_SHA256:
            raise ValueError("Classification model checksum mismatch")
        result["model_sha256"] = raw["model_sha256"]
    for key in ("input_duration_seconds", "window_count", "clipped_samples"):
        if key in raw:
            if not number(raw[key]) or raw[key] < 0:
                raise ValueError("Invalid classification metadata")
            result[key] = raw[key]
    if "warnings" in raw:
        if not isinstance(raw["warnings"], list) or any(value not in {"clipped_input"} for value in raw["warnings"]):
            raise ValueError("Invalid classification warning")
        result["warnings"] = raw["warnings"]
    if "score_aggregation" in raw:
        if raw["score_aggregation"] != "mean_frame_scores_then_max_member_class":
            raise ValueError("Invalid classification score method")
        result["score_aggregation"] = raw["score_aggregation"]
    return result


def process_claim(claim, settings=None, *, infer=None):
    settings = settings or get_settings()
    try:
        with SessionLocal() as session:
            chunk = session.get(AudioChunk, claim.audio_chunk_id)
            if chunk is None:
                raise FileNotFoundError()
            path = resolve_audio_path(chunk.file_path, settings, checksum=chunk.checksum)
            expected_hash = chunk.checksum
        # No database/event/device lock is held during file reads or inference.
        with path.open("rb") as source:
            if hashlib.file_digest(source, "sha256").hexdigest() != expected_hash:
                raise ValueError("Original audio integrity check failed")
        if infer is None:
            from app.classification import infer_recording
            infer = infer_recording
        result = _validated_result(infer(path, model_path=settings.classification_model_path))
        with SessionLocal() as session, session.begin():
            row = session.scalar(select(RecordingClassification).where(RecordingClassification.id == claim.job_id).with_for_update())
            if not _owns(row, claim):
                return False
            row.status, row.result, row.primary_category = "completed", result, result["primary_category"]
            row.classified_at = row.updated_at = clock.now()
            row.lease_token, row.lease_until, row.error = None, None, None
        return True
    except SQLAlchemyError:
        raise
    except Exception as error:
        log.warning("Classification failed for recording %s (%s)", claim.audio_chunk_id, type(error).__name__)
        with SessionLocal() as session, session.begin():
            row = session.scalar(select(RecordingClassification).where(RecordingClassification.id == claim.job_id).with_for_update())
            if not _owns(row, claim):
                return False
            row.status = "failed" if row.attempts >= settings.classification_max_attempts else "pending"
            row.error = ("Original recording is unavailable." if isinstance(error, FileNotFoundError)
                         else "Sound classification could not be completed; the sound-level reading is unaffected.")
            row.available_at = clock.now() + timedelta(seconds=min(3600,
                settings.classification_retry_base_seconds * 2 ** min(row.attempts - 1, 12)))
            row.updated_at, row.lease_token, row.lease_until = clock.now(), None, None
        return False


def run_once(settings=None, *, infer=None, prefer_oldest=False):
    settings = settings or get_settings()
    if not settings.classification_enabled or settings.classification_scope != "recordings":
        return False
    enqueue_missing(settings)
    claim = claim_job(settings, prefer_oldest=prefer_oldest)
    if claim is None:
        return False
    process_claim(claim, settings, infer=infer)
    return True
