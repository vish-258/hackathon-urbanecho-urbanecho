"""Opt-in public demos reveal one location and retain administrator boundaries."""
from copy import deepcopy
import json
from uuid import UUID

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import IncidentAnalysis, RecordingGroup
from scripts.simulate import pcm24_wav
from tests.test_integration import accept, device, location, metadata, process

pytestmark = pytest.mark.integration


@pytest.fixture
def published(client, admin_headers, settings, monkeypatch):
    records = {}
    for key in ("public", "private"):
        loc = location(client, admin_headers, name=f"SIMULATED {key} location",
                       threshold_type="dbfs_rms", threshold_value=-20)
        dev = device(client, admin_headers, loc["id"])
        original = pcm24_wav(16000, 1, .5 if key == "public" else .4)
        clip = accept(client, dev, audio=original)
        process(settings)
        incident = client.get("/incidents", headers=admin_headers,
                              params={"location_id": loc["id"]}).json()["items"][0]
        records[key] = {"location": loc, "device": dev, "clip": clip,
                        "original": original, "incident": incident}
    monkeypatch.setattr(settings, "public_demo_location_id", UUID(records["public"]["location"]["id"]))
    return records


def test_public_access_is_opt_in_and_bootstrap_never_returns_credentials(client, settings, admin_headers):
    assert settings.public_demo_location_id is None
    bootstrap = client.get("/app/access")
    assert bootstrap.status_code == 200
    assert bootstrap.json() == {"access": "admin", "read_only": False}
    assert bootstrap.headers["cache-control"] == "no-store"
    assert settings.admin_token not in bootstrap.text
    for route in ("/locations", "/devices", "/locations/status", "/events", "/events/stream",
                  "/audio", "/measurements", "/incidents", "/recordings", "/capabilities"):
        assert client.get(route).status_code == 401, route
    assert client.get("/capabilities", headers=admin_headers).status_code == 200


def test_public_bootstrap_and_capabilities_reflect_read_only_access(client, published, admin_headers, settings):
    bootstrap = client.get("/app/access")
    assert bootstrap.json() == {"access": "public", "read_only": True}
    assert settings.admin_token not in bootstrap.text
    assert client.get("/app/access", headers=admin_headers).json() == {"access": "admin", "read_only": False}
    assert client.get("/capabilities").json()["daily_summaries"]["generation_available"] is False
    assert client.get("/capabilities", headers=admin_headers).json()["daily_summaries"]["generation_available"] is True


def test_public_lists_scope_before_counts_and_pagination(client, published, admin_headers):
    private = published["private"]
    for route in ("/locations", "/devices", "/audio", "/measurements", "/incidents", "/locations/status"):
        result = client.get(route, params={"limit": 1})
        assert result.status_code == 200, (route, result.text)
        page = result.json()
        assert page["total"] == 1 and len(page["items"]) == 1, (route, page)
        assert private["location"]["id"] not in result.text
        assert private["device"]["id"] not in result.text
        empty = client.get(route, params={"limit": 1, "offset": 1}).json()
        assert empty["total"] == 1 and empty["items"] == [], (route, empty)
        assert client.get(route, headers=admin_headers).json()["total"] == 2, route


def test_routes_outside_the_public_app_remain_private(client, published, admin_headers):
    paths = ("/locations/nearby?latitude=12.9716&longitude=77.5946&radius_m=1000",
             "/locations/geojson", "/events", "/classification/status",
             f'/audio/{published["public"]["clip"]["id"]}',
             f'/audio/{published["private"]["clip"]["id"]}')
    for path in paths:
        assert client.get(path).status_code == 401, path
        assert client.get(path, headers=admin_headers).status_code == 200, path


def test_foreign_filters_cannot_broaden_public_scope(client, published):
    private = published["private"]
    for route in ("/audio", "/measurements", "/incidents", "/locations/status", "/recordings", "/events/stream"):
        for field, value in (("location_id", private["location"]["id"]), ("device_id", private["device"]["id"])):
            response = client.get(route, params={field: value})
            assert response.status_code == 404, (route, field, response.text)


def test_direct_public_records_and_originals_work_but_foreign_records_are_hidden(client, published, admin_headers):
    for kind, expected in (("public", 200), ("private", 404)):
        row = published[kind]
        paths = (f'/locations/{row["location"]["id"]}', f'/locations/{row["location"]["id"]}/threshold',
                 f'/locations/{row["location"]["id"]}/threshold/versions', f'/devices/{row["device"]["id"]}',
                 f'/audio/{row["clip"]["id"]}/file',
                 f'/audio/{row["clip"]["id"]}/classification', f'/incidents/{row["incident"]["id"]}',
                 f'/incidents/{row["incident"]["id"]}/measurements', f'/incidents/{row["incident"]["id"]}/analysis')
        for path in paths:
            response = client.get(path)
            assert response.status_code == expected, (path, response.text)
            assert client.get(path, headers=admin_headers).status_code == 200, path
    public = published["public"]
    audio = client.get(f'/audio/{public["clip"]["id"]}/file')
    assert audio.content == public["original"]
    assert audio.headers["cache-control"] == "private, no-store"
    assert client.get(f'/incidents/{published["private"]["incident"]["id"]}/audio/file').status_code == 404


@pytest.mark.parametrize("authorization", ["Bearer incorrect", "Basic incorrect", "Bearer"])
def test_explicit_invalid_authorization_never_falls_back_to_guest(client, published, authorization):
    for path in ("/app/access", "/capabilities", "/locations", "/devices", "/events/stream"):
        result = client.get(path, headers={"Authorization": authorization})
        assert result.status_code == 403, (path, authorization, result.text)


def test_device_credentials_remain_scoped_and_do_not_become_admin_or_guest(client, published):
    row = published["public"]
    headers = {"Authorization": f'Bearer {row["device"]["token"]}'}
    for path in ("/app/access", "/capabilities", "/locations", "/devices", "/events/stream", "/incidents"):
        assert client.get(path, headers=headers).status_code == 403, path
    # Existing device-owned upload/status/download contracts remain usable.
    assert client.get(f'/audio/{row["clip"]["id"]}/file', headers=headers).content == row["original"]
    assert client.get(f'/audio/{published["private"]["clip"]["id"]}/file', headers=headers).status_code == 401


def test_public_mode_preserves_local_admin_session_and_device_listener_boundaries(client, published, settings, monkeypatch):
    from fastapi.testclient import TestClient
    from app.device_api import create_device_app
    from tests.test_local_access import BROWSER_HEADERS, ORIGIN, READ_HEADERS, connect

    monkeypatch.setattr(settings, "local_browser_access", True)
    client.base_url = ORIGIN
    connect(client)
    assert client.get("/app/access", headers=READ_HEADERS).json() == {"access": "admin", "read_only": False}
    assert client.get("/locations", headers=READ_HEADERS).json()["total"] == 2
    row = published["public"]
    assert client.patch(f'/devices/{row["device"]["id"]}', headers=BROWSER_HEADERS,
                        json={"enabled": False, "expected_revision": 1}).status_code == 200
    with TestClient(create_device_app(), base_url=ORIGIN) as hardware:
        assert hardware.get("/app/access").status_code == 404
        assert hardware.get("/locations").status_code == 404
        assert hardware.get(f'/audio/{row["clip"]["id"]}/file').status_code == 401


def test_public_read_access_never_grants_mutation_or_upload_permission(client, published):
    row = published["public"]
    mutations = (
        ("POST", "/locations", {"name": "Unauthorized", "latitude": 1, "longitude": 1, "timezone": "UTC",
                                 "threshold_type": "dbfs_rms", "threshold_value": -20, "interval_seconds": 1}),
        ("PATCH", f'/locations/{row["location"]["id"]}', {"name": "Unauthorized", "expected_revision": 1}),
        ("PATCH", f'/locations/{row["location"]["id"]}/threshold', {"threshold_value": -10, "threshold_type": "dbfs_rms", "interval_seconds": 1, "expected_revision": 1}),
        ("POST", "/devices", {"location_id": row["location"]["id"]}),
        ("PATCH", f'/devices/{row["device"]["id"]}', {"enabled": False, "expected_revision": 1}),
        ("POST", f'/incidents/{row["incident"]["id"]}/analysis', {}),
        ("POST", f'/audio/{row["clip"]["id"]}/classification', {}),
        ("POST", "/daily-summaries/generate", {"location_id": row["location"]["id"], "reporting_date": "2026-02-01"}),
    )
    for method, path, body in mutations:
        response = client.request(method, path, json=body)
        assert response.status_code == 401, (path, response.text)
    assert client.post("/audio", data={"metadata": json.dumps(metadata(row["device"], sequence=1))},
                       files={"file": ("synthetic.wav", row["original"], "audio/wav")}).status_code == 401
    assert client.post("/upload", content=b"\0\0" * 16000, headers={"X-Device-ID": row["device"]["id"]}).status_code == 401


def test_saved_reports_are_scoped_to_the_public_location(client, published, admin_headers):
    for kind, expected in (("public", 200), ("private", 404)):
        row = published[kind]
        params = {"location_id": row["location"]["id"], "reporting_date": "2026-02-01"}
        created = client.post("/daily-summaries/generate", headers=admin_headers, json=params)
        assert created.status_code == 202, created.text
        identifier = created.json()["report"]["id"]
        assert client.get("/daily-summaries", params=params).status_code == expected
        assert client.get(f"/daily-summaries/jobs/{identifier}").status_code == expected
        assert client.get(f"/daily-summaries/jobs/{identifier}", headers=admin_headers).status_code == 200


def test_public_sse_replays_only_published_events(client, published, monkeypatch):
    from app import live_api
    from app.events import encode_sse
    from tests.test_events import add_event

    cursor = client.get("/locations/status").json()["cursor"]
    add_event(published["public"]["location"]["id"], noise_status="normal")
    add_event(published["private"]["location"]["id"], noise_status="excessive")

    async def finite_stream(request, value, location_id=None, device_id=None, settings=None, access=None):
        for event in live_api.read_scoped_events(value, location_id, device_id, settings, access):
            yield encode_sse(event)

    monkeypatch.setattr(live_api, "stream_events", finite_stream)
    response = client.get("/events/stream", params={"cursor": cursor})
    assert response.status_code == 200
    assert published["public"]["location"]["id"] in response.text
    assert published["private"]["location"]["id"] not in response.text


@pytest.mark.parametrize("starting_location", ["public", "private"])
def test_reassignment_events_do_not_publish_private_observations_or_incident_links(
        client, published, admin_headers, settings, fake_clock, monkeypatch, starting_location):
    from app import live_api
    from app.events import encode_sse

    old = published[starting_location]
    new = published["private" if starting_location == "public" else "public"]
    cursor = client.get("/locations/status").json()["cursor"]
    fake_clock.advance(1)
    moved = client.patch(f'/devices/{old["device"]["id"]}', headers=admin_headers,
                         json={"location_id": new["location"]["id"], "expected_revision": 1})
    assert moved.status_code == 200, moved.text
    clip = accept(client, old["device"], audio=pcm24_wav(16000, 1, .25),
                  meta=metadata(old["device"], sequence=1, captured_at=fake_clock.now().isoformat()))
    process(settings)
    measurement_id = client.get(f'/audio/{clip["id"]}', headers=admin_headers).json()["measurements"][0]["id"]

    async def finite_stream(request, value, location_id=None, device_id=None, settings=None, access=None):
        for event in live_api.read_scoped_events(value, location_id, device_id, settings, access):
            yield encode_sse(event)

    monkeypatch.setattr(live_api, "stream_events", finite_stream)
    params = {"cursor": cursor, "location_id": published["public"]["location"]["id"]}
    guest = client.get("/events/stream", params=params)
    admin = client.get("/events/stream", params=params, headers=admin_headers)
    assert guest.status_code == admin.status_code == 200
    events = [json.loads(line[6:]) for line in guest.text.splitlines() if line.startswith("data: ")]
    assert events and published["private"]["location"]["id"] not in guest.text
    if starting_location == "public":
        assert measurement_id in admin.text and measurement_id not in guest.text
        assert moved.json()["current_assignment_id"] in admin.text
        assert moved.json()["current_assignment_id"] not in guest.text
        closed = next(event for event in events if event["event_type"] == "incident.closed")
        assert closed.get("measurement_value") is None
    else:
        assert old["incident"]["id"] in admin.text and old["incident"]["id"] not in guest.text
        assert old["incident"]["id"] not in client.get("/incidents").text
        current = next(event for event in events if event["event_type"] == "incident.opened")
        assert old["incident"]["id"] not in client.get(f'/incidents/{current["incident_id"]}').text


@pytest.mark.parametrize("foreign_reference", ["segment", "opening_audio_id", "location_id"])
def test_incident_playback_checks_every_manifest_original(client, admin_headers, settings, fake_clock, monkeypatch, foreign_reference):
    from app.incident_analysis_jobs import run_once
    from tests.test_incident_analysis import fixture, fake_infer

    monkeypatch.setattr(settings, "classification_enabled", True)
    monkeypatch.setattr(settings, "classification_scope", "incidents")
    monkeypatch.setattr(settings, "incident_context_before_seconds", 1)
    monkeypatch.setattr(settings, "incident_context_after_seconds", 1)
    _, public, _, incident_id, _, _ = fixture(client, admin_headers, settings, fake_clock)
    private = location(client, admin_headers, name="Private incident context", threshold_type="dbfs_rms", threshold_value=-20)
    other_device = device(client, admin_headers, private["id"])
    other_clip = accept(client, other_device, meta=metadata(other_device, captured_at=fake_clock.now().isoformat()))
    assert run_once(settings, infer=fake_infer)
    monkeypatch.setattr(settings, "public_demo_location_id", UUID(public["id"]))
    analysis = client.get(f"/incidents/{incident_id}/analysis")
    assert analysis.status_code == 200, analysis.text
    audio = client.get(f"/incidents/{incident_id}/audio/file", params={"revision": analysis.json()["revision"]})
    assert audio.status_code == 200 and audio.content.startswith(b"RIFF"), audio.text
    # An imported/stale manifest must not grant access to a private original.
    with SessionLocal() as db, db.begin():
        row = db.scalar(select(IncidentAnalysis).where(IncidentAnalysis.incident_id == incident_id))
        manifest = deepcopy(row.manifest)
        if foreign_reference == "segment":
            manifest["segments"][0]["audio_id"] = other_clip["id"]
        else:
            manifest[foreign_reference] = private["id"] if foreign_reference == "location_id" else other_clip["id"]
        row.manifest = manifest
    assert client.get(f"/incidents/{incident_id}/audio/file").status_code == 404
    assert client.get(f"/incidents/{incident_id}/analysis").status_code == 404


def test_recording_groups_and_their_originals_are_scoped(client, admin_headers, settings, fake_clock, monkeypatch):
    from tests.test_recording_groups import add, finish

    records = {}
    for kind in ("public", "private"):
        loc = location(client, admin_headers, name=f"SIMULATED {kind} group", threshold_type="dbfs_rms", threshold_value=-20)
        dev = device(client, admin_headers, loc["id"])
        clips = add(client, dev, fake_clock, fake_clock.now())
        finish(settings)
        group = client.get("/recordings", headers=admin_headers, params={"location_id": loc["id"]}).json()["items"][0]
        records[kind] = (loc, clips, group)
    monkeypatch.setattr(settings, "public_demo_location_id", UUID(records["public"][0]["id"]))
    listed = client.get("/recordings").json()
    assert listed["total"] == 1 and listed["items"][0]["id"] == records["public"][2]["id"]
    assert client.get("/recordings", params={"offset": 1}).json()["items"] == []
    for kind, expected in (("public", 200), ("private", 404)):
        identifier = records[kind][2]["id"]
        for path in (f"/recordings/{identifier}", f"/recordings/{identifier}/file"):
            assert client.get(path).status_code == expected, path
            assert client.get(path, headers=admin_headers).status_code == 200, path
    public_id = records["public"][2]["id"]
    with SessionLocal() as db, db.begin():
        group = db.get(RecordingGroup, UUID(public_id))
        manifest = deepcopy(group.manifest)
        manifest["segments"][0]["audio_id"] = records["private"][1][0]["id"]
        group.manifest = manifest
    assert client.get(f"/recordings/{public_id}/file").status_code == 404
