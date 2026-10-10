"""Forward migration with legacy data against a separate disposable PostgreSQL DB."""
import uuid
import os
import hashlib
from datetime import timedelta

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from sqlalchemy.exc import DBAPIError

pytestmark = pytest.mark.integration


def test_upgrade_preserves_original_snapshots_and_marks_legacy_history(privileged, monkeypatch, settings, fake_clock):
    # Never migrate backward or clear the running app/test fixture database.
    database = f"test_noise_upgrade_{uuid.uuid4().hex}"
    admin = create_engine(privileged.url.set(database='postgres'), isolation_level='AUTOCOMMIT')
    migration_engine = None
    app_engine = None
    try:
        with admin.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{database}"'))
        migration_url = privileged.url.set(database=database)
        monkeypatch.setenv('MIGRATION_DATABASE_URL', migration_url.render_as_string(hide_password=False))
        config = Config('alembic.ini')
        command.upgrade(config, '0001_initial')
        migration_engine = create_engine(migration_url)
        location_id, previous_location_id, device_id, chunk_id, measurement_id, incident_id = [uuid.uuid4() for _ in range(6)]
        pending_id, job_id = uuid.uuid4(), uuid.uuid4()
        from scripts.simulate import pcm24_wav
        pending_audio = pcm24_wav(16000, 1, .5)
        (settings.audio_root / 'legacy-pending.wav').write_bytes(pending_audio)
        values = dict(pending=pending_id,job=job_id,now=fake_clock.now(),capture=fake_clock.now()-timedelta(seconds=1),
                      checksum=hashlib.sha256(pending_audio).hexdigest(),loc=location_id, previous=previous_location_id, dev=device_id, chunk=chunk_id,
                      measurement=measurement_id, incident=incident_id)
        with migration_engine.begin() as connection:
            connection.execute(text("""INSERT INTO locations(id,name,point,timezone,threshold_value,threshold_type,interval_seconds)
              VALUES(:loc,'Current place',ST_SetSRID(ST_MakePoint(77,12),4326)::geography,'UTC',-15,'dbfs_rms',1),
              (:previous,'Previous place',ST_SetSRID(ST_MakePoint(88,22),4326)::geography,'UTC',65,'spl_z_leq',1)"""), values)
            connection.execute(text("""INSERT INTO devices(id,location_id,microphone_model,credential_hash)
              VALUES(:dev,:loc,'INMP441','not-a-real-credential')"""), values)
            connection.execute(text("""INSERT INTO audio_chunks(id,device_id,location_id,location_snapshot,device_chunk_id,session_id,
              sequence,captured_at,received_at,duration_seconds,sample_rate,audio_format,checksum,file_path,status,
              threshold_value,threshold_type,interval_seconds)
              VALUES(:chunk,:dev,:previous,jsonb_build_object('name','Historical coordinates','latitude',22,'longitude',88),
              'old-original','old-session',0,'2026-01-01T01:00:00Z','2026-01-01T01:01:00Z',1,16000,'wav_pcm_s24le_mono',
              repeat('a',64),'original/unchanged.wav','completed',60,'spl_z_leq',1)"""), values)
            connection.execute(text("""INSERT INTO measurements(id,audio_chunk_id,measured_at,interval_seconds,value_db,digital_dbfs,
              measurement_type,calibration_status,calibration_version,processing_version,breach)
              VALUES(:measurement,:chunk,'2026-01-01T01:00:00Z',1,80,-20,'spl_z_leq','calibrated','original-calibration','v1',true)"""), values)
            connection.execute(text("""INSERT INTO incidents(id,device_id,location_id,started_at,threshold_value,threshold_type,peak_db,status)
              VALUES(:incident,:dev,:previous,'2026-01-01T01:00:00Z',60,'spl_z_leq',80,'active')"""), values)
            connection.execute(text("""INSERT INTO audio_chunks(id,device_id,location_id,location_snapshot,device_chunk_id,session_id,
              sequence,captured_at,received_at,duration_seconds,sample_rate,audio_format,checksum,file_path,status,
              threshold_value,threshold_type,interval_seconds)
              VALUES(:pending,:dev,:loc,jsonb_build_object('name','Current place','latitude',12,'longitude',77),
              'pending-original','legacy-session',1,:capture,:now,1,16000,'wav_pcm_s24le_mono',
              :checksum,'legacy-pending.wav','pending',-15,'dbfs_rms',1)"""), values)
            connection.execute(text("""INSERT INTO processing_jobs(id,audio_chunk_id,status,available_at,created_at,updated_at)
              VALUES(:job,:pending,'pending',:now,:now,:now)"""), values)
        command.upgrade(config, 'head')
        with migration_engine.connect() as connection:
            chunk = connection.execute(text('SELECT * FROM audio_chunks WHERE id=:chunk'), values).mappings().one()
            assert chunk['legacy_ingestion'] is True
            assert chunk['capture_interval_ms'] is None
            assert chunk['file_path'] == 'original/unchanged.wav' and chunk['checksum'] == 'a' * 64
            assert chunk['threshold_value'] == 60 and chunk['location_id'] == previous_location_id
            assert chunk['location_snapshot']['longitude'] == 88
            assignment = connection.execute(text('SELECT * FROM device_assignments WHERE id=:id'), {'id': chunk['assignment_id']}).mappings().one()
            assert assignment['location_id'] == previous_location_id and assignment['ended_at'] is not None
            current = connection.execute(text('SELECT current_assignment_id FROM devices WHERE id=:dev'), values).scalar_one()
            assert current != chunk['assignment_id']
            measurement = connection.execute(text('SELECT * FROM measurements WHERE id=:measurement'), values).mappings().one()
            assert measurement['value_db'] == 80 and measurement['breach'] is True
            assert measurement['result_version'] == 'legacy-v1' and measurement['is_reprocessing']
            assert measurement['received_at'] == chunk['received_at']
            evaluation = connection.execute(text('SELECT * FROM measurement_evaluations')).mappings().one()
            assert evaluation['status'] == 'legacy_historical' and evaluation['live'] is False
            # A newer configured 65 dB rule must not be attributed to a legacy
            # 60 dB evaluation when the actual revision history never existed.
            assert evaluation['threshold_version_id'] is None
            incident = connection.execute(text('SELECT * FROM incidents')).mappings().one()
            assert incident['id'] == incident_id and incident['peak_db'] == 80
            assert incident['threshold_value'] == 60 and incident['location_snapshot']['latitude'] == 22
            assert incident['status'] == 'closed' and incident['closed_reason'] == 'legacy_migration'
            assert connection.execute(text('SELECT count(*) FROM durable_events')).scalar_one() == 0
            assert connection.execute(text('SELECT count(*) FROM stream_states')).scalar_one() == 0
            with pytest.raises(DBAPIError, match='append-only'):
                connection.execute(text('UPDATE threshold_versions SET threshold_value=0'))
            connection.rollback()
        app_engine = create_engine(make_url(os.environ['TEST_DATABASE_URL']).set(database=database))
        import app.worker as worker
        monkeypatch.setattr(worker, 'SessionLocal', lambda: Session(bind=app_engine, expire_on_commit=False))
        claim = worker.claim_job(settings)
        assert claim is not None and claim.audio_chunk_id == pending_id
        worker.process_claim(claim, settings)
        with migration_engine.connect() as connection:
            pending_result = connection.execute(text("""SELECT m.value_db,m.is_reprocessing,m.result_version,
              e.status,e.diagnostic,e.live,e.threshold_version_id FROM measurements m
              JOIN measurement_evaluations e ON e.measurement_id=m.id WHERE m.audio_chunk_id=:pending"""), values).mappings().one()
            assert pending_result['value_db'] > -15
            assert pending_result['is_reprocessing'] and pending_result['result_version'] == 'legacy-processed-v2'
            assert pending_result['status'] == 'legacy_historical' and pending_result['live'] is False
            assert pending_result['diagnostic'] == 'legacy_snapshot_preserved'
            assert pending_result['threshold_version_id'] is None
            assert connection.execute(text('SELECT count(*) FROM durable_events')).scalar_one() == 0
            assert connection.execute(text('SELECT count(*) FROM incidents')).scalar_one() == 1
            assert connection.execute(text('SELECT status FROM processing_jobs WHERE id=:job'), values).scalar_one() == 'completed'
            assert (settings.audio_root / 'legacy-pending.wav').read_bytes() == pending_audio
    finally:
        if app_engine is not None:
            app_engine.dispose()
        if migration_engine is not None:
            migration_engine.dispose()
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)'))
        admin.dispose()
