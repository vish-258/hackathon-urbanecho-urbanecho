"""Synthetic measurement fixtures. Calibration here is test-only, never hardware evidence."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import uuid

from geoalchemy2.elements import WKTElement
from sqlalchemy import select

BASE = datetime(2026, 10, 9, 10, 0, tzinfo=timezone.utc)
NOW = BASE + timedelta(seconds=90)
SYNTHETIC_CALIBRATION = {'method': 'spl_z_leq', 'version': 'synthetic-test-only-v1',
    'offset_db': 100.0, 'sample_rate': 16000,
    'calibrated_at': '2026-01-01T00:00:00Z', 'valid_until': '2027-01-01T00:00:00Z'}


@dataclass
class SyntheticStream:
    device_id: uuid.UUID
    location_id: uuid.UUID
    assignment_id: uuid.UUID
    rule_id: uuid.UUID
    method: str = 'spl_z_leq'


def create_stream(threshold=60.0, *, method='spl_z_leq', recovery=3, location_id=None):
    from app.db import SessionLocal
    from app.events import lock_event_clock
    from app.models import Location, Device, DeviceAssignment, ThresholdVersion
    with SessionLocal() as session, session.begin():
        lock_event_clock(session)
        location = session.get(Location, location_id) if location_id else None
        if location is None:
            location = Location(name='Synthetic calibration fixture', point=WKTElement('POINT(77.5 12.5)', srid=4326),
                                timezone='UTC', threshold_value=threshold, threshold_type=method, interval_seconds=1)
            session.add(location)
            session.flush()
            rule = ThresholdVersion(location_id=location.id, revision=1,
                effective_at=datetime(1970, 1, 1, tzinfo=timezone.utc), threshold_value=threshold,
                threshold_type=method, weighting='Z' if method == 'spl_z_leq' else 'none',
                channel_policy='mono', interval_seconds=1, recovery_count=recovery, created_at=BASE)
            session.add(rule)
        else:
            rule = session.scalar(select(ThresholdVersion).where(ThresholdVersion.location_id == location.id)
                                  .order_by(ThresholdVersion.revision.desc()).limit(1))
        device = Device(location_id=location.id, credential_hash='synthetic-test-not-a-credential',
                        microphone_model='synthetic-test-only', calibration=dict(SYNTHETIC_CALIBRATION),
                        enabled=True, assignment_revision=1)
        session.add(device)
        session.flush()
        assignment = DeviceAssignment(device_id=device.id, location_id=location.id,
            location_snapshot={'id': str(location.id), 'name': location.name, 'latitude': 12.5,
                               'longitude': 77.5, 'timezone': 'UTC'},
            effective_at=datetime(1970, 1, 1, tzinfo=timezone.utc), created_at=BASE)
        session.add(assignment)
        session.flush()
        device.current_assignment_id = assignment.id
        return SyntheticStream(device.id, location.id, assignment.id, rule.id, method)


def put_reading(stream, sequence, value, *, captured_at=None, session_id='synthetic-session',
                result=None, chunk_fields=None, result_version='initial', reprocessing=False,
                evaluate=True, source_id=None):
    from app.db import SessionLocal
    from app.events import lock_event_clock
    from app.evaluation import evaluate_measurement, persist_measurement
    from app.models import AudioChunk, DeviceAssignment, ThresholdVersion
    with SessionLocal() as session, session.begin():
        lock_event_clock(session)
        chunk = session.get(AudioChunk, source_id) if source_id else None
        if chunk is None:
            assignment = session.get(DeviceAssignment, stream.assignment_id)
            rule = session.get(ThresholdVersion, stream.rule_id)
            chunk = AudioChunk(device_id=stream.device_id, location_id=stream.location_id,
                assignment_id=stream.assignment_id, location_snapshot=dict(assignment.location_snapshot),
                device_chunk_id=f'synthetic-{uuid.uuid4()}', session_id=session_id, sequence=sequence,
                captured_at=captured_at or BASE + timedelta(seconds=sequence), received_at=NOW,
                duration_seconds=1.0, sample_rate=16000, audio_format='pcm_s24le', checksum='0' * 64,
                file_path=f'synthetic-fixtures/{uuid.uuid4()}.wav', status='completed',
                threshold_value=rule.threshold_value, threshold_type=rule.threshold_type,
                interval_seconds=rule.interval_seconds, calibration=dict(SYNTHETIC_CALIBRATION))
            for key, item in (chunk_fields or {}).items():
                setattr(chunk, key, item)
            session.add(chunk)
            session.flush()
        values = dict(value_db=value, digital_dbfs=-20.0, measurement_type=stream.method,
            calibration_status='calibrated' if stream.method == 'spl_z_leq' else 'not_required',
            calibration_version=SYNTHETIC_CALIBRATION['version'] if stream.method == 'spl_z_leq' else None,
            processing_version='synthetic-measurement-fixture-v1', breach=False,
            weighting='Z' if stream.method == 'spl_z_leq' else 'none', channel_policy='mono', quality_status='good')
        values.update(result or {})
        measurement = persist_measurement(session, chunk, values, result_version=result_version,
                                          is_reprocessing=reprocessing)
        evaluation = evaluate_measurement(session, measurement) if evaluate else None
        return SimpleNamespace(measurement_id=measurement.id, audio_id=chunk.id,
            evaluation_id=evaluation.id if evaluation else None, status=evaluation.status if evaluation else None,
            diagnostic=evaluation.diagnostic if evaluation else None, breach=evaluation.breach if evaluation else None,
            live=evaluation.live if evaluation else None)


def records(model):
    from app.db import SessionLocal
    with SessionLocal() as session:
        return session.scalars(select(model)).all()


def add_rule(stream, *, at, threshold=60.0, method=None, recovery=3):
    from app.db import SessionLocal
    from app.events import lock_event_clock
    from app.models import ThresholdVersion
    with SessionLocal() as session, session.begin():
        lock_event_clock(session)
        latest = session.scalar(select(ThresholdVersion).where(ThresholdVersion.location_id == stream.location_id)
                                .order_by(ThresholdVersion.revision.desc()).limit(1))
        method = method or latest.threshold_type
        rule = ThresholdVersion(location_id=stream.location_id, revision=latest.revision + 1,
            effective_at=at, threshold_value=threshold, threshold_type=method,
            weighting='Z' if method == 'spl_z_leq' else 'none', channel_policy='mono', interval_seconds=1,
            recovery_count=recovery, created_at=BASE)
        session.add(rule)
        session.flush()
        return rule.id
