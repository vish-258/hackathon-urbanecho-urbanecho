"""Saved original audio stays playable independently of measurement eligibility."""
from datetime import timedelta, timezone
import uuid

import pytest
from fastapi.testclient import TestClient

from scripts.simulate import pcm24_wav
from test_integration import (SYNTHETIC_CALIBRATION, accept, device, location,
                              metadata, process)
from test_local_access import ORIGIN, READ_HEADERS, connect

pytestmark = pytest.mark.integration


def test_recordings_include_pending_and_uncalibrated_originals(client, admin_headers, settings, fake_clock):
    loc = location(client, admin_headers, name="Recorded test bench")
    dev = device(client, admin_headers, loc["id"], external_id="ESP-TESTBENCH")
    first = accept(client, dev, meta=metadata(dev, captured_at=fake_clock.now().isoformat()))
    process(settings)
    detail = client.get(f'/audio/{first["id"]}', headers=admin_headers).json()
    assert detail["measurements"][0]["calibration_status"] == "calibration_required"
    assert detail["measurements"][0]["evaluation"]["status"] == "ineligible"
    second = accept(client, dev, meta=metadata(dev, sequence=1,
        captured_at=(fake_clock.now() + timedelta(seconds=1)).isoformat()))
    response = client.get("/audio", headers=admin_headers)
    assert response.status_code == 200, response.text
    rows = response.json()["items"]
    assert [row["id"] for row in rows] == [second["id"], first["id"]]
    assert [row["status"] for row in rows] == ["pending", "completed"]
    for row in rows:
        assert row["device_external_id"] == "ESP-TESTBENCH"
        assert row["location_snapshot"]["name"] == "Recorded test bench"
        assert row["threshold_type"] == "spl_z_leq"
        assert row["source_kind"] == "recorded"
        assert row["calibration_present"] is False and row["calibration_version"] is None
        assert row["duration_seconds"] == 1 and row["sample_rate"] == 16000
        assert row["audio_format"] == "wav_pcm_s24le_mono"
        assert row["received_at"] and row["captured_at"]
        assert not {"file_path", "credential_hash", "token", "checksum", "calibration", "job"} & row.keys()
    assert dev["token"] not in response.text
    original = client.get(f'/audio/{first["id"]}/file', headers=admin_headers)
    assert original.status_code == 200 and original.content == pcm24_wav(16000, 1, .5)
    assert original.headers["content-type"] == "audio/wav"
    assert original.headers["cache-control"] == "private, no-store"


def test_recordings_filter_by_saved_location_after_device_moves(client, admin_headers, fake_clock):
    old = location(client, admin_headers, name="Original recording location")
    new = location(client, admin_headers, name="New device location")
    dev = device(client, admin_headers, old["id"])
    old_capture = accept(client, dev, meta=metadata(dev, captured_at=fake_clock.now().isoformat()))
    fake_clock.advance(5)
    moved = client.patch(f'/devices/{dev["id"]}', headers=admin_headers,
        json={"location_id": new["id"], "expected_revision": dev["config_revision"]})
    assert moved.status_code == 200, moved.text
    new_capture = accept(client, dev, meta=metadata(dev, sequence=1, captured_at=fake_clock.now().isoformat()))
    delayed_old = accept(client, dev, meta=metadata(dev, sequence=2,
        captured_at=(fake_clock.now() - timedelta(seconds=1)).isoformat()))
    for location_id, expected in [(old["id"], [delayed_old["id"], old_capture["id"]]),
                                  (new["id"], [new_capture["id"]])]:
        page = client.get("/audio", headers=admin_headers,
            params={"location_id": location_id, "device_id": dev["id"]}).json()
        assert [row["id"] for row in page["items"]] == expected
        assert all(row["location_id"] == location_id for row in page["items"])
    old_page = client.get("/audio", headers=admin_headers, params={"location_id": old["id"]}).json()
    assert all(row["location_snapshot"]["name"] == old["name"] for row in old_page["items"])
    assert client.get("/audio", headers=admin_headers,
        params={"device_id": str(uuid.uuid4())}).json()["total"] == 0


def test_recordings_capture_bounds_and_tied_pagination(client, admin_headers, fake_clock):
    loc = location(client, admin_headers)
    first = device(client, admin_headers, loc["id"])
    second = device(client, admin_headers, loc["id"])
    start = fake_clock.now()
    captures = [accept(client, dev, meta=metadata(dev, captured_at=start.isoformat()))["id"]
                for dev in (first, second)]
    latest = accept(client, first, meta=metadata(first, sequence=1,
        captured_at=(start + timedelta(seconds=1)).isoformat()))
    expected = [latest["id"], *sorted(captures)]
    pages = [client.get("/audio", headers=admin_headers, params={"limit": 1, "offset": offset}).json()
             for offset in range(3)]
    assert [page["items"][0]["id"] for page in pages] == expected
    assert all(page["total"] == 3 and page["limit"] == 1 for page in pages)
    assert [page["offset"] for page in pages] == [0, 1, 2]
    # Inclusive endpoints accept explicit offsets and select by capture, not receipt.
    same_moment = start.astimezone(timezone(timedelta(hours=5, minutes=30))).isoformat()
    bounded = client.get("/audio", headers=admin_headers,
        params={"since": same_moment, "until": same_moment}).json()
    assert [row["id"] for row in bounded["items"]] == sorted(captures)
    empty = client.get("/audio", headers=admin_headers, params={"offset": 100}).json()
    assert empty["items"] == [] and empty["total"] == 3


def test_recordings_receipt_bound_keeps_late_arrivals_out_of_open_page(client, admin_headers, fake_clock):
    loc = location(client, admin_headers)
    dev = device(client, admin_headers, loc["id"])
    bound = fake_clock.now()
    ids = [accept(client, dev, meta=metadata(dev, sequence=seq,
           captured_at=(bound - timedelta(seconds=5 - seq)).isoformat()))["id"]
           for seq in range(2)]
    params = {"until": bound.isoformat(), "received_until": bound.isoformat(), "limit": 1}
    first = client.get("/audio", headers=admin_headers, params=params).json()
    assert first["total"] == 2 and first["items"][0]["id"] == ids[1]
    fake_clock.advance(2)
    delayed = accept(client, dev, meta=metadata(dev, sequence=2,
        captured_at=(bound - timedelta(seconds=1)).isoformat()))
    second = client.get("/audio", headers=admin_headers, params={**params, "offset": 1}).json()
    assert second["total"] == 2 and second["items"][0]["id"] == ids[0]
    refreshed = client.get("/audio", headers=admin_headers, params={"until": bound.isoformat()}).json()
    assert refreshed["total"] == 3 and refreshed["items"][0]["id"] == delayed["id"]


@pytest.mark.parametrize("params", [
    {"limit": 0}, {"limit": 201}, {"offset": -1}, {"device_id": "invalid"},
    {"location_id": "invalid"}, {"since": "2026-02-01T12:00:00"},
    {"until": "2026-02-01T12:00:00"},
    {"received_until": "2026-02-01T12:00:00"},
    {"since": "2026-02-02T12:00:00Z", "until": "2026-02-01T12:00:00Z"},
])
def test_recordings_reject_invalid_filters(client, admin_headers, params):
    assert client.get("/audio", headers=admin_headers, params=params).status_code == 422


def test_recordings_preserve_persisted_simulation_and_calibration_labels(client, admin_headers):
    loc = location(client, admin_headers, name="SIMULATED audio fixture")
    dev = device(client, admin_headers, loc["id"])
    accept(client, dev)
    other = location(client, admin_headers, name="Another audio fixture")
    calibrated = device(client, admin_headers, other["id"], calibration=SYNTHETIC_CALIBRATION)
    accept(client, calibrated)
    rows = client.get("/audio", headers=admin_headers).json()["items"]
    assert {row["source_kind"] for row in rows} == {"simulated"}
    row = next(row for row in rows if row["device_id"] == calibrated["id"])
    assert row["calibration_present"] is True
    assert row["calibration_version"] == SYNTHETIC_CALIBRATION["version"]


def test_measurement_playback_preserves_reprocessed_simulation_label(client, admin_headers, settings):
    from scripts.reprocess import reprocess
    loc = location(client, admin_headers, name="Recorded test bench")
    dev = device(client, admin_headers, loc["id"])
    saved = accept(client, dev)
    process(settings)
    reprocess(uuid.UUID(saved["id"]), "synthetic-playback-label-test", SYNTHETIC_CALIBRATION)
    rows = client.get("/measurements", headers=admin_headers).json()["items"]
    assert len(rows) == 2
    assert {row["result_version"]: row["source_kind"] for row in rows} == {
        "initial": "recorded", "synthetic-playback-label-test": "simulated"}
    assert {row["audio_chunk_id"] for row in rows} == {saved["id"]}
    # The original audio's capture-time provenance is retained separately.
    assert client.get("/audio", headers=admin_headers).json()["items"][0]["source_kind"] == "recorded"


def test_recordings_require_admin_and_file_keeps_device_ownership(client, admin_headers):
    loc = location(client, admin_headers)
    owner = device(client, admin_headers, loc["id"])
    other = device(client, admin_headers, loc["id"])
    saved = accept(client, owner)
    url = f'/audio/{saved["id"]}/file'
    assert client.get("/audio").status_code == 401
    assert client.get(url).status_code == 401
    assert client.get("/audio", headers={"Authorization": "Bearer incorrect"}).status_code == 403
    assert client.get("/audio", headers={"Authorization": f'Bearer {owner["token"]}'}).status_code == 403
    assert client.get(url, headers={"Authorization": f'Bearer {owner["token"]}'}).status_code == 200
    assert client.get(url, headers={"Authorization": f'Bearer {other["token"]}'}).status_code == 401


def test_local_browser_can_list_and_play_but_cookie_cannot_bypass_boundaries(client, admin_headers, settings, monkeypatch):
    monkeypatch.setattr(settings, "local_browser_access", True)
    loc = location(client, admin_headers)
    owner = device(client, admin_headers, loc["id"])
    saved = accept(client, owner)
    client.base_url = ORIGIN
    connect(client)
    url = f'/audio/{saved["id"]}/file'
    for path in ("/audio", url):
        assert client.get(path, headers=READ_HEADERS).status_code == 200
        assert client.get(path).status_code == 401
        assert client.get(path, headers={**READ_HEADERS, "X-Forwarded-For": "127.0.0.1"}).status_code == 401
        assert client.get(path, headers={**READ_HEADERS, "Host": "192.168.1.10:8000"}).status_code == 401
        assert client.get(path, headers={**READ_HEADERS, "Origin": "http://untrusted.test"}).status_code == 401
        assert client.get(path, headers={**READ_HEADERS, "Authorization": "Bearer incorrect"}).status_code in (401, 403)
    # The optional LAN app must never inherit browser playback privilege, even
    # with a valid copied cookie and spoofed loopback Host on an HTTP test client.
    from app.device_api import create_device_app
    with TestClient(create_device_app(), base_url=ORIGIN) as hardware:
        hardware.cookies.update(client.cookies)
        assert hardware.get("/audio", headers=READ_HEADERS).status_code in (404, 405)
        assert hardware.get(url, headers=READ_HEADERS).status_code == 401
        assert hardware.get(url, headers={"Authorization": f'Bearer {owner["token"]}'}).status_code == 200
        assert hardware.get(url, headers=admin_headers).status_code == 403


def test_missing_original_returns_clear_error(client, admin_headers, settings):
    from app.storage import resolve_audio_path
    from app.models import AudioChunk
    from app.db import SessionLocal
    loc = location(client, admin_headers)
    dev = device(client, admin_headers, loc["id"])
    saved = accept(client, dev)
    with SessionLocal() as db:
        path = resolve_audio_path(db.get(AudioChunk, uuid.UUID(saved["id"])).file_path, settings)
    path.unlink()
    assert client.get("/audio", headers=admin_headers).json()["total"] == 1
    response = client.get(f'/audio/{saved["id"]}/file', headers=admin_headers)
    assert response.status_code == 503
    assert "Original audio unavailable" in response.json()["error"]["message"]
