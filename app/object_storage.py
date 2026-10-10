"""Bounded, server-only access to immutable originals in a private Supabase bucket.

The durable object is authoritative; local files are a disposable playback cache.
There is deliberately no remote delete or overwrite operation here.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import time
from typing import Any, BinaryIO
from urllib.parse import quote

import httpx


class AudioStorageError(OSError):
    """A deliberately redacted, retryable storage failure."""


class AudioReadLimitError(AudioStorageError):
    """An intact original cannot be read within the caller's byte budget."""


# Historical originals remain readable if an operator lowers today's upload
# limit. This matches the existing bounded historical WAV reader.
MAX_STORED_AUDIO_BYTES = 100_000_000
_BLOCK_SIZE = 64 * 1024
_METADATA_LIMIT = 16 * 1024
_REFERENCE = re.compile(r"originals/([0-9a-f]{2})/([0-9a-f]{32})\.wav")


def audio_read_limit(max_bytes: int | None = None) -> int:
    if max_bytes is None:
        return MAX_STORED_AUDIO_BYTES
    if type(max_bytes) is not int or max_bytes < 0:
        raise ValueError("Audio read budget must be a nonnegative integer")
    return min(MAX_STORED_AUDIO_BYTES, max_bytes)


def validate_object_reference(reference: str) -> None:
    match = _REFERENCE.fullmatch(reference)
    if not match or match[1] != match[2][:2]:
        raise ValueError("invalid stored audio reference")


def _client(settings: Any) -> httpx.Client:
    secret = settings.supabase_service_role_key
    key = secret.get_secret_value() if hasattr(secret, "get_secret_value") else secret
    return httpx.Client(
        base_url=f"{settings.supabase_url.rstrip('/')}/storage/v1/",
        headers={"Authorization": f"Bearer {key}", "apikey": key,
                 "Accept-Encoding": "identity"},
        timeout=httpx.Timeout(settings.audio_storage_timeout_seconds, connect=5, pool=5),
        follow_redirects=False,
        trust_env=False,
    )


def _deadline(settings: Any) -> float:
    return time.monotonic() + settings.audio_storage_timeout_seconds


def _check_deadline(deadline: float) -> None:
    if time.monotonic() > deadline:
        raise AudioStorageError("Audio storage operation timed out")


def _status(response: httpx.Response, operation: str) -> None:
    # Neither provider bodies nor request exceptions are included: they can
    # contain credentials, URLs, object names, or upstream implementation detail.
    if not 200 <= response.status_code < 300:
        raise AudioStorageError(f"Audio storage {operation} failed (HTTP {response.status_code})")


def _private_bucket(client: httpx.Client, settings: Any, deadline: float) -> None:
    bucket = quote(settings.supabase_storage_bucket, safe="")
    with client.stream("GET", f"bucket/{bucket}") as response:
        _status(response, "bucket check")
        data = bytearray()
        for chunk in response.iter_bytes(_BLOCK_SIZE):
            _check_deadline(deadline)
            data.extend(chunk)
            if len(data) > _METADATA_LIMIT:
                raise AudioStorageError("Audio storage returned invalid bucket metadata")
    try:
        metadata = json.loads(data)
    except (ValueError, UnicodeError):
        raise AudioStorageError("Audio storage returned invalid bucket metadata") from None
    if (not isinstance(metadata, dict) or metadata.get("public") is not False
            or metadata.get("id") != settings.supabase_storage_bucket):
        raise AudioStorageError("Audio storage requires the configured private bucket")


def check_audio_bucket(settings: Any) -> None:
    """Check existing bucket and access; never create or reconfigure a bucket."""
    try:
        with _client(settings) as client:
            _private_bucket(client, settings, _deadline(settings))
    except httpx.HTTPError:
        raise AudioStorageError("Audio storage bucket check is unavailable") from None


def upload_original(path: Path, reference: str, checksum: str, size_bytes: int, settings: Any) -> None:
    """Finish an immutable upload before the caller records it in SQL."""
    validate_object_reference(reference)
    if size_bytes > settings.max_upload_bytes or size_bytes <= 0 or path.stat().st_size != size_bytes:
        raise AudioStorageError("Original audio size is invalid")
    deadline = _deadline(settings)

    def blocks(source: BinaryIO):
        digest, size = hashlib.sha256(), 0
        while block := source.read(_BLOCK_SIZE):
            _check_deadline(deadline)
            size += len(block)
            if size > size_bytes:
                raise AudioStorageError("Original audio changed before storage completed")
            digest.update(block)
            yield block
        if size != size_bytes or digest.hexdigest() != checksum:
            raise AudioStorageError("Original audio changed before storage completed")

    try:
        with _client(settings) as client:
            _private_bucket(client, settings, deadline)
            bucket = quote(settings.supabase_storage_bucket, safe="")
            with path.open("rb") as source, client.stream(
                "POST", f"object/{bucket}/{reference}", content=blocks(source),
                headers={"Content-Type": "audio/wav", "Content-Length": str(size_bytes), "x-upsert": "false"},
            ) as response:
                _check_deadline(deadline)
                _status(response, "upload")
    except httpx.HTTPError:
        raise AudioStorageError("Audio storage upload is unavailable") from None


def download_original(reference: str, target: BinaryIO, checksum: str, settings: Any,
                      *, max_bytes: int | None = None) -> None:
    """Stream and verify into an unpublished local staging file."""
    validate_object_reference(reference)
    if not checksum or not re.fullmatch(r"[0-9a-f]{64}", checksum):
        raise AudioStorageError("A saved checksum is required to restore original audio")
    limit = audio_read_limit(max_bytes)
    if limit == 0:
        raise AudioReadLimitError("Stored audio exceeds the remaining read budget")
    deadline = _deadline(settings)
    try:
        with _client(settings) as client:
            _private_bucket(client, settings, deadline)
            bucket = quote(settings.supabase_storage_bucket, safe="")
            with client.stream("GET", f"object/authenticated/{bucket}/{reference}") as response:
                _status(response, "download")
                if response.status_code != 200 or response.headers.get("content-encoding", "identity") != "identity":
                    raise AudioStorageError("Audio storage returned an invalid original response")
                length = response.headers.get("content-length")
                try:
                    expected_size = int(length) if length is not None else None
                except ValueError:
                    raise AudioStorageError("Audio storage returned an invalid original size") from None
                if expected_size is not None and expected_size <= 0:
                    raise AudioStorageError("Audio storage returned an invalid original size")
                if expected_size is not None and expected_size > limit:
                    raise AudioReadLimitError("Stored audio exceeds the remaining read budget")
                digest, size = hashlib.sha256(), 0
                for chunk in response.iter_bytes(_BLOCK_SIZE):
                    _check_deadline(deadline)
                    size += len(chunk)
                    if size > limit:
                        raise AudioReadLimitError("Stored audio exceeds the remaining read budget")
                    digest.update(chunk)
                    target.write(chunk)
                if expected_size is not None and size != expected_size:
                    raise AudioStorageError("Stored audio download was incomplete")
                if digest.hexdigest() != checksum:
                    raise AudioStorageError("Stored audio checksum verification failed")
    except httpx.HTTPError:
        raise AudioStorageError("Audio storage download is unavailable") from None
