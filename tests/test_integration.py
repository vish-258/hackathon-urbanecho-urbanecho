"""Real PostgreSQL/PostGIS coverage. Synthetic calibration is never hardware calibration."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
import uuid
import wave

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from scripts.simulate import pcm24_wav
from app import clock

pytestmark = pytest.mark.integration

CAPTURED = datetime(2026, 2, 1, 12, 0, tzinfo=timezone.utc)
SYNTHETIC_CALIBRATION = {
    'method': 'spl_z_leq', 'version': 'synthetic-test-only-v1', 'offset_db': 100.0,
    'sample_rate': 16000, 'calibrated_at': '2026-01-01T00:00:00Z',
    'valid_until': '2027-01-01T00:00:00Z',
}


def location(client, headers, **overrides):
    body = dict(name='Test location', latitude=12.9716, longitude=77.5946,
                timezone='Asia/Kolkata', threshold_value=75.0,
                threshold_type='spl_z_leq', interval_seconds=1)
    body.update(overrides)
    response = client.post('/locations', headers=headers, json=body)
    assert response.status_code == 201, response.text
    return response.json()


def device(client, headers, location_id, **overrides):
    response = client.post('/devices', headers=headers, json=dict(location_id=location_id, **overrides))
    assert response.status_code == 201, response.text
    return response.json()


def metadata(dev, *, sequence=0, **overrides):
    body = dict(device_id=dev['id'], chunk_id=f'test-{sequence}', session_id='session-test',
                sequence=sequence, captured_at=(CAPTURED + timedelta(seconds=sequence)).isoformat())
    body.update(overrides)
    return body


def upload(client, dev, *, audio=None, meta=None):
    return client.post('/audio', headers={'Authorization': f'Bearer {dev["token"]}'},
                       data={'metadata': json.dumps(metadata(dev) if meta is None else meta)},
                       files={'file': ('../../untrusted.wav', pcm24_wav(16000, 1, .5) if audio is None else audio, 'audio/wav')})


def accept(client, dev, **kwargs):
    response = upload(client, dev, **kwargs)
    assert response.status_code == 202, response.text
    return response.json()


def counts(db):
    return tuple(db.execute(text(f'SELECT count(*) FROM {name}')).scalar_one()
                 for name in ('audio_chunks', 'processing_jobs', 'measurements', 'incidents'))


def process(settings):
    from app.worker import run_once
    assert run_once(settings=settings) is True


def test_health_postgis_schema_and_restricted_role(client, db, privileged):
    assert client.get('/health/live').status_code == 200
    assert client.get('/health/ready').status_code == 200
    assert 'POSTGIS=' in db.execute(text('SELECT PostGIS_Full_Version()')).scalar_one()
    role = db.execute(text('SELECT rolsuper, rolcreatedb, rolcreaterole FROM pg_roles WHERE rolname=current_user')).one()
    assert not any(role)
    geo_type, srid = db.execute(text("SELECT type, srid FROM geography_columns WHERE f_table_name='locations'")).one()
    assert geo_type.lower() == 'point' and srid == 4326
    indexes = db.execute(text("SELECT indexdef FROM pg_indexes WHERE tablename='locations'")).scalars().all()
    assert any('using gist (point)' in value.lower() for value in indexes)
    for table, column in [('audio_chunks', 'captured_at'), ('measurements', 'measured_at'), ('incidents', 'started_at')]:
        assert db.execute(text('SELECT data_type FROM information_schema.columns WHERE table_name=:t AND column_name=:c'),
                          {'t': table, 'c': column}).scalar_one() == 'timestamp with time zone'
    with pytest.raises(DBAPIError):
        db.execute(text('CREATE TABLE app_must_not_create_tables (id integer)'))
    db.rollback()


def test_coordinate_order_radius_metres_geojson_and_pagination(client, admin_headers, db):
    origin = location(client, admin_headers, name='Origin', latitude=12.9716, longitude=77.5946)
    close = location(client, admin_headers, name='Near', latitude=12.9766, longitude=77.5946)
    far = location(client, admin_headers, name='Far', latitude=13.0716, longitude=77.5946)
    coords = db.execute(text('SELECT ST_X(point::geometry), ST_Y(point::geometry) FROM locations WHERE id=:id'), {'id': origin['id']}).one()
    assert coords == pytest.approx((77.5946, 12.9716), abs=1e-8)
    response = client.get('/locations/nearby', params=dict(latitude=12.9716, longitude=77.5946, radius_m=1000), headers=admin_headers)
    assert response.status_code == 200, response.text
    results = {item['id']: item for item in response.json()['items']}
    assert set(results) == {origin['id'], close['id']}
    assert results[origin['id']]['distance_m'] == pytest.approx(0, abs=.01)
    assert 540 < results[close['id']]['distance_m'] < 570
    features = client.get('/locations/geojson', headers=admin_headers).json()
    assert features['type'] == 'FeatureCollection'
    feature = next(item for item in features['features'] if str(item['id']) == origin['id'])
    assert feature['geometry']['coordinates'] == pytest.approx([77.5946, 12.9716])
    page = client.get('/locations?limit=1&offset=1', headers=admin_headers).json()
    assert page['total'] == 3 and len(page['items']) == 1 and page['offset'] == 1


@pytest.mark.parametrize('invalid', [
    {'latitude': 90.01}, {'latitude': -90.01}, {'longitude': 180.01}, {'longitude': -180.01},
    {'timezone': 'Not/A_Timezone'}, {'threshold_type': 'db'}, {'interval_seconds': 0},
    {'interval_seconds': 1.5}, {'threshold_value': 1, 'threshold_type': 'dbfs_rms'},
])
def test_invalid_location_rejected(client, admin_headers, invalid):
    body = dict(name='Invalid', latitude=12, longitude=77, timezone='UTC', threshold_value=75,
                threshold_type='spl_z_leq', interval_seconds=1)
    body.update(invalid)
    response = client.post('/locations', headers=admin_headers, json=body)
    assert response.status_code == 422, response.text
    assert 'error' in response.json()


def test_admin_and_device_permissions(client, admin_headers):
    assert client.get('/locations').status_code == 401
    loc = location(client, admin_headers)
    first = device(client, admin_headers, loc['id'])
    second = device(client, admin_headers, loc['id'])
    device_headers = {'Authorization': f'Bearer {first["token"]}'}
    assert client.get('/locations', headers=device_headers).status_code in (401, 403)
    assert client.post('/audio', headers=admin_headers, data={'metadata': json.dumps(metadata(first))},
                       files={'file': ('x.wav', pcm24_wav(16000, 1, .5), 'audio/wav')}).status_code in (401, 403)
    assert upload(client, first, meta=metadata(second)).status_code in (401, 403)
    result = accept(client, first)
    other = {'Authorization': f'Bearer {second["token"]}'}
    assert client.get(f'/audio/{result["id"]}', headers=other).status_code in (401, 403, 404)
    assert client.get(f'/audio/{result["id"]}/file', headers=other).status_code in (401, 403, 404)
    assert client.patch(f'/devices/{first["id"]}', headers=admin_headers, json={'enabled': False, 'expected_revision': 1}).status_code == 200
    assert upload(client, first, meta=metadata(first, sequence=1)).status_code in (401, 403)
    safe = client.get(f'/devices/{second["id"]}', headers=admin_headers).json()
    assert 'token' not in safe and 'credential_hash' not in safe


def test_original_bytes_checksum_timestamp_and_historical_snapshots(client, admin_headers, db, settings):
    from app.models import AudioChunk, Device
    loc = location(client, admin_headers)
    dev = device(client, admin_headers, loc['id'])
    original = pcm24_wav(16000, 1, .5)
    meta = metadata(dev, captured_at='2026-02-01T17:30:00+05:30')
    result = accept(client, dev, audio=original, meta=meta)
    saved = db.get(AudioChunk, uuid.UUID(result['id']))
    assert saved.device_id == uuid.UUID(dev['id']) and saved.location_id == uuid.UUID(loc['id'])
    assert saved.captured_at == CAPTURED
    assert saved.checksum == hashlib.sha256(original).hexdigest()
    assert (settings.audio_root / saved.file_path).read_bytes() == original
    assert '..' not in saved.file_path and 'untrusted' not in saved.file_path
    assert client.get(f'/audio/{result["id"]}/file', headers=admin_headers).content == original
    assert db.get(Device, uuid.UUID(dev['id'])).credential_hash != dev['token']
    assert db.get(Device, uuid.UUID(dev['id'])).last_contact_at is not None
    replacement = location(client, admin_headers, name='New assignment', latitude=22, longitude=88)
    assert client.patch(f'/devices/{dev["id"]}', headers=admin_headers, json={'location_id': replacement['id'], 'calibration': SYNTHETIC_CALIBRATION, 'expected_revision': 1}).status_code == 200
    assert client.patch(f'/locations/{loc["id"]}/threshold', headers=admin_headers,
                        json={'threshold_value': 50, 'threshold_type': 'spl_z_leq', 'interval_seconds': 2, 'expected_revision': 1}).status_code == 200
    db.expire_all()
    saved = db.get(AudioChunk, uuid.UUID(result['id']))
    assert saved.location_id == uuid.UUID(loc['id']) and saved.threshold_value == 75 and saved.interval_seconds == 1
    assert saved.calibration is None
    assert saved.location_snapshot['latitude'] == pytest.approx(12.9716)
    process(settings)
    measurement = client.get('/measurements', headers=admin_headers).json()['items'][0]
    assert measurement['calibration_status'] == 'calibration_required'
    assert client.get('/incidents', headers=admin_headers).json()['total'] == 0


@pytest.mark.parametrize('change', [
    {'captured_at': '2026-02-01T12:00:00'}, {'captured_at': 'not-a-date'},
    {'sequence': -1}, {'sequence': 1.25}, {'session_id': ''}, {'chunk_id': ''},
])
def test_invalid_upload_metadata(client, admin_headers, db, change):
    dev = device(client, admin_headers, location(client, admin_headers)['id'])
    response = upload(client, dev, meta=metadata(dev, **change))
    assert response.status_code == 422, response.text
    assert counts(db) == (0, 0, 0, 0)


def wav_with_format(channels=1, width=3, rate=16000, duration=1):
    out = io.BytesIO()
    with wave.open(out, 'wb') as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(width)
        wav.setframerate(rate)
        wav.writeframes(bytes(round(rate * duration) * width * channels))
    return out.getvalue()


@pytest.mark.parametrize('audio', [
    b'not a wav', wav_with_format(channels=2), wav_with_format(width=2),
    wav_with_format(rate=8000), wav_with_format(duration=0), wav_with_format(duration=2.1),
    b'x' * 200001, pcm24_wav(16000, 1, .5)[:-1],
])
def test_invalid_audio_format_duration_size_and_truncation(client, admin_headers, db, settings, audio):
    dev = device(client, admin_headers, location(client, admin_headers)['id'])
    response = upload(client, dev, audio=audio)
    assert response.status_code in (413, 422), response.text
    assert counts(db) == (0, 0, 0, 0)
    assert not list(settings.audio_root.rglob('*.wav'))
    assert not list(settings.audio_root.rglob('*.part'))


def test_identical_retry_conflicting_content_and_metadata(client, admin_headers, db):
    dev = device(client, admin_headers, location(client, admin_headers)['id'])
    first = accept(client, dev)
    retry = upload(client, dev)
    assert retry.status_code == 200 and retry.json()['duplicate'] is True
    assert retry.json()['id'] == first['id']
    assert upload(client, dev, audio=pcm24_wav(16000, 1, .1)).status_code == 409
    assert upload(client, dev, meta=metadata(dev, sequence=4, chunk_id='test-0')).status_code == 409
    assert counts(db) == (1, 1, 0, 0)


def test_concurrent_duplicate_uploads_create_one_job_and_original(client, admin_headers, db, settings):
    dev = device(client, admin_headers, location(client, admin_headers)['id'])
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda _: upload(client, dev), range(4)))
    assert sorted(response.status_code for response in responses) == [200, 200, 200, 202], [r.text for r in responses]
    assert len({response.json()['id'] for response in responses}) == 1
    assert counts(db) == (1, 1, 0, 0)
    assert len(list(settings.audio_root.rglob('*.wav'))) == 1


def test_uncalibrated_audio_records_digital_level_without_physical_incident(client, admin_headers, db, settings):
    dev = device(client, admin_headers, location(client, admin_headers, threshold_value=0)['id'])
    accepted = accept(client, dev)
    process(settings)
    assert counts(db) == (1, 1, 1, 0)
    value = client.get('/measurements', headers=admin_headers).json()['items'][0]
    assert value['digital_dbfs'] == pytest.approx(-9.0309, abs=.01)
    assert value['value_db'] is None and value['calibration_status'] == 'calibration_required'
    assert value['breach'] is False
    assert client.get(f'/audio/{accepted["id"]}', headers=admin_headers).json()['status'] == 'completed'


def test_synthetic_calibration_groups_consecutive_breaches_and_resolves(client, admin_headers, db, settings):
    dev = device(client, admin_headers, location(client, admin_headers)['id'], calibration=SYNTHETIC_CALIBRATION)
    for sequence, amplitude in enumerate([.5, .6, .001, .001, .001]):
        accept(client, dev, meta=metadata(dev, sequence=sequence), audio=pcm24_wav(16000, 1, amplitude))
        process(settings)
    measurements = client.get('/measurements', headers=admin_headers, params={'device_id': dev['id']}).json()
    assert measurements['total'] == 5
    assert sorted(item['breach'] for item in measurements['items']) == [False, False, False, True, True]
    assert all(item['calibration_version'] == SYNTHETIC_CALIBRATION['version'] for item in measurements['items'])
    incidents = client.get('/incidents', headers=admin_headers, params={'device_id': dev['id']}).json()
    assert incidents['total'] == 1
    incident = incidents['items'][0]
    assert incident['status'] == 'resolved' and incident['threshold_value'] == 75
    assert incident['peak_db'] == pytest.approx(92.55, abs=.02)
    assert datetime.fromisoformat(incident['started_at'].replace('Z', '+00:00')) == CAPTURED


@pytest.mark.parametrize('override', [{'sample_rate': 48000}, {'valid_until': '2026-01-15T00:00:00Z'}])
def test_inapplicable_calibration_cannot_raise_physical_alert(client, admin_headers, settings, override):
    calibration = {**SYNTHETIC_CALIBRATION, **override}
    dev = device(client, admin_headers, location(client, admin_headers, threshold_value=0)['id'], calibration=calibration)
    accept(client, dev)
    process(settings)
    assert client.get('/incidents', headers=admin_headers).json()['total'] == 0
    measurement = client.get('/measurements', headers=admin_headers).json()['items'][0]
    assert measurement['value_db'] is None and measurement['breach'] is False


def test_missing_file_retry_recovers_without_duplicate_measurements(client, admin_headers, db, settings):
    from app.models import AudioChunk, ProcessingJob
    dev = device(client, admin_headers, location(client, admin_headers)['id'])
    accepted = accept(client, dev)
    chunk = db.get(AudioChunk, uuid.UUID(accepted['id']))
    path = settings.audio_root / chunk.file_path
    original = path.read_bytes()
    path.unlink()
    process(settings)
    db.expire_all()
    job = db.execute(select(ProcessingJob)).scalar_one()
    assert job.status == 'retry' and job.attempts == 1 and job.last_error
    path.write_bytes(original)
    job.available_at = clock.now() - timedelta(seconds=1)
    db.commit()
    process(settings)
    db.expire_all()
    assert db.execute(select(ProcessingJob)).scalar_one().status == 'completed'
    assert counts(db) == (1, 1, 1, 0)
    from app.worker import run_once
    assert run_once(settings=settings) is False
    assert path.read_bytes() == original


def test_expired_lease_is_reclaimed_after_worker_restart(client, admin_headers, db, settings):
    from app.models import ProcessingJob
    from app.worker import claim_job
    dev = device(client, admin_headers, location(client, admin_headers)['id'])
    accept(client, dev)
    claim = claim_job(settings)
    assert claim is not None
    assert claim_job(settings) is None
    db.execute(text("UPDATE processing_jobs SET lease_until=:expired WHERE status='processing'"), {"expired": clock.now() - timedelta(seconds=1)})
    db.commit()
    process(settings)
    job = db.execute(select(ProcessingJob)).scalar_one()
    assert job.status == 'completed' and job.attempts == 2
    assert counts(db) == (1, 1, 1, 0)


def test_commit_failure_leaves_reconcilable_orphan_and_preserves_accepted_audio(client, admin_headers, db, settings, monkeypatch):
    from sqlalchemy.exc import SQLAlchemyError
    from sqlalchemy.orm import Session
    from app.reconcile import reconcile
    dev = device(client, admin_headers, location(client, admin_headers)['id'])
    accepted = accept(client, dev)
    def fail_commit(session):
        raise SQLAlchemyError('synthetic transaction failure')
    with monkeypatch.context() as patch:
        patch.setattr(Session, 'commit', fail_commit)
        failed = upload(client, dev, meta=metadata(dev, sequence=1))
    assert failed.status_code == 503, failed.text
    assert counts(db) == (1, 1, 0, 0)
    report = reconcile(settings)
    assert len(report['orphan_files']) == 1 and report['missing_committed_files'] == []
    assert report['deleted_count'] == 0
    cleaned = reconcile(settings, delete_orphans=True)
    assert cleaned['deleted_count'] == 1
    assert len(list(settings.audio_root.rglob('*.wav'))) == 1
    assert client.get(f'/audio/{accepted["id"]}/file', headers=admin_headers).content == pcm24_wav(16000, 1, .5)
    retried = upload(client, dev, meta=metadata(dev, sequence=1))
    assert retried.status_code == 202
    assert counts(db) == (2, 2, 0, 0)


def test_digital_threshold_is_labelled_and_does_not_claim_spl(client, admin_headers, settings):
    dev = device(client, admin_headers, location(client, admin_headers, threshold_type='dbfs_rms', threshold_value=-15)['id'])
    accept(client, dev)
    process(settings)
    measurement = client.get('/measurements', headers=admin_headers).json()['items'][0]
    assert measurement['measurement_type'] == 'dbfs_rms'
    assert measurement['value_db'] == pytest.approx(-9.0309, abs=.01)
    assert measurement['calibration_status'] == 'not_required'
    incident = client.get('/incidents', headers=admin_headers).json()['items'][0]
    assert incident['threshold_type'] == 'dbfs_rms'


def test_out_of_order_chunks_preserve_first_committed_live_incident(client, admin_headers, settings):
    dev = device(client, admin_headers, location(client, admin_headers)['id'], calibration=SYNTHETIC_CALIBRATION)
    for sequence in [2, 0, 1]:
        accept(client, dev, meta=metadata(dev, sequence=sequence))
        process(settings)
    incidents = client.get('/incidents', headers=admin_headers).json()
    assert incidents['total'] == 1
    assert datetime.fromisoformat(incidents['items'][0]['started_at'].replace('Z', '+00:00')) == CAPTURED + timedelta(seconds=2)


def test_replaced_lease_cannot_commit_stale_worker_result(client, admin_headers, db, settings):
    from app.worker import claim_job, process_claim
    dev = device(client, admin_headers, location(client, admin_headers)['id'])
    accept(client, dev)
    stale = claim_job(settings)
    db.execute(text("UPDATE processing_jobs SET lease_until=:expired WHERE status='processing'"), {"expired": clock.now() - timedelta(seconds=1)})
    db.commit()
    current = claim_job(settings)
    assert current and stale and current.lease_token != stale.lease_token
    process_claim(stale, settings)
    assert counts(db) == (1, 1, 0, 0)
    process_claim(current, settings)
    assert counts(db) == (1, 1, 1, 0)
