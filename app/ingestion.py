"""Shared transactional ingestion for original PCM24 and wrapped PCM16 audio."""
from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select

from app import clock
from app.audio import AudioTooLargeError, AudioValidationError, validate_wav
from app.auth import authenticate_device
from app.config import get_settings
from app.configuration import assignment_at, threshold_at
from app.events import lock_event_clock
from app.models import AudioChunk, Device, Location, ProcessingJob
from app.storage import cleanup_staged, finalize_audio, lock_storage, stage_audio


def ingest_recording(db, token, meta, source, *, audio_format="wav_pcm_s24le_mono"):
    """Preserve bytes, historical attribution and duplicate identity, then queue once."""
    authenticate_device(db, meta.device_id, token)
    lock_event_clock(db)
    # Serializes assignment changes and same-device duplicate requests.
    device = db.scalar(select(Device).where(Device.id == meta.device_id).with_for_update().execution_options(populate_existing=True))
    authenticate_device(db, meta.device_id, token)
    lock_storage(db)
    staged = None
    try:
        staged = stage_audio(source, get_settings())
        existing = db.scalar(select(AudioChunk).where(AudioChunk.device_id == device.id, AudioChunk.device_chunk_id == meta.chunk_id))
        if existing:
            if existing.checksum != staged.checksum or existing.captured_at != meta.captured_at or existing.session_id != meta.session_id or existing.sequence != meta.sequence:
                raise HTTPException(409, "Chunk ID was already used with different bytes or metadata")
            device.last_contact_at = clock.now()
            db.commit()
            return {"id": existing.id, "status": existing.status, "duplicate": True}
        info = validate_wav(staged.path, get_settings(), allow_pcm16=audio_format == "wav_pcm_s16le_mono")
        expected_width = 2 if audio_format == "wav_pcm_s16le_mono" else 3
        if info.sample_width != expected_width:
            raise AudioValidationError("WAV sample width does not match the upload contract")
        assignment = assignment_at(db, device.id, meta.captured_at)
        if assignment is None:
            raise HTTPException(422, "No registered device assignment covers the capture timestamp")
        location = db.get(Location, assignment.location_id)
        if location is None:
            raise HTTPException(422, "Registered capture location is unavailable")
        rule = threshold_at(db, location.id, meta.captured_at)
        # Preserve format-valid audio even when its method/interval is not
        # evaluable. The worker records a diagnostic instead of recovery.
        snapshot = assignment.location_snapshot
        file_path = finalize_audio(staged, get_settings())
        selected = rule or location
        row = AudioChunk(device_id=device.id, location_id=location.id, assignment_id=assignment.id, location_snapshot=jsonable_encoder(snapshot), device_chunk_id=meta.chunk_id, session_id=meta.session_id, sequence=meta.sequence, captured_at=meta.captured_at, received_at=clock.now(), duration_seconds=info.duration_seconds, sample_rate=info.sample_rate, audio_format=audio_format, checksum=staged.checksum, file_path=file_path, status="pending", threshold_value=selected.threshold_value, threshold_type=selected.threshold_type, interval_seconds=selected.interval_seconds, calibration=device.calibration)
        db.add(row)
        db.flush()
        db.add(ProcessingJob(audio_chunk_id=row.id, status="pending", available_at=clock.now(),
                             created_at=clock.now(), updated_at=clock.now()))
        device.last_contact_at = clock.now()
        db.commit()
        # Commit errors can be ambiguous: leave any final orphan for locked reconciliation.
        return {"id": row.id, "status": row.status, "duplicate": False}
    except AudioTooLargeError as exc:
        raise HTTPException(413, str(exc))
    except AudioValidationError as exc:
        raise HTTPException(422, str(exc))
    finally:
        if staged is not None:
            cleanup_staged(staged)
