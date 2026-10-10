import hashlib
import json
import logging
import os
import secrets
import tempfile
from contextlib import asynccontextmanager
from typing import Annotated, Literal
from uuid import UUID

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials
from geoalchemy2 import Geography, Geometry
from pydantic import AwareDatetime, ValidationError
from sqlalchemy import String, cast, func, or_, select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.auth import authenticate_device, bearer, is_admin, require_admin, token_hash, token_value
from app.config import get_settings
from app.db import get_db, wait_for_database
from app.models import AudioChunk, Device, Incident, Location, Measurement, ProcessingJob, MeasurementEvaluation, ThresholdVersion, DurableEvent, EventClock
from app.schemas import DeviceCreate, DeviceUpdate, LocationCreate, LocationUpdate, ThresholdUpdate, UploadMetadata
from app.ingestion import ingest_recording
from app.pcm_api import router as pcm_router
from app import clock
from app.configuration import (append_threshold, create_assignment, create_initial_threshold,
                               latest_threshold, location_snapshot, threshold_at)
from app.events import emit_event, lock_event_clock
from app.live_api import router as live_router
from app.application_api import router as application_router
from app.daily_api import router as daily_router
from app.daily import source_kind
from app.classification_api import router as classification_router
from app.incident_analysis_api import router as incident_analysis_router
from app.recording_groups import router as recording_groups_router
from app.classification_jobs import classifications_for
from app.storage import resolve_audio_path

log = logging.getLogger(__name__)
DB = Annotated[Session, Depends(get_db)]
Admin = Annotated[None, Depends(require_admin)]
Token = Annotated[str, Depends(token_value)]
Limit = Annotated[int, Query(ge=1, le=200)]
Offset = Annotated[int, Query(ge=0)]
Latitude = Annotated[float, Query(ge=-90, le=90, allow_inf_nan=False)]
Longitude = Annotated[float, Query(ge=-180, le=180, allow_inf_nan=False)]


class BodyTooLarge(Exception):
    pass


class UploadSizeLimit:
    """Bound streamed multipart requests too, before the parser spools to disk."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        maximum = get_settings().max_upload_bytes + 65536
        headers = dict(scope.get("headers", []))
        length = headers.get(b"content-length")
        try:
            if length is not None and int(length) > maximum:
                raise BodyTooLarge
        except (ValueError, BodyTooLarge):
            return await JSONResponse({"error": {"code": "upload_too_large", "message": "Request exceeds upload limit"}}, status_code=413)(scope, receive, send)
        seen = 0
        async def limited_receive():
            nonlocal seen
            message = await receive()
            seen += len(message.get("body", b""))
            if seen > maximum:
                raise HTTPException(413, "Request exceeds upload limit")
            return message
        try:
            await self.app(scope, limited_receive, send)
        except BodyTooLarge:
            await JSONResponse({"error": {"code": "upload_too_large", "message": "Request exceeds upload limit"}}, status_code=413)(scope, receive, send)


def point(longitude, latitude):
    return cast(func.ST_SetSRID(func.ST_MakePoint(longitude, latitude), 4326), Geography("POINT", srid=4326))


def model_dict(row, exclude=()):
    return {col.name: getattr(row, col.name) for col in row.__table__.columns if col.name not in exclude}


def location_dict(db, row):
    lon, lat = db.execute(select(func.ST_X(cast(Location.point, Geometry)), func.ST_Y(cast(Location.point, Geometry))).where(Location.id == row.id)).one()
    metadata = {"name": row.name, "latitude": lat, "longitude": lon, "timezone": row.timezone}
    version = hashlib.sha256(json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {**model_dict(row, ("point",)), **metadata, "configuration_version": version}


def require_row(db, model, identifier):
    row = db.get(model, identifier)
    if row is None:
        raise HTTPException(404, f"{model.__name__} not found")
    return row


def device_configuration_event(db, device, location, reason):
    snapshot = location_snapshot(db, location)
    emit_event(db, "location.status_changed", {
        "assignment_id": device.current_assignment_id, "device_enabled": device.enabled,
        "location_name": snapshot["name"], "latitude": snapshot["latitude"],
        "longitude": snapshot["longitude"], "transition_reason": reason,
    }, device_id=device.id, location_id=location.id)


def page(db, query, limit, offset, serializer=model_dict, *, serialize_many=None):
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.scalars(query.limit(limit).offset(offset)).all()
    items = serialize_many(rows) if serialize_many else [serializer(row) for row in rows]
    return {"items": items, "total": total, "limit": limit, "offset": offset}


def measurement_dicts(db, rows):
    """Fetch related history once per batch, retaining each reading's saved rule."""
    if not rows:
        return []
    chunks = {row.id: row for row in db.scalars(select(AudioChunk).where(
        AudioChunk.id.in_({item.audio_chunk_id for item in rows})))}
    classifications = classifications_for(db, chunks)
    evaluations = {row.measurement_id: row for row in db.scalars(select(MeasurementEvaluation).where(
        MeasurementEvaluation.measurement_id.in_([item.id for item in rows])))}
    rule_ids = {item.threshold_version_id for item in evaluations.values() if item.threshold_version_id}
    rules = {row.id: row for row in db.scalars(select(ThresholdVersion).where(
        ThresholdVersion.id.in_(rule_ids)))} if rule_ids else {}
    items = []
    for row in rows:
        chunk = chunks[row.audio_chunk_id]
        evaluation = evaluations.get(row.id)
        rule = rules.get(evaluation.threshold_version_id) if evaluation else None
        items.append({**model_dict(row, ("content_hash",)),
            "device_id": chunk.device_id, "location_id": chunk.location_id,
            "location_snapshot": chunk.location_snapshot, "captured_at": chunk.captured_at,
            "duration_seconds": chunk.duration_seconds,
            "capture_interval_ms": chunk.capture_interval_ms,
            "source_kind": source_kind(chunk, row),
            "classification": classifications[chunk.id],
            "threshold_value": rule.threshold_value if rule else chunk.threshold_value,
            "threshold_type": rule.threshold_type if rule else chunk.threshold_type,
            "threshold_version": model_dict(rule) if rule else None,
            "evaluation": model_dict(evaluation, ("content_hash",)) if evaluation else None})
    return items


def incident_dicts(db, rows):
    """Readable registered codes accompany UUIDs without fetching credentials."""
    if not rows:
        return []
    codes = dict(db.execute(select(Device.id, Device.external_id).where(
        Device.id.in_({row.device_id for row in rows}))).all())
    return [{**model_dict(row), "device_external_id": codes.get(row.device_id)} for row in rows]


def recording_dicts(db, rows):
    """List original captures independently of whether usable levels exist yet."""
    if not rows:
        return []
    codes = dict(db.execute(select(Device.id, Device.external_id).where(
        Device.id.in_({row.device_id for row in rows}))).all())
    classifications = classifications_for(db, [row.id for row in rows])
    fields = ("id", "device_id", "location_id", "location_snapshot", "captured_at",
              "received_at", "duration_seconds", "capture_interval_ms", "sample_rate", "audio_format",
              "status", "threshold_type")
    return [{**{field: getattr(row, field) for field in fields},
             "device_external_id": codes.get(row.device_id), "source_kind": source_kind(row),
             "classification": classifications[row.id],
             # Presence is capture-time metadata, not a claim of acoustic accuracy.
             "calibration_present": row.calibration is not None,
             "calibration_version": (row.calibration or {}).get("version")}
            for row in rows]


@asynccontextmanager
async def lifespan(app):
    wait_for_database()
    settings = get_settings()
    settings.audio_root.mkdir(parents=True, exist_ok=True)
    yield


def create_app():
    app = FastAPI(title="Environmental Noise Monitor", version="1.0.0", lifespan=lifespan,
                  description="Durable PCM24 WAV and raw PCM16 compatibility uploads, spatial location queries, and versioned digital/calibrated processing. Admin and device endpoints use separate bearer credentials.")
    app.state.local_audio_playback = True
    app.add_middleware(UploadSizeLimit)
    # Register /locations/status before the dynamic location UUID route.
    app.include_router(live_router)
    app.include_router(application_router)
    app.include_router(daily_router)
    app.include_router(classification_router)
    app.include_router(incident_analysis_router)
    app.include_router(pcm_router)
    # Preserve the legacy /recordings/{key}.wav route before the group UUID route.
    app.include_router(recording_groups_router)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request, exc):
        return JSONResponse({"error": {"code": f"http_{exc.status_code}", "message": exc.detail}}, status_code=exc.status_code, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def input_error(request, exc):
        # Omit raw input so credentials and file payloads cannot enter errors.
        details = [{"location": list(x["loc"]), "message": x["msg"], "type": x["type"]} for x in exc.errors()]
        return JSONResponse({"error": {"code": "validation_error", "message": "Invalid request", "details": details}}, status_code=422)

    @app.exception_handler(IntegrityError)
    async def conflict_error(request, exc):
        return JSONResponse({"error": {"code": "conflict", "message": "Request conflicts with an existing record or database constraint"}}, status_code=409)

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request, exc):
        log.warning("Database request failed: %s", type(exc).__name__)
        return JSONResponse({"error": {"code": "database_unavailable", "message": "Database temporarily unavailable; retry the same request ID"}}, status_code=503, headers={"Retry-After": "2"})

    @app.exception_handler(OSError)
    async def storage_error(request, exc):
        log.warning("Storage request failed: %s", type(exc).__name__)
        return JSONResponse({"error": {"code": "storage_unavailable", "message": "Audio storage temporarily unavailable"}}, status_code=503)

    @app.exception_handler(Exception)
    async def unexpected_error(request, exc):
        log.error("Unexpected request failure: %s", type(exc).__name__)
        return JSONResponse({"error": {"code": "internal_error", "message": "An unexpected server error occurred"}}, status_code=500)

    @app.get("/health/live", tags=["health"])
    def live():
        return {"status": "alive"}

    @app.get("/health/ready", tags=["health"])
    def ready(db: DB):
        version = db.scalar(text("SELECT PostGIS_Full_Version()"))
        revision = db.scalar(text("SELECT version_num FROM alembic_version"))
        db.scalar(select(Location.id).limit(1))
        db.scalar(select(EventClock.epoch).where(EventClock.id == 1))
        root = get_settings().audio_root
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=root) as probe:
            probe.write(b"ready")
            probe.flush()
            os.fsync(probe.fileno())
        return {"status": "ready", "postgis": version, "migration": revision}

    @app.post("/locations", status_code=201, tags=["locations"])
    def create_location(payload: LocationCreate, db: DB, admin: Admin):
        lock_event_clock(db)
        values = payload.model_dump(exclude={"latitude", "longitude", "weighting", "channel_policy", "recovery_count"})
        row = Location(**values, point=point(payload.longitude, payload.latitude))
        db.add(row)
        db.flush()
        rule = create_initial_threshold(db, row, payload)
        emit_event(db, "location.status_changed", {"location_name": row.name, "latitude": payload.latitude,
                   "longitude": payload.longitude, "noise_status": "unknown", "data_status": "unknown",
                   "threshold_value": rule.threshold_value, "threshold_version_id": rule.id,
                   "threshold_revision": rule.revision, "transition_reason": "location_registered"}, location_id=row.id)
        db.commit()
        return location_dict(db, row)

    @app.get("/locations", tags=["locations"])
    def locations(db: DB, admin: Admin, limit: Limit = 50, offset: Offset = 0):
        return page(db, select(Location).order_by(Location.created_at, Location.id), limit, offset, lambda row: location_dict(db, row))

    @app.get("/locations/nearby", tags=["locations"])
    def nearby(latitude: Latitude, longitude: Longitude, radius_m: Annotated[float, Query(gt=0, le=20000000, allow_inf_nan=False)], db: DB, admin: Admin, limit: Limit = 50, offset: Offset = 0):
        origin = point(longitude, latitude)
        distance = func.ST_Distance(Location.point, origin)
        predicate = func.ST_DWithin(Location.point, origin, radius_m)
        rows = db.execute(select(Location, distance.label("distance_m")).where(predicate).order_by(distance, Location.id).limit(limit).offset(offset)).all()
        total = db.scalar(select(func.count()).select_from(Location).where(predicate))
        return {"items": [{**location_dict(db, loc), "distance_m": dist} for loc, dist in rows], "total": total, "limit": limit, "offset": offset}

    @app.get("/locations/geojson", tags=["locations"])
    def geojson(db: DB, admin: Admin, limit: Limit = 50, offset: Offset = 0):
        result = page(db, select(Location).order_by(Location.created_at, Location.id), limit, offset, lambda row: location_dict(db, row))
        features = [{"type": "Feature", "id": str(loc["id"]), "geometry": {"type": "Point", "coordinates": [loc["longitude"], loc["latitude"]]}, "properties": {k: v for k, v in loc.items() if k not in ("latitude", "longitude")}} for loc in result.pop("items")]
        return {"type": "FeatureCollection", "features": features, **result}

    @app.get("/locations/{location_id}", tags=["locations"])
    def get_location(location_id: UUID, db: DB, admin: Admin):
        return location_dict(db, require_row(db, Location, location_id))

    @app.patch("/locations/{location_id}", tags=["locations"])
    def update_location(location_id: UUID, payload: LocationUpdate, db: DB, admin: Admin):
        lock_event_clock(db)
        row = db.scalar(select(Location).where(Location.id == location_id).with_for_update())
        if row is None:
            raise HTTPException(404, "Location not found")
        previous = location_dict(db, row)
        if payload.expected_version != previous["configuration_version"]:
            raise HTTPException(409, "Location changed; reload before editing")
        changes = payload.model_dump(exclude_unset=True, exclude={"expected_version"})
        if all(previous[key] == value for key, value in changes.items()):
            return previous
        row.name = changes.get("name", row.name)
        row.timezone = changes.get("timezone", row.timezone)
        row.point = point(changes.get("longitude", previous["longitude"]),
                          changes.get("latitude", previous["latitude"]))
        db.flush()
        # Start new snapshot periods rather than rewriting the original place
        # attached to saved or delayed audio. The evaluator retains an old open
        # incident until new evidence arrives under the replacement assignment.
        devices = db.scalars(select(Device).where(Device.location_id == location_id)
                             .order_by(Device.id).with_for_update()).all()
        for device in devices:
            create_assignment(db, device, row)
            device.config_revision += 1
            device_configuration_event(db, device, row, "location_configured")
        if not devices:
            snapshot = location_snapshot(db, row)
            emit_event(db, "location.status_changed", {
                "location_name": snapshot["name"], "latitude": snapshot["latitude"],
                "longitude": snapshot["longitude"], "transition_reason": "location_configured",
            }, location_id=location_id)
        result = location_dict(db, row)
        db.commit()
        return result

    @app.get("/locations/{location_id}/threshold", tags=["locations"])
    def get_threshold(location_id: UUID, db: DB, admin: Admin, at: AwareDatetime | None = None):
        require_row(db, Location, location_id)
        latest = latest_threshold(db, location_id)
        selected = threshold_at(db, location_id, at or clock.now())
        return {"current": model_dict(selected) if selected else None,
                "latest": model_dict(latest) if latest else None,
                "latest_revision": latest.revision if latest else 0}

    @app.get("/locations/{location_id}/threshold/versions", tags=["locations"])
    def threshold_versions(location_id: UUID, db: DB, admin: Admin, limit: Limit = 50, offset: Offset = 0):
        require_row(db, Location, location_id)
        return page(db, select(ThresholdVersion).where(ThresholdVersion.location_id == location_id)
                    .order_by(ThresholdVersion.revision.desc()), limit, offset)

    @app.patch("/locations/{location_id}/threshold", tags=["locations"])
    def update_threshold(location_id: UUID, payload: ThresholdUpdate, db: DB, admin: Admin):
        rule = append_threshold(db, location_id, payload)
        snapshot = location_snapshot(db, require_row(db, Location, location_id))
        emit_event(db, "location.status_changed", {"location_name": snapshot["name"],
                   "latitude": snapshot["latitude"], "longitude": snapshot["longitude"],
                   "threshold_value": rule.threshold_value, "threshold_version_id": rule.id,
                   "threshold_revision": rule.revision, "transition_reason": "threshold_configured"}, location_id=location_id)
        db.commit()
        return model_dict(rule)

    @app.post("/devices", status_code=201, tags=["devices"])
    def register_device(payload: DeviceCreate, db: DB, admin: Admin):
        lock_event_clock(db)
        location = require_row(db, Location, payload.location_id)
        token = secrets.token_urlsafe(32)
        values = payload.model_dump(mode="python", exclude={"calibration"}, exclude_none=True)
        row = Device(**values, credential_hash=token_hash(token), enabled=True, calibration=payload.calibration.model_dump(mode="json") if payload.calibration else None)
        db.add(row)
        db.flush()
        create_assignment(db, row, location, initial=True)
        device_configuration_event(db, row, location, "device_registered")
        db.commit()
        return {**model_dict(row, ("credential_hash",)), "token": token}

    @app.get("/devices", tags=["devices"])
    def devices(db: DB, admin: Admin, limit: Limit = 50, offset: Offset = 0):
        return page(db, select(Device).order_by(Device.id), limit, offset, lambda row: model_dict(row, ("credential_hash",)))

    @app.get("/devices/{device_id}", tags=["devices"])
    def device(device_id: UUID, db: DB, admin: Admin):
        return model_dict(require_row(db, Device, device_id), ("credential_hash",))

    @app.patch("/devices/{device_id}", tags=["devices"])
    def update_device(device_id: UUID, payload: DeviceUpdate, db: DB, admin: Admin):
        lock_event_clock(db)
        row = db.scalar(select(Device).where(Device.id == device_id).with_for_update())
        if row is None:
            raise HTTPException(404, "Device not found")
        if payload.expected_revision != row.config_revision:
            raise HTTPException(409, {"message": "Device configuration changed; reload before editing", "current_revision": row.config_revision})
        reassigned = payload.location_id is not None and payload.location_id != row.location_id
        if reassigned:
            device_configuration_event(db, row, require_row(db, Location, row.location_id), "device_removed")
        if payload.location_id is not None and payload.location_id != row.location_id:
            location = require_row(db, Location, payload.location_id)
            create_assignment(db, row, location)
        for key in payload.model_fields_set - {"expected_revision", "location_id"}:
            value = getattr(payload, key)
            setattr(row, key, value.model_dump(mode="json") if key == "calibration" and value else value)
        row.config_revision += 1
        device_configuration_event(db, row, require_row(db, Location, row.location_id),
                                   "device_reassigned" if reassigned else "device_configured")
        db.commit()
        return model_dict(row, ("credential_hash",))

    @app.post("/audio", status_code=202, tags=["audio"])
    def upload(db: DB, token: Token, file: Annotated[UploadFile, File(description="Mono packed PCM24 WAV")], metadata: Annotated[str, Form(description="JSON: device_id, chunk_id, captured_at (with timezone), session_id, sequence")]):
        if len(metadata) > 8192:
            raise HTTPException(422, "Metadata exceeds 8192 characters")
        try:
            meta = UploadMetadata.model_validate_json(metadata)
        except ValidationError as exc:
            raise HTTPException(422, {"message": "Invalid upload metadata", "details": [{"field": list(e["loc"]), "message": e["msg"]} for e in exc.errors()]})
        result = ingest_recording(db, token, meta, file.file)
        return JSONResponse(jsonable_encoder(result), status_code=200 if result["duplicate"] else 202)

    @app.get("/audio", tags=["audio"])
    def recordings(db: DB, admin: Admin, device_id: UUID | None = None, location_id: UUID | None = None,
                   since: AwareDatetime | None = None, until: AwareDatetime | None = None,
                   received_until: AwareDatetime | None = None,
                   limit: Limit = 50, offset: Offset = 0):
        """Saved WAV captures, including pending and ineligible measurements.

        since/until are inclusive capture bounds. Freeze received_until too when
        paging so late historical uploads cannot shift the selected snapshot.
        Location uses the immutable assignment, not today's device mapping.
        """
        if since and until and since > until:
            raise HTTPException(422, "since must not be after until")
        query = select(AudioChunk)
        if device_id:
            query = query.where(AudioChunk.device_id == device_id)
        if location_id:
            query = query.where(AudioChunk.location_id == location_id)
        if since:
            query = query.where(AudioChunk.captured_at >= since)
        if until:
            query = query.where(AudioChunk.captured_at <= until)
        if received_until:
            query = query.where(AudioChunk.received_at <= received_until)
        return page(db, query.order_by(AudioChunk.captured_at.desc(), AudioChunk.id), limit, offset,
                    serialize_many=lambda rows: recording_dicts(db, rows))

    def authorized_chunk(db, chunk_id, token):
        row = require_row(db, AudioChunk, chunk_id)
        if not is_admin(token):
            authenticate_device(db, row.device_id, token)
        return row

    @app.get("/audio/{chunk_id}", tags=["audio"])
    def audio_status(chunk_id: UUID, db: DB, token: Token):
        row = authorized_chunk(db, chunk_id, token)
        job = db.scalar(select(ProcessingJob).where(ProcessingJob.audio_chunk_id == row.id))
        results = db.scalars(select(Measurement).where(Measurement.audio_chunk_id == row.id).order_by(Measurement.received_at, Measurement.id)).all()
        return {**model_dict(row, ("file_path",)), "job": model_dict(job, ("lease_token",)) if job else None,
                "classification": classifications_for(db, [row.id])[row.id],
                "measurements": measurement_dicts(db, results)}

    @app.get("/audio/{chunk_id}/file", tags=["audio"])
    def download_audio(chunk_id: UUID, db: DB, request: Request,
                       credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]):
        if credentials is not None:
            # Explicit bearer credentials retain precedence over local cookies.
            row = authorized_chunk(db, chunk_id, credentials.credentials)
        else:
            if not request.app.state.local_audio_playback:
                raise HTTPException(401, "Bearer token required", headers={"WWW-Authenticate": "Bearer"})
            require_admin(request, None)
            row = require_row(db, AudioChunk, chunk_id)
        path = resolve_audio_path(row.file_path, get_settings())
        if not path.is_file():
            raise HTTPException(503, "Original audio unavailable; restore storage from backup")
        return FileResponse(path, media_type="audio/wav", filename=f"{row.id}.wav",
                            headers={"ETag": f'"{row.checksum}"', "Cache-Control": "private, no-store"})

    @app.get("/measurements", tags=["measurements"])
    def measurements(db: DB, admin: Admin, device_id: UUID | None = None, location_id: UUID | None = None,
                     since: AwareDatetime | None = None, until: AwareDatetime | None = None,
                     limit: Limit = 50, offset: Offset = 0):
        query = select(Measurement).join(AudioChunk, Measurement.audio_chunk_id == AudioChunk.id)
        if device_id:
            query = query.where(AudioChunk.device_id == device_id)
        if location_id:
            query = query.where(AudioChunk.location_id == location_id)
        if since and until and since > until:
            raise HTTPException(422, "since must not be after until")
        if since:
            query = query.where(Measurement.measured_at >= since)
        if until:
            query = query.where(Measurement.measured_at <= until)
        return page(db, query.order_by(Measurement.measured_at.desc(), Measurement.id), limit, offset,
                    serialize_many=lambda rows: measurement_dicts(db, rows))

    @app.get("/incidents", tags=["incidents"])
    def incidents(db: DB, admin: Admin, device_id: UUID | None = None, location_id: UUID | None = None,
                  status: Literal["active", "recovering", "resolved", "closed"] | None = None,
                  since: AwareDatetime | None = None, until: AwareDatetime | None = None,
                  q: Annotated[str | None, Query(max_length=200)] = None,
                  limit: Limit = 50, offset: Offset = 0):
        query = select(Incident)
        if q and q.strip():
            # Escaped substring matching treats wildcard characters literally.
            term = q.strip()
            query = query.join(Location, Incident.location_id == Location.id).join(Device, Incident.device_id == Device.id)
            query = query.where(or_(
                Location.name.icontains(term, autoescape=True),
                Incident.location_snapshot["name"].astext.icontains(term, autoescape=True),
                Device.microphone_model.icontains(term, autoescape=True),
                Device.external_id.icontains(term, autoescape=True),
                cast(Device.id, String).icontains(term, autoescape=True),
                cast(Incident.id, String).icontains(term, autoescape=True),
            ))
        if device_id:
            query = query.where(Incident.device_id == device_id)
        if location_id:
            query = query.where(Incident.location_id == location_id)
        if status:
            query = query.where(Incident.status == status)
        if since and until and since > until:
            raise HTTPException(422, "since must not be after until")
        if since:
            query = query.where(Incident.started_at >= since)
        if until:
            query = query.where(Incident.started_at <= until)
        return page(db, query.order_by(Incident.started_at.desc(), Incident.id), limit, offset,
                    serialize_many=lambda rows: incident_dicts(db, rows))

    @app.get("/incidents/{incident_id}", tags=["incidents"])
    def incident(incident_id: UUID, db: DB, admin: Admin):
        row = require_row(db, Incident, incident_id)
        rule = db.get(ThresholdVersion, row.threshold_version_id) if row.threshold_version_id else None
        return {**incident_dicts(db, [row])[0], "threshold_version": model_dict(rule) if rule else None}

    @app.get("/incidents/{incident_id}/measurements", tags=["incidents"])
    def incident_measurements(incident_id: UUID, db: DB, admin: Admin,
                              limit: Limit = 50, offset: Offset = 0):
        incident = require_row(db, Incident, incident_id)
        if not incident.stream_id or not incident.threshold_version_id:
            return {"items": [], "total": 0, "limit": limit, "offset": offset,
                    "association_available": False,
                    "reason": "This legacy incident does not record its evaluation stream and policy."}
        # An incident belongs to one assignment/method stream and immutable rule.
        # Device-only or time-only joins could wrongly attach a reassignment,
        # reprocessing result, delayed historical upload, or different policy.
        query = select(Measurement).join(MeasurementEvaluation,
                    MeasurementEvaluation.measurement_id == Measurement.id).where(
                    MeasurementEvaluation.stream_id == incident.stream_id,
                    MeasurementEvaluation.threshold_version_id == incident.threshold_version_id,
                    MeasurementEvaluation.live.is_(True),
                    Measurement.measured_at >= incident.started_at)
        if incident.ended_at is not None:
            query = query.where(Measurement.measured_at < incident.ended_at)
        result = page(db, query.order_by(Measurement.measured_at, Measurement.id), limit, offset,
                      serialize_many=lambda rows: measurement_dicts(db, rows))
        return {**result, "association_available": True}

    @app.get("/events", tags=["live"])
    def event_history(db: DB, admin: Admin, location_id: UUID | None = None, device_id: UUID | None = None,
                      event_type: Literal["incident.opened", "incident.updated", "incident.resolved", "incident.closed", "location.status_changed"] | None = None,
                      limit: Limit = 50, offset: Offset = 0):
        query = select(DurableEvent)
        if location_id:
            query = query.where(DurableEvent.location_id == location_id)
        if device_id:
            query = query.where(DurableEvent.device_id == device_id)
        if event_type:
            query = query.where(DurableEvent.event_type == event_type)
        return page(db, query.order_by(DurableEvent.pointer.desc()), limit, offset, lambda row: row.payload)

    return app


app = create_app()
