"""Durable original-audio storage; filesystem/SQL coordination is explicit."""
from __future__ import annotations

import hashlib
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

from sqlalchemy import text

from app.audio import AudioTooLargeError
from app.object_storage import (
    AudioReadLimitError, AudioStorageError, audio_read_limit, download_original,
    upload_original, validate_object_reference,
)

STORAGE_ADVISORY_LOCK = 741908732159


@dataclass(frozen=True)
class StagedAudio:
    path: Path
    checksum: str
    size_bytes: int


def lock_storage(session: Any, *, exclusive: bool = False) -> None:
    """Acquire before writing; hold until SQL commit/rollback, including errors.

    Reconciliation uses the exclusive variant. This prevents it from deleting a
    file between its creation and the transaction which records that file.
    """
    function = "pg_advisory_xact_lock" if exclusive else "pg_advisory_xact_lock_shared"
    session.execute(text(f"SELECT {function}(:key)"), {"key": STORAGE_ADVISORY_LOCK})


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _ensure_directory(path: Path) -> None:
    if not path.exists():
        path.mkdir(parents=True, exist_ok=True)
        _fsync_directory(path.parent)


def stage_audio(source: BinaryIO, settings: Any) -> StagedAudio:
    root = Path(settings.audio_root)
    _ensure_directory(root)
    staging = root / ".staging"
    _ensure_directory(staging)
    path = staging / f"{uuid.uuid4().hex}.part"
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("xb") as target:
            while data := source.read(1024 * 1024):
                size += len(data)
                if size > settings.max_upload_bytes:
                    raise AudioTooLargeError("audio exceeds the configured upload-size limit")
                digest.update(data)
                target.write(data)
            target.flush()
            os.fsync(target.fileno())
        _fsync_directory(staging)
        return StagedAudio(path, digest.hexdigest(), size)
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def finalize_audio(staged: StagedAudio, settings: Any) -> str:
    root = Path(settings.audio_root)
    identity = uuid.uuid4().hex
    directory = root / "originals" / identity[:2]
    _ensure_directory(root / "originals")
    _ensure_directory(directory)
    destination = directory / f"{identity}.wav"
    reference = destination.relative_to(root).as_posix()
    if getattr(settings, "audio_storage_backend", "filesystem") == "supabase":
        # A remote failure must prevent the caller from committing an AudioChunk.
        # An ambiguous upload/SQL failure can leave an immutable remote orphan;
        # it is never automatically deleted or overwritten.
        upload_original(staged.path, reference, staged.checksum, staged.size_bytes, settings)
    # Hard linking refuses to overwrite even in the unlikely event of a UUID
    # collision. Both paths are in the same volume; no copy or conversion occurs.
    os.link(staged.path, destination)
    _fsync_directory(directory)
    staged.path.unlink()
    _fsync_directory(staged.path.parent)
    return reference


def _local_audio_path(file_path: str, settings: Any) -> Path:
    root = Path(settings.audio_root).resolve()
    candidate = (root / file_path).resolve()
    if candidate == root or not candidate.is_relative_to(root):
        raise ValueError("invalid stored audio reference")
    return candidate


def resolve_audio_path(file_path: str, settings: Any, checksum: str | None = None,
                       *, max_bytes: int | None = None) -> Path:
    candidate = _local_audio_path(file_path, settings)
    if getattr(settings, "audio_storage_backend", "filesystem") != "supabase":
        if max_bytes is not None and candidate.exists() and candidate.stat().st_size > audio_read_limit(max_bytes):
            raise AudioReadLimitError("Stored audio exceeds the remaining read budget")
        return candidate
    validate_object_reference(file_path)
    limit = audio_read_limit(max_bytes)
    if limit == 0:
        raise AudioReadLimitError("Stored audio exceeds the remaining read budget")
    if candidate.exists():
        if candidate.stat().st_size > limit:
            raise AudioReadLimitError("Cached audio exceeds the remaining read budget")
        if checksum:
            _verify_cache(candidate, checksum, limit)
        return candidate
    if not checksum:
        raise AudioStorageError("A saved checksum is required to restore original audio")
    _ensure_directory(candidate.parent.parent)
    _ensure_directory(candidate.parent)
    # Upload staging is protected by the transaction's storage lock and can be
    # reconciled. Cache downloads happen outside SQL locks; keep them separate
    # so reconciliation cannot unlink an active download.
    staging = Path(settings.audio_root) / ".cache-staging"
    _ensure_directory(staging)
    temporary = staging / f"{uuid.uuid4().hex}.part"
    try:
        with temporary.open("xb") as target:
            download_original(file_path, target, checksum, settings, max_bytes=limit)
            target.flush()
            os.fsync(target.fileno())
        try:
            # A concurrent reader may have restored the same immutable object.
            # Linking is atomic, exposes only complete bytes, and never overwrites.
            os.link(temporary, candidate)
            _fsync_directory(candidate.parent)
        except FileExistsError:
            _verify_cache(candidate, checksum, limit)
        return candidate
    finally:
        temporary.unlink(missing_ok=True)
        _fsync_directory(staging)


def _verify_cache(path: Path, checksum: str, limit: int) -> None:
    if path.stat().st_size > limit:
        raise AudioReadLimitError("Cached audio exceeds the remaining read budget")
    digest, size = hashlib.sha256(), 0
    with path.open("rb") as source:
        while block := source.read(64 * 1024):
            size += len(block)
            if size > limit:
                raise AudioReadLimitError("Cached audio exceeds the remaining read budget")
            digest.update(block)
    if digest.hexdigest() != checksum:
        raise AudioStorageError("Cached audio checksum verification failed")


def remove_audio(file_path: str, settings: Any) -> None:
    # Removes local files/cache only. Do not fetch a missing object just to remove
    # it, and never delete the remote authoritative original.
    path = _local_audio_path(file_path, settings)
    if path.exists():
        path.unlink()
        _fsync_directory(path.parent)


def cleanup_staged(staged: StagedAudio) -> None:
    # A duplicate upload commits before the handler's finally block, so a
    # concurrent reconciler may already have removed this unreferenced stage.
    staged.path.unlink(missing_ok=True)
    _fsync_directory(staged.path.parent)
