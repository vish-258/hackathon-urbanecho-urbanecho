"""Compatibility for the ESP test sender: raw PCM16, with durable attribution.

No append-only session files or process-local duplicate state. Each capture is
an immutable WAV recording sent through the same ingestion/worker pipeline.
"""
import hashlib
import io
import re
import wave
from datetime import timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import Response
from pydantic import AwareDatetime
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app import clock
from app.audio import validate_wav
from app.auth import authenticate_device, is_admin, token_value
from app.config import get_settings
from app.db import get_db
from app.ingestion import ingest_recording
from app.models import AudioChunk, Device
from app.schemas import UploadMetadata
from app.storage import resolve_audio_path

router = APIRouter(tags=["ESP compatibility"])
DB = Annotated[Session, Depends(get_db)]
Token = Annotated[str, Depends(token_value)]
DeviceHeader = Annotated[str, Header(alias="X-Device-ID", max_length=36)]
SessionHeader = Annotated[str, Header(alias="X-Session", min_length=1, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")]
SequenceHeader = Annotated[int, Header(alias="X-Seq", ge=0, le=9223372036854775807)]
CapturedHeader = Annotated[AwareDatetime, Header(alias="X-Captured-At")]
RAW_LIMIT = 1_000_000
RATE = 16000
FORMAT = "wav_pcm_s16le_mono"


def device_uuid(db, value):
    if re.fullmatch(r"(?:[0-9a-fA-F]{32}|[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})", value):
        return UUID(value)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", value):
        raise HTTPException(400, "X-Device-ID must be a registered device code or UUID")
    identity = db.scalar(select(Device.id).where(Device.external_id == value))
    if identity is None:
        raise HTTPException(401, "Device code is not registered or credentials are invalid")
    return identity


async def bounded_body(request, maximum):
    if request.headers.get("content-encoding", "identity").lower() != "identity":
        raise HTTPException(415, "Compressed request bodies are unsupported")
    body = bytearray()
    async for data in request.stream():
        if len(body) + len(data) > maximum:
            raise HTTPException(413, "Request exceeds this endpoint's byte limit")
        body.extend(data)
    return bytes(body)


def pcm_wav(pcm):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(RATE)
        wav.writeframes(pcm)
    return buffer.getvalue()


@router.get("/ping")
def ping():
    """Connectivity only. Does not certify microphone, worker or database health."""
    return {"pong": True}


@router.post("/text")
async def device_text(request: Request, db: DB, token: Token, x_device_id: DeviceHeader):
    """Authenticated, bounded diagnostic echo. Message text is never logged."""
    device = authenticate_device(db, device_uuid(db, x_device_id), token)
    body = await bounded_body(request, 1000)
    device.last_contact_at = clock.now()
    db.commit()
    return {"ok": True, "received": body.decode("utf-8", errors="replace")}


@router.post("/upload")
async def raw_upload(request: Request, db: DB, token: Token,
                     x_device_id: DeviceHeader, x_session: SessionHeader,
                     x_seq: SequenceHeader, x_captured_at: CapturedHeader,
                     x_capture_interval_ms: Annotated[int | None, Header(alias="X-Capture-Interval-Ms", ge=1, le=3600000)] = None):
    """Signed little-endian PCM16, mono 16 kHz. Timestamp is capture START.

    HTTP 200 acknowledges durable acceptance or an identical retry, not completed
    processing. Poll the returned audio ID through the existing /audio API.
    """
    identity = device_uuid(db, x_device_id)
    authenticate_device(db, identity, token)
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/octet-stream":
        raise HTTPException(415, "Send raw PCM16 bytes as application/octet-stream, not WAV or multipart")
    settings = get_settings()
    body = await bounded_body(request, min(RAW_LIMIT, settings.max_upload_bytes - 44))
    if not body or len(body) % 2:
        raise HTTPException(400, "PCM16 must contain complete, nonempty two-byte samples")
    # Raw sample pairs can have any byte values, including a RIFF-like prefix.
    # The declared transport determines interpretation; do not magic-sniff PCM.
    meta = UploadMetadata(device_id=identity, chunk_id=f"pcm16:{x_session}:{x_seq}",
                          session_id=f"pcm16-{x_session}", sequence=x_seq, captured_at=x_captured_at,
                          capture_interval_ms=x_capture_interval_ms)
    # The header is deterministic and the PCM sample bytes are unchanged.
    result = await run_in_threadpool(ingest_recording, db, token, meta, io.BytesIO(pcm_wav(body)), audio_format=FORMAT)
    key = f"{identity.hex}_{x_session}"
    return jsonable_encoder({"ok": True, "seq": x_seq, **result, "recording_key": key,
                             "recording_url": f"/recordings/{key}.wav"})


@router.get("/recordings/{key}.wav")
def session_recording(key: str, db: DB, token: Token):
    """Export an ordered, contiguous session without hiding drops or gaps.

    Session export is deliberately bounded. Individual originals always remain
    available through /audio/{id}/file, including incomplete sessions.
    """
    match = re.fullmatch(r"([0-9a-fA-F]{32})_([A-Za-z0-9_-]{1,32})", key)
    if match is None:
        raise HTTPException(400, "Use the recording_key returned by /upload")
    identity, session_id = UUID(match[1]), "pcm16-" + match[2]
    if not is_admin(token):
        authenticate_device(db, identity, token)
    rows = db.scalars(select(AudioChunk).where(AudioChunk.device_id == identity,
                       AudioChunk.session_id == session_id, AudioChunk.audio_format == FORMAT)
                     .order_by(AudioChunk.sequence, AudioChunk.id).limit(10001)).all()
    if not rows:
        raise HTTPException(404, "No recordings in this session")
    maximum = get_settings().max_upload_bytes
    if len(rows) > 10000 or sum(round(r.duration_seconds * RATE) * 2 for r in rows) + 44 > maximum:
        raise HTTPException(413, "Session export too large; download individual /audio/{id}/file recordings")
    pcm = bytearray()
    previous_end = None
    for index, row in enumerate(rows):
        if row.sequence != index:
            raise HTTPException(409, "Session has missing or conflicting sequence numbers; download individual recordings")
        if previous_end is not None and abs((row.captured_at - previous_end).total_seconds()) > .001:
            raise HTTPException(409, "Session capture windows contain a gap or overlap; download individual recordings")
        previous_end = row.captured_at + timedelta(seconds=row.duration_seconds)
        path = resolve_audio_path(row.file_path, get_settings())
        if not path.is_file():
            raise HTTPException(503, "Original audio unavailable; restore storage from backup")
        payload = path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != row.checksum:
            raise HTTPException(503, "Original audio integrity check failed")
        info = validate_wav(path, get_settings(), allow_pcm16=True)
        if info.sample_width != 2 or info.sample_rate != RATE:
            raise HTTPException(503, "Stored recording does not match the session format")
        if len(pcm) + info.data_bytes + 44 > maximum:
            raise HTTPException(413, "Session export exceeds byte limit")
        pcm.extend(payload[info.data_offset:info.data_offset + info.data_bytes])
    body = pcm_wav(pcm)
    return Response(body, media_type="audio/wav", headers={"Cache-Control": "no-store",
                    "Content-Disposition": f'attachment; filename="{identity.hex}_{match[2]}.wav"',
                    "ETag": '"' + hashlib.sha256(body).hexdigest() + '"'})
