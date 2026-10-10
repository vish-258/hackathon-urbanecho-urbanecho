"""Access-scoped snapshots, durable SSE replay, and the browser demo."""
from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, StreamingResponse
from geoalchemy2 import Geometry
from sqlalchemy import cast, func, select
from starlette.concurrency import run_in_threadpool

from app import clock
from app.config import get_settings
from app.read_access import Read
from app.db import SessionLocal
from app.events import (cursor_error, cursor_for, encode_sse, lock_event_clock,
                        read_events, serialize_stream_reading, sweep_freshness, validate_cursor)
from app.models import Device, Incident, Location, Measurement, MeasurementEvaluation, StreamState

router = APIRouter()
STATIC = Path(__file__).with_name("static")


def validate_filters(session, location_id=None, device_id=None):
    if location_id is not None and session.get(Location, location_id) is None:
        raise HTTPException(404, "Location not found")
    if device_id is not None and session.get(Device, device_id) is None:
        raise HTTPException(404, "Device not found")


def serialize_incident(row):
    # Incidents contain only public domain data. Never serialize Device or AudioChunk.
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


def snapshot(location_id=None, device_id=None, limit=50, offset=0, settings=None, access=None):
    settings = settings or get_settings()
    with SessionLocal() as session:
        clock_row = lock_event_clock(session)
        validate_filters(session, location_id, device_id)
        sweep_freshness(session, settings)
        query = select(Location)
        if location_id is not None:
            query = query.where(Location.id == location_id)
        if device_id is not None:
            # Include a reassigned device's unresolved incident at its former location.
            related = select(StreamState.location_id).where(StreamState.device_id == device_id)
            current = select(Device.location_id).where(Device.id == device_id)
            query = query.where(Location.id.in_(related.union(current)))
        total = session.scalar(select(func.count()).select_from(query.subquery()))
        locations = session.scalars(query.order_by(Location.id).limit(limit).offset(offset)).all()
        ids = [location.id for location in locations]
        state_query = select(StreamState).where(StreamState.location_id.in_(ids))
        incident_query = select(Incident).where(Incident.location_id.in_(ids), Incident.status.in_(["active", "recovering"]))
        if device_id is not None:
            state_query = state_query.where(StreamState.device_id == device_id)
            incident_query = incident_query.where(Incident.device_id == device_id)
        states = session.scalars(state_query.order_by(StreamState.device_id, StreamState.id)).all()
        incidents = session.scalars(incident_query.order_by(Incident.started_at, Incident.id)).all()
        devices_query = select(Device).where(Device.location_id.in_(ids))
        if device_id is not None:
            devices_query = devices_query.where(Device.id == device_id)
        devices = session.scalars(devices_query).all()
        current_assignments = {device.id: device.current_assignment_id for device in devices}
        cutoff = clock.now() - timedelta(seconds=settings.data_stale_seconds)
        items = []
        for location in locations:
            longitude, latitude = session.execute(select(func.ST_X(cast(Location.point, Geometry)),
                                                           func.ST_Y(cast(Location.point, Geometry)))
                                                   .where(Location.id == location.id)).one()
            unresolved = [item for item in incidents if item.location_id == location.id]
            # Retire historical stream display once a rule changes, unless it still has
            # an unresolved incident. Device recovery and incidents remain independent.
            loc_states = [state for state in states if state.location_id == location.id]
            latest = {}
            unresolved_streams = {item.stream_id for item in unresolved}
            for state in loc_states:
                if state.assignment_id != current_assignments.get(state.device_id) and state.id not in unresolved_streams:
                    continue
                key = (state.device_id, state.assignment_id)
                previous = latest.get(key)
                if previous is None or (state.watermark or state.updated_at) > (previous.watermark or previous.updated_at):
                    latest[key] = state
            displayed = [state for state in loc_states if state in latest.values() or state.id in unresolved_streams]
            streams = []
            for state in displayed:
                measurement = session.get(Measurement, state.last_measurement_id) if state.last_measurement_id else None
                evaluation = session.scalar(select(MeasurementEvaluation).where(
                    MeasurementEvaluation.measurement_id == state.observed_measurement_id)) if state.observed_measurement_id else None
                fresh = state.watermark is not None and state.watermark >= cutoff and state.data_status == "fresh"
                streams.append({"id": state.id, "device_id": state.device_id, "assignment_id": state.assignment_id,
                                "noise_status": state.noise_status, "data_status": "fresh" if fresh else
                                "invalid" if state.watermark is not None and state.watermark >= cutoff and state.data_status == "invalid" else "stale",
                                **serialize_stream_reading(state, measurement),
                                "diagnostic": evaluation.diagnostic if evaluation else None,
                                "threshold_version_id": state.threshold_version_id, "recovery_streak": state.recovery_streak})
            statuses = {item.status for item in unresolved}
            noise = ("excessive" if "active" in statuses else "recovering" if "recovering" in statuses
                     else "normal" if any(item["noise_status"] == "normal" for item in streams) else "unknown")
            loc_devices = [device for device in devices if device.location_id == location.id]
            known_devices = {item["device_id"] for item in streams}
            missing = any(device.id not in known_devices or not device.enabled for device in loc_devices)
            freshness = ("unknown" if not streams else "stale" if missing or any(item["data_status"] == "stale" for item in streams)
                         else "invalid" if any(item["data_status"] == "invalid" for item in streams) else "fresh")
            items.append({"id": location.id, "name": location.name, "latitude": latitude, "longitude": longitude,
                          "noise_status": noise, "data_status": freshness, "streams": streams,
                          "devices": [{"id": device.id, "enabled": device.enabled,
                                       "assignment_id": device.current_assignment_id} for device in loc_devices],
                          "unresolved_incident_ids": [item.id for item in unresolved]})
        incident_items = [serialize_incident(item) for item in incidents]
        if access is not None:
            incident_items = access.incidents(session, incident_items)
        result = jsonable_encoder({"items": items, "total": total, "limit": limit, "offset": offset,
                                   "incidents": incident_items,
                                   "cursor": cursor_for(clock_row), "as_of": clock.now(),
                                   "data_stale_seconds": settings.data_stale_seconds})
        session.commit()
        return result


@router.get("/locations/status", tags=["live"])
def location_status(access: Read, location_id: UUID | None = None, device_id: UUID | None = None,
                    limit: Annotated[int, Query(ge=1, le=200)] = 50,
                    offset: Annotated[int, Query(ge=0)] = 0):
    location_id = access.location_filter(location_id)
    if access.read_only and device_id is not None:
        with SessionLocal() as session:
            access.require_device(session, device_id)
    return snapshot(location_id, device_id, limit, offset, access=access)


def preflight(value, location_id, device_id):
    with SessionLocal() as session:
        validate_filters(session, location_id, device_id)
        validate_cursor(session, value)


def read_scoped_events(value, location_id=None, device_id=None, settings=None, access=None):
    records = read_events(value, location_id, device_id, settings)
    if access is not None and access.read_only:
        with SessionLocal() as session:
            return access.events(session, records)
    return records


async def stream_events(request, value, location_id=None, device_id=None, settings=None, access=None):
    settings = settings or get_settings()
    loop = asyncio.get_running_loop()
    heartbeat_at = loop.time()
    yield ": connected\n\n"
    while not await request.is_disconnected():
        try:
            records = await run_in_threadpool(read_scoped_events, value, location_id, device_id, settings, access)
        except HTTPException as exc:
            # An already-running stream cannot change its HTTP status. End with an
            # explicit control event; reconnect gets the corresponding 409/410.
            yield f"event: stream.resync_required\ndata: {json.dumps(exc.detail)}\n\n"
            return
        for record in records:
            if await request.is_disconnected():
                return
            yield encode_sse(record)
            value = record["cursor"]
            heartbeat_at = loop.time()
        if loop.time() - heartbeat_at >= settings.sse_heartbeat_seconds:
            yield ": heartbeat\n\n"
            heartbeat_at = loop.time()
        if len(records) < settings.sse_batch_size:
            await asyncio.sleep(settings.sse_poll_seconds)


@router.get("/events/stream", tags=["live"])
async def event_stream(request: Request, access: Read, cursor: str | None = None,
                       last_event_id: Annotated[str | None, Header()] = None,
                       location_id: UUID | None = None, device_id: UUID | None = None):
    if cursor and last_event_id and cursor != last_event_id:
        raise cursor_error(400, "conflicting_cursors", "Use one consistent replay cursor")
    value = last_event_id or cursor
    location_id = access.location_filter(location_id)
    if access.read_only and device_id is not None:
        def check_device():
            with SessionLocal() as session:
                access.require_device(session, device_id)
        await run_in_threadpool(check_device)
    await run_in_threadpool(preflight, value, location_id, device_id)
    return StreamingResponse(stream_events(request, value, location_id, device_id, access=access), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache, no-store", "X-Accel-Buffering": "no",
                                      "X-Content-Type-Options": "nosniff"})


@router.get("/demo", include_in_schema=False)
def demo():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store",
        "Content-Security-Policy": "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; img-src 'self'; base-uri 'none'; frame-ancestors 'none'",
        "Referrer-Policy": "no-referrer"})


@router.get("/demo/{asset}", include_in_schema=False)
def demo_asset(asset: str):
    if asset not in {"demo.js", "state.mjs", "style.css"}:
        raise HTTPException(404, "Asset not found")
    return FileResponse(STATIC / asset, media_type="text/javascript" if asset.endswith((".js", ".mjs")) else "text/css")
