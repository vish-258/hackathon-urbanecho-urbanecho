"""Real database coverage of daily scheduling, safe retry and application access."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
import uuid

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select

from app import daily_jobs
from app.db import SessionLocal, get_engine
from app.main import create_app
from app.models import DailyReport, DailySummary, DurableEvent, Location
from live_helpers import BASE, NOW, create_stream, put_reading

pytestmark = pytest.mark.integration


def request_report(client, headers, stream, day="2026-10-09"):
    response = client.post("/daily-summaries/generate", headers=headers,
                           json={"location_id": str(stream.location_id), "reporting_date": day})
    assert response.status_code == 202, response.text
    return response.json()


def fetch_report(client, headers, stream, day="2026-10-09"):
    response = client.get("/daily-summaries", headers=headers,
                          params={"location_id": str(stream.location_id), "reporting_date": day})
    assert response.status_code == 200, response.text
    return response.json()


def test_daily_api_validation_and_existing_auth(client, admin_headers, fake_clock):
    fake_clock.set(NOW)
    stream = create_stream()
    params = {"location_id": str(stream.location_id), "reporting_date": "2026-10-09"}
    assert client.get("/daily-summaries", params=params).status_code == 401
    assert client.post("/daily-summaries/generate", json=params).status_code == 401
    assert client.get(f"/daily-summaries/jobs/{uuid.uuid4()}").status_code == 401
    assert client.get("/daily-summaries", params=params,
                      headers={"Authorization": "Bearer invalid-device-token"}).status_code == 403
    assert client.get("/daily-summaries", headers=admin_headers,
                      params={**params, "location_id": "invalid"}).status_code == 422
    assert client.get("/daily-summaries", headers=admin_headers,
                      params={**params, "reporting_date": "2026-02-30"}).status_code == 422
    assert client.post("/daily-summaries/generate", headers=admin_headers,
                       json={**params, "reporting_date": "2026-10-10"}).status_code == 422
    assert client.post("/daily-summaries/generate", headers=admin_headers,
                       json={**params, "location_id": str(uuid.uuid4())}).status_code == 404
    assert client.post("/daily-summaries/generate", headers=admin_headers,
                       json={**params, "unexpected": True}).status_code == 422
    assert client.get(f"/daily-summaries/jobs/{uuid.uuid4()}", headers=admin_headers).status_code == 404
    empty = fetch_report(client, admin_headers, stream)
    assert empty["report"] is None and empty["summaries"] == []
    previous = client.get("/daily-summaries", headers=admin_headers,
                          params={"location_id": str(stream.location_id)}).json()
    assert previous["reporting_date"] == "2026-10-08"


def test_daily_queue_recalculation_identity_and_persisted_results(client, admin_headers, fake_clock, settings):
    fake_clock.set(NOW)
    stream = create_stream()
    put_reading(stream, 0, 55, captured_at=BASE, evaluate=False)
    queued = request_report(client, admin_headers, stream)
    assert queued["report"]["status"] == "queued"
    report_id = queued["report"]["id"]
    duplicate = request_report(client, admin_headers, stream)
    assert duplicate["report"]["id"] == report_id
    assert daily_jobs.run_daily_once(settings) is True
    assert daily_jobs.run_daily_once(settings) is False
    saved = fetch_report(client, admin_headers, stream)
    assert saved["report"]["status"] == "completed"
    assert saved["report"]["source_as_of"] is not None
    assert saved["summaries"]
    summary_ids = [row["id"] for row in saved["summaries"]]
    assert client.get(f"/daily-summaries/jobs/{report_id}", headers=admin_headers).json() == saved
    rerun = request_report(client, admin_headers, stream)
    assert rerun["report"]["id"] == report_id
    assert [row["id"] for row in rerun["summaries"]] == summary_ids
    assert daily_jobs.run_daily_once(settings) is True
    recalculated = fetch_report(client, admin_headers, stream)
    assert [row["id"] for row in recalculated["summaries"]] == summary_ids
    with SessionLocal() as session:
        assert session.scalar(select(func.count()).select_from(DailyReport)) == 1
        assert session.scalar(select(func.count()).select_from(DailySummary)) == len(summary_ids)
    # Opening a new engine connection/application must read the same saved rows.
    get_engine().dispose()
    with TestClient(create_app(), raise_server_exceptions=False) as restarted:
        assert fetch_report(restarted, admin_headers, stream) == recalculated


def test_daily_concurrent_requests_share_one_job(client, admin_headers, fake_clock):
    fake_clock.set(NOW)
    stream = create_stream()
    with ThreadPoolExecutor(max_workers=4) as pool:
        reports = list(pool.map(lambda _: request_report(client, admin_headers, stream), range(4)))
    assert len({item["report"]["id"] for item in reports}) == 1
    with SessionLocal() as session:
        assert session.scalar(select(func.count()).select_from(DailyReport)) == 1


def test_daily_skipped_calendar_date_is_a_validation_error(client, admin_headers, fake_clock):
    fake_clock.set(NOW)
    stream = create_stream()
    with SessionLocal() as session, session.begin():
        session.get(Location, stream.location_id).timezone = "Pacific/Apia"
    result = client.post("/daily-summaries/generate", headers=admin_headers, json={
        "location_id": str(stream.location_id), "reporting_date": "2011-12-30",
    })
    assert result.status_code == 422


def test_daily_local_browser_can_generate_without_admin_token(settings, monkeypatch, fake_clock):
    fake_clock.set(NOW)
    stream = create_stream()
    monkeypatch.setattr(settings, "local_browser_access", True)
    headers = {"Origin": "http://localhost:8000", "X-Soundwatch-Local": "1", "Sec-Fetch-Site": "same-origin"}
    with TestClient(create_app(), base_url="http://localhost:8000", raise_server_exceptions=False) as browser:
        assert browser.post("/app/session", headers=headers, json={}).status_code == 200
        report = request_report(browser, headers, stream)
        assert report["report"]["status"] == "queued"
        assert fetch_report(browser, headers, stream)["report"]["id"] == report["report"]["id"]
        assert browser.post("/daily-summaries/generate", headers={"X-Soundwatch-Local": "1"},
                            json={"location_id": str(stream.location_id), "reporting_date": "2026-10-09"}).status_code == 401


def test_daily_schedule_local_midnight_grace_and_restart_catchup(settings, fake_clock):
    utc = create_stream()
    india = create_stream()
    with SessionLocal() as session, session.begin():
        session.get(Location, india.location_id).timezone = "Asia/Kolkata"
    before = datetime(2026, 10, 9, 18, 34, tzinfo=timezone.utc)  # India 00:04 on Oct 10.
    fake_clock.set(before)
    assert daily_jobs.schedule_previous_days(settings) == 1
    fake_clock.advance(60)
    assert daily_jobs.schedule_previous_days(settings) == 1
    assert daily_jobs.schedule_previous_days(settings) == 0
    with SessionLocal() as session:
        reports = {row.location_id: row for row in session.scalars(select(DailyReport))}
        assert reports[utc.location_id].reporting_date == date(2026, 10, 8)
        assert reports[india.location_id].reporting_date == date(2026, 10, 9)
        assert reports[india.location_id].day_start_utc == datetime(2026, 10, 8, 18, 30, tzinfo=timezone.utc)
        assert reports[india.location_id].day_end_utc == datetime(2026, 10, 9, 18, 30, tzinfo=timezone.utc)
    # A restarted scheduler later on the same local day is a no-op.
    fake_clock.advance(2 * 3600)
    assert daily_jobs.schedule_previous_days(settings) == 0


def test_daily_report_keeps_original_timezone_when_location_changes(client, admin_headers, fake_clock):
    fake_clock.set(NOW)
    stream = create_stream()
    first = request_report(client, admin_headers, stream)
    with SessionLocal() as session, session.begin():
        session.get(Location, stream.location_id).timezone = "Asia/Kolkata"
    same = request_report(client, admin_headers, stream)
    assert same["timezone"] == "UTC"
    assert same["report"]["day_start_utc"] == first["report"]["day_start_utc"]


def test_daily_schedule_finalizes_a_manually_generated_provisional_day(client, admin_headers, fake_clock, settings):
    fake_clock.set(NOW)
    stream = create_stream()
    initial = request_report(client, admin_headers, stream)
    assert daily_jobs.run_daily_once(settings) is True
    provisional = fetch_report(client, admin_headers, stream)
    assert provisional["report"]["source_as_of"] < provisional["report"]["day_end_utc"]
    fake_clock.set(datetime(2026, 10, 10, 0, 5, tzinfo=timezone.utc))
    assert daily_jobs.schedule_previous_days(settings) == 1
    queued = fetch_report(client, admin_headers, stream)
    assert queued["report"]["status"] == "queued"
    assert queued["report"]["id"] == initial["report"]["id"]
    assert daily_jobs.run_daily_once(settings) is True
    assert daily_jobs.schedule_previous_days(settings) == 0


def test_daily_saved_date_uses_frozen_timezone_after_a_calendar_day_shift(client, admin_headers, fake_clock):
    fake_clock.set(datetime(2026, 10, 8, 20, 0, tzinfo=timezone.utc))
    stream = create_stream()
    with SessionLocal() as session, session.begin():
        session.get(Location, stream.location_id).timezone = "Asia/Kolkata"
    first = request_report(client, admin_headers, stream)
    with SessionLocal() as session, session.begin():
        session.get(Location, stream.location_id).timezone = "America/Los_Angeles"
    same = fetch_report(client, admin_headers, stream)
    assert same["timezone"] == "Asia/Kolkata"
    assert same["report"]["id"] == first["report"]["id"]
    assert request_report(client, admin_headers, stream)["report"]["id"] == first["report"]["id"]


def test_daily_retry_sanitizes_failure_and_recovers(client, admin_headers, fake_clock, settings, monkeypatch):
    fake_clock.set(NOW)
    stream = create_stream()
    request_report(client, admin_headers, stream)
    real_generate = daily_jobs.generate_report
    def failure(*args):
        raise RuntimeError("synthetic secret that must not be exposed")
    monkeypatch.setattr(daily_jobs, "generate_report", failure)
    assert daily_jobs.run_daily_once(settings) is True
    retry = fetch_report(client, admin_headers, stream)
    assert retry["report"]["status"] == "queued"
    assert "secret" not in retry["report"]["error"]
    assert "retry automatically" in retry["report"]["error"]
    assert daily_jobs.run_daily_once(settings) is False
    fake_clock.advance(10)
    monkeypatch.setattr(daily_jobs, "generate_report", real_generate)
    assert daily_jobs.run_daily_once(settings) is True
    completed = fetch_report(client, admin_headers, stream)
    assert completed["report"]["status"] == "completed"
    assert completed["report"]["attempts"] == 2 and completed["report"]["error"] is None


def test_daily_expired_lease_cannot_overwrite_new_worker(client, admin_headers, fake_clock, settings):
    fake_clock.set(NOW)
    stream = create_stream()
    request_report(client, admin_headers, stream)
    old = daily_jobs.claim_daily_job(settings)
    assert old is not None
    fake_clock.advance(settings.daily_job_lease_seconds + 1)
    replacement = daily_jobs.claim_daily_job(settings)
    assert replacement is not None and replacement.lease_token != old.lease_token
    daily_jobs.process_daily_claim(old, settings)
    assert fetch_report(client, admin_headers, stream)["report"]["status"] == "processing"
    daily_jobs.process_daily_claim(replacement, settings)
    assert fetch_report(client, admin_headers, stream)["report"]["status"] == "completed"


def test_daily_retry_exhaustion_and_manual_recovery(client, admin_headers, fake_clock, settings, monkeypatch):
    fake_clock.set(NOW)
    monkeypatch.setattr(settings, "daily_job_max_attempts", 1)
    stream = create_stream()
    original = request_report(client, admin_headers, stream)
    assert daily_jobs.claim_daily_job(settings) is not None
    fake_clock.advance(settings.daily_job_lease_seconds + 1)
    assert daily_jobs.claim_daily_job(settings) is None
    failed = fetch_report(client, admin_headers, stream)
    assert failed["report"]["status"] == "failed"
    retry = request_report(client, admin_headers, stream)
    assert retry["report"]["id"] == original["report"]["id"]
    assert retry["report"]["attempts"] == 0 and retry["report"]["error"] is None
    assert daily_jobs.run_daily_once(settings) is True


def test_daily_processing_never_emits_current_noise_events(client, admin_headers, fake_clock, settings):
    fake_clock.set(NOW)
    stream = create_stream()
    put_reading(stream, 0, 75, captured_at=BASE)
    with SessionLocal() as session:
        before = session.scalar(select(func.count()).select_from(DurableEvent))
    request_report(client, admin_headers, stream)
    assert daily_jobs.run_daily_once(settings) is True
    with SessionLocal() as session:
        assert session.scalar(select(func.count()).select_from(DurableEvent)) == before
