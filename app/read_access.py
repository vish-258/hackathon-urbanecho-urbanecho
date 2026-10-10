"""Explicitly scoped anonymous reads for a published demonstration location.

Only routes that opt into Read may serve public data. Mutations continue using
require_admin, and public filtering happens before counting or pagination.
"""
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import select

from app.auth import bearer, has_local_session, require_admin
from app.config import get_settings
from app.models import AudioChunk, Device, Incident, Measurement

# A reassignment can close a published incident using a new private reading.
# Keep the public incident transition, but never publish that new observation.
OBSERVATION_FIELDS = frozenset({
    "measurement_id", "measurement_value", "value_db", "measurement_type",
    "weighting", "channel_policy", "interval_seconds", "measured_at", "received_at",
    "assignment_id", "diagnostic", "quality_status", "calibration_status",
})


@dataclass(frozen=True)
class ReadAccess:
    public_location_id: UUID | None = None

    @property
    def read_only(self) -> bool:
        return self.public_location_id is not None

    def require_location(self, location_id) -> None:
        if self.read_only and location_id != self.public_location_id:
            raise HTTPException(404, "Record not found")

    def location_filter(self, location_id=None):
        if location_id is not None:
            self.require_location(location_id)
        return self.public_location_id if self.read_only else location_id

    def scope(self, query, location_column):
        return query.where(location_column == self.public_location_id) if self.read_only else query

    def require_device(self, db, device_id) -> None:
        if self.read_only and device_id is not None:
            device = db.get(Device, device_id)
            if device is None:
                raise HTTPException(404, "Record not found")
            self.require_location(device.location_id)

    def require_manifest(self, db, manifest) -> None:
        """A published incident must not expose context from a private location."""
        if not self.read_only or not manifest:
            return
        try:
            self.require_location(UUID(str(manifest["location_id"])))
            identifiers = {UUID(str(segment["audio_id"])) for segment in manifest.get("segments", [])}
            if manifest.get("opening_audio_id"):
                identifiers.add(UUID(str(manifest["opening_audio_id"])))
        except (KeyError, ValueError, TypeError):
            raise HTTPException(404, "Record not found") from None
        if not identifiers:
            return
        rows = db.execute(select(AudioChunk.id, AudioChunk.location_id).where(AudioChunk.id.in_(identifiers))).all()
        if len(rows) != len(identifiers) or any(location_id != self.public_location_id for _, location_id in rows):
            raise HTTPException(404, "Record not found")

    def incidents(self, db, items):
        """Do not reveal links to a device's earlier private-location incident."""
        if not self.read_only:
            return items
        previous = {UUID(str(item["previous_incident_id"])) for item in items if item.get("previous_incident_id")}
        allowed = set(db.scalars(select(Incident.id).where(
            Incident.id.in_(previous), Incident.location_id == self.public_location_id))) if previous else set()
        return [{**item, "previous_incident_id": item.get("previous_incident_id")
                 if item.get("previous_incident_id") and UUID(str(item["previous_incident_id"])) in allowed else None}
                for item in items]

    def events(self, db, records):
        if not self.read_only or not records:
            return records
        payloads = self.incidents(db, [record["data"] for record in records])
        identifiers = {UUID(str(item["measurement_id"])) for item in payloads if item.get("measurement_id")}
        allowed = set(db.scalars(select(Measurement.id).join(AudioChunk, Measurement.audio_chunk_id == AudioChunk.id).where(
            Measurement.id.in_(identifiers), AudioChunk.location_id == self.public_location_id))) if identifiers else set()
        result = []
        for record, payload in zip(records, payloads):
            if payload.get("measurement_id") and UUID(str(payload["measurement_id"])) not in allowed:
                payload = {key: value for key, value in payload.items() if key not in OBSERVATION_FIELDS}
            result.append({**record, "data": payload})
        return result


def require_read(request: Request, credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]) -> ReadAccess:
    # Explicit credentials always win; a bad token must never become a guest.
    if "authorization" in request.headers and credentials is None:
        raise HTTPException(403, "Administrator permission required")
    if credentials is not None or has_local_session(request) or request.method not in {"GET", "HEAD"}:
        require_admin(request, credentials)
        return ReadAccess()
    location_id = get_settings().public_demo_location_id
    if location_id is not None:
        return ReadAccess(location_id)
    require_admin(request, credentials)
    return ReadAccess()


Read = Annotated[ReadAccess, Depends(require_read)]
