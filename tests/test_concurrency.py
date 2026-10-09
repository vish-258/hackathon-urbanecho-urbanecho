"""Deterministic real-PostgreSQL tests of the concurrency safety boundaries."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import time
import uuid

import pytest
from sqlalchemy import select, text

from test_integration import SYNTHETIC_CALIBRATION, accept, counts, device, location, metadata, upload
from scripts.simulate import pcm24_wav

pytestmark = pytest.mark.integration


def test_overlapping_session_timestamp_is_historical_and_cannot_resolve(client, admin_headers, settings):
    from app.worker import run_once

    dev = device(client, admin_headers, location(client, admin_headers)['id'],
                 calibration=SYNTHETIC_CALIBRATION)
    for session_id in ['session-a', 'session-b']:
        accept(client, dev, meta=metadata(dev, session_id=session_id, chunk_id=session_id))
        assert run_once(settings)
    assert client.get('/incidents', headers=admin_headers).json()['total'] == 1
    accept(client, dev, meta=metadata(dev, sequence=1, session_id='session-b', chunk_id='quiet'),
           audio=pcm24_wav(16000, 1, .001))
    assert run_once(settings)
    repaired = client.get('/incidents', headers=admin_headers).json()
    assert repaired['total'] == 1
    assert all(item['status'] == 'recovering' for item in repaired['items'])
    assert repaired['items'][0]['recovery_streak'] == 1


def test_workers_skip_locked_job_and_complete_one_device_concurrently(client, admin_headers, db, settings):
    from app.models import ProcessingJob
    from app.worker import claim_job, process_claim

    dev = device(client, admin_headers, location(client, admin_headers)['id'],
                 calibration=SYNTHETIC_CALIBRATION)
    for sequence in range(3):
        accept(client, dev, meta=metadata(dev, sequence=sequence))
    locked = db.scalar(select(ProcessingJob).order_by(ProcessingJob.created_at, ProcessingJob.id)
                       .with_for_update().limit(1))
    assert locked is not None
    locked_id = locked.id
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(claim_job, settings) for _ in range(2)]
        try:
            claims = [future.result(timeout=5) for future in futures]
            assert all(claim is not None for claim in claims)
            assert len({claim.job_id for claim in claims}) == 2
            assert locked_id not in {claim.job_id for claim in claims}
        finally:
            # Also unblocks an incorrectly implemented claimant on test failure.
            db.rollback()
    final_claim = claim_job(settings)
    assert final_claim and final_claim.job_id == locked_id
    assert claim_job(settings) is None
    with ThreadPoolExecutor(max_workers=3) as pool:
        completions = [pool.submit(process_claim, claim, settings) for claim in [*claims, final_claim]]
        for completion in completions:
            completion.result(timeout=10)
    assert counts(db) == (3, 3, 3, 1)
    assert set(db.scalars(select(ProcessingJob.status))) == {'completed'}
    incident = client.get('/incidents', headers=admin_headers).json()['items'][0]
    assert incident['status'] == 'active'


def test_reconciler_waits_for_inflight_file_commit_and_preserves_original(
    client, admin_headers, db, settings, monkeypatch,
):
    import app.ingestion as api_module
    from app.models import AudioChunk
    from app.reconcile import reconcile
    from app.storage import STORAGE_ADVISORY_LOCK

    dev = device(client, admin_headers, location(client, admin_headers)['id'])
    durable_file_ready = Event()
    permit_commit = Event()
    finalized = {}
    real_finalize = api_module.finalize_audio

    def paused_finalize(staged, current_settings):
        reference = real_finalize(staged, current_settings)
        finalized['reference'] = reference
        durable_file_ready.set()
        if not permit_commit.wait(10):
            raise RuntimeError('Synthetic test gate timed out')
        return reference

    monkeypatch.setattr(api_module, 'finalize_audio', paused_finalize)
    with ThreadPoolExecutor(max_workers=2) as pool:
        incoming = pool.submit(upload, client, dev)
        cleaner = None
        try:
            assert durable_file_ready.wait(5), 'Upload did not reach durable file boundary'
            path = settings.audio_root / finalized['reference']
            assert path.is_file()
            assert counts(db) == (0, 0, 0, 0)
            cleaner = pool.submit(reconcile, settings, delete_orphans=True)
            deadline = time.monotonic() + 5
            waiting = False
            while time.monotonic() < deadline:
                waiting = bool(db.scalar(text(
                    "SELECT count(*) FROM pg_locks WHERE locktype='advisory' "
                    "AND classid=:high AND objid=:low AND mode='ExclusiveLock' AND NOT granted"
                ), {'high': STORAGE_ADVISORY_LOCK >> 32, 'low': STORAGE_ADVISORY_LOCK & 0xFFFFFFFF}))
                if waiting:
                    break
                time.sleep(.01)
            assert waiting, 'Reconciler must wait for the ingestion transaction storage lock'
            assert not cleaner.done()
            assert path.is_file()
        finally:
            permit_commit.set()
            response = incoming.result(timeout=10)
            report = cleaner.result(timeout=10) if cleaner is not None else None
    assert response.status_code == 202, response.text
    assert report == {'orphan_files': [], 'missing_committed_files': [], 'deleted_count': 0}
    saved = db.get(AudioChunk, uuid.UUID(response.json()['id']))
    assert saved and saved.file_path == finalized['reference']
    assert path.is_file()
    assert counts(db) == (1, 1, 0, 0)
