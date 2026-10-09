"""Durable leased job worker. Run with ``python -m app.worker``."""
from __future__ import annotations

import logging
import signal
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app import clock
from app.config import get_settings
from app.db import SessionLocal, wait_for_database
from app.daily_jobs import daily_worker_loop
from app.models import AudioChunk, Device, ProcessingJob
from app.processing import calculate_measurement
from app.evaluation import persist_measurement, evaluate_measurement
from app.events import lock_event_clock, sweep_freshness
from app.reconcile import reconcile

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Claim:
    job_id: uuid.UUID
    audio_chunk_id: uuid.UUID
    lease_token: uuid.UUID


def _now() -> datetime:
    return clock.now()


def claim_job(settings: Any) -> Claim | None:
    now = _now()
    with SessionLocal() as session, session.begin():
        expired = session.scalars(
            select(ProcessingJob)
            .where(ProcessingJob.status == "processing", ProcessingJob.lease_until <= now)
            .order_by(ProcessingJob.lease_until)
            .with_for_update(skip_locked=True).limit(100)
        ).all()
        for job in expired:
            exhausted = job.attempts >= settings.job_max_attempts
            job.status = "failed" if exhausted else "retry"
            job.available_at = now
            job.lease_until = None
            job.lease_token = None
            job.last_error = "Processing lease expired; previous worker did not finish"
            job.updated_at = now
            session.get(AudioChunk, job.audio_chunk_id).status = "failed" if exhausted else "pending"
        session.flush()
        job = session.scalar(
            select(ProcessingJob)
            .where(ProcessingJob.status.in_(["pending", "retry"]), ProcessingJob.available_at <= now,
                   ProcessingJob.attempts < settings.job_max_attempts)
            .order_by(ProcessingJob.available_at, ProcessingJob.created_at, ProcessingJob.id)
            .with_for_update(skip_locked=True).limit(1)
        )
        if job is None:
            return None
        token = uuid.uuid4()
        job.status = "processing"
        job.attempts += 1
        job.lease_token = token
        job.lease_until = now + timedelta(seconds=settings.job_lease_seconds)
        job.updated_at = now
        session.get(AudioChunk, job.audio_chunk_id).status = "processing"
        return Claim(job.id, job.audio_chunk_id, token)


def _owns_lease(job: ProcessingJob | None, claim: Claim) -> bool:
    return bool(job is not None and job.status == "processing" and job.lease_token == claim.lease_token
                and job.lease_until is not None and job.lease_until > _now())


def _record_failure(claim: Claim, error: Exception, settings: Any) -> None:
    with SessionLocal() as session, session.begin():
        job = session.scalar(select(ProcessingJob).where(ProcessingJob.id == claim.job_id).with_for_update())
        if not _owns_lease(job, claim):
            return
        exhausted = job.attempts >= settings.job_max_attempts
        job.status = "failed" if exhausted else "retry"
        job.last_error = f"{type(error).__name__}: {error}"[:2000]
        job.available_at = _now() + timedelta(seconds=min(
            settings.job_retry_base_seconds * 2 ** min(job.attempts - 1, 12), 3600))
        job.lease_until = None
        job.lease_token = None
        job.updated_at = _now()
        session.get(AudioChunk, claim.audio_chunk_id).status = "failed" if exhausted else "pending"


def process_claim(claim: Claim, settings: Any) -> None:
    try:
        # Loading a detached snapshot keeps expensive audio work out of SQL locks.
        with SessionLocal() as session:
            chunk = session.get(AudioChunk, claim.audio_chunk_id)
            if chunk is None:
                raise ValueError("processing job references missing audio metadata")
            session.expunge(chunk)
        result = calculate_measurement(chunk, settings)
        with SessionLocal() as session, session.begin():
            # Every live/configuration writer takes the global event clock
            # first, then device and job locks. Event cursor order is commit order.
            lock_event_clock(session)
            session.execute(select(Device.id).where(Device.id == chunk.device_id).with_for_update())
            job = session.scalar(select(ProcessingJob).where(ProcessingJob.id == claim.job_id).with_for_update())
            if not _owns_lease(job, claim):
                logger.info("Discarded result from expired/replaced lease for %s", claim.job_id)
                return
            measurement = persist_measurement(session, chunk, result,
                result_version="legacy-processed-v2" if chunk.legacy_ingestion else "initial",
                is_reprocessing=chunk.legacy_ingestion)
            evaluate_measurement(session, measurement, settings=settings)
            session.get(AudioChunk, chunk.id).status = "completed"
            job.status = "completed"
            job.lease_token = None
            job.lease_until = None
            job.last_error = None
            job.updated_at = _now()
            session.flush()
    except SQLAlchemyError:
        # An uncertain DB commit is never blindly replayed. Its original lease
        # will expire if it did not commit; a completed job cannot be reclaimed.
        raise
    except Exception as error:
        logger.warning("Audio processing failed for %s: %s", claim.audio_chunk_id, type(error).__name__)
        _record_failure(claim, error, settings)


def run_once(settings: Any | None = None) -> bool:
    settings = settings or get_settings()
    with SessionLocal() as session, session.begin():
        sweep_freshness(session, settings=settings)
    claim = claim_job(settings)
    if claim is None:
        return False
    process_claim(claim, settings)
    return True


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = get_settings()
    stop = threading.Event()

    def request_stop(signum: int, frame: Any) -> None:
        logger.info("Shutdown requested; finishing the current job before stopping")
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    daily_thread = threading.Thread(target=daily_worker_loop, args=(stop, settings),
                                    name="daily-reports", daemon=True)
    daily_thread.start()
    next_reconciliation = 0.0
    while not stop.is_set():
        try:
            wait_for_database()
            if time.monotonic() >= next_reconciliation:
                report = reconcile(settings, delete_orphans=True)
                if report["deleted_count"]:
                    logger.info("Reconciled %s uncommitted audio files", report["deleted_count"])
                if report["missing_committed_files"]:
                    logger.error("Missing %s committed originals; restore from backup",
                                 len(report["missing_committed_files"]))
                next_reconciliation = time.monotonic() + 3600
            worked = run_once(settings)
            if not worked:
                stop.wait(settings.worker_poll_seconds)
        except SQLAlchemyError:
            logger.warning("Database unavailable; durable jobs will resume after reconnection")
            stop.wait(settings.worker_poll_seconds)
    # Graceful shutdown normally commits the in-flight report. If the container
    # stops first, the report's durable lease makes the next worker retry safely.
    daily_thread.join(timeout=10)


if __name__ == "__main__":
    main()
