"""Continuous files are byte-exact groups, never new sound measurements."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import io
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app.db import SessionLocal, get_engine
from app.models import AudioChunk, Device, RecordingGroup, RecordingGroupPart, RecordingGroupScanState
from app.recording_group_worker import claim_group, discover_groups, process_group, run_once
from app.storage import resolve_audio_path
from tests.test_classification_jobs import snapshot
from tests.test_integration import SYNTHETIC_CALIBRATION, accept, device, location, metadata, process
from tests.test_local_access import ORIGIN, READ_HEADERS, connect
from tests.test_pcm_upload_api import accepted, headers, raw_pcm, registered, wav_samples

pytestmark = pytest.mark.integration
PCM = raw_pcm(.5)


def add(client, dev, fake_clock, start, sequences=range(10), *, session="group_test", shift=None, interval=None):
    result = []
    for seq in sequences:
        capture = start + timedelta(seconds=seq + (shift or {}).get(seq, 0))
        fake_clock.set(max(fake_clock.now(), capture + timedelta(seconds=1)))
        request_headers = headers(dev, sequence=seq, session=session, captured=capture)
        if interval is not None:
            request_headers["X-Capture-Interval-Ms"] = str(interval)
        response = client.post("/upload", headers=request_headers, content=PCM)
        assert response.status_code == 200, response.text
        result.append(response.json())
    return result


def groups(client, admin_headers, **params):
    response = client.get("/recordings", headers=admin_headers, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def finish(settings):
    for _ in range(50):
        if not run_once(settings):
            return
    pytest.fail("Recording group work did not become idle")


def test_two_devices_produce_real_ten_second_files_without_changing_live_history(client, admin_headers, settings, fake_clock):
    start = fake_clock.now()
    first_loc, first_dev = registered(client, admin_headers)
    second_loc = location(client, admin_headers, name="SIMULATED second room", threshold_type="dbfs_rms", threshold_value=-20)
    second_dev = device(client, admin_headers, second_loc["id"])
    first = add(client, first_dev, fake_clock, start)
    second = add(client, second_dev, fake_clock, start)
    for _ in range(20):
        process(settings)
    before = snapshot()
    finish(settings)
    assert snapshot() == before
    page = groups(client, admin_headers)
    assert page["total"] == 2
    for row in page["items"]:
        assert row["status"] == "ready" and row["file_available"] and row["revision"]
        assert row["duration_seconds"] == row["target_duration_seconds"] == 10
        assert row["recording_count"] == 10 and row["issues"] == [] and row["gap_count"] == 0
        assert row["source_kind"] == "simulated" and row["calibration_status"] == "uncalibrated"
        response = client.get(f'/recordings/{row["id"]}/file', headers=admin_headers, params={"revision": row["revision"]})
        assert len(response.content) == 320044
        assert wav_samples(response) == PCM * 10
        assert response.headers["content-length"] == "320044" and response.headers["cache-control"] == "private, no-store"
        assert "classification" not in row
        assert not {"segments", "file_path", "lease_token", "credential_hash"} & row.keys()
    for result in first + second:
        assert wav_samples(client.get(f'/audio/{result["id"]}/file', headers=admin_headers)) == PCM
    assert groups(client, admin_headers, device_id=first_dev["id"])["total"] == 1
    assert groups(client, admin_headers, location_id=second_loc["id"])["items"][0]["device_id"] == second_dev["id"]
    get_engine().dispose()
    assert groups(client, admin_headers) == page
    assert snapshot() == before
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(RecordingGroupPart)) == 20
    # Existing session export still routes to the PCM adapter and retains its
    # smaller test upload/export bound (200 kB), rather than UUID parsing (422).
    legacy = client.get(first[-1]["recording_url"], headers=headers(first_dev))
    assert legacy.status_code == 413 and "Session export too large" in legacy.text


def test_partial_group_and_late_retry_complete_same_saved_identity(client, admin_headers, settings, fake_clock):
    start = fake_clock.now()
    _, dev = registered(client, admin_headers)
    add(client, dev, fake_clock, start, [0, 1, 2, 3, 5, 6, 7, 8, 9])
    finish(settings)
    row = groups(client, admin_headers)["items"][0]
    assert row["status"] == "collecting" and row["missing_sequences"] == [4]
    assert row["received_seconds"] == 9 and not row["file_available"]
    assert client.get(f'/recordings/{row["id"]}/file', headers=admin_headers).status_code == 409
    fake_clock.advance(31)
    finish(settings)
    partial = groups(client, admin_headers)["items"][0]
    assert partial["status"] == "partial" and partial["id"] == row["id"]
    late = add(client, dev, fake_clock, start, [4])[0]
    duplicate = add(client, dev, fake_clock, start, [4])[0]
    assert duplicate["duplicate"] and duplicate["id"] == late["id"]
    finish(settings)
    ready = groups(client, admin_headers)["items"][0]
    assert ready["id"] == row["id"] and ready["status"] == "ready" and ready["received_seconds"] == 10
    assert client.get(f'/recordings/{ready["id"]}/file', headers=admin_headers, params={"revision": row["revision"]}).status_code == 409
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(AudioChunk)) == 10
        assert db.scalar(select(func.count()).select_from(RecordingGroup)) == 1


def test_reordered_arrival_is_sorted_by_sequence_and_not_by_receipt(client, admin_headers, settings, fake_clock):
    start = fake_clock.now()
    _, dev = registered(client, admin_headers)
    add(client, dev, fake_clock, start, reversed(range(10)))
    finish(settings)
    row = groups(client, admin_headers)["items"][0]
    assert row["status"] == "ready" and row["captured_at"] == start.isoformat()
    assert wav_samples(client.get(f'/recordings/{row["id"]}/file', headers=admin_headers)) == PCM * 10


@pytest.mark.parametrize("shift,interval,code", [({9: .1}, None, "timestamp_gap_or_overlap"),
                                                 ({9: -.1}, None, "timestamp_gap_or_overlap"),
                                                 ({}, 10000, "capture_gaps")])
def test_gaps_overlaps_and_intermittent_capture_never_become_fake_continuous_files(client, admin_headers, settings, fake_clock, shift, interval, code):
    start = fake_clock.now()
    _, dev = registered(client, admin_headers)
    add(client, dev, fake_clock, start, shift=shift, interval=interval)
    finish(settings)
    row = groups(client, admin_headers)["items"][0]
    assert row["status"] == "partial" and code in row["issue_codes"]
    assert row["received_seconds"] == 10 and not row["file_available"]
    assert client.get(f'/recordings/{row["id"]}/file', headers=admin_headers).status_code == 409


def test_assignment_session_and_device_boundaries_are_never_joined(client, admin_headers, settings, fake_clock):
    start = fake_clock.now()
    old_loc, dev = registered(client, admin_headers)
    add(client, dev, fake_clock, start, range(5))
    new_loc = location(client, admin_headers, name="SIMULATED moved room", threshold_type="dbfs_rms", threshold_value=-20)
    moved = client.patch(f'/devices/{dev["id"]}', headers=admin_headers,
        json={"location_id": new_loc["id"], "expected_revision": dev["config_revision"]})
    assert moved.status_code == 200
    add(client, dev, fake_clock, start, range(5, 20))
    add(client, dev, fake_clock, start + timedelta(seconds=20), range(5), session="second_boot")
    finish(settings)
    old = groups(client, admin_headers, location_id=old_loc["id"])["items"]
    new = groups(client, admin_headers, location_id=new_loc["id"])["items"]
    assert len(old) == 1 and old[0]["recording_count"] == 5 and not old[0]["file_available"]
    assert len(new) == 3 and sum(row["file_available"] for row in new) == 1
    ready = next(row for row in new if row["file_available"])
    assert ready["sequence_start"] == 10 and ready["location_snapshot"]["name"] == new_loc["name"]
    assert wav_samples(client.get(f'/recordings/{ready["id"]}/file', headers=admin_headers)) == PCM * 10


def test_calibration_profiles_must_be_consistent(client, admin_headers, settings, fake_clock):
    start = fake_clock.now()
    _, dev = registered(client, admin_headers)
    add(client, dev, fake_clock, start, range(5))
    changed = client.patch(f'/devices/{dev["id"]}', headers=admin_headers,
        json={"calibration": {**SYNTHETIC_CALIBRATION, "pcm_bits": 16}, "expected_revision": dev["config_revision"]})
    assert changed.status_code == 200
    add(client, dev, fake_clock, start, range(5, 10))
    finish(settings)
    row = groups(client, admin_headers)["items"][0]
    assert row["status"] == "failed" and row["calibration_status"] == "mixed"
    assert "incompatible_provenance" in row["issue_codes"]
    assert client.get(f'/recordings/{row["id"]}/file', headers=admin_headers).status_code == 409


def test_non_pcm16_and_short_originals_remain_originals_and_are_not_relabelled(client, admin_headers, settings, fake_clock):
    start = fake_clock.now()
    _, dev = registered(client, admin_headers)
    accept(client, dev, meta=metadata(dev, captured_at=start.isoformat()))
    accepted(client, dev, pcm=raw_pcm(duration=.5), captured=start, session="short")
    assert not run_once(settings)
    assert groups(client, admin_headers)["total"] == 0
    assert client.get("/audio", headers=admin_headers).json()["total"] == 2


def test_conflicting_sequence_is_rejected_without_deleting_either_original(client, admin_headers, settings, fake_clock):
    from app.ingestion import ingest_recording
    from app.pcm_api import pcm_wav
    from app.schemas import UploadMetadata
    start = fake_clock.now()
    _, dev = registered(client, admin_headers)
    add(client, dev, fake_clock, start)
    # The standard PCM endpoint already rejects changed retries; this exercises
    # restored/imported conflicting metadata that the general schema permits.
    with SessionLocal() as db:
        ingest_recording(db, dev["token"], UploadMetadata(device_id=dev["id"], chunk_id="conflicting-import",
            session_id="pcm16-group_test", sequence=3, captured_at=start + timedelta(seconds=3)),
            io.BytesIO(pcm_wav(raw_pcm(.2))), audio_format="wav_pcm_s16le_mono")
    finish(settings)
    row = groups(client, admin_headers)["items"][0]
    assert row["status"] == "failed" and "conflicting_sequences" in row["issue_codes"]
    assert client.get(f'/recordings/{row["id"]}/file', headers=admin_headers).status_code == 409
    assert client.get("/audio", headers=admin_headers).json()["total"] == 11


def test_original_integrity_is_checked_during_assembly_and_again_before_download(client, admin_headers, settings, fake_clock):
    start = fake_clock.now()
    _, dev = registered(client, admin_headers)
    originals = add(client, dev, fake_clock, start)
    with SessionLocal() as db:
        path = resolve_audio_path(db.get(AudioChunk, uuid.UUID(originals[0]["id"])).file_path, settings)
    original = path.read_bytes()
    path.write_bytes(b"corrupt")
    finish(settings)
    row = groups(client, admin_headers)["items"][0]
    assert row["status"] == "failed" and "original_invalid" in row["issue_codes"]
    path.write_bytes(original)
    fake_clock.advance(301)
    finish(settings)
    row = groups(client, admin_headers)["items"][0]
    assert row["status"] == "ready"
    path.write_bytes(b"corrupt after readiness")
    response = client.get(f'/recordings/{row["id"]}/file', headers=admin_headers)
    assert response.status_code == 503 and str(path) not in response.text
    path.write_bytes(original)
    assert wav_samples(client.get(f'/recordings/{row["id"]}/file', headers=admin_headers)) == PCM * 10


def test_missing_originals_produce_no_audio_not_silence(client, admin_headers, settings, fake_clock):
    start = fake_clock.now()
    _, dev = registered(client, admin_headers)
    add(client, dev, fake_clock, start)
    with SessionLocal() as db:
        for row in db.scalars(select(AudioChunk)):
            resolve_audio_path(row.file_path, settings).unlink()
    finish(settings)
    row = groups(client, admin_headers)["items"][0]
    assert row["status"] == "no_audio" and row["received_seconds"] == 0
    assert row["recording_count"] == 0 and len(row["unusable_sequences"]) == 10
    assert client.get(f'/recordings/{row["id"]}/file', headers=admin_headers).status_code == 409


def test_membership_and_pagination_stay_stable_while_late_parts_and_groups_arrive(client, admin_headers, settings, fake_clock):
    start = fake_clock.now()
    _, dev = registered(client, admin_headers)
    add(client, dev, fake_clock, start, range(9))
    finish(settings)
    boundary = fake_clock.now().isoformat()
    fixed = dict(until=boundary, received_until=boundary, limit=1, offset=0)
    before = groups(client, admin_headers, **fixed)
    add(client, dev, fake_clock, start, range(9, 20))
    finish(settings)
    after = groups(client, admin_headers, **fixed)
    assert after["total"] == before["total"] == 1
    assert after["items"][0]["id"] == before["items"][0]["id"]
    assert after["items"][0]["status"] == "ready"
    assert groups(client, admin_headers)["total"] == 2
    assert client.get("/recordings", headers=admin_headers, params={"until": "2026-01-01T12:00:00"}).status_code == 422
    assert client.get("/recordings", headers=admin_headers, params={"limit": 201}).status_code == 422


def test_concurrent_discovery_leases_and_restart_are_idempotent(client, admin_headers, settings, fake_clock):
    start = fake_clock.now()
    _, dev = registered(client, admin_headers)
    add(client, dev, fake_clock, start)
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(lambda _: discover_groups(), range(2))) == 10
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda _: claim_group(), range(2)))
    assert sum(claim is not None for claim in claims) == 1
    first = next(claim for claim in claims if claim)
    fake_clock.advance(31)
    get_engine().dispose()
    second = claim_group()
    assert second and second.lease_token != first.lease_token
    assert not process_group(first, settings)
    assert process_group(second, settings)
    assert groups(client, admin_headers)["items"][0]["status"] == "ready"
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(RecordingGroup)) == 1
        assert db.scalar(select(func.count()).select_from(RecordingGroupPart)) == 10


def test_new_part_during_build_invalidates_stale_manifest(client, admin_headers, settings, fake_clock, monkeypatch):
    import app.recording_group_worker as worker
    start = fake_clock.now()
    _, dev = registered(client, admin_headers)
    add(client, dev, fake_clock, start, range(9))
    discover_groups()
    claim = claim_group()
    original_builder = worker.build_group
    def racing(*args, **kwargs):
        result = original_builder(*args, **kwargs)
        add(client, dev, fake_clock, start, [9])
        discover_groups()
        return result
    with monkeypatch.context() as patch:
        patch.setattr(worker, "build_group", racing)
        assert not process_group(claim, settings)
    finish(settings)
    row = groups(client, admin_headers)["items"][0]
    assert row["status"] == "ready" and row["recording_count"] == 10


def test_history_cursor_is_bounded_and_newest_and_oldest_work_both_progress(client, admin_headers, settings, fake_clock, monkeypatch):
    import app.recording_group_worker as worker
    monkeypatch.setattr(worker, "BATCH_SIZE", 5)
    start = fake_clock.now()
    _, dev = registered(client, admin_headers)
    add(client, dev, fake_clock, start, range(30))
    fake_clock.advance(3600)
    assert discover_groups() == 5
    with SessionLocal() as db:
        cursor = db.get(RecordingGroupScanState, 1).after_audio_id
        assert cursor is not None
    get_engine().dispose()
    assert discover_groups() == 5
    new_start = fake_clock.now()
    add(client, dev, fake_clock, new_start, [0], session="newest")
    assert discover_groups() == 6
    recent = claim_group()
    oldest = claim_group(prefer_oldest=True)
    with SessionLocal() as db:
        assert db.get(RecordingGroup, recent.group_id).session_id == "pcm16-newest"
        assert db.get(RecordingGroup, oldest.group_id).sequence_start == 0


def test_admin_local_access_and_device_listener_boundary(client, admin_headers, settings, fake_clock, monkeypatch, db):
    start = fake_clock.now()
    _, dev = registered(client, admin_headers)
    add(client, dev, fake_clock, start)
    finish(settings)
    identifier = groups(client, admin_headers)["items"][0]["id"]
    paths = ("/recordings", f"/recordings/{identifier}", f"/recordings/{identifier}/file")
    for path in paths:
        assert client.get(path).status_code == 401
        assert client.get(path, headers=headers(dev)).status_code == 403
    assert client.get(f"/recordings/{uuid.uuid4()}", headers=admin_headers).status_code == 404
    monkeypatch.setattr(settings, "local_browser_access", True)
    client.base_url = ORIGIN
    connect(client)
    for path in paths:
        assert client.get(path, headers=READ_HEADERS).status_code == 200
        assert client.get(path, headers={**READ_HEADERS, "X-Forwarded-For": "127.0.0.1"}).status_code == 401
    from app.device_api import create_device_app
    with TestClient(create_device_app(), base_url=ORIGIN) as hardware:
        hardware.cookies.update(client.cookies)
        assert hardware.get("/recordings", headers=READ_HEADERS).status_code == 404
        assert hardware.get(f"/recordings/{identifier}/file", headers=READ_HEADERS).status_code == 404
    with pytest.raises(DBAPIError):
        db.execute(text("DELETE FROM recording_group_parts"))
    db.rollback()


def test_assignment_boundary_is_rechecked_before_playback(client, admin_headers, settings, fake_clock):
    from app.models import DeviceAssignment
    start = fake_clock.now()
    _, dev = registered(client, admin_headers)
    add(client, dev, fake_clock, start)
    finish(settings)
    row = groups(client, admin_headers)["items"][0]
    assert row["file_available"]
    with SessionLocal() as db, db.begin():
        group = db.get(RecordingGroup, uuid.UUID(row["id"]))
        db.get(DeviceAssignment, group.assignment_id).ended_at = start + timedelta(seconds=5)
    assert client.get(f'/recordings/{row["id"]}/file', headers=admin_headers).status_code == 409
