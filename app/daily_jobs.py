"""Durable daily-report work, separate from the latency-sensitive audio worker.

The scheduler queues yesterday once after a location's local 00:05. A manual
request may repeat a completed report; old summaries remain readable until its
replacement transaction commits. Daily aggregation never writes live events.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from app import clock
from app.config import get_settings
from app.daily import day_bounds, generate_report
from app.db import SessionLocal, wait_for_database
from app.models import DailyReport, Location

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DailyClaim:
    report_id: uuid.UUID
    lease_token: uuid.UUID


def enqueue_report(session, location: Location, reporting_date: date, *,
                   now: datetime, recalculate: bool = False,
                   only_finalize: bool = False) -> DailyReport:
    """Insert one reporting-day identity; concurrent requests share its queue row."""
    query = select(DailyReport).where(
        DailyReport.location_id == location.id, DailyReport.reporting_date == reporting_date,
    )
    report = session.scalar(query)
    if report is not None and (not recalculate or report.status in {"queued", "processing"}):
        return report
    if report is None:
        start, end = day_bounds(reporting_date, location.timezone)
        session.execute(insert(DailyReport).values(
            id=uuid.uuid4(), location_id=location.id, reporting_date=reporting_date,
            timezone=location.timezone, day_start_utc=start, day_end_utc=end,
            status="queued", attempts=0, available_at=now, requested_at=now,
            updated_at=now,
        ).on_conflict_do_nothing(index_elements=[DailyReport.location_id, DailyReport.reporting_date]))
    report = session.scalar(query.with_for_update().execution_options(populate_existing=True))
    needs_final = (report.status == "completed" and
                   (report.source_as_of is None or report.source_as_of < report.day_end_utc))
    if recalculate and report.status in {"completed", "failed"} and (not only_finalize or needs_final):
        report.status = "queued"
        report.attempts = 0
        report.available_at = now
        report.requested_at = now
        report.started_at = None
        report.lease_until = None
        report.lease_token = None
        report.error = None
        report.updated_at = now
    session.flush()
    return report


def schedule_previous_days(settings: Any | None = None, *, now: datetime | None = None) -> int:
    """Catch up yesterday after the configured local midnight grace period."""
    settings = settings or get_settings()
    now = now or clock.now()
    scheduled = 0
    with SessionLocal() as session, session.begin():
        for location in session.scalars(select(Location).order_by(Location.id)):
            local_now = now.astimezone(ZoneInfo(location.timezone))
            if local_now.hour * 60 + local_now.minute < settings.daily_schedule_minute:
                continue
            day = local_now.date() - timedelta(days=1)
            existing = session.scalar(select(DailyReport).where(
                DailyReport.location_id == location.id, DailyReport.reporting_date == day,
            ))
            if existing is None:
                enqueue_report(session, location, day, now=now)
                scheduled += 1
            elif (existing.status == "completed" and
                  (existing.source_as_of is None or existing.source_as_of < existing.day_end_utc)):
                # A manual report for today is provisional. Finalize it once
                # after midnight instead of mistaking its identity for a final run.
                enqueue_report(session, location, day, now=now, recalculate=True, only_finalize=True)
                scheduled += 1
    return scheduled


def claim_daily_job(settings: Any | None = None) -> DailyClaim | None:
    settings = settings or get_settings()
    now = clock.now()
    with SessionLocal() as session, session.begin():
        expired = session.scalars(select(DailyReport).where(
            DailyReport.status == "processing", DailyReport.lease_until <= now,
        ).order_by(DailyReport.lease_until).with_for_update(skip_locked=True).limit(100)).all()
        for report in expired:
            report.status = "failed" if report.attempts >= settings.daily_job_max_attempts else "queued"
            report.available_at = now
            report.lease_until = None
            report.lease_token = None
            report.error = "Daily processing was interrupted. Retry this report if processing does not resume."
            report.updated_at = now
        session.flush()
        report = session.scalar(select(DailyReport).where(
            DailyReport.status == "queued", DailyReport.available_at <= now,
            DailyReport.attempts < settings.daily_job_max_attempts,
        ).order_by(DailyReport.available_at, DailyReport.requested_at, DailyReport.id)
            .with_for_update(skip_locked=True).limit(1))
        if report is None:
            return None
        report.status = "processing"
        report.attempts += 1
        report.started_at = now
        report.lease_token = uuid.uuid4()
        report.lease_until = now + timedelta(seconds=settings.daily_job_lease_seconds)
        report.updated_at = now
        return DailyClaim(report.id, report.lease_token)


def _owns_lease(report: DailyReport | None, claim: DailyClaim) -> bool:
    return bool(report is not None and report.status == "processing"
                and report.lease_token == claim.lease_token
                and report.lease_until is not None and report.lease_until > clock.now())


def _record_failure(claim: DailyClaim, settings: Any) -> None:
    with SessionLocal() as session, session.begin():
        report = session.scalar(select(DailyReport).where(
            DailyReport.id == claim.report_id).with_for_update())
        if not _owns_lease(report, claim):
            return
        exhausted = report.attempts >= settings.daily_job_max_attempts
        report.status = "failed" if exhausted else "queued"
        # Never expose database errors, paths, connection strings, or credentials.
        report.error = ("Daily processing could not finish. Please recalculate the report."
                        if exhausted else "Daily processing will retry automatically.")
        report.available_at = clock.now() + timedelta(seconds=min(
            settings.job_retry_base_seconds * 2 ** min(report.attempts - 1, 12), 3600))
        report.lease_until = None
        report.lease_token = None
        report.updated_at = clock.now()


def process_daily_claim(claim: DailyClaim, settings: Any | None = None) -> None:
    settings = settings or get_settings()
    try:
        with SessionLocal() as session:
            # One consistent source snapshot and one atomic report replacement.
            # Only the report row is locked; live audio/event rows remain writable.
            session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
            try:
                report = session.scalar(select(DailyReport).where(
                    DailyReport.id == claim.report_id).with_for_update())
                if not _owns_lease(report, claim):
                    session.rollback()
                    return
                source_as_of = clock.now()
                generate_report(session, report, source_as_of)
                if not _owns_lease(report, claim):
                    session.rollback()
                    return
                report.status = "completed"
                report.source_as_of = source_as_of
                report.completed_at = clock.now()
                report.updated_at = report.completed_at
                report.error = None
                report.lease_token = None
                report.lease_until = None
                session.commit()
            except BaseException:
                session.rollback()
                raise
    except SQLAlchemyError:
        # A possibly committed transaction is recovered by its durable lease.
        raise
    except Exception as error:
        logger.warning("Daily report %s failed (%s)", claim.report_id, type(error).__name__)
        _record_failure(claim, settings)


def run_daily_once(settings: Any | None = None) -> bool:
    settings = settings or get_settings()
    claim = claim_daily_job(settings)
    if claim is None:
        return False
    process_daily_claim(claim, settings)
    return True


def daily_worker_loop(stop: threading.Event, settings: Any | None = None) -> None:
    """Dedicated worker thread: calculations cannot hold up audio queue polling."""
    settings = settings or get_settings()
    next_schedule = 0.0
    while not stop.is_set():
        try:
            wait_for_database()
            if settings.daily_schedule_enabled and time.monotonic() >= next_schedule:
                schedule_previous_days(settings)
                next_schedule = time.monotonic() + settings.daily_schedule_poll_seconds
            if not run_daily_once(settings):
                stop.wait(settings.worker_poll_seconds)
        except SQLAlchemyError:
            logger.warning("Daily processing is waiting for the database; saved jobs will resume")
            stop.wait(settings.worker_poll_seconds)
        except Exception as error:
            # Keep one malformed location/configuration from terminating the thread.
            logger.warning("Daily worker will retry after %s", type(error).__name__)
            stop.wait(settings.daily_schedule_poll_seconds)
