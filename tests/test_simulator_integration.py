"""The repeatable Step 6 runner exercises the actual API and audio worker.

Hardware time and network transport are replaced in this test; the recording
bytes, authentication, database, evaluator and daily aggregation remain real.
"""
from datetime import datetime, timezone
import importlib.util
import io
import json
from pathlib import Path
import sys
from threading import Lock
from types import SimpleNamespace

import pytest


scripts = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(scripts))
try:
    spec = importlib.util.spec_from_file_location("step6_simulator", scripts / "verify-simulator.py")
    demo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(demo)
finally:
    sys.path.pop(0)


def test_canonical_equality_matches_existing_backend_integer_rms(tmp_path):
    from app.audio import analyze_audio, validate_wav
    audio = demo.fixture_audio()[0]
    path = tmp_path / "equality.wav"
    path.write_bytes(audio)
    settings = SimpleNamespace(max_duration_seconds=60, allowed_sample_rates=[16000], max_upload_bytes=200000)
    digital = analyze_audio(path, validate_wav(path, settings)).digital_dbfs
    for threshold in (60, 62, 60.125, 85.3):
        offset = threshold - demo._application._fixture.digital_rms(audio)
        assert digital + offset == threshold


def test_admin_environment_takes_precedence_without_executing_env_file(tmp_path, monkeypatch):
    path = tmp_path / "environment"
    path.write_text('ADMIN_TOKEN="private-file-token"\nOTHER=$(do-not-execute)\n')
    monkeypatch.setenv("ADMIN_TOKEN", "private-environment-token")
    assert demo.admin_token(path) == "private-environment-token"
    monkeypatch.delenv("ADMIN_TOKEN")
    assert demo.admin_token(path) == "private-file-token"


def test_long_stale_window_refused_before_any_writes(tmp_path, monkeypatch):
    args = SimpleNamespace(url="http://example.invalid", credentials=tmp_path / "missing.json", max_stale_wait=60)
    calls = []
    def api(base, token, path, *other):
        calls.append(path)
        return {"data_stale_seconds": 120}
    monkeypatch.setattr(demo, "api", api)
    with pytest.raises(RuntimeError, match="raise --max-stale-wait"):
        demo.run(args, "private")
    assert calls == ["/locations/status?limit=1"]
    assert not args.credentials.exists()


@pytest.mark.integration
@pytest.mark.parametrize("focus_station", [None, "workshop"])
def test_full_three_station_runner_repeats_preserving_rules_and_saved_history(
        client, settings, admin_headers, fake_clock, tmp_path, monkeypatch, capsys, focus_station):
    from app.daily_jobs import claim_daily_job, process_daily_claim
    from app.worker import run_once

    fake_clock.set(datetime(2026, 10, 9, 10, 0, tzinfo=timezone.utc))
    class ControlledDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            value = fake_clock.now()
            return value.astimezone(tz) if tz else value.replace(tzinfo=None)

    def api(base, admin, path, payload=None, method=None):
        method = method or ("GET" if payload is None else "POST")
        response = client.request(method, path, headers=admin_headers,
                                  **({"json": payload} if payload is not None else {}))
        assert response.status_code < 300, (method, path, response.status_code)
        if path == "/daily-summaries/generate":
            claim = claim_daily_job(settings)
            assert claim is not None
            process_daily_claim(claim, settings)
        return response.json()

    transport_lock = Lock()
    def upload(base, item, metadata, audio):
        # This adapter runs a worker synchronously rather than starting a worker
        # daemon. Serialize enqueue+drain so it cannot claim another sender's job
        # while that sender's completed() call expects its own job to be done.
        with transport_lock:
            response = client.post("/audio", headers={"Authorization": "Bearer " + item["token"]},
                                   data={"metadata": json.dumps(metadata)}, files={"file": ("synthetic.wav", audio, "audio/wav")})
            assert response.status_code in (200, 202), response.status_code
            if response.status_code == 202:
                assert run_once(settings)
            return response.status_code, response.json()

    def completed(base, admin, identity):
        body = client.get("/audio/" + identity, headers=admin_headers).json()
        assert body["status"] == "completed"
        return body["measurements"][-1]

    def download(request, **unused):
        response = client.get(request.full_url.removeprefix("http://testserver"),
                              headers={"Authorization": request.get_header("Authorization")})
        assert response.status_code == 200
        return io.BytesIO(response.content)

    monkeypatch.setattr(demo, "datetime", ControlledDatetime)
    monkeypatch.setattr(demo._application, "datetime", ControlledDatetime)
    monkeypatch.setattr(demo, "api", api)
    monkeypatch.setattr(demo._application, "api", api)
    monkeypatch.setattr(demo, "upload", upload)
    monkeypatch.setattr(demo._application._fixture, "wait_processed", completed)
    monkeypatch.setattr(demo.urllib.request, "urlopen", download)
    monkeypatch.setattr(demo.time, "sleep", fake_clock.advance)
    args = SimpleNamespace(url="http://testserver", credentials=tmp_path / ".local" / "application.json",
                           duration=90, hold_seconds=0, max_stale_wait=60, focus_station=focus_station)
    state = demo._application.prepare(args, settings.admin_token, 100)
    gate = state["stations"]["gate"]["location_id"]
    rule = api(args.url, "private", "/locations/" + gate + "/threshold")
    api(args.url, "private", "/locations/" + gate + "/threshold",
        {**demo._application.THRESHOLD, "expected_revision": rule["latest_revision"], "threshold_value": 62}, method="PATCH")
    report_ids = None
    for attempt in range(2):
        demo.run(args, settings.admin_token)
        assert api(args.url, "private", "/locations?limit=1")["total"] == 3
        assert api(args.url, "private", "/devices?limit=1")["total"] == 3
        gate_rule = api(args.url, "private", "/locations/" + gate + "/threshold")
        assert gate_rule["current"]["threshold_value"] == 62
        assert gate_rule["latest_revision"] == 2
        ids = []
        for key, item in state["stations"].items():
            expected_incidents = attempt + 1 if focus_station is None or focus_station == key else 0
            incidents = api(args.url, "private", "/incidents?device_id=" + item["id"])
            assert incidents["total"] == expected_incidents
            assert all(row["status"] == "resolved" and row["breach_count"] == 3 for row in incidents["items"])
            saved = api(args.url, "private", f"/daily-summaries?location_id={item['location_id']}&reporting_date=2026-10-09")
            assert len(saved["summaries"]) == 1
            summary = saved["summaries"][0]
            ids.append((saved["report"]["id"], summary["id"]))
            assert summary["definition"]["source_kind"] == "simulated"
            assert summary["statistics"]["measurement_count"] == 10 * (attempt + 1)
            assert summary["statistics"]["incident_count"] == expected_incidents
            assert summary["statistics"]["coverage_status"] == "partial"
        if report_ids is not None:
            assert report_ids == ids
        report_ids = ids
    output = capsys.readouterr().out
    assert settings.admin_token not in output
    assert all(item["token"] not in output for item in state["stations"].values())
    phases = [json.loads(line)["phase"] for line in output.splitlines() if '"phase"' in line]
    assert phases.count("PASS") == 2
    assert phases.count("stale_verified") == 2
