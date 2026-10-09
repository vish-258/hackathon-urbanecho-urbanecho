"""Worker crash/fencing/reprocessing guarantees using real PostgreSQL transactions."""
from concurrent.futures import ThreadPoolExecutor
import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from test_integration import accept, device, location, metadata
from test_audio import wav

pytestmark = pytest.mark.integration


def prepared(client, admin_headers):
    loc = location(client, admin_headers, threshold_type='dbfs_rms', threshold_value=-15)
    dev = device(client, admin_headers, loc['id'])
    return dev, accept(client, dev)


def totals(db):
    from app.models import DurableEvent, Evaluation, Incident, Measurement
    return tuple(db.scalar(select(func.count()).select_from(model)) for model in (Measurement, Evaluation, Incident, DurableEvent))


@pytest.mark.parametrize('failure_kind', ['crash', 'database'])
def test_before_commit_failure_rolls_back_measurement_incident_events_and_watermark(
    client, admin_headers, db, settings, monkeypatch, fake_clock, failure_kind,
):
    import app.worker as worker
    from app.models import ProcessingJob, StreamState
    prepared(client, admin_headers)
    baseline = totals(db)
    claim = worker.claim_job(settings)
    assert claim is not None
    real_evaluate = worker.evaluate_measurement

    class WorkerCrash(BaseException):
        pass

    failure_type = WorkerCrash if failure_kind == 'crash' else SQLAlchemyError

    def fail_after_transition(session, measurement, settings=None):
        real_evaluate(session, measurement, settings=settings)
        session.flush()
        raise failure_type('synthetic failure before commit')

    with monkeypatch.context() as patch:
        patch.setattr(worker, 'evaluate_measurement', fail_after_transition)
        with pytest.raises(failure_type):
            worker.process_claim(claim, settings)
    assert totals(db) == baseline
    assert db.scalar(select(func.count()).select_from(StreamState)) == 0
    job = db.get(ProcessingJob, claim.job_id)
    assert job.status == 'processing' and job.attempts == 1
    fake_clock.advance(settings.job_lease_seconds + 1)
    recovered = worker.claim_job(settings)
    assert recovered and recovered.lease_token != claim.lease_token
    # Advance made the old recording too old for live, so restore clock only
    # after establishing a valid replacement lease; this is deliberate fixture
    # control, not a sleep or a production timestamp rewrite.
    fake_clock.advance(-(settings.job_lease_seconds + 1))
    worker.process_claim(recovered, settings)
    assert totals(db)[:3] == (1, 1, 1)
    assert totals(db)[3] >= 1


def test_after_commit_crash_retry_and_two_workers_same_claim_are_idempotent(
    client, admin_headers, db, settings,
):
    from app.models import DurableEvent, Incident, ProcessingJob
    from app.worker import claim_job, process_claim
    prepared(client, admin_headers)
    claim = claim_job(settings)
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(process_claim, claim, settings) for _ in range(2)]
        for job in jobs:
            job.result(timeout=10)
    before = totals(db)
    assert before[:3] == (1, 1, 1)
    assert db.scalar(select(Incident.breach_count)) == 1
    assert db.scalar(select(func.count()).select_from(DurableEvent).where(DurableEvent.event_type == 'incident.opened')) == 1
    # A crashed process may not have observed its successful commit. Re-running
    # that exact claim is fenced by the completed job, with no count/event replay.
    process_claim(claim, settings)
    assert totals(db) == before
    assert db.get(ProcessingJob, claim.job_id).status == 'completed'


def test_processing_failure_is_visible_retryable_and_preserved_original_unchanged(
    client, admin_headers, db, settings, fake_clock,
):
    from app.models import AudioChunk, ProcessingJob
    from app.worker import run_once
    _, accepted = prepared(client, admin_headers)
    baseline = totals(db)
    chunk = db.get(AudioChunk, uuid.UUID(accepted['id']))
    path = settings.audio_root / chunk.file_path
    original = path.read_bytes()
    altered = bytearray(original)
    altered[-1] ^= 1
    path.write_bytes(altered)
    assert run_once(settings)
    job = db.scalar(select(ProcessingJob))
    assert job.status == 'retry' and 'checksum' in job.last_error
    assert totals(db) == baseline
    path.write_bytes(original)
    fake_clock.advance(settings.job_retry_base_seconds + .001)
    assert run_once(settings)
    db.expire_all()
    assert db.get(ProcessingJob, job.id).status == 'completed'
    assert totals(db)[:3] == (1, 1, 1)
    assert path.read_bytes() == original


def test_clipped_audio_and_silence_preserve_bytes_without_recovery(
    client, admin_headers, db, settings,
):
    from app.models import AudioChunk, Incident, Measurement
    from app.worker import run_once
    dev, _ = prepared(client, admin_headers)
    assert run_once(settings)
    for sequence, samples, expected_quality in [
        (1, [-(1 << 23), (1 << 23) - 1] * 8000, 'clipped'),
        (2, [0] * 16000, 'silence'),
    ]:
        original = wav(samples)
        accepted = accept(client, dev, meta=metadata(dev, sequence=sequence), audio=original)
        assert run_once(settings)
        measurement = db.scalar(select(Measurement).where(Measurement.audio_chunk_id == uuid.UUID(accepted['id'])))
        assert measurement.quality_status == expected_quality and measurement.breach is False
        chunk = db.get(AudioChunk, measurement.audio_chunk_id)
        assert (settings.audio_root / chunk.file_path).read_bytes() == original
    db.expire_all()
    incident = db.scalar(select(Incident))
    assert incident.status == 'active' and incident.recovery_streak == 0


def test_versioned_historical_reprocess_does_not_replay_alerts(client, admin_headers, db, settings):
    from app.models import AudioChunk, Evaluation, Measurement
    from app.worker import run_once
    from scripts.reprocess import reprocess
    _, accepted = prepared(client, admin_headers)
    assert run_once(settings)
    before = totals(db)
    audio_id = uuid.UUID(accepted['id'])
    original = db.get(AudioChunk, audio_id)
    original_bytes = (settings.audio_root / original.file_path).read_bytes()
    created = reprocess(audio_id, 'historical-v2')
    assert created['live'] is False and created['evaluation_status'] == 'historical_reprocessing'
    assert totals(db) == (before[0] + 1, before[1] + 1, before[2], before[3])
    assert reprocess(audio_id, 'historical-v2') == created
    assert totals(db) == (before[0] + 1, before[1] + 1, before[2], before[3])
    assert (settings.audio_root / original.file_path).read_bytes() == original_bytes
    versions = set(db.scalars(select(Measurement.result_version)))
    assert versions == {'initial', 'historical-v2'}
    with pytest.raises(ValueError, match='non-reserved'):
        reprocess(audio_id, 'initial')


def test_reprocessing_with_explicit_replacement_calibration_keeps_ingestion_snapshot(
    client, admin_headers, db, settings,
):
    from app.models import AudioChunk, Measurement
    from app.worker import run_once
    from scripts.reprocess import reprocess
    from test_integration import SYNTHETIC_CALIBRATION
    # Clearly synthetic test calibration only; production CLI never invents one.
    dev = device(client, admin_headers, location(client, admin_headers)['id'])
    accepted = accept(client, dev)
    assert run_once(settings)
    before = totals(db)
    identifier = uuid.UUID(accepted['id'])
    reprocess(identifier, 'synthetic-calibration-v2', SYNTHETIC_CALIBRATION)
    db.expire_all()
    assert db.get(AudioChunk, identifier).calibration is None
    readings = db.scalars(select(Measurement).order_by(Measurement.result_version)).all()
    initial = next(reading for reading in readings if reading.result_version == 'initial')
    new = next(reading for reading in readings if reading.result_version != 'initial')
    assert initial.value_db is None and initial.calibration_snapshot is None
    assert new.value_db > 75 and new.calibration_snapshot['version'] == SYNTHETIC_CALIBRATION['version']
    assert totals(db) == (2, 2, before[2], before[3])
    from app.evaluation import MeasurementConflict
    with pytest.raises(MeasurementConflict, match='different measurement content'):
        reprocess(identifier, 'synthetic-calibration-v2', {**SYNTHETIC_CALIBRATION, 'offset_db': 101})
    assert totals(db) == (2, 2, before[2], before[3])


@pytest.mark.parametrize('override', [
    {'status': 'failed'}, {'weighting': 'A'}, {'channel_policy': 'stereo'},
    {'offset_db': True}, {'offset_db': '100'}, {'offset_db': float('nan')},
    {'offset_db': float('inf')}, {'offset_db': float('-inf')},
    {'sample_rate': True}, {'calibrated_at': 123}, {'valid_until': None},
])
def test_invalid_calibration_is_diagnostic_never_a_physical_comparison(settings, override):
    from app.processing import calculate_measurement
    from test_audio import fixture_chunk
    from test_integration import SYNTHETIC_CALIBRATION
    chunk = fixture_chunk(settings)
    chunk.calibration = {**SYNTHETIC_CALIBRATION, **override}
    result = calculate_measurement(chunk, settings)
    assert result.value_db is None and result.calibration_status == 'calibration_required'
    assert result.breach is False
