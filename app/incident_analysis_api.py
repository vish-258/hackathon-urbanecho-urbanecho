"""Authenticated incident evidence playback and saved sound estimates."""
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.auth import require_admin
from app.config import get_settings
from app.db import get_db
from app.incident_analysis_jobs import analysis_dict, enqueue_incident, get_analysis
from app.incident_audio import prepare_playback
from app.models import Incident

router = APIRouter(tags=["incident audio"])
DB = Annotated[Session, Depends(get_db)]
Admin = Annotated[None, Depends(require_admin)]


def require_incident(db, incident_id):
    incident = db.get(Incident, incident_id)
    if incident is None:
        raise HTTPException(404, "Incident not found")
    return incident


@router.get("/incidents/{incident_id}/analysis")
def analysis(incident_id: UUID, db: DB, admin: Admin):
    return analysis_dict(db, require_incident(db, incident_id))


@router.post("/incidents/{incident_id}/analysis", status_code=202)
def recalculate(incident_id: UUID, db: DB, admin: Admin):
    incident = require_incident(db, incident_id)
    settings = get_settings()
    if not settings.classification_enabled or settings.classification_scope != "incidents":
        raise HTTPException(409, "Incident sound analysis is disabled")
    row = enqueue_incident(db, incident, retry=True, settings=settings)
    db.commit()
    return analysis_dict(db, incident, row, settings)


@router.get("/incidents/{incident_id}/audio/file")
def audio(incident_id: UUID, db: DB, admin: Admin,
          revision: Annotated[str | None, Query(pattern=r"^[a-f0-9]{64}$")] = None):
    require_incident(db, incident_id)
    row = get_analysis(db, incident_id)
    if row is None or not row.manifest or not row.manifest.get("available"):
        raise HTTPException(409, "Incident audio is not available yet")
    if revision is not None and revision != row.revision:
        raise HTTPException(409, "This incident audio snapshot changed; refresh its details before playing")
    try:
        stream, length = prepare_playback(db, row.manifest, get_settings())
    except (OSError, ValueError):
        raise HTTPException(503, "An original recording is unavailable or failed its integrity check; recalculate incident audio") from None
    etag = row.revision
    # The generator only needs verified filesystem spans. Release the read
    # transaction/pooled connection before a potentially long audio download.
    db.rollback()
    return StreamingResponse(stream, media_type="audio/wav", headers={
        "Content-Length": str(length), "Cache-Control": "private, no-store",
        "Content-Disposition": f'attachment; filename="urbanecho-incident-{incident_id}.wav"',
        "ETag": f'"{etag}"', "X-Content-Type-Options": "nosniff"})
