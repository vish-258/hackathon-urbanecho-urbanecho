"""Immutable rule history and explicit device assignment periods."""
from datetime import datetime, timezone

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from geoalchemy2 import Geometry
from sqlalchemy import cast, func, or_, select

from app import clock
from app.config import get_settings
from app.events import lock_event_clock
from app.models import DeviceAssignment, Location, ThresholdVersion

BASELINE = datetime(1970, 1, 1, tzinfo=timezone.utc)


def threshold_at(session, location_id, measured_at):
    return session.scalar(select(ThresholdVersion).where(
        ThresholdVersion.location_id == location_id,
        ThresholdVersion.effective_at <= measured_at,
    ).order_by(ThresholdVersion.effective_at.desc(), ThresholdVersion.revision.desc()).limit(1))


def latest_threshold(session, location_id):
    return session.scalar(select(ThresholdVersion).where(
        ThresholdVersion.location_id == location_id,
    ).order_by(ThresholdVersion.revision.desc()).limit(1))


def rule_values(payload):
    values = {key: getattr(payload, key) for key in (
        'threshold_value', 'threshold_type', 'weighting', 'channel_policy',
        'interval_seconds', 'recovery_count')}
    if 'recovery_count' not in payload.model_fields_set:
        values['recovery_count'] = get_settings().recovery_count
    return values


def create_initial_threshold(session, location, payload):
    lock_event_clock(session)
    rule = ThresholdVersion(location_id=location.id, revision=1,
                            effective_at=BASELINE, created_at=clock.now(), **rule_values(payload))
    session.add(rule)
    session.flush()
    return rule


def append_threshold(session, location_id, payload):
    lock_event_clock(session)
    location = session.scalar(select(Location).where(Location.id == location_id).with_for_update())
    if location is None:
        raise HTTPException(404, 'Location not found')
    latest = latest_threshold(session, location_id)
    revision = latest.revision if latest else 0
    if payload.expected_revision != revision:
        raise HTTPException(409, {'message': 'Threshold revision changed; reload before editing', 'current_revision': revision})
    now = clock.now()
    effective = payload.effective_at or now
    if effective < now:
        raise HTTPException(422, 'Threshold edits cannot be retroactive')
    if latest is not None and effective <= latest.effective_at:
        raise HTTPException(409, 'Effective time must follow the latest configured rule')
    rule = ThresholdVersion(location_id=location_id, revision=revision + 1,
                            effective_at=effective, created_at=now, **rule_values(payload))
    session.add(rule)
    # These compatibility columns describe the latest configured rule. Live
    # processing always selects immutable versions by measurement timestamp.
    location.threshold_value = rule.threshold_value
    location.threshold_type = rule.threshold_type
    location.interval_seconds = rule.interval_seconds
    session.flush()
    return rule


def location_snapshot(session, location):
    longitude, latitude = session.execute(select(
        func.ST_X(cast(Location.point, Geometry)), func.ST_Y(cast(Location.point, Geometry)),
    ).where(Location.id == location.id)).one()
    return jsonable_encoder({'id': location.id, 'name': location.name, 'latitude': latitude,
                            'longitude': longitude, 'timezone': location.timezone})


def assignment_at(session, device_id, captured_at):
    return session.scalar(select(DeviceAssignment).where(
        DeviceAssignment.device_id == device_id,
        DeviceAssignment.effective_at <= captured_at,
        or_(DeviceAssignment.ended_at.is_(None), DeviceAssignment.ended_at > captured_at),
    ).order_by(DeviceAssignment.effective_at.desc(), DeviceAssignment.id).limit(1))


def create_assignment(session, device, location, *, initial=False):
    lock_event_clock(session)
    effective = BASELINE if initial else clock.now()
    if not initial and device.current_assignment_id:
        previous = session.get(DeviceAssignment, device.current_assignment_id)
        if previous is not None:
            if effective <= previous.effective_at:
                raise HTTPException(409, 'Assignment time must follow the current assignment')
            previous.ended_at = effective
            session.flush()  # Release the partial unique current-assignment slot.
    row = DeviceAssignment(device_id=device.id, location_id=location.id,
                           location_snapshot=location_snapshot(session, location),
                           effective_at=effective, created_at=clock.now())
    session.add(row)
    session.flush()
    device.location_id = location.id
    device.current_assignment_id = row.id
    if not initial:
        device.assignment_revision += 1
    return row
