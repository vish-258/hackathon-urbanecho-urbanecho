"""Explicit sampling cadence never invents sound coverage between recordings."""
from datetime import timedelta
import hashlib
import json

import pytest

from tests.live_helpers import BASE, NOW, create_stream, put_reading, records
from tests.test_integration import device, location, metadata, process, upload
from tests.test_pcm_upload_api import headers, raw_pcm

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def capture_clock(fake_clock):
    fake_clock.set(NOW)


def scheduled(stream, sequence, value, *, at=None, period=10000, **kwargs):
    return put_reading(stream, sequence, value, captured_at=BASE + timedelta(seconds=at if at is not None else sequence * 10),
                       chunk_fields={"capture_interval_ms": period}, **kwargs)


def test_scheduled_samples_recover_without_counting_unrecorded_duration():
    from app.models import AudioChunk, Incident, Measurement, StreamState
    from tests.test_daily import _run
    stream = create_stream()
    for seq, level in enumerate([80, 50, 50, 50]):
        assert scheduled(stream, seq, level).status == "eligible_live"
    incident = records(Incident)[0]
    assert incident.status == "resolved" and incident.ended_at == BASE + timedelta(seconds=31)
    assert records(StreamState)[0].window_end == BASE + timedelta(seconds=31)
    assert all(row.interval_seconds == 1 for row in records(Measurement))
    assert all(row.capture_interval_ms == 10000 and row.duration_seconds == 1 for row in records(AudioChunk))
    stats = _run(stream)[0][2]
    assert stats["measurement_count"] == 4
    assert stats["usable_duration_seconds"] == stats["recorded_duration_seconds"] == 4
    assert stats["missing_duration_seconds"] == 86400 - 4


@pytest.mark.parametrize("change", ["missing_sequence", "late_capture", "early_capture", "new_session", "period_change", "invalid"])
def test_recovery_still_resets_for_unscheduled_or_invalid_samples(change):
    from app.models import Incident
    stream = create_stream()
    scheduled(stream, 0, 80)
    scheduled(stream, 1, 50)
    assert records(Incident)[0].recovery_streak == 1
    seq, at, options = 2, 20, {}
    if change == "missing_sequence":
        seq = 3
    elif change == "late_capture":
        at = 30
    elif change == "early_capture":
        at = 11
    elif change == "new_session":
        options["session_id"] = "replacement-session"
    elif change == "period_change":
        options["period"] = 20000
    elif change == "invalid":
        scheduled(stream, 2, None, result={"quality_status": "clipped"})
        assert records(Incident)[0].recovery_streak == 0
        seq, at = 3, 30
    scheduled(stream, seq, 50, at=at, **options)
    incident = records(Incident)[0]
    assert incident.status == "recovering" and incident.recovery_streak == 1


def test_cadence_transition_legacy_hash_and_identical_replay_remain_compatible():
    from app.db import SessionLocal
    from app.evaluation import _canonical, _fingerprint, evaluate_measurement
    from app.models import AudioChunk, DurableEvent, Incident, Measurement
    stream = create_stream()
    original = put_reading(stream, 0, 80)
    with SessionLocal() as db:
        chunk = db.get(AudioChunk, original.audio_id)
        measurement = db.get(Measurement, original.measurement_id)
        assert chunk.capture_interval_ms is None
        # The pre-migration source hash had no cadence key at all. Keep its
        # canonical field set as a frozen compatibility fixture.
        measurement_fields = ("audio_chunk_id", "measured_at", "received_at", "interval_seconds", "value_db",
            "digital_dbfs", "measurement_type", "calibration_status", "calibration_version", "processing_version",
            "weighting", "channel_policy", "quality_status", "result_version", "is_reprocessing", "calibration_snapshot")
        source_fields = ("id", "device_id", "location_id", "assignment_id", "location_snapshot", "captured_at",
            "received_at", "session_id", "sequence", "duration_seconds", "sample_rate", "checksum")
        data = {"measurement": {field: getattr(measurement, field) for field in measurement_fields},
                "source": {field: getattr(chunk, field) for field in source_fields}}
        for field in ("interval_seconds", "value_db", "digital_dbfs"):
            data["measurement"][field] = float(data["measurement"][field])
        data["source"]["duration_seconds"] = float(data["source"]["duration_seconds"])
        legacy_hash = hashlib.sha256(json.dumps(_canonical(data), sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        assert _fingerprint(measurement, chunk) == measurement.content_hash == legacy_hash
        old_count = len(records(DurableEvent))
        assert evaluate_measurement(db, measurement).id == original.evaluation_id
        db.commit()
    assert len(records(DurableEvent)) == old_count
    # A deliberate cadence switch resets once, then scheduled normal samples
    # recover. Legacy gaps without declared cadence retain the strict old rule.
    put_reading(stream, 1, 50, captured_at=BASE + timedelta(seconds=10))
    put_reading(stream, 2, 50, captured_at=BASE + timedelta(seconds=20))
    assert records(Incident)[0].recovery_streak == 1
    scheduled(stream, 3, 50)
    assert records(Incident)[0].recovery_streak == 1
    scheduled(stream, 4, 50)
    scheduled(stream, 5, 50)
    assert records(Incident)[0].status == "resolved"


def test_historical_reprocessing_retains_cadence_without_live_replay():
    from app.models import DurableEvent, Incident
    stream = create_stream()
    source = scheduled(stream, 0, 80)
    scheduled(stream, 1, 50)
    before = len(records(DurableEvent))
    result = put_reading(stream, 0, 50, source_id=source.audio_id, result_version="cadence-reprocess", reprocessing=True)
    assert result.status == "historical_reprocessing" and result.live is False
    assert len(records(DurableEvent)) == before
    assert records(Incident)[0].recovery_streak == 1
    scheduled(stream, 2, 50)
    scheduled(stream, 3, 50)
    assert records(Incident)[0].status == "resolved"


def test_missing_prior_measurement_pointer_cannot_recover_across_an_unknown_gap():
    from app.db import SessionLocal
    from app.models import Incident, StreamState
    stream = create_stream()
    scheduled(stream, 0, 80)
    scheduled(stream, 1, 50)
    with SessionLocal() as db, db.begin():
        state = db.get(StreamState, records(StreamState)[0].id)
        state.last_measurement_id = None
    scheduled(stream, 2, 50, at=30)
    assert records(Incident)[0].recovery_streak == 1


def test_pcm_header_is_saved_retry_safe_and_does_not_expand_audio(client, admin_headers, settings, fake_clock):
    loc = location(client, admin_headers, threshold_type="dbfs_rms", threshold_value=-20)
    dev = device(client, admin_headers, loc["id"])
    upload_headers = {**headers(dev, captured=fake_clock.now()), "X-Capture-Interval-Ms": "10000"}
    pcm = raw_pcm()
    response = client.post("/upload", headers=upload_headers, content=pcm)
    assert response.status_code == 200, response.text
    audio_id = response.json()["id"]
    retry = client.post("/upload", headers=upload_headers, content=pcm)
    assert retry.status_code == 200 and retry.json()["duplicate"] is True
    changed = {**upload_headers, "X-Capture-Interval-Ms": "20000"}
    assert client.post("/upload", headers=changed, content=pcm).status_code == 409
    missing = {key: value for key, value in upload_headers.items() if key != "X-Capture-Interval-Ms"}
    assert client.post("/upload", headers=missing, content=pcm).status_code == 409
    process(settings)
    detail = client.get(f"/audio/{audio_id}", headers=admin_headers).json()
    assert detail["duration_seconds"] == 1 and detail["capture_interval_ms"] == 10000
    measurement = detail["measurements"][0]
    assert measurement["capture_interval_ms"] == 10000 and measurement["interval_seconds"] == 1
    assert measurement["evaluation"]["status"] == "eligible_live"
    assert client.get("/audio", headers=admin_headers).json()["items"][0]["capture_interval_ms"] == 10000


@pytest.mark.parametrize("interval", [0, -1, 999, 3600001, True, 1000.5, "10000"])
def test_multipart_capture_cadence_validation(client, admin_headers, fake_clock, interval):
    loc = location(client, admin_headers)
    dev = device(client, admin_headers, loc["id"])
    response = upload(client, dev, meta=metadata(dev, captured_at=fake_clock.now().isoformat(), capture_interval_ms=interval))
    assert response.status_code == 422, response.text
    assert client.get("/audio", headers=admin_headers).json()["total"] == 0


@pytest.mark.parametrize("value", ["0", "-1", "999", "3600001", "true", "1000.5"])
def test_pcm_rejects_invalid_or_too_short_cadence(client, admin_headers, fake_clock, value):
    loc = location(client, admin_headers)
    dev = device(client, admin_headers, loc["id"])
    response = client.post("/upload", headers={**headers(dev, captured=fake_clock.now()), "X-Capture-Interval-Ms": value}, content=raw_pcm())
    assert response.status_code == 422, response.text
    assert client.get("/audio", headers=admin_headers).json()["total"] == 0
