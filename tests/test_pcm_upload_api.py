"""Prototype PCM transport through the real database, worker, and daily pipeline.

The generated samples are labelled simulation fixtures, never physical evidence.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import io
import math
import struct
import uuid
import wave

from fastapi.testclient import TestClient
import pytest

from tests.test_integration import CAPTURED, counts, device, location, process, upload


pytestmark = pytest.mark.integration


def raw_pcm(amplitude=.5, *, duration=1):
    return b"".join(struct.pack("<h", round(32767 * amplitude * math.sin(2 * math.pi * 1000 * frame / 16000)))
                    for frame in range(round(16000 * duration)))


def registered(client, admin_headers, **location_overrides):
    loc = location(client, admin_headers, name="SIMULATED · PCM adapter test",
                   threshold_type="dbfs_rms", threshold_value=-20.0, **location_overrides)
    return loc, device(client, admin_headers, loc["id"], microphone_model="SIMULATED PCM16 fixture")


def headers(dev, *, sequence=0, session="boot_01", captured=None, compact=False):
    return {"Authorization": "Bearer " + dev["token"],
            "Content-Type": "application/octet-stream",
            "X-Device-ID": uuid.UUID(dev["id"]).hex if compact else dev["id"],
            "X-Session": session, "X-Seq": str(sequence),
            "X-Captured-At": (captured or CAPTURED + timedelta(seconds=sequence)).isoformat()}


def send(client, dev, *, pcm=None, **kwargs):
    return client.post("/upload", headers=headers(dev, **kwargs), content=raw_pcm() if pcm is None else pcm)


def accepted(client, dev, **kwargs):
    result = send(client, dev, **kwargs)
    assert result.status_code == 200, result.text
    assert result.json()["ok"] is True
    return result.json()


def wav_samples(response):
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("audio/wav")
    with wave.open(io.BytesIO(response.content), "rb") as stream:
        assert (stream.getnchannels(), stream.getsampwidth(), stream.getframerate()) == (1, 2, 16000)
        return stream.readframes(stream.getnframes())


def test_ping_and_bounded_authenticated_text_do_not_create_recordings(client, admin_headers, db, capsys, caplog):
    assert client.get("/ping").json() == {"pong": True}
    _, dev = registered(client, admin_headers)
    assert client.post("/text", content=b"hello").status_code == 401
    request_headers = headers(dev, compact=True)
    response = client.post("/text", headers=request_headers, content="microphone ready ✓".encode())
    assert response.status_code == 200, response.text
    assert response.json() == {"ok": True, "received": "microphone ready ✓"}
    assert "microphone ready" not in capsys.readouterr().out + caplog.text
    assert client.post("/text", headers=request_headers, content=b"x" * 1001).status_code == 413
    assert counts(db) == (0, 0, 0, 0)


@pytest.mark.parametrize("compact", [False, True])
def test_pcm16_is_saved_intact_and_processed_by_existing_worker(client, admin_headers, db, settings, compact):
    loc, dev = registered(client, admin_headers)
    pcm = raw_pcm()
    result = accepted(client, dev, pcm=pcm, compact=compact)
    assert result["seq"] == 0 and result["duplicate"] is False
    assert result["recording_key"] == uuid.UUID(dev["id"]).hex + "_boot_01"
    assert result["recording_url"] == "/recordings/" + result["recording_key"] + ".wav"
    status = client.get("/audio/" + result["id"], headers=admin_headers).json()
    assert status["location_id"] == loc["id"]
    assert status["audio_format"] == "wav_pcm_s16le_mono"
    assert status["session_id"] == "pcm16-boot_01" and status["device_chunk_id"] == "pcm16:boot_01:0"
    assert status["duration_seconds"] == 1 and status["sample_rate"] == 16000
    assert wav_samples(client.get("/audio/" + result["id"] + "/file", headers=admin_headers)) == pcm
    assert wav_samples(client.get(result["recording_url"], headers=headers(dev))) == pcm
    process(settings)
    result = client.get("/audio/" + result["id"], headers=admin_headers).json()
    assert result["status"] == "completed"
    measurement = result["measurements"][0]
    assert measurement["measurement_type"] == "dbfs_rms"
    assert measurement["calibration_status"] == "not_required"
    assert measurement["quality_status"] == "good"
    assert measurement["value_db"] == pytest.approx(-9.031, abs=.002)
    assert counts(db) == (1, 1, 1, 1)


@pytest.mark.parametrize("change", [
    {"X-Session": "../escape"}, {"X-Session": ""}, {"X-Session": "x" * 33},
    {"X-Seq": "-1"}, {"X-Seq": "1.5"}, {"X-Seq": str(2 ** 63)},
    {"X-Device-ID": "unregistered-alias"}, {"X-Captured-At": "2026-02-01T12:00:00"},
    {"X-Captured-At": "not-a-date"}, {"X-Captured-At": None},
])
def test_invalid_identity_sequence_or_capture_time_never_creates_audio(client, admin_headers, db, change):
    _, dev = registered(client, admin_headers)
    request_headers = headers(dev)
    for key, value in change.items():
        if value is None:
            request_headers.pop(key)
        else:
            request_headers[key] = value
    response = client.post("/upload", headers=request_headers, content=raw_pcm())
    assert response.status_code in (400, 401, 422), response.text
    assert counts(db) == (0, 0, 0, 0)


@pytest.mark.parametrize("body", [b"", b"x", b"xxx"])
def test_empty_or_partial_pcm_sample_rejected(client, admin_headers, db, body):
    _, dev = registered(client, admin_headers)
    assert send(client, dev, pcm=body).status_code == 400
    assert counts(db) == (0, 0, 0, 0)


@pytest.mark.parametrize("prefix", [b"RIFF", b"RF64"])
def test_raw_pcm_with_container_like_sample_bytes_is_preserved(client, admin_headers, prefix):
    _, dev = registered(client, admin_headers)
    # These are legitimate little-endian integer sample pairs, not a container.
    pcm = prefix + raw_pcm()[len(prefix):]
    saved = accepted(client, dev, pcm=pcm)
    assert wav_samples(client.get(saved["recording_url"], headers=headers(dev))) == pcm


def test_upload_bounds_and_content_type(client, admin_headers, db, settings, monkeypatch):
    _, dev = registered(client, admin_headers)
    # Raise global limits so the adapter's own 1 MB bound is exercised.
    with monkeypatch.context() as patch:
        patch.setattr(settings, "max_upload_bytes", 2_000_000)
        patch.setattr(settings, "max_duration_seconds", 60)
        assert send(client, dev, pcm=b"\0" * 1_000_002).status_code == 413
    response = send(client, dev, pcm=raw_pcm(duration=2.1))
    assert response.status_code == 422, response.text
    for media_type in ("application/json", "audio/wav"):
        request_headers = {**headers(dev), "Content-Type": media_type}
        assert client.post("/upload", headers=request_headers, content=raw_pcm()).status_code == 415
    assert counts(db) == (0, 0, 0, 0)


def test_device_auth_disabled_state_and_recording_ownership(client, admin_headers, db):
    loc, dev = registered(client, admin_headers)
    other = device(client, admin_headers, loc["id"])
    unsigned = headers(dev)
    unsigned.pop("Authorization")
    assert client.post("/upload", headers=unsigned, content=raw_pcm()).status_code == 401
    wrong = {**headers(dev), "Authorization": "Bearer " + other["token"]}
    assert client.post("/upload", headers=wrong, content=raw_pcm()).status_code == 401
    assert client.post("/upload", headers={**headers(dev), **admin_headers}, content=raw_pcm()).status_code in (401, 403)
    saved = accepted(client, dev)
    assert client.get(saved["recording_url"]).status_code == 401
    assert client.get(saved["recording_url"], headers=headers(other)).status_code in (401, 403)
    assert client.get(saved["recording_url"], headers=admin_headers).status_code == 200
    disabled = client.patch("/devices/" + dev["id"], headers=admin_headers,
                            json={"expected_revision": 1, "enabled": False})
    assert disabled.status_code == 200, disabled.text
    assert send(client, dev, sequence=1).status_code == 401
    assert client.post("/text", headers=headers(dev), content=b"hello").status_code == 401
    assert client.get(saved["recording_url"], headers=headers(dev)).status_code == 401
    assert counts(db) == (1, 1, 0, 0)


def test_duplicate_restart_conflict_and_parallel_retry_create_one_record(client, admin_headers, db, settings):
    from app.db import get_engine
    from app.main import create_app

    _, dev = registered(client, admin_headers)
    first = accepted(client, dev)
    get_engine().dispose()
    with TestClient(create_app(), raise_server_exceptions=False) as restarted:
        retry = accepted(restarted, dev)
        assert retry["duplicate"] and retry["id"] == first["id"]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: send(client, dev), range(4)))
    assert all(row.status_code == 200 and row.json()["duplicate"] for row in results)
    assert {row.json()["id"] for row in results} == {first["id"]}
    assert send(client, dev, pcm=raw_pcm(.25)).status_code == 409
    assert send(client, dev, captured=CAPTURED + timedelta(seconds=1)).status_code == 409
    assert counts(db) == (1, 1, 0, 0)
    assert len(list(settings.audio_root.rglob("*.wav"))) == 1
    assert not list(settings.audio_root.rglob("*.part"))


def test_parallel_first_upload_is_durable_and_idempotent(client, admin_headers, db, settings):
    _, dev = registered(client, admin_headers)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: send(client, dev), range(4)))
    assert all(row.status_code == 200 for row in results), [row.text for row in results]
    assert len({row.json()["id"] for row in results}) == 1
    assert sorted(row.json()["duplicate"] for row in results) == [False, True, True, True]
    assert counts(db) == (1, 1, 0, 0)
    assert len(list(settings.audio_root.rglob("*.wav"))) == 1


def test_session_export_orders_out_of_order_arrivals_and_refuses_missing_data(client, admin_headers, db):
    _, dev = registered(client, admin_headers)
    pieces = [raw_pcm(.1), raw_pcm(.2), raw_pcm(.3)]
    last = accepted(client, dev, sequence=2, pcm=pieces[2])
    assert client.get(last["recording_url"], headers=headers(dev)).status_code == 409
    accepted(client, dev, sequence=0, pcm=pieces[0])
    assert client.get(last["recording_url"], headers=headers(dev)).status_code == 409
    accepted(client, dev, sequence=1, pcm=pieces[1])
    assert wav_samples(client.get(last["recording_url"], headers=headers(dev))) == b"".join(pieces)
    assert counts(db) == (3, 3, 0, 0)
    # A separate reboot/session cannot alter the original session export.
    accepted(client, dev, session="boot_02", pcm=raw_pcm(.4))
    assert wav_samples(client.get(last["recording_url"], headers=headers(dev))) == b"".join(pieces)


@pytest.mark.parametrize("offset", [0, 2])
def test_session_export_refuses_overlap_or_timestamp_gap(client, admin_headers, offset):
    _, dev = registered(client, admin_headers)
    first = accepted(client, dev)
    accepted(client, dev, sequence=1, captured=CAPTURED + timedelta(seconds=offset))
    assert client.get(first["recording_url"], headers=headers(dev)).status_code == 409


def test_session_export_bounds_and_missing_session(client, admin_headers, settings, monkeypatch):
    _, dev = registered(client, admin_headers)
    first = accepted(client, dev)
    accepted(client, dev, sequence=1)
    with monkeypatch.context() as patch:
        patch.setattr(settings, "max_upload_bytes", 40_000)
        assert client.get(first["recording_url"], headers=headers(dev)).status_code == 413
    missing = "/recordings/" + uuid.UUID(dev["id"]).hex + "_unknown.wav"
    assert client.get(missing, headers=headers(dev)).status_code == 404


def test_pcm_flow_persists_live_incident_recovery_notifications_and_daily_summary(client, admin_headers, settings, db):
    from app.daily_jobs import run_daily_once

    loc, dev = registered(client, admin_headers)
    for sequence, amplitude in enumerate([.5, .6, .001, .001, .001]):
        accepted(client, dev, sequence=sequence, pcm=raw_pcm(amplitude))
        process(settings)
    assert counts(db) == (5, 5, 5, 1)
    incidents = client.get("/incidents", headers=admin_headers).json()["items"]
    assert incidents[0]["status"] == "resolved" and incidents[0]["location_id"] == loc["id"]
    assert incidents[0]["threshold_type"] == "dbfs_rms"
    events = client.get("/events", headers=admin_headers).json()["items"]
    kinds = [event["event_type"] for event in events]
    assert kinds.count("incident.opened") == kinds.count("incident.resolved") == 1
    live = client.get("/locations/status", headers=admin_headers,
                      params={"location_id": loc["id"]}).json()["items"][0]
    assert live["noise_status"] == "normal" and live["data_status"] == "fresh"
    params = {"location_id": loc["id"], "reporting_date": "2026-02-01"}
    queued = client.post("/daily-summaries/generate", headers=admin_headers, json=params)
    assert queued.status_code == 202, queued.text
    assert run_daily_once(settings)
    report = client.get("/daily-summaries", headers=admin_headers, params=params).json()
    assert report["report"]["status"] == "completed"
    assert len(report["summaries"]) == 1
    summary = report["summaries"][0]
    assert summary["definition"]["source_kind"] == "simulated"
    assert summary["definition"]["unit"] == "dBFS"
    stats = summary["statistics"]
    assert stats["measurement_count"] == 5 and stats["incident_count"] == 1
    assert stats["usable_duration_seconds"] == 5 and stats["coverage_status"] == "partial"
    levels = client.get("/measurements", headers=admin_headers).json()["items"]
    expected_average = 10 * math.log10(sum(10 ** (row["value_db"] / 10) for row in levels) / 5)
    assert stats["average_db"] == pytest.approx(expected_average)
    repeated = client.post("/daily-summaries/generate", headers=admin_headers, json=params)
    assert repeated.json()["report"]["id"] == report["report"]["id"]
    assert run_daily_once(settings)
    again = client.get("/daily-summaries", headers=admin_headers, params=params).json()
    assert [row["id"] for row in again["summaries"]] == [summary["id"]]


def test_native_pcm24_upload_still_works_and_keeps_strict_transport(client, admin_headers, settings):
    _, dev = registered(client, admin_headers)
    original = upload(client, dev)
    assert original.status_code == 202, original.text
    process(settings)
    native = client.get("/audio/" + original.json()["id"], headers=admin_headers).json()
    assert native["audio_format"] == "wav_pcm_s24le_mono"
    wrapped_pcm16 = io.BytesIO()
    with wave.open(wrapped_pcm16, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16000)
        stream.writeframes(raw_pcm())
    assert upload(client, dev, audio=wrapped_pcm16.getvalue(),
                  meta={"device_id": dev["id"], "chunk_id": "native-pcm16-rejected", "session_id": "native-test",
                        "sequence": 1, "captured_at": (CAPTURED + timedelta(seconds=1)).isoformat()}).status_code == 422


def test_late_capture_keeps_local_day_and_does_not_create_current_alert(client, admin_headers, settings):
    from app.daily_jobs import run_daily_once

    loc, dev = registered(client, admin_headers)
    # 23:59:59.5 in Asia/Kolkata on Jan31: half of this 1s recording belongs to Feb1.
    captured = CAPTURED.replace(day=31, month=1, hour=18, minute=29, second=59, microsecond=500000)
    accepted(client, dev, captured=captured)
    process(settings)
    assert client.get("/incidents", headers=admin_headers).json()["total"] == 0
    for day in ("2026-01-31", "2026-02-01"):
        params = {"location_id": loc["id"], "reporting_date": day}
        queued = client.post("/daily-summaries/generate", headers=admin_headers, json=params)
        assert queued.status_code == 202, queued.text
        assert run_daily_once(settings)
        report = client.get("/daily-summaries", headers=admin_headers, params=params).json()
        stats = report["summaries"][0]["statistics"]
        assert stats["measurement_count"] == 1 and stats["usable_duration_seconds"] == pytest.approx(.5)
        assert stats["incident_count"] == 0 and stats["coverage_status"] == "partial"


def test_device_listener_exposes_compatibility_routes_without_management(client, admin_headers, settings):
    from app.device_api import create_device_app

    _, dev = registered(client, admin_headers)
    with TestClient(create_device_app(), raise_server_exceptions=False) as listener:
        assert listener.get("/ping").json() == {"pong": True}
        assert listener.post("/text", headers=headers(dev), content=b"ready").status_code == 200
        saved = accepted(listener, dev)
        assert wav_samples(listener.get(saved["recording_url"], headers=headers(dev))) == raw_pcm()
        assert listener.get(saved["recording_url"], headers=admin_headers).status_code == 403
        process(settings)
        assert listener.get("/audio/" + saved["id"], headers=headers(dev)).json()["status"] == "completed"
        for path in ("/app", "/locations", "/devices", "/events", "/docs", "/daily-summaries"):
            assert listener.get(path).status_code == 404
