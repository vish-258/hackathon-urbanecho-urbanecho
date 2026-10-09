"""Classification persistence tests use a labelled fake model, never claim accuracy."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app.classification_contract import CATEGORIES, MAPPING_VERSION, MODEL_VERSION
from app.classification_jobs import (claim_job, classifications_for, enqueue_missing, enqueue_recording,
                                     process_claim, record_worker_status, run_once, worker_status)
from app.db import SessionLocal, get_engine
from app.models import (AudioChunk, ClassificationScanState, DurableEvent, Incident, Measurement,
                        RecordingClassification)
from tests.test_integration import accept, device, location, metadata, process
from tests.test_local_access import ORIGIN, READ_HEADERS, connect

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def classifier_settings(settings, monkeypatch):
    monkeypatch.setattr(settings, "classification_enabled", True)
    monkeypatch.setattr(settings, "classification_scope", "recordings")
    monkeypatch.setattr(settings, "classification_retry_base_seconds", .1)
    monkeypatch.setattr(settings, "classification_max_attempts", 2)


def fake_model(path, *, model_path=None):
    assert path.read_bytes().startswith(b"RIFF")
    return {"primary_category": "voice", "category_scores": {key: .8 if key == "voice" else .02 for key in CATEGORIES},
            "top_labels": [{"label": "Speech", "score": .8, "class_index": 0}],
            "model_version": MODEL_VERSION, "mapping_version": MAPPING_VERSION,
            "confidence_status": "classified", "uncertainty_reason": None, "score_threshold": .25,
            "analyzed_duration_seconds": 1, "input_sample_rate": 16000, "model_sample_rate": 16000}


def saved(client, admin_headers, fake_clock, *, name="SIMULATED classification fixture"):
    loc = location(client, admin_headers, name=name, threshold_type="dbfs_rms", threshold_value=-20)
    dev = device(client, admin_headers, loc["id"])
    audio = accept(client, dev, meta=metadata(dev, captured_at=fake_clock.now().isoformat()))
    return loc, dev, uuid.UUID(audio["id"])


def snapshot():
    with SessionLocal() as db:
        return {"audio": [(row.id, row.status, row.file_path, row.checksum) for row in db.scalars(select(AudioChunk).order_by(AudioChunk.id))],
                "measurement": [(row.id, row.content_hash, row.breach) for row in db.scalars(select(Measurement).order_by(Measurement.id))],
                "incident": [(row.id, row.status, row.recovery_streak) for row in db.scalars(select(Incident).order_by(Incident.id))],
                "events": [(row.id, row.pointer) for row in db.scalars(select(DurableEvent).order_by(DurableEvent.pointer))]}


def test_saved_result_survives_restart_and_is_batched_in_history(client, admin_headers, settings, fake_clock):
    _, _, audio_id = saved(client, admin_headers, fake_clock)
    pending = client.get(f"/audio/{audio_id}/classification", headers=admin_headers).json()
    assert pending["status"] == "pending" and pending["queued"] is False
    process(settings)
    before = snapshot()
    record_worker_status("ready")
    assert run_once(settings, infer=fake_model)
    assert snapshot() == before
    get_engine().dispose()
    result = client.get(f"/audio/{audio_id}/classification", headers=admin_headers).json()
    assert result["status"] == "completed" and result["primary_category"] == "voice"
    assert result["category_scores"]["voice"] == .8 and result["top_labels"][0]["label"] == "Speech"
    assert result["classified_at"] and result["attempts"] == 1 and result["worker_status"] == "ready"
    assert not {"file_path", "lease_token", "lease_until", "result"} & result.keys()
    for path in ("/audio", "/measurements"):
        assert client.get(path, headers=admin_headers).json()["items"][0]["classification"] == result
    incident_id = client.get("/incidents", headers=admin_headers).json()["items"][0]["id"]
    assert client.get(f"/incidents/{incident_id}/measurements", headers=admin_headers).json()["items"][0]["classification"] == result
    assert not run_once(settings, infer=fake_model)
    assert client.post(f"/audio/{audio_id}/classification", headers=admin_headers).json() == result
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(RecordingClassification)) == 1


def test_duplicate_queue_requests_and_workers_share_one_result(client, admin_headers, settings, fake_clock):
    _, _, audio_id = saved(client, admin_headers, fake_clock)
    def queue(_):
        with SessionLocal() as db, db.begin():
            return enqueue_recording(db, db.get(AudioChunk, audio_id)).id
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert len(set(pool.map(queue, range(4)))) == 1
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda _: claim_job(settings), range(2)))
    assert sum(claim is not None for claim in claims) == 1
    assert process_claim(next(claim for claim in claims if claim), settings, infer=fake_model)


def test_failure_retries_are_safe_and_never_change_noise_pipeline(client, admin_headers, settings, fake_clock):
    _, _, audio_id = saved(client, admin_headers, fake_clock)
    before = snapshot()
    def broken(*args, **kwargs):
        raise RuntimeError("secret-token-and-/private/model/path")
    assert run_once(settings, infer=broken)
    assert snapshot() == before
    first = client.get(f"/audio/{audio_id}/classification", headers=admin_headers)
    assert first.json()["status"] == "pending" and first.json()["attempts"] == 1
    assert "secret-token" not in first.text and "/private/" not in first.text
    assert claim_job(settings) is None
    # Classification failure cannot block the independent live-level worker.
    process(settings)
    assert client.get("/incidents", headers=admin_headers).json()["total"] == 1
    noise = snapshot()
    fake_clock.advance(.2)
    assert run_once(settings, infer=broken)
    assert snapshot() == noise
    failed = client.get(f"/audio/{audio_id}/classification", headers=admin_headers).json()
    assert failed["status"] == "failed" and failed["attempts"] == 2
    retried = client.post(f"/audio/{audio_id}/classification", headers=admin_headers)
    assert retried.status_code == 202 and retried.json()["attempts"] == 0
    assert run_once(settings, infer=fake_model)
    assert snapshot() == noise


def test_expired_claim_cannot_overwrite_retry_and_restart_resumes(client, admin_headers, settings, fake_clock):
    _, _, audio_id = saved(client, admin_headers, fake_clock)
    enqueue_missing(settings)
    first = claim_job(settings)
    assert first
    get_engine().dispose()
    fake_clock.advance(settings.classification_lease_seconds + 1)
    second = claim_job(settings)
    assert second and second.job_id == first.job_id and second.lease_token != first.lease_token
    assert not process_claim(first, settings, infer=fake_model)
    assert process_claim(second, settings, infer=fake_model)
    assert client.get(f"/audio/{audio_id}/classification", headers=admin_headers).json()["attempts"] == 2


def test_missing_original_retries_without_deleting_metadata_and_can_be_restored(client, admin_headers, settings, fake_clock):
    from app.storage import resolve_audio_path
    _, _, audio_id = saved(client, admin_headers, fake_clock)
    with SessionLocal() as db:
        path = resolve_audio_path(db.get(AudioChunk, audio_id).file_path, settings)
    original = path.read_bytes()
    path.unlink()
    run_once(settings, infer=fake_model)
    response = client.get(f"/audio/{audio_id}/classification", headers=admin_headers).json()
    assert response["status"] == "pending" and response["error"] == "Original recording is unavailable."
    assert client.get("/audio", headers=admin_headers).json()["total"] == 1
    path.write_bytes(original)
    fake_clock.advance(.2)
    assert run_once(settings, infer=fake_model)
    assert client.get(f"/audio/{audio_id}/classification", headers=admin_headers).json()["status"] == "completed"


def test_missing_model_is_global_unavailability_and_does_not_claim_recordings(client, admin_headers, settings, fake_clock, monkeypatch):
    import app.classification as model
    import app.classification_worker as worker
    _, _, audio_id = saved(client, admin_headers, fake_clock)
    class OneLoop:
        stopped = False
        def is_set(self): return self.stopped
        def set(self): self.stopped = True
        def wait(self, seconds): self.stopped = True
    def unavailable(**kwargs):
        raise RuntimeError("/private/model secret detail")
    with monkeypatch.context() as patch:
        patch.setattr(worker.threading, "Event", OneLoop)
        patch.setattr(worker.signal, "signal", lambda *args: None)
        patch.setattr(model, "prepare_model", unavailable)
        worker.main()
    result = client.get(f"/audio/{audio_id}/classification", headers=admin_headers)
    assert result.json()["status"] == "pending" and result.json()["queued"] is False
    assert result.json()["worker_status"] == "unavailable"
    assert "/private/" not in result.text and "secret detail" not in result.text


def test_backfill_cursor_is_bounded_resumable_and_serves_new_and_old(client, admin_headers, settings, fake_clock, monkeypatch):
    monkeypatch.setattr(settings, "classification_scan_batch_size", 2)
    origin = fake_clock.now()
    fake_clock.advance(-3600)
    _, dev, first_id = saved(client, admin_headers, fake_clock)
    ids = [first_id]
    for seq in range(1, 7):
        fake_clock.advance(1)
        result = accept(client, dev, meta=metadata(dev, sequence=seq, captured_at=fake_clock.now().isoformat()))
        ids.append(uuid.UUID(result["id"]))
    fake_clock.set(origin)
    assert enqueue_missing(settings) == 2
    with SessionLocal() as db:
        cursor = db.get(ClassificationScanState, (MODEL_VERSION, MAPPING_VERSION)).after_audio_id
        assert cursor == ids[1]
    get_engine().dispose()
    assert enqueue_missing(settings) == 2
    recent = accept(client, dev, meta=metadata(dev, sequence=7, captured_at=fake_clock.now().isoformat()))
    assert enqueue_missing(settings) == 3  # two older rows plus the new arrival
    newest = claim_job(settings)
    assert newest.audio_chunk_id == uuid.UUID(recent["id"])
    oldest = claim_job(settings, prefer_oldest=True)
    assert oldest.audio_chunk_id == first_id
    enqueue_missing(settings)
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(RecordingClassification)) == 8


def test_existing_model_results_remain_when_current_version_is_enqueued(client, admin_headers, settings, fake_clock):
    _, _, audio_id = saved(client, admin_headers, fake_clock)
    with SessionLocal() as db, db.begin():
        db.add(RecordingClassification(audio_chunk_id=audio_id, model_version="previous-model", mapping_version="previous-mapping",
            status="completed", source_received_at=fake_clock.now(), primary_category="other",
            result={"primary_category": "other"}, classified_at=fake_clock.now()))
    assert run_once(settings, infer=fake_model)
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(RecordingClassification)) == 2
    assert client.get(f"/audio/{audio_id}/classification", headers=admin_headers).json()["model_version"] == MODEL_VERSION


@pytest.mark.parametrize("mode", ["uncertain", "no_usable_audio"])
def test_ambiguous_or_unusable_audio_is_explicit_completed_result(client, admin_headers, settings, fake_clock, mode):
    _, _, audio_id = saved(client, admin_headers, fake_clock)
    def unsure(path, **kwargs):
        value = fake_model(path, **kwargs)
        value.update(primary_category="other", confidence_status=mode, uncertainty_reason="Test-only ambiguous recording")
        return value
    assert run_once(settings, infer=unsure)
    row = client.get(f"/audio/{audio_id}/classification", headers=admin_headers).json()
    assert row["status"] == "completed" and row["confidence_status"] == mode and row["primary_category"] == "other"


@pytest.mark.parametrize("change", [{"primary_category": "imaginary"}, {"top_labels": [{"label": "x", "score": float("nan"), "class_index": 0}]},
    {"model_version": "incorrect"}, {"category_scores": {"voice": .9}}])
def test_invalid_model_outputs_are_not_persisted(client, admin_headers, settings, fake_clock, change):
    _, _, audio_id = saved(client, admin_headers, fake_clock)
    def invalid(path, **kwargs):
        return {**fake_model(path, **kwargs), **deepcopy(change)}
    run_once(settings, infer=invalid)
    row = client.get(f"/audio/{audio_id}/classification", headers=admin_headers).json()
    assert row["status"] == "pending" and row["primary_category"] is None


def test_local_admin_access_service_readiness_and_lan_boundary(client, admin_headers, settings, fake_clock, monkeypatch):
    _, dev, audio_id = saved(client, admin_headers, fake_clock)
    path = f"/audio/{audio_id}/classification"
    assert client.get(path).status_code == 401
    assert client.get(path, headers={"Authorization": "Bearer " + dev["token"]}).status_code == 403
    assert client.post(path, headers={"Authorization": "Bearer " + dev["token"]}).status_code == 403
    assert client.get(f"/audio/{uuid.uuid4()}/classification", headers=admin_headers).status_code == 404
    monkeypatch.setattr(settings, "local_browser_access", True)
    client.base_url = ORIGIN
    connect(client)
    assert client.get(path, headers=READ_HEADERS).status_code == 200
    record_worker_status("unavailable", "The sound classification model is unavailable; saved recordings are waiting.")
    response = client.get(path, headers=READ_HEADERS).json()
    assert response["status"] == "pending" and response["queued"] is False
    assert response["worker_status"] == "unavailable"
    assert client.get("/classification/status", headers=READ_HEADERS).json()["worker_status"] == "unavailable"
    monkeypatch.setattr(settings, "classification_enabled", False)
    assert not run_once(settings, infer=fake_model)
    assert client.get(path, headers=READ_HEADERS).json()["worker_status"] == "disabled"
    assert client.post(path, headers=admin_headers).status_code == 409
    from app.device_api import create_device_app
    with TestClient(create_device_app(), base_url=ORIGIN) as hardware:
        hardware.cookies.update(client.cookies)
        assert hardware.get(path, headers=READ_HEADERS).status_code == 404
        assert hardware.get("/classification/status", headers=READ_HEADERS).status_code == 404


def test_classifier_role_can_update_results_but_cannot_drop_tables(db):
    with pytest.raises(DBAPIError):
        db.execute(text("DROP TABLE recording_classifications"))
    db.rollback()


def test_classification_pages_fetch_results_in_one_batch(client, admin_headers, settings, fake_clock, monkeypatch):
    _, dev, _ = saved(client, admin_headers, fake_clock)
    for seq in range(1, 4):
        accept(client, dev, meta=metadata(dev, sequence=seq, captured_at=fake_clock.now().isoformat()))
    from sqlalchemy import event
    queries = []
    def count(connection, cursor, statement, parameters, context, executemany):
        if "recording_classifications." in statement:
            queries.append(statement)
    event.listen(get_engine(), "before_cursor_execute", count)
    try:
        page = client.get("/audio", headers=admin_headers).json()
    finally:
        event.remove(get_engine(), "before_cursor_execute", count)
    assert page["total"] == 4 and len(queries) == 1
    assert all(item["classification"]["status"] == "pending" for item in page["items"])


def test_classifications_keep_capture_location_after_move_and_preserve_original_bytes(client, admin_headers, settings, fake_clock):
    import hashlib
    from scripts.simulate import pcm24_wav
    first_loc, first_dev, first_id = saved(client, admin_headers, fake_clock, name="SIMULATED original room")
    second_loc = location(client, admin_headers, name="SIMULATED second room", threshold_type="dbfs_rms", threshold_value=-20)
    second_dev = device(client, admin_headers, second_loc["id"])
    second_audio = pcm24_wav(16000, 1, .02)
    second = accept(client, second_dev, audio=second_audio, meta=metadata(second_dev, captured_at=fake_clock.now().isoformat()))
    first_bytes = client.get(f"/audio/{first_id}/file", headers=admin_headers).content
    def distinct(path, **kwargs):
        value = fake_model(path, **kwargs)
        category = "voice" if path.read_bytes() == first_bytes else "music"
        value["primary_category"] = category
        value["category_scores"] = {key: .8 if key == category else .02 for key in CATEGORIES}
        return value
    assert run_once(settings, infer=distinct)
    assert run_once(settings, infer=distinct)
    fake_clock.advance(1)
    assert client.patch(f'/devices/{first_dev["id"]}', headers=admin_headers,
        json={"location_id": second_loc["id"], "expected_revision": first_dev["config_revision"]}).status_code == 200
    first_page = client.get("/audio", params={"location_id": first_loc["id"]}, headers=admin_headers).json()["items"]
    second_page = client.get("/audio", params={"location_id": second_loc["id"]}, headers=admin_headers).json()["items"]
    assert [(item["id"], item["classification"]["primary_category"]) for item in first_page] == [(str(first_id), "voice")]
    assert [(item["id"], item["classification"]["primary_category"]) for item in second_page] == [(second["id"], "music")]
    assert first_page[0]["location_snapshot"]["name"] == first_loc["name"]
    for identifier, original in ((str(first_id), first_bytes), (second["id"], second_audio)):
        assert client.get(f"/audio/{identifier}/file", headers=admin_headers).content == original
        with SessionLocal() as db:
            assert db.get(AudioChunk, uuid.UUID(identifier)).checksum == hashlib.sha256(original).hexdigest()
