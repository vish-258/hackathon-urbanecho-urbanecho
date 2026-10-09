"""Durable events with transactionally allocated, commit-ordered replay positions.

Every writer of live or configuration state takes the singleton clock lock FIRST.
No database transaction, lock, or connection is held across a network yield.
"""
from __future__ import annotations

import json
from datetime import timedelta
from uuid import UUID, uuid4

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select

from app import clock
from app.config import get_settings
from app.db import SessionLocal
from app.models import (Device, DeviceAssignment, DurableEvent, EventClock, Incident,
                        Measurement, StreamState, ThresholdVersion)

EVENT_TYPES = frozenset({"incident.opened", "incident.updated", "incident.resolved",
                         "incident.closed", "location.status_changed"})
# Explicit projection prevents a model serialization from leaking tokens, paths,
# calibration documents, lease tokens, database settings, or future private fields.
PUBLIC_FIELDS = frozenset({
    "stream_id", "assignment_id", "device_enabled", "location_name", "latitude", "longitude",
    "measurement_id", "measurement_value", "value_db", "measurement_type",
    "weighting", "channel_policy", "interval_seconds", "threshold_value",
    "threshold_version_id", "threshold_revision", "measured_at", "received_at",
    "incident_status", "transition_reason", "noise_status", "data_status",
    "peak_db", "latest_db", "breach_count", "recovery_streak", "recovery_count",
    "started_at", "ended_at", "last_occurrence_at", "previous_incident_id",
    "last_measurement_at", "diagnostic", "quality_status", "calibration_status",
})


def lock_event_clock(session):
    """Lock and return the migration-created singleton; held until commit/rollback."""
    row = session.scalar(select(EventClock).where(EventClock.id == 1)
                         .with_for_update().execution_options(populate_existing=True))
    if row is None:
        raise RuntimeError("Event clock is missing; apply database migrations")
    return row


def cursor_for(clock_row, position=None):
    return f"{clock_row.epoch}:{clock_row.last_position if position is None else position}"


def emit_event(session, event_type, payload, incident_id=None, device_id=None, location_id=None):
    if event_type not in EVENT_TYPES:
        raise ValueError("Unsupported durable event type")
    if location_id is None:
        raise ValueError("Every event must identify a location")
    clock_row = lock_event_clock(session)
    clock_row.last_position += 1
    event_id, created_at = uuid4(), clock.now()
    public = {key: value for key, value in payload.items() if key in PUBLIC_FIELDS}
    public.update(event_id=event_id, event_type=event_type, incident_id=incident_id,
                  device_id=device_id, location_id=location_id, event_created_at=created_at)
    encoded = jsonable_encoder(public)
    # Reject nonfinite values before reaching JSONB or an SSE consumer.
    json.dumps(encoded, allow_nan=False)
    row = DurableEvent(id=event_id, pointer=clock_row.last_position, event_type=event_type,
                       incident_id=incident_id, device_id=device_id, location_id=location_id,
                       payload=encoded, created_at=created_at)
    session.add(row)
    session.flush()
    return row


def cursor_error(status, code, message):
    return HTTPException(status, {"code": code, "message": message,
                                  "resync_required": True, "snapshot_url": "/locations/status"})


def parse_cursor(value):
    if not value:
        raise cursor_error(409, "snapshot_required", "Load a location snapshot before subscribing")
    try:
        epoch, raw_position = value.split(":")
        if len(raw_position) > 19 or not raw_position.isascii() or not raw_position.isdigit():
            raise ValueError
        position = int(raw_position)
        if str(position) != raw_position or position > 9223372036854775807:
            raise ValueError
        return UUID(epoch), position
    except (ValueError, AttributeError):
        raise cursor_error(400, "malformed_cursor", "Cursor must be epoch UUID followed by a nonnegative position")


def validate_cursor(session, value, settings=None):
    """Validate globally before filtering; users are administrators with all-location access.

    Expiry refers to the oldest *missed* event. A newly loaded snapshot remains
    usable even when there have been no events for longer than the replay window.
    """
    settings = settings or get_settings()
    epoch, position = parse_cursor(value)
    row = session.get(EventClock, 1)
    if row is None or epoch != row.epoch:
        raise cursor_error(409, "unknown_cursor_epoch", "Cursor belongs to another event history")
    if position > row.last_position:
        raise cursor_error(409, "future_cursor", "Cursor is newer than committed event history")
    if position and session.scalar(select(DurableEvent.id).where(DurableEvent.pointer == position)) is None:
        raise cursor_error(409, "unknown_cursor", "Cursor is not in retained event history")
    missed = session.scalar(select(DurableEvent).where(DurableEvent.pointer > position)
                            .order_by(DurableEvent.pointer).limit(1))
    if missed and missed.created_at < clock.now() - timedelta(seconds=settings.event_replay_seconds):
        raise cursor_error(410, "expired_cursor", "Missed events exceed the replay window; reload the snapshot")
    return row, position


def read_events(value, location_id=None, device_id=None, settings=None):
    """Independent short polling transaction; returns materialized SSE records."""
    settings = settings or get_settings()
    with SessionLocal() as session:
        clock_row, position = validate_cursor(session, value, settings)
        # Bound to the committed clock observed during this poll.
        query = select(DurableEvent).where(DurableEvent.pointer > position,
                                          DurableEvent.pointer <= clock_row.last_position)
        if location_id is not None:
            query = query.where(DurableEvent.location_id == location_id)
        if device_id is not None:
            query = query.where(DurableEvent.device_id == device_id)
        rows = session.scalars(query.order_by(DurableEvent.pointer).limit(settings.sse_batch_size)).all()
        return [{"cursor": cursor_for(clock_row, row.pointer), "event_type": row.event_type,
                 "data": row.payload} for row in rows]


def encode_sse(record):
    return (f"id: {record['cursor']}\nevent: {record['event_type']}\n"
            f"data: {json.dumps(record['data'], separators=(',', ':'), allow_nan=False)}\n\n")


def sweep_freshness(session, settings=None):
    """Persist fresh→stale transitions without altering noise or closing incidents."""
    settings = settings or get_settings()
    lock_event_clock(session)
    cutoff = clock.now() - timedelta(seconds=settings.data_stale_seconds)
    # Measurement time, not receipt time: a delayed upload cannot disguise old data.
    states = session.scalars(select(StreamState).where(StreamState.data_status.in_(["fresh", "invalid"]),
                                                       StreamState.watermark < cutoff)).all()
    for state in states:
        state.data_status = "stale"
        state.updated_at = clock.now()
        assignment = session.get(DeviceAssignment, state.assignment_id)
        snapshot = assignment.location_snapshot if assignment else {}
        rule = session.get(ThresholdVersion, state.threshold_version_id) if state.threshold_version_id else None
        measurement = session.get(Measurement, state.last_measurement_id) if state.last_measurement_id else None
        incident = session.scalar(select(Incident).where(Incident.stream_id == state.id,
                                                          Incident.status.in_(["active", "recovering"])))
        device = session.get(Device, state.device_id)
        newest = session.scalar(select(StreamState.id).where(StreamState.device_id == state.device_id,
                         StreamState.assignment_id == state.assignment_id)
                         .order_by(StreamState.watermark.desc().nullslast(), StreamState.updated_at.desc()).limit(1))
        if incident is None and (device is None or device.current_assignment_id != state.assignment_id or newest != state.id):
            # Historical/retired streams must not reappear on the live map merely
            # because their last measurement eventually becomes old.
            continue
        payload = {
            "stream_id": state.id, "assignment_id": state.assignment_id,
            "location_name": snapshot.get("name"), "latitude": snapshot.get("latitude"),
            "longitude": snapshot.get("longitude"), "measurement_value": state.last_value,
            "measurement_type": measurement.measurement_type if measurement else None,
            "interval_seconds": measurement.interval_seconds if measurement else None,
            "threshold_value": rule.threshold_value if rule else None,
            "threshold_version_id": state.threshold_version_id,
            "measured_at": state.watermark, "noise_status": state.noise_status,
            "data_status": "stale", "incident_status": incident.status if incident else None,
            "transition_reason": "data_stale", "recovery_streak": state.recovery_streak,
        }
        emit_event(session, "location.status_changed", payload,
                   incident_id=incident.id if incident else None, device_id=state.device_id,
                   location_id=state.location_id)
    return len(states)
