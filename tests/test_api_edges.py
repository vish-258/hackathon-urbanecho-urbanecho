"""Boundary and uncertain-commit behavior against the real database."""
import json

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.models import AudioChunk, ProcessingJob
from scripts.simulate import pcm24_wav
from test_integration import accept, device, location, metadata, upload

pytestmark = pytest.mark.integration


def test_chunked_body_without_content_length_is_bounded(client, admin_headers, db, settings):
    dev = device(client, admin_headers, location(client, admin_headers)['id'])
    boundary = 'bounded-request'
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="metadata"\r\n\r\n'
            + json.dumps(metadata(dev))
            + f'\r\n--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="large.wav"\r\nContent-Type: audio/wav\r\n\r\n').encode()
    data = body + b'x' * (settings.max_upload_bytes + 65537) + f'\r\n--{boundary}--\r\n'.encode()
    response = client.post('/audio', headers={
        'Authorization': f'Bearer {dev["token"]}',
        'Content-Type': f'multipart/form-data; boundary={boundary}',
    }, content=iter(data[start:start + 8192] for start in range(0, len(data), 8192)))
    assert response.status_code == 413, response.text
    assert 'error' in response.json()
    assert db.scalar(select(func.count()).select_from(AudioChunk)) == 0
    assert not list(settings.audio_root.rglob('*.wav'))


def test_committed_but_lost_acknowledgement_retries_without_losing_audio(client, admin_headers, db, settings, monkeypatch):
    from app.reconcile import reconcile
    dev = device(client, admin_headers, location(client, admin_headers)['id'])
    original_commit = Session.commit
    def commit_then_disconnect(session):
        original_commit(session)
        raise OperationalError('synthetic lost commit acknowledgement', None, Exception())
    with monkeypatch.context() as patch:
        patch.setattr(Session, 'commit', commit_then_disconnect)
        failed = upload(client, dev)
    assert failed.status_code == 503
    assert db.scalar(select(func.count()).select_from(AudioChunk)) == 1
    assert reconcile(settings, delete_orphans=True)['deleted_count'] == 0
    retried = upload(client, dev)
    assert retried.status_code == 200 and retried.json()['duplicate']
    assert db.scalar(select(func.count()).select_from(ProcessingJob)) == 1
    original = client.get(f'/audio/{retried.json()["id"]}/file', headers=admin_headers)
    assert original.content == pcm24_wav(16000, 1, .5)


def test_int64_sequence_and_equivalent_timestamp_retry(client, admin_headers):
    dev = device(client, admin_headers, location(client, admin_headers)['id'])
    meta = metadata(dev, sequence=0)
    meta.update(sequence=2**40, captured_at='2026-02-01T17:30:00+05:30')
    accepted = accept(client, dev, meta=meta)
    meta['captured_at'] = '2026-02-01T12:00:00Z'
    retried = upload(client, dev, meta=meta)
    assert retried.status_code == 200
    assert retried.json()['id'] == accepted['id']


@pytest.mark.parametrize('parameters', [
    {'latitude': 'nan', 'longitude': 77, 'radius_m': 1000},
    {'latitude': 12, 'longitude': 'inf', 'radius_m': 1000},
    {'latitude': 12, 'longitude': 77, 'radius_m': 'nan'},
    {'latitude': 12, 'longitude': 77, 'radius_m': -1},
])
def test_invalid_nearby_parameters(client, admin_headers, parameters):
    response = client.get('/locations/nearby', headers=admin_headers, params=parameters)
    assert response.status_code == 422


def test_reused_id_conflicts_even_if_new_bytes_are_invalid_wav(client, admin_headers):
    dev = device(client, admin_headers, location(client, admin_headers)['id'])
    accept(client, dev)
    response = upload(client, dev, audio=b'different and malformed WAV bytes')
    assert response.status_code == 409


def test_identical_retry_survives_updated_validation_settings(client, admin_headers, settings, monkeypatch):
    dev = device(client, admin_headers, location(client, admin_headers)['id'])
    accepted = accept(client, dev)
    # A supported-rate change affects new uploads, not already accepted chunks.
    monkeypatch.setattr(settings, 'allowed_sample_rates', [48000])
    response = upload(client, dev)
    assert response.status_code == 200 and response.json()['id'] == accepted['id']
