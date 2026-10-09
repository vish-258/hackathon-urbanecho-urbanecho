"""Authenticated classification state; independent of device transport APIs."""
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import require_admin
from app.classification_jobs import classification_dict, classifications_for, enqueue_recording, worker_status
from app.config import get_settings
from app.db import get_db
from app.models import AudioChunk

router = APIRouter(tags=["sound classification"])
DB = Annotated[Session, Depends(get_db)]
Admin = Annotated[None, Depends(require_admin)]


@router.get("/classification/status")
def status(db: DB, admin: Admin):
    return worker_status(db)


@router.get("/audio/{audio_id}/classification")
def classification(audio_id: UUID, db: DB, admin: Admin):
    if db.get(AudioChunk, audio_id) is None:
        raise HTTPException(404, "Audio recording not found")
    return classifications_for(db, [audio_id])[audio_id]


@router.post("/audio/{audio_id}/classification", status_code=202)
def retry_classification(audio_id: UUID, db: DB, admin: Admin):
    chunk = db.get(AudioChunk, audio_id)
    if chunk is None:
        raise HTTPException(404, "Audio recording not found")
    if not get_settings().classification_enabled:
        raise HTTPException(409, "Automatic sound classification is disabled")
    if get_settings().classification_scope == "incidents":
        raise HTTPException(409, "Sound classification runs on incidents. Open the incident to generate or retry its sound estimate.")
    row = enqueue_recording(db, chunk, retry=True)
    db.commit()
    return classification_dict(row, worker_status(db))
