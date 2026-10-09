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
    # Hard linking refuses to overwrite even in the unlikely event of a UUID
    # collision. Both paths are in the same volume; no copy or conversion occurs.
    os.link(staged.path, destination)
    _fsync_directory(directory)
    staged.path.unlink()
    _fsync_directory(staged.path.parent)
    return destination.relative_to(root).as_posix()


def resolve_audio_path(file_path: str, settings: Any) -> Path:
    root = Path(settings.audio_root).resolve()
    candidate = (root / file_path).resolve()
    if candidate == root or not candidate.is_relative_to(root):
        raise ValueError("invalid stored audio reference")
    return candidate


def remove_audio(file_path: str, settings: Any) -> None:
    path = resolve_audio_path(file_path, settings)
    if path.exists():
        path.unlink()
        _fsync_directory(path.parent)


def cleanup_staged(staged: StagedAudio) -> None:
    # A duplicate upload commits before the handler's finally block, so a
    # concurrent reconciler may already have removed this unreferenced stage.
    staged.path.unlink(missing_ok=True)
    _fsync_directory(staged.path.parent)
