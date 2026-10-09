"""Retryable incident analysis; never writes measurement or live-alert state."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
import logging
import math
import threading
import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from app import clock
from app.classification_contract import MAPPING_VERSION, MODEL_VERSION, SCORE_THRESHOLD
from app.classification_jobs import _validated_result, record_worker_status, worker_status
from app.config import get_settings
from app.db import SessionLocal
from app.incident_audio import build_manifest, collect_sources, digest, public_manifest
from app.models import Incident, IncidentAnalysis

log = logging.getLogger(__name__)


def _version():
    return (IncidentAnalysis.model_version == MODEL_VERSION, IncidentAnalysis.mapping_version == MAPPING_VERSION)


def get_analysis(session, incident_id):
    return session.scalar(select(IncidentAnalysis).where(IncidentAnalysis.incident_id == incident_id, *_version()))


def analysis_dict(session, incident, row=None, settings=None):
    settings = settings or get_settings()
    row = row or get_analysis(session, incident.id)
    service = worker_status(session, settings)
    status = row.status if row else "pending"
    manifest = row.manifest if row else None
    result = row.result if row else None
    # A previous immutable snapshot remains usable while its replacement runs.
    classification = {"status": "completed" if result else status, "scope": "incident",
        "primary_category": None, "category_scores": {}, "top_labels": [],
        "confidence_status": None, "uncertainty_reason": None, "score_threshold": SCORE_THRESHOLD,
        **(result or {}), "model_version": MODEL_VERSION, "mapping_version": MAPPING_VERSION,
        "classified_at": row.generated_at if row else None,
        "error": row.error if row else None, "worker_status": service["worker_status"],
        "worker_error": service["worker_error"], "refreshing": status in {"pending", "processing"} and bool(result)}
    return {"incident_id": str(incident.id), "status": status,
        "provisional": manifest["provisional"] if manifest else True,
        "revision": row.revision if row else None,
        "generated_at": row.generated_at if row else None,
        "refresh_after": row.next_scan_at if row else None,
        "attempts": row.attempts if row else 0, "error": row.error if row else None,
        "worker_status": service["worker_status"], "worker_error": service["worker_error"],
        "classification": classification, "audio": public_manifest(manifest, row.revision if row else None)}


def enqueue_incident(session, incident, *, retry=False, settings=None):
    settings = settings or get_settings()
    now = clock.now()
    session.execute(insert(IncidentAnalysis).values(id=uuid.uuid4(), incident_id=incident.id,
        model_version=MODEL_VERSION, mapping_version=MAPPING_VERSION, status="pending", attempts=0,
        snapshot_at=now, available_at=now, next_scan_at=now, created_at=now, updated_at=now)
        .on_conflict_do_nothing(constraint="uq_incident_analysis_version"))
    row = session.scalar(select(IncidentAnalysis).where(IncidentAnalysis.incident_id == incident.id,
                         *_version()).with_for_update())
    if retry and row.status in {"completed", "failed"}:
        row.status, row.attempts, row.error = "pending", 0, None
        row.snapshot_at, row.available_at, row.updated_at = now, now, now
        row.input_fingerprint = None
    session.flush()
    return row


def enqueue_missing(settings=None):
    """Discover actual incidents only; refresh saved snapshots in bounded batches.

    Finished snapshots check late captures every five minutes. Active/post-roll
    snapshots refresh no faster than the configured ten-second interval.
    """
    settings = settings or get_settings()
    now = clock.now()
    count = 0
    with SessionLocal() as session, session.begin():
        exists = select(IncidentAnalysis.id).where(IncidentAnalysis.incident_id == Incident.id, *_version()).exists()
        # There are far fewer incidents than recordings; both newest and oldest
        # bounded discovery prevent a backlog from starving current incidents.
        newest = session.scalars(select(Incident).where(~exists).order_by(Incident.started_at.desc(), Incident.id.desc())
                                 .limit(settings.classification_scan_batch_size)).all()
        oldest = session.scalars(select(Incident).where(~exists).order_by(Incident.started_at, Incident.id)
                                 .limit(settings.classification_scan_batch_size)).all()
        for incident in {row.id: row for row in newest + oldest}.values():
            enqueue_incident(session, incident, settings=settings)
            count += 1
        due = session.scalars(select(IncidentAnalysis).where(*_version(),
            IncidentAnalysis.status == "completed", IncidentAnalysis.next_scan_at <= now)
            .order_by(IncidentAnalysis.next_scan_at, IncidentAnalysis.id).with_for_update(skip_locked=True)
            .limit(min(settings.classification_scan_batch_size, 4))).all()
        for row in due:
            incident = session.get(Incident, row.incident_id)
            sources = collect_sources(session, incident, now, settings)
            provisional = sources.definition["provisional"]
            row.next_scan_at = now + timedelta(seconds=settings.incident_analysis_refresh_seconds if provisional else 300)
            unavailable = any(item["reason"] == "original_unavailable_or_invalid" for item in (row.manifest or {}).get("excluded", []))
            if row.input_fingerprint != sources.fingerprint or unavailable:
                row.status, row.attempts, row.error = "pending", 0, None
                row.snapshot_at, row.available_at, row.updated_at = now, now, now
                row.input_fingerprint = None
                count += 1
    return count


@dataclass(frozen=True)
class IncidentClaim:
    job_id: uuid.UUID
    incident_id: uuid.UUID
    lease_token: uuid.UUID


def claim_job(settings=None, *, prefer_oldest=False):
    settings = settings or get_settings()
    now = clock.now()
    with SessionLocal() as session, session.begin():
        expired = session.scalars(select(IncidentAnalysis).where(*_version(),
            IncidentAnalysis.status == "processing", IncidentAnalysis.lease_until <= now)
            .order_by(IncidentAnalysis.lease_until).with_for_update(skip_locked=True).limit(100)).all()
        for row in expired:
            row.status = "failed" if row.attempts >= settings.classification_max_attempts else "pending"
            row.error = "Incident analysis stopped before finishing; retrying is safe."
            row.available_at, row.updated_at = now, now
            row.lease_until, row.lease_token = None, None
        session.flush()
        order = IncidentAnalysis.created_at.asc() if prefer_oldest else IncidentAnalysis.created_at.desc()
        row = session.scalar(select(IncidentAnalysis).where(*_version(), IncidentAnalysis.status == "pending",
            IncidentAnalysis.available_at <= now, IncidentAnalysis.attempts < settings.classification_max_attempts)
            .order_by(order, IncidentAnalysis.id).with_for_update(skip_locked=True).limit(1))
        if row is None:
            return None
        row.status, row.attempts = "processing", row.attempts + 1
        row.lease_token, row.lease_until = uuid.uuid4(), now + timedelta(seconds=settings.classification_lease_seconds)
        row.updated_at = now
        return IncidentClaim(row.id, row.incident_id, row.lease_token)


def _owns(row, claim):
    return bool(row and row.status == "processing" and row.lease_token == claim.lease_token
                and row.lease_until is not None and row.lease_until > clock.now())


@contextmanager
def _heartbeat(claim, settings, *, model_ready=True):
    stopped = threading.Event()
    def renew():
        while not stopped.wait(max(.2, settings.classification_lease_seconds / 3)):
            try:
                with SessionLocal() as session, session.begin():
                    row = session.scalar(select(IncidentAnalysis).where(IncidentAnalysis.id == claim.job_id).with_for_update())
                    if not _owns(row, claim):
                        return
                    row.lease_until = clock.now() + timedelta(seconds=settings.classification_lease_seconds)
                if model_ready:
                    record_worker_status("ready")
                else:
                    record_worker_status("unavailable", "The sound classification model is unavailable; incident audio is being prepared.")
            except SQLAlchemyError:
                log.warning("Could not renew incident analysis lease for %s", claim.incident_id)
                return
    thread = threading.Thread(target=renew, name="incident-analysis-lease", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stopped.set()
        thread.join(timeout=1)


def _result(raw):
    result = _validated_result(raw)
    for key in ("segment_count", "contiguous_run_count", "short_fragment_count", "short_fragment_duration_seconds",
                "silent_window_count", "excluded_duration_seconds"):
        if key in raw:
            value = raw[key]
            if (not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value < 0):
                raise ValueError("Invalid incident model metadata")
            result[key] = value
    return result


def process_claim(claim, settings=None, *, infer=None, prepare_only=False):
    settings = settings or get_settings()
    try:
        with SessionLocal() as session:
            row = session.get(IncidentAnalysis, claim.job_id)
            if not _owns(row, claim):
                return False
            snapshot_at = clock.now() if prepare_only else row.snapshot_at
            incident = session.get(Incident, claim.incident_id)
            sources = collect_sources(session, incident, snapshot_at, settings)
        with _heartbeat(claim, settings, model_ready=not prepare_only):
            manifest, segments = build_manifest(sources, settings)
            # Audio availability is independent of ML inference. Publish the
            # verified immutable span revision even if the model then fails.
            # Never attach an older estimate to newly changed evidence.
            with SessionLocal() as session, session.begin():
                row = session.scalar(select(IncidentAnalysis).where(IncidentAnalysis.id == claim.job_id).with_for_update())
                if not _owns(row, claim):
                    return False
                incident = session.get(Incident, claim.incident_id)
                current = collect_sources(session, incident, snapshot_at, settings)
                if current.fingerprint != sources.fingerprint:
                    row.status, row.attempts = "pending", 0
                    row.snapshot_at = clock.now()
                    row.available_at = clock.now() + timedelta(seconds=settings.incident_analysis_refresh_seconds)
                    row.lease_token, row.lease_until = None, None
                    row.updated_at = clock.now()
                    return False
                revision = digest({"source": sources.fingerprint, "manifest": manifest})
                if row.revision != revision:
                    row.result, row.generated_at = None, None
                row.manifest, row.revision, row.input_fingerprint = manifest, revision, sources.fingerprint
                if prepare_only:
                    # Global model unavailability must not consume per-incident
                    # retries or prevent playback of the verified saved audio.
                    row.status, row.attempts = "pending", max(0, row.attempts - 1)
                    row.snapshot_at = snapshot_at
                    row.available_at = clock.now() + timedelta(seconds=30)
                    row.updated_at, row.lease_token, row.lease_until = clock.now(), None, None
                    return True
            if infer is None:
                from app.incident_classification import infer_segments
                infer = infer_segments
            result = _result(infer(segments, model_path=settings.classification_model_path))
            with SessionLocal() as session, session.begin():
                row = session.scalar(select(IncidentAnalysis).where(IncidentAnalysis.id == claim.job_id).with_for_update())
                if not _owns(row, claim):
                    return False
                incident = session.get(Incident, claim.incident_id)
                current = collect_sources(session, incident, snapshot_at, settings)
                if current.fingerprint != sources.fingerprint:
                    # A newly committed capture or a changed incident boundary
                    # invalidates this result. Keep the preceding saved snapshot.
                    row.status, row.attempts = "pending", 0
                    row.snapshot_at = clock.now()
                    row.available_at = clock.now() + timedelta(seconds=settings.incident_analysis_refresh_seconds)
                    row.lease_token, row.lease_until = None, None
                    row.updated_at = clock.now()
                    return False
                row.manifest, row.result = manifest, result
                row.input_fingerprint = sources.fingerprint
                row.revision = digest({"source": sources.fingerprint, "manifest": manifest})
                row.status, row.error = "completed", None
                row.generated_at = row.updated_at = clock.now()
                row.next_scan_at = clock.now() + timedelta(seconds=settings.incident_analysis_refresh_seconds
                                                          if manifest["provisional"] else 300)
                row.lease_until, row.lease_token = None, None
        return True
    except SQLAlchemyError:
        raise
    except Exception as error:
        log.warning("Incident analysis failed for %s (%s)", claim.incident_id, type(error).__name__)
        with SessionLocal() as session, session.begin():
            row = session.scalar(select(IncidentAnalysis).where(IncidentAnalysis.id == claim.job_id).with_for_update())
            if not _owns(row, claim):
                return False
            row.status = "failed" if row.attempts >= settings.classification_max_attempts else "pending"
            row.error = "Incident sound analysis could not be completed; saved recordings and live alerts are unaffected."
            row.available_at = clock.now() + timedelta(seconds=min(3600,
                settings.classification_retry_base_seconds * 2 ** min(row.attempts - 1, 12)))
            row.updated_at, row.lease_token, row.lease_until = clock.now(), None, None
        return False


def run_once(settings=None, *, prefer_oldest=False, infer=None, prepare_only=False):
    settings = settings or get_settings()
    if not settings.classification_enabled or settings.classification_scope != "incidents":
        return False
    enqueue_missing(settings)
    claim = claim_job(settings, prefer_oldest=prefer_oldest)
    if claim is None:
        return False
    process_claim(claim, settings, infer=infer, prepare_only=prepare_only)
    return True
