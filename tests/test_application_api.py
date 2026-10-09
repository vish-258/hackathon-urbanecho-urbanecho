"""Application contracts use real PostGIS history and authenticated APIs."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import uuid

import pytest
from sqlalchemy import select

from app.models import DeviceAssignment
from test_integration import (SYNTHETIC_CALIBRATION, accept, device, location,
                              metadata, process)

pytestmark = pytest.mark.integration


def test_location_edit_preserves_snapshots_and_delayed_assignment(client, admin_headers, db, settings, fake_clock):
    old = location(client, admin_headers, name="Original place")
    dev = device(client, admin_headers, old["id"], calibration=SYNTHETIC_CALIBRATION)
    first = accept(client, dev, meta=metadata(dev, captured_at=(fake_clock.now() - timedelta(seconds=2)).isoformat()))
    process(settings)
    first_incident = client.get("/incidents", headers=admin_headers).json()["items"][0]
    update = client.patch(f'/locations/{old["id"]}', headers=admin_headers, json={
        "expected_version": old["configuration_version"], "name": "Updated place",
        "latitude": 22.5, "longitude": 88.3, "timezone": "UTC",
    })
    assert update.status_code == 200, update.text
    assert update.json()["configuration_version"] != old["configuration_version"]
    assert update.json()["latitude"] == 22.5
    changed = client.get(f'/devices/{dev["id"]}', headers=admin_headers).json()
    assert changed["assignment_revision"] == changed["config_revision"] == 2
    assignments = db.scalars(select(DeviceAssignment).where(DeviceAssignment.device_id == uuid.UUID(dev["id"]))
                            .order_by(DeviceAssignment.effective_at)).all()
    assert len(assignments) == 2
    assert assignments[0].ended_at == assignments[1].effective_at == fake_clock.now()
    assert assignments[0].location_snapshot["name"] == "Original place"
    assert assignments[1].location_snapshot["name"] == "Updated place"
    # Editing the label or map position is not evidence of sound recovery.
    assert client.get(f'/incidents/{first_incident["id"]}', headers=admin_headers).json()["status"] == "active"
    delayed = accept(client, dev, meta=metadata(dev, sequence=1,
                         captured_at=(fake_clock.now() - timedelta(seconds=1)).isoformat()))
    process(settings)
    delayed_audio = client.get(f'/audio/{delayed["id"]}', headers=admin_headers).json()
    assert delayed_audio["location_snapshot"]["name"] == "Original place"
    assert delayed_audio["measurements"][0]["evaluation"]["diagnostic"] == "historical_assignment"
    latest = accept(client, dev, meta=metadata(dev, sequence=2, captured_at=fake_clock.now().isoformat()))
    process(settings)
    new_audio = client.get(f'/audio/{latest["id"]}', headers=admin_headers).json()
    assert new_audio["location_snapshot"]["name"] == "Updated place"
    saved = client.get(f'/audio/{first["id"]}', headers=admin_headers).json()
    assert saved["location_snapshot"]["latitude"] == old["latitude"]
    old_incident = client.get(f'/incidents/{first_incident["id"]}', headers=admin_headers).json()
    assert old_incident["status"] == "closed" and old_incident["location_snapshot"]["name"] == "Original place"
    related = client.get(f'/incidents/{first_incident["id"]}/measurements', headers=admin_headers).json()
    assert related["association_available"] is True and related["total"] == 1
    assert related["items"][0]["audio_chunk_id"] == first["id"]
    assert related["items"][0]["device_id"] == dev["id"]
    assert related["items"][0]["duration_seconds"] == 1


def test_location_edit_optimistic_concurrency_and_noop(client, admin_headers):
    loc = location(client, admin_headers)
    def edit(name):
        return client.patch(f'/locations/{loc["id"]}', headers=admin_headers, json={
            "expected_version": loc["configuration_version"], "name": name,
        })
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(edit, ["First proposed name", "Second proposed name"]))
    assert sorted(row.status_code for row in results) == [200, 409]
    current = client.get(f'/locations/{loc["id"]}', headers=admin_headers).json()
    noop = client.patch(f'/locations/{loc["id"]}', headers=admin_headers, json={
        "expected_version": current["configuration_version"], "name": current["name"],
    })
    assert noop.status_code == 200 and noop.json()["configuration_version"] == current["configuration_version"]


@pytest.mark.parametrize("fields", [
    {"name": ""}, {"name": "   "}, {"name": None}, {"latitude": None}, {"latitude": "12"},
    {"latitude": True}, {"latitude": 91}, {"longitude": -181}, {"timezone": "Imaginary/Place"},
    {"threshold_value": 55}, {},
])
def test_location_edit_strict_validation(client, admin_headers, fields):
    loc = location(client, admin_headers)
    response = client.patch(f'/locations/{loc["id"]}', headers=admin_headers,
        json={"expected_version": loc["configuration_version"], **fields})
    assert response.status_code == 422, response.text


def test_measurement_historical_threshold_and_related_readings(client, admin_headers, settings, fake_clock):
    loc = location(client, admin_headers)
    dev = device(client, admin_headers, loc["id"], calibration=SYNTHETIC_CALIBRATION)
    first = accept(client, dev, meta=metadata(dev, captured_at=(fake_clock.now() - timedelta(seconds=1)).isoformat()))
    process(settings)
    incident = client.get("/incidents", headers=admin_headers).json()["items"][0]
    change = client.patch(f'/locations/{loc["id"]}/threshold', headers=admin_headers, json={
        "threshold_value": 100, "threshold_type": "spl_z_leq", "interval_seconds": 1, "expected_revision": 1,
    })
    assert change.status_code == 200
    accept(client, dev, meta=metadata(dev, sequence=1, captured_at=fake_clock.now().isoformat()))
    process(settings)
    measurements = client.get("/measurements", headers=admin_headers).json()["items"]
    assert [row["threshold_version"]["revision"] for row in measurements] == [2, 1]
    assert [row["threshold_value"] for row in measurements] == [100, 75]
    assert all(row["threshold_type"] == "spl_z_leq" and row["device_id"] == dev["id"] for row in measurements)
    assert all("file_path" not in row and "content_hash" not in row for row in measurements)
    related = client.get(f'/incidents/{incident["id"]}/measurements', headers=admin_headers).json()
    assert related["total"] == 1 and related["items"][0]["audio_chunk_id"] == first["id"]


def test_incident_search_date_range_and_pagination(client, admin_headers, settings, fake_clock):
    loc = location(client, admin_headers, name="Park 50% zone")
    dev = device(client, admin_headers, loc["id"], microphone_model="Synthetic meter", calibration=SYNTHETIC_CALIBRATION)
    accept(client, dev, meta=metadata(dev, captured_at=fake_clock.now().isoformat()))
    process(settings)
    for term in ["park", "50%", "SYNTHETIC METER", dev["id"][:8]]:
        response = client.get("/incidents", params={"q": term, "limit": 1}, headers=admin_headers)
        assert response.status_code == 200 and response.json()["total"] == 1
    assert client.get("/incidents", params={"q": "missing%"}, headers=admin_headers).json()["total"] == 0
    assert client.get("/incidents", params={"q": "_"}, headers=admin_headers).json()["total"] == 0
    assert client.get("/incidents", params={"since": (fake_clock.now() + timedelta(seconds=1)).isoformat()},
                      headers=admin_headers).json()["total"] == 0
    page = client.get("/incidents", params={"offset": 1, "limit": 1}, headers=admin_headers).json()
    assert page["total"] == 1 and page["items"] == []


def test_capabilities_and_new_api_auth(client, admin_headers):
    assert client.get("/capabilities").status_code == 401
    capabilities = client.get("/capabilities", headers=admin_headers)
    assert capabilities.status_code == 200
    assert capabilities.json()["daily_summaries"]["available"] is True
    assert capabilities.json()["daily_summaries"]["generation_available"] is True
    assert capabilities.json()["live_updates"]["freshness_seconds"] > 0
    identifier = uuid.uuid4()
    assert client.get(f"/incidents/{identifier}/measurements").status_code == 401
    assert client.get(f"/incidents/{identifier}/measurements", headers=admin_headers).status_code == 404
    assert client.patch(f"/locations/{identifier}", json={"expected_version": "0" * 64, "name": "No"}).status_code == 401


def test_application_static_containment_and_headers(client, monkeypatch, tmp_path):
    from app import application_api
    root = tmp_path / "public"
    root.mkdir()
    (root / "index.html").write_text("<!doctype html><title>Application</title>")
    (root / "ui.mjs").write_text("export const ready = true;")
    secret = tmp_path / "private.js"
    secret.write_text("not public")
    (root / "escape.js").symlink_to(secret)
    monkeypatch.setattr(application_api, "STATIC", root)
    response = client.get("/app")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert "https://tile.openstreetmap.org" in response.headers["content-security-policy"]
    assert client.get("/app/ui.mjs").headers["content-type"].startswith("text/javascript")
    assert client.get("/app/escape.js").status_code == 404
    assert client.get("/app/%2e%2e/private.js").status_code == 404
    assert client.get("/", follow_redirects=False).headers["location"] == "/app"
