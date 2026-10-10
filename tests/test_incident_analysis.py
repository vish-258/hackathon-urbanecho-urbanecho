"""Incident evidence uses real persisted WAVs; fake estimates do not claim accuracy."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import hashlib
import io
import uuid
import wave

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app.classification_contract import CATEGORIES, MAPPING_VERSION, MODEL_VERSION
from app.db import SessionLocal, get_engine
from app.incident_analysis_jobs import claim_job, enqueue_incident, enqueue_missing, process_claim, run_once
from app.incident_audio import build_manifest, collect_sources
from app.models import AudioChunk, Incident, IncidentAnalysis, RecordingClassification
from app.storage import resolve_audio_path
from scripts.simulate import pcm24_wav
from tests.test_classification_jobs import snapshot
from tests.test_integration import accept, device, location, metadata, process
from tests.test_local_access import ORIGIN, READ_HEADERS, connect

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def incident_settings(settings, monkeypatch):
    monkeypatch.setattr(settings, "classification_scope", "incidents")
    monkeypatch.setattr(settings, "classification_enabled", True)
    monkeypatch.setattr(settings, "incident_context_before_seconds", 1)
    monkeypatch.setattr(settings, "incident_context_after_seconds", 1)
    monkeypatch.setattr(settings, "classification_max_attempts", 2)
    monkeypatch.setattr(settings, "classification_retry_base_seconds", .1)


def fake_infer(segments, *, model_path=None):
    for item in segments:
        assert item["path"].read_bytes().startswith(b"RIFF")
        assert item["end_frame"] > item["start_frame"]
    seconds = sum((item["end_frame"] - item["start_frame"]) / 16000 for item in segments)
    return {"primary_category": "voice" if segments else "other",
        "category_scores": {key: .8 if segments and key == "voice" else .02 for key in CATEGORIES},
        "top_labels": [{"label": "Speech", "score": .8, "class_index": 0}] if segments else [],
        "model_version": MODEL_VERSION, "mapping_version": MAPPING_VERSION,
        "confidence_status": "classified" if segments else "no_usable_audio",
        "uncertainty_reason": None if segments else "no_usable_audio", "score_threshold": .25,
        "analyzed_duration_seconds": seconds, "input_duration_seconds": seconds,
        "input_sample_rate": 16000, "model_sample_rate": 16000,
        "segment_count": len(segments), "contiguous_run_count": sum(item["break_before"] for item in segments),
        "short_fragment_count": 0, "short_fragment_duration_seconds": 0, "excluded_duration_seconds": 0}


def fixture(client, admin_headers, settings, fake_clock, *, positions=(0, 1, 2), closed=True, core_end=2):
    origin = fake_clock.now()
    loc = location(client, admin_headers, name="SIMULATED incident audio", threshold_type="dbfs_rms", threshold_value=-20)
    dev = device(client, admin_headers, loc["id"])
    originals, identifiers = {}, {}
    for seq, offset in enumerate(positions):
        fake_clock.set(origin + timedelta(seconds=offset + 1))
        data = pcm24_wav(16000, 1, .5 if offset >= 1 else .01)
        accepted = accept(client, dev, audio=data,
            meta=metadata(dev, sequence=seq, captured_at=(origin + timedelta(seconds=offset)).isoformat()))
        originals[offset], identifiers[offset] = data, uuid.UUID(accepted["id"])
        process(settings)
    with SessionLocal() as db, db.begin():
        incident = db.scalar(select(Incident).where(Incident.device_id == uuid.UUID(dev["id"])))
        assert incident is not None
        if closed:
            # A fixed closed boundary makes exact-frame tests independent of
            # the recovery-count policy, tested separately by evaluation tests.
            incident.status, incident.closed_reason = "closed", "test_fixture"
            incident.ended_at = origin + timedelta(seconds=core_end)
        incident_id = incident.id
    if closed:
        fake_clock.set(origin + timedelta(seconds=max(positions) + 30))
    return origin, loc, dev, incident_id, originals, identifiers


def state(client, headers, identifier):
    response = client.get(f"/incidents/{identifier}/analysis", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def pcm(data):
    with wave.open(io.BytesIO(data), "rb") as reader:
        return reader.readframes(reader.getnframes())


def test_normal_recordings_never_get_automatic_classification(client, admin_headers, settings, fake_clock):
    loc = location(client, admin_headers, threshold_type="dbfs_rms", threshold_value=-20)
    dev = device(client, admin_headers, loc["id"])
    accept(client, dev, audio=pcm24_wav(16000, 1, .01), meta=metadata(dev, captured_at=fake_clock.now().isoformat()))
    process(settings)
    assert not run_once(settings, infer=fake_infer)
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(IncidentAnalysis)) == 0
        assert db.scalar(select(func.count()).select_from(RecordingClassification)) == 0
    recording = client.get("/audio", headers=admin_headers).json()["items"][0]
    assert recording["classification"]["status"] == "not_requested"
    assert client.post(f'/audio/{recording["id"]}/classification', headers=admin_headers).status_code == 409


def test_whole_incident_playback_has_context_but_inference_only_core_and_survives_restart(client, admin_headers, settings, fake_clock):
    _, _, _, identifier, originals, ids = fixture(client, admin_headers, settings, fake_clock)
    pending = state(client, admin_headers, identifier)
    assert pending["status"] == "pending" and not pending["audio"]["available"]
    before = snapshot()
    seen = []
    def infer(segments, **kwargs):
        seen.extend(segments)
        return fake_infer(segments, **kwargs)
    assert run_once(settings, infer=infer)
    assert snapshot() == before
    assert len(seen) == 1 and seen[0]["start_frame"] == 0 and seen[0]["end_frame"] == 16000
    assert seen[0]["path"].read_bytes() == originals[1]
    result = state(client, admin_headers, identifier)
    assert result["status"] == "completed" and result["provisional"] is False
    assert result["audio"]["duration_seconds"] == 3 and result["audio"]["core_coverage_seconds"] == 1
    assert result["audio"]["coverage_percent"] == 100 and result["audio"]["gap_count"] == 0
    assert result["audio"]["source_kind"] == "simulated"
    assert result["classification"]["primary_category"] == "voice"
    assert not {"segments", "file_path", "lease_token", "stream_key"} & result["audio"].keys()
    get_engine().dispose()
    assert state(client, admin_headers, identifier) == result
    audio = client.get(f"/incidents/{identifier}/audio/file", params={"revision": result["revision"]}, headers=admin_headers)
    assert audio.status_code == 200
    assert pcm(audio.content) == b"".join(pcm(originals[key]) for key in sorted(originals))
    assert audio.headers["content-length"] == str(len(audio.content))
    for offset, audio_id in ids.items():
        assert client.get(f"/audio/{audio_id}/file", headers=admin_headers).content == originals[offset]
    assert not run_once(settings, infer=infer)
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(IncidentAnalysis)) == 1


def test_gaps_are_omitted_and_never_joined_for_classification(client, admin_headers, settings, fake_clock):
    _, _, _, identifier, originals, _ = fixture(client, admin_headers, settings, fake_clock,
                                               positions=(0, 1, 3, 4), core_end=4)
    seen = []
    def infer(segments, **kwargs):
        seen.extend(segments)
        return fake_infer(segments, **kwargs)
    assert run_once(settings, infer=infer)
    result = state(client, admin_headers, identifier)
    assert result["audio"]["duration_seconds"] == 4 and result["audio"]["window_duration_seconds"] == 5
    assert result["audio"]["coverage_percent"] == 80
    assert result["audio"]["gap_count"] == 1 and result["audio"]["gaps"][0]["duration_seconds"] == 1
    assert len(seen) == 2 and all(item["break_before"] for item in seen)
    assert result["classification"]["contiguous_run_count"] == 2
    audio = client.get(f"/incidents/{identifier}/audio/file", headers=admin_headers)
    assert pcm(audio.content) == b"".join(pcm(originals[key]) for key in sorted(originals))


def test_overlap_and_crossing_boundary_trim_frames_without_mutating_originals(client, admin_headers, settings, fake_clock):
    origin, _, dev, identifier, originals, _ = fixture(client, admin_headers, settings, fake_clock)
    extra = pcm24_wav(16000, 1, .2)
    accept(client, dev, audio=extra, meta=metadata(dev, sequence=30, chunk_id="overlap-late",
        captured_at=(origin + timedelta(seconds=1.5)).isoformat()))
    with SessionLocal() as db, db.begin():
        incident = db.get(Incident, identifier)
        incident.started_at = origin + timedelta(seconds=1.25)
        incident.ended_at = origin + timedelta(seconds=1.75)
    assert run_once(settings, infer=fake_infer)
    result = state(client, admin_headers, identifier)
    assert result["audio"]["duration_seconds"] == 2.5
    assert result["audio"]["core_coverage_seconds"] == .5
    assert result["classification"]["input_duration_seconds"] == .5
    playback = client.get(f"/incidents/{identifier}/audio/file", headers=admin_headers)
    expected = pcm(originals[0])[4000 * 3:] + pcm(originals[1]) + pcm(extra)[8000 * 3:] + pcm(originals[2])[8000 * 3:12000 * 3]
    assert pcm(playback.content) == expected


def test_late_recording_changes_revision_and_stale_requested_playback_is_rejected(client, admin_headers, settings, fake_clock):
    origin, _, dev, identifier, _, _ = fixture(client, admin_headers, settings, fake_clock,
                                              positions=(0, 1, 3, 4), core_end=4)
    assert run_once(settings, infer=fake_infer)
    old = state(client, admin_headers, identifier)
    accept(client, dev, meta=metadata(dev, sequence=30, captured_at=(origin + timedelta(seconds=2)).isoformat()))
    fake_clock.advance(301)
    assert run_once(settings, infer=fake_infer)
    new = state(client, admin_headers, identifier)
    assert old["revision"] != new["revision"] and new["audio"]["coverage_percent"] == 100
    assert client.get(f"/incidents/{identifier}/audio/file", params={"revision": old["revision"]}, headers=admin_headers).status_code == 409
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(IncidentAnalysis)) == 1


def test_source_arriving_during_inference_discards_stale_result(client, admin_headers, settings, fake_clock):
    origin, _, dev, identifier, _, _ = fixture(client, admin_headers, settings, fake_clock,
                                              positions=(0, 1, 3, 4), core_end=4)
    def race(segments, **kwargs):
        accept(client, dev, meta=metadata(dev, sequence=30, captured_at=(origin + timedelta(seconds=2)).isoformat()))
        return fake_infer(segments, **kwargs)
    assert run_once(settings, infer=race)
    row = state(client, admin_headers, identifier)
    assert row["status"] == "pending" and row["classification"]["primary_category"] is None
    assert row["audio"]["available"]  # preceding verified audio remains playable
    fake_clock.advance(11)
    assert run_once(settings, infer=fake_infer)
    assert state(client, admin_headers, identifier)["audio"]["coverage_percent"] == 100


def test_active_snapshots_throttled_then_finalized_after_postroll_grace(client, admin_headers, settings, fake_clock):
    origin, _, _, identifier, _, _ = fixture(client, admin_headers, settings, fake_clock, closed=False)
    assert run_once(settings, infer=fake_infer)
    first = state(client, admin_headers, identifier)
    assert first["provisional"] is True
    fake_clock.advance(9)
    assert not run_once(settings, infer=fake_infer)
    fake_clock.advance(1)
    assert run_once(settings, infer=fake_infer)
    assert state(client, admin_headers, identifier)["revision"] != first["revision"]
    with SessionLocal() as db, db.begin():
        incident = db.get(Incident, identifier)
        incident.ended_at, incident.status = fake_clock.now(), "resolved"
    fake_clock.advance(10)
    assert run_once(settings, infer=fake_infer)
    assert state(client, admin_headers, identifier)["provisional"] is True  # 1s post +10s grace
    fake_clock.advance(10)
    assert run_once(settings, infer=fake_infer)
    assert state(client, admin_headers, identifier)["provisional"] is False


def test_duplicate_requests_and_expired_workers_cannot_overwrite(client, admin_headers, settings, fake_clock):
    _, _, _, identifier, _, _ = fixture(client, admin_headers, settings, fake_clock)
    def queue(_):
        with SessionLocal() as db, db.begin():
            return enqueue_incident(db, db.get(Incident, identifier), retry=True).id
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert len(set(pool.map(queue, range(4)))) == 1
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda _: claim_job(settings), range(2)))
    assert sum(item is not None for item in claims) == 1
    first = next(item for item in claims if item)
    fake_clock.advance(settings.classification_lease_seconds + 1)
    second = claim_job(settings)
    assert second and second.lease_token != first.lease_token
    assert not process_claim(first, settings, infer=fake_infer)
    assert process_claim(second, settings, infer=fake_infer)
    assert state(client, admin_headers, identifier)["attempts"] == 2


def test_failure_is_safe_retryable_and_preserves_live_pipeline(client, admin_headers, settings, fake_clock):
    _, _, _, identifier, _, _ = fixture(client, admin_headers, settings, fake_clock)
    before = snapshot()
    def broken(*args, **kwargs):
        raise RuntimeError("sensitive /private/path token")
    assert run_once(settings, infer=broken)
    fake_clock.advance(.2)
    assert run_once(settings, infer=broken)
    result = state(client, admin_headers, identifier)
    assert result["status"] == "failed"
    assert result["audio"]["available"]
    assert client.get(f"/incidents/{identifier}/audio/file", headers=admin_headers).status_code == 200
    assert "/private/" not in str(result) and "sensitive" not in str(result)
    assert snapshot() == before
    assert client.post(f"/incidents/{identifier}/analysis", headers=admin_headers).status_code == 202
    assert run_once(settings, infer=fake_infer)
    assert state(client, admin_headers, identifier)["status"] == "completed"
    assert snapshot() == before


def test_missing_corrupt_sources_explicit_and_playback_integrity_checked(client, admin_headers, settings, fake_clock):
    _, _, _, identifier, originals, ids = fixture(client, admin_headers, settings, fake_clock)
    with SessionLocal() as db:
        path = resolve_audio_path(db.get(AudioChunk, ids[1]).file_path, settings)
    path.write_bytes(b"broken bytes")
    assert run_once(settings, infer=fake_infer)
    result = state(client, admin_headers, identifier)
    assert result["audio"]["excluded_count"] == 1 and result["audio"]["gap_count"] == 1
    assert result["classification"]["confidence_status"] == "no_usable_audio"
    path.write_bytes(originals[1])
    assert client.post(f"/incidents/{identifier}/analysis", headers=admin_headers).status_code == 202
    assert run_once(settings, infer=fake_infer)
    assert state(client, admin_headers, identifier)["audio"]["excluded_count"] == 0
    path.write_bytes(b"changed after analysis")
    response = client.get(f"/incidents/{identifier}/audio/file", headers=admin_headers)
    assert response.status_code == 503 and str(path) not in response.text


def test_historical_mapping_excludes_other_devices_and_later_assignments(client, admin_headers, settings, fake_clock):
    origin, loc, dev, identifier, originals, _ = fixture(client, admin_headers, settings, fake_clock)
    other = device(client, admin_headers, loc["id"])
    accept(client, other, meta=metadata(other, captured_at=(origin + timedelta(seconds=1)).isoformat()))
    new_loc = location(client, admin_headers, name="Different room", threshold_type="dbfs_rms", threshold_value=-20)
    moved = client.patch(f'/devices/{dev["id"]}', headers=admin_headers,
        json={"location_id": new_loc["id"], "expected_revision": dev["config_revision"]})
    assert moved.status_code == 200
    accept(client, dev, meta=metadata(dev, sequence=30, captured_at=fake_clock.now().isoformat()))
    assert run_once(settings, infer=fake_infer)
    result = state(client, admin_headers, identifier)
    assert result["audio"]["location_id"] == loc["id"] and result["audio"]["recording_count"] == 3
    assert pcm(client.get(f"/incidents/{identifier}/audio/file", headers=admin_headers).content) == b"".join(pcm(originals[key]) for key in sorted(originals))


def test_explicit_limits_legacy_missing_association_and_privileges(client, admin_headers, settings, fake_clock, monkeypatch, db):
    _, _, _, identifier, _, _ = fixture(client, admin_headers, settings, fake_clock)
    import app.incident_audio as audio
    monkeypatch.setattr(audio, "MAX_RECORDED_SECONDS", 1)
    assert run_once(settings, infer=fake_infer)
    value = state(client, admin_headers, identifier)
    assert value["audio"]["truncated"] and value["audio"]["duration_seconds"] == 1
    assert value["audio"]["truncation_reason"]
    with SessionLocal() as session, session.begin():
        session.get(Incident, identifier).stream_id = None
    client.post(f"/incidents/{identifier}/analysis", headers=admin_headers)
    assert run_once(settings, infer=fake_infer)
    value = state(client, admin_headers, identifier)
    assert not value["audio"]["association_verified"] and not value["audio"]["available"]
    assert value["classification"]["confidence_status"] == "no_usable_audio"
    with pytest.raises(DBAPIError):
        db.execute(text("DROP TABLE incident_analyses"))
    db.rollback()


def test_auth_local_sessions_and_device_listener_boundary(client, admin_headers, settings, fake_clock, monkeypatch):
    _, _, dev, identifier, _, _ = fixture(client, admin_headers, settings, fake_clock)
    assert run_once(settings, infer=fake_infer)
    path = f"/incidents/{identifier}/analysis"
    audio = f"/incidents/{identifier}/audio/file"
    for route in (path, audio):
        assert client.get(route).status_code == 401
        assert client.get(route, headers={"Authorization": "Bearer " + dev["token"]}).status_code == 403
    assert client.get(f"/incidents/{uuid.uuid4()}/analysis", headers=admin_headers).status_code == 404
    monkeypatch.setattr(settings, "local_browser_access", True)
    client.base_url = ORIGIN
    connect(client)
    assert client.get(path, headers=READ_HEADERS).status_code == 200
    assert client.get(audio, headers=READ_HEADERS).status_code == 200
    assert client.get(audio, headers={**READ_HEADERS, "X-Forwarded-For": "127.0.0.1"}).status_code == 401
    from app.device_api import create_device_app
    with TestClient(create_device_app(), base_url=ORIGIN) as hardware:
        hardware.cookies.update(client.cookies)
        assert hardware.get(path, headers=READ_HEADERS).status_code == 404
        assert hardware.get(audio, headers=READ_HEADERS).status_code == 404


def test_missing_assignment_context_and_incompatible_formats_are_not_claimed_as_full_coverage(client, admin_headers, settings, fake_clock, monkeypatch):
    origin, _, dev, identifier, _, _ = fixture(client, admin_headers, settings, fake_clock)
    monkeypatch.setattr(settings, "incident_context_before_seconds", 5)
    accept(client, dev, audio=pcm24_wav(32000, 1, .2), meta=metadata(dev, sequence=30,
        captured_at=(origin + timedelta(seconds=.5)).isoformat()))
    assert run_once(settings, infer=fake_infer)
    value = state(client, admin_headers, identifier)
    assert value["audio"]["context_before_seconds"] == 5
    assert value["audio"]["window_duration_seconds"] == 7
    assert value["audio"]["coverage_percent"] == pytest.approx(300 / 7)
    assert value["audio"]["gaps"][0]["duration_seconds"] == 4
    assert value["audio"]["exclusion_reasons"] == {"incompatible_audio_format_or_source": 1}


def test_all_missing_audio_is_explicit_no_data_and_never_a_fake_wav(client, admin_headers, settings, fake_clock):
    _, _, _, identifier, _, ids = fixture(client, admin_headers, settings, fake_clock)
    with SessionLocal() as db:
        for chunk in db.scalars(select(AudioChunk).where(AudioChunk.id.in_(ids.values()))):
            resolve_audio_path(chunk.file_path, settings).unlink()
    assert run_once(settings, infer=fake_infer)
    value = state(client, admin_headers, identifier)
    assert value["status"] == "completed" and not value["audio"]["available"]
    assert value["audio"]["coverage_seconds"] == 0 and value["audio"]["gap_count"] == 1
    assert value["audio"]["excluded_count"] == 3
    assert value["classification"]["confidence_status"] == "no_usable_audio"
    assert client.get(f"/incidents/{identifier}/audio/file", headers=admin_headers).status_code == 409


def test_changed_evidence_cannot_keep_an_old_classification_when_new_inference_fails(client, admin_headers, settings, fake_clock):
    origin, _, dev, identifier, _, _ = fixture(client, admin_headers, settings, fake_clock,
                                              positions=(0, 1, 3, 4), core_end=4)
    assert run_once(settings, infer=fake_infer)
    prior = state(client, admin_headers, identifier)
    accept(client, dev, meta=metadata(dev, sequence=30, captured_at=(origin + timedelta(seconds=2)).isoformat()))
    client.post(f"/incidents/{identifier}/analysis", headers=admin_headers)
    def broken(*args, **kwargs):
        raise RuntimeError("test-only unavailable inference")
    assert run_once(settings, infer=broken)
    value = state(client, admin_headers, identifier)
    assert value["revision"] != prior["revision"] and value["audio"]["coverage_percent"] == 100
    assert value["classification"]["primary_category"] is None
    assert value["classification"]["status"] == "pending"
    assert client.get(f"/incidents/{identifier}/audio/file", headers=admin_headers).status_code == 200


def test_source_read_budget_is_explicit_and_keeps_core_priority(client, admin_headers, settings, fake_clock, monkeypatch):
    _, _, _, identifier, originals, _ = fixture(client, admin_headers, settings, fake_clock)
    import app.incident_audio as audio
    monkeypatch.setattr(audio, "MAX_SOURCE_BYTES", len(originals[1]))
    assert run_once(settings, infer=fake_infer)
    value = state(client, admin_headers, identifier)
    assert value["audio"]["truncated"] and value["audio"]["exclusion_reasons"] == {"source_read_limit": 2}
    assert value["audio"]["core_coverage_seconds"] == 1 and value["audio"]["duration_seconds"] == 1
    assert value["classification"]["primary_category"] == "voice"


def test_initial_model_unavailability_does_not_block_incident_playback_or_consume_retries(client, admin_headers, settings, fake_clock):
    from app.classification_jobs import record_worker_status
    _, _, _, identifier, _, _ = fixture(client, admin_headers, settings, fake_clock)
    record_worker_status("unavailable", "The sound classification model is unavailable; saved recordings are waiting.")
    def must_not_infer(*args, **kwargs):
        pytest.fail("Audio preparation must not call the unavailable model")
    before = snapshot()
    assert run_once(settings, infer=must_not_infer, prepare_only=True)
    value = state(client, admin_headers, identifier)
    assert value["status"] == "pending" and value["attempts"] == 0
    assert value["audio"]["available"] and value["worker_status"] == "unavailable"
    assert value["classification"]["primary_category"] is None
    assert client.get(f"/incidents/{identifier}/audio/file", headers=admin_headers).status_code == 200
    assert snapshot() == before
    fake_clock.advance(31)
    record_worker_status("ready")
    assert run_once(settings, infer=fake_infer)
    value = state(client, admin_headers, identifier)
    assert value["status"] == "completed" and value["attempts"] == 1


def test_audio_only_retries_keep_active_snapshot_growing_during_model_outage(client, admin_headers, settings, fake_clock):
    origin, _, dev, identifier, _, _ = fixture(client, admin_headers, settings, fake_clock, closed=False)
    assert run_once(settings, prepare_only=True)
    first = state(client, admin_headers, identifier)
    fake_clock.advance(31)
    accept(client, dev, meta=metadata(dev, sequence=30, captured_at=(origin + timedelta(seconds=3)).isoformat()))
    assert run_once(settings, prepare_only=True)
    latest = state(client, admin_headers, identifier)
    assert latest["revision"] != first["revision"] and latest["provisional"]
    assert latest["audio"]["duration_seconds"] == 4
    assert latest["audio"]["ended_at"] == fake_clock.now().isoformat()
    assert latest["status"] == "pending" and latest["attempts"] == 0
