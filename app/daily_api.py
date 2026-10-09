"""Authenticated saved daily reports and durable generation requests."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Annotated
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import clock
from app.auth import require_admin
from app.daily_jobs import enqueue_report
from app.db import get_db
from app.models import DailyReport, DailySummary, Location

router = APIRouter(prefix="/daily-summaries", tags=["daily summaries"])
DB = Annotated[Session, Depends(get_db)]
Admin = Annotated[None, Depends(require_admin)]


class GenerateDailyReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    location_id: UUID
    reporting_date: date


def _location(session: Session, identifier: UUID) -> Location:
    location = session.get(Location, identifier)
    if location is None:
        raise HTTPException(404, "Location not found")
    return location


def _date(session: Session, location: Location, requested: date | None) -> date:
    frozen_timezone = None if requested is None else session.scalar(select(DailyReport.timezone).where(
        DailyReport.location_id == location.id, DailyReport.reporting_date == requested,
    ))
    today = clock.now().astimezone(ZoneInfo(frozen_timezone or location.timezone)).date()
    day = requested or today - timedelta(days=1)
    if day > today:
        raise HTTPException(422, "Choose today or an earlier local calendar date")
    return day


def envelope(session: Session, location: Location, day: date,
             report: DailyReport | None = None) -> dict:
    if report is None:
        report = session.scalar(select(DailyReport).where(
            DailyReport.location_id == location.id, DailyReport.reporting_date == day,
        ))
    saved = [] if report is None else session.scalars(select(DailySummary).where(
        DailySummary.report_id == report.id,
    ).order_by(DailySummary.definition_key)).all()
    report_json = None if report is None else {
        field: getattr(report, field) for field in (
            "id", "status", "attempts", "requested_at", "started_at", "completed_at",
            "updated_at", "source_as_of", "error", "day_start_utc", "day_end_utc",
        )
    }
    if report_json is not None:
        report_json["last_error"] = report.error
    return {
        "location_id": location.id, "reporting_date": day,
        "timezone": report.timezone if report else location.timezone,
        "report": report_json,
        "summaries": [{field: getattr(row, field) for field in (
            "id", "definition_key", "definition", "statistics", "calculated_at",
        )} for row in saved],
    }


@router.get("")
def get_daily_summary(db: DB, admin: Admin, location_id: UUID,
                      reporting_date: Annotated[date | None, Query()] = None):
    db.connection(execution_options={"isolation_level": "REPEATABLE READ"})
    location = _location(db, location_id)
    return envelope(db, location, _date(db, location, reporting_date))


@router.post("/generate", status_code=202)
def generate_daily_summary(body: GenerateDailyReport, db: DB, admin: Admin):
    location = _location(db, body.location_id)
    day = _date(db, location, body.reporting_date)
    try:
        enqueue_report(db, location, day, now=clock.now(), recalculate=True)
    except (ValueError, OverflowError):
        raise HTTPException(422, "This calendar date has no supported time range in the location's timezone") from None
    db.commit()
    # A worker may finish between enqueue and response. Read one generation's
    # metadata and summary rows together, rather than mixing old/new snapshots.
    db.connection(execution_options={"isolation_level": "REPEATABLE READ"})
    db.expire_all()
    return envelope(db, _location(db, body.location_id), day)


@router.get("/jobs/{report_id}")
def get_daily_job(report_id: UUID, db: DB, admin: Admin):
    db.connection(execution_options={"isolation_level": "REPEATABLE READ"})
    report = db.get(DailyReport, report_id)
    if report is None:
        raise HTTPException(404, "Daily report not found")
    return envelope(db, _location(db, report.location_id), report.reporting_date, report)
