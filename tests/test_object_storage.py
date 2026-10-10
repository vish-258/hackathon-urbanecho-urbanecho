"""Cloud originals remain immutable and verifiable when local cache disappears."""
import hashlib
import io
from pathlib import Path
from types import SimpleNamespace

import httpx
from pydantic import SecretStr, ValidationError
import pytest

from app.config import Settings
from app import object_storage
from app.object_storage import AudioReadLimitError, AudioStorageError, check_audio_bucket
from app.storage import cleanup_staged, finalize_audio, remove_audio, resolve_audio_path, stage_audio


REFERENCE = "originals/ab/" + "ab" * 16 + ".wav"
DATA = b"RIFF-test-original-bytes" * 4000
CHECKSUM = hashlib.sha256(DATA).hexdigest()
KEY = "unit-test-service-role-do-not-print"


@pytest.fixture
def cloud(tmp_path, monkeypatch):
    settings = SimpleNamespace(audio_root=tmp_path / "cache", max_upload_bytes=200000,
        audio_storage_backend="supabase", supabase_url="https://project.supabase.co",
        supabase_service_role_key=SecretStr(KEY), supabase_storage_bucket="original-audio",
        audio_storage_timeout_seconds=10)
    state = SimpleNamespace(objects={}, requests=[], public=False, bucket_status=200,
                            upload_status=200, download_status=200, download_headers={}, download_stream=None)

    def serve(request):
        state.requests.append(request)
        assert request.headers["authorization"] == f"Bearer {KEY}"
        assert request.headers["apikey"] == KEY
        path = request.url.path
        if path == "/storage/v1/bucket/original-audio":
            return httpx.Response(state.bucket_status, json={"id": "original-audio", "public": state.public})
        if request.method == "POST":
            assert request.headers["x-upsert"] == "false"
            assert request.headers["content-type"] == "audio/wav"
            assert path.startswith("/storage/v1/object/original-audio/")
            content = request.read()
            assert len(content) == int(request.headers["content-length"])
            if state.upload_status != 200:
                return httpx.Response(state.upload_status, json={"secret": KEY})
            if path in state.objects:
                return httpx.Response(409)
            state.objects[path] = content
            return httpx.Response(200, json={"Key": path})
        assert request.method == "GET"
        path = path.replace("/object/authenticated/", "/object/")
        if state.download_stream:
            return httpx.Response(state.download_status, headers=state.download_headers, stream=state.download_stream)
        return httpx.Response(state.download_status, content=state.objects.get(path, DATA), headers=state.download_headers)

    original_factory = object_storage._client

    def mock_client(configuration):
        client = original_factory(configuration)
        client._transport = httpx.MockTransport(serve)
        return client

    monkeypatch.setattr(object_storage, "_client", mock_client)
    return settings, state


def test_upload_then_cache_loss_restores_exact_original(cloud):
    settings, state = cloud
    staged = stage_audio(io.BytesIO(DATA), settings)
    reference = finalize_audio(staged, settings)
    assert not staged.path.exists()
    assert state.objects["/storage/v1/object/original-audio/" + reference] == DATA
    cache = settings.audio_root / reference
    cache.unlink()
    restored = resolve_audio_path(reference, settings, CHECKSUM)
    assert restored.read_bytes() == DATA
    assert list((settings.audio_root / ".staging").iterdir()) == []
    assert list((settings.audio_root / ".cache-staging").iterdir()) == []
    assert [r.method for r in state.requests] == ["GET", "POST", "GET", "GET"]


@pytest.mark.parametrize("status", [401, 403, 409, 413, 429, 500])
def test_upload_failure_never_returns_a_committable_reference(cloud, status):
    settings, state = cloud
    state.upload_status = status
    staged = stage_audio(io.BytesIO(DATA), settings)
    with pytest.raises(AudioStorageError) as failure:
        finalize_audio(staged, settings)
    assert KEY not in str(failure.value)
    assert staged.path.read_bytes() == DATA
    assert list((settings.audio_root / "originals").rglob("*.wav")) == []
    cleanup_staged(staged)


@pytest.mark.parametrize("public", [True, None, "false", 0])
def test_public_or_unverified_bucket_fails_closed(cloud, public):
    settings, state = cloud
    state.public = public
    with pytest.raises(AudioStorageError, match="private bucket"):
        check_audio_bucket(settings)
    assert len(state.requests) == 1


def test_corrupt_download_is_never_published(cloud):
    settings, state = cloud
    with pytest.raises(AudioStorageError, match="checksum"):
        resolve_audio_path(REFERENCE, settings, "0" * 64)
    assert not (settings.audio_root / REFERENCE).exists()
    assert list((settings.audio_root / ".cache-staging").iterdir()) == []


def test_corrupt_existing_cache_is_rejected_without_overwrite(cloud):
    settings, state = cloud
    cache = settings.audio_root / REFERENCE
    cache.parent.mkdir(parents=True)
    cache.write_bytes(b"corrupt")
    with pytest.raises(AudioStorageError, match="checksum"):
        resolve_audio_path(REFERENCE, settings, CHECKSUM)
    assert cache.read_bytes() == b"corrupt"
    assert state.requests == []


def test_missing_saved_checksum_does_not_download(cloud):
    settings, state = cloud
    with pytest.raises(AudioStorageError, match="saved checksum"):
        resolve_audio_path(REFERENCE, settings)
    assert state.requests == []


@pytest.mark.parametrize("reference", ["../outside.wav", "/tmp/outside.wav", "originals/ab/../../outside.wav",
    "originals/ab/%2e%2e/file.wav", "originals/ab/" + "cc" * 16 + ".wav", "originals/ab/user.wav"])
def test_unsafe_or_non_uuid_references_do_not_contact_storage(cloud, reference):
    settings, state = cloud
    with pytest.raises(ValueError):
        resolve_audio_path(reference, settings, CHECKSUM)
    assert state.requests == []


def test_symlink_escape_is_rejected(cloud, tmp_path):
    settings, state = cloud
    settings.audio_root.mkdir()
    (settings.audio_root / "originals").symlink_to(tmp_path)
    with pytest.raises(ValueError):
        resolve_audio_path(REFERENCE, settings, CHECKSUM)
    assert state.requests == []


def test_delete_is_local_only_and_does_not_restore_missing_cache(cloud):
    settings, state = cloud
    staged = stage_audio(io.BytesIO(DATA), settings)
    reference = finalize_audio(staged, settings)
    before = len(state.requests)
    remove_audio(reference, settings)
    remove_audio(reference, settings)
    assert len(state.requests) == before
    assert list(state.objects.values()) == [DATA]


@pytest.mark.parametrize("headers", [{"content-length": "100000001"}, {"content-length": "invalid"},
                                     {"content-length": "0"}, {"content-encoding": "gzip"}])
def test_invalid_response_metadata_cannot_publish_cache(cloud, headers):
    settings, state = cloud
    state.download_headers = headers
    with pytest.raises(AudioStorageError):
        resolve_audio_path(REFERENCE, settings, CHECKSUM)
    assert not (settings.audio_root / REFERENCE).exists()


def test_stream_size_is_bounded_without_content_length(cloud):
    settings, state = cloud
    class Oversized(httpx.SyncByteStream):
        def __iter__(self):
            yield b"x" * settings.max_upload_bytes
            yield b"x"
    state.download_stream = Oversized()
    with pytest.raises(AudioReadLimitError, match="read budget"):
        resolve_audio_path(REFERENCE, settings, CHECKSUM, max_bytes=settings.max_upload_bytes)
    assert not (settings.audio_root / REFERENCE).exists()


def test_interrupted_download_removes_partial_cache_and_redacts_provider_details(cloud):
    settings, state = cloud
    class Interrupted(httpx.SyncByteStream):
        def __iter__(self):
            yield DATA[:70000]
            raise httpx.ReadError(f"secret={KEY}")
    state.download_stream = Interrupted()
    with pytest.raises(AudioStorageError) as failure:
        resolve_audio_path(REFERENCE, settings, CHECKSUM)
    assert KEY not in str(failure.value)
    assert failure.value.__suppress_context__
    assert not (settings.audio_root / REFERENCE).exists()
    assert list((settings.audio_root / ".cache-staging").iterdir()) == []


def test_download_publishes_only_after_stream_completes(cloud):
    settings, state = cloud
    destination = settings.audio_root / REFERENCE
    class Checked(httpx.SyncByteStream):
        def __iter__(self):
            yield DATA[:70000]
            assert not destination.exists()
            yield DATA[70000:]
            assert not destination.exists()
    state.download_stream = Checked()
    assert resolve_audio_path(REFERENCE, settings, CHECKSUM).read_bytes() == DATA


def test_partial_http_response_is_rejected(cloud):
    settings, state = cloud
    state.download_status = 206
    with pytest.raises(AudioStorageError, match="invalid original response"):
        resolve_audio_path(REFERENCE, settings, CHECKSUM)
    assert not (settings.audio_root / REFERENCE).exists()


def test_concurrent_restore_does_not_replace_published_original(cloud, monkeypatch):
    settings, state = cloud
    from app import storage
    original_link = storage.os.link
    def race(source, destination):
        destination.write_bytes(DATA)
        original_link(source, destination)
    monkeypatch.setattr(storage.os, "link", race)
    assert resolve_audio_path(REFERENCE, settings, CHECKSUM).read_bytes() == DATA
    assert list((settings.audio_root / ".cache-staging").iterdir()) == []


def test_upload_timeout_redacts_request_details(cloud, monkeypatch):
    settings, state = cloud
    def timeout(*args, **kwargs):
        raise httpx.ConnectTimeout(f"Authorization: {KEY}")
    monkeypatch.setattr(httpx.Client, "send", timeout)
    staged = stage_audio(io.BytesIO(DATA), settings)
    with pytest.raises(AudioStorageError) as failure:
        finalize_audio(staged, settings)
    assert KEY not in str(failure.value)
    assert failure.value.__suppress_context__
    assert list((settings.audio_root / "originals").rglob("*.wav")) == []


def test_redirect_cannot_forward_credentials_to_another_origin(cloud):
    settings, state = cloud
    state.bucket_status = 302
    with pytest.raises(AudioStorageError, match="HTTP 302"):
        check_audio_bucket(settings)
    assert len(state.requests) == 1


def test_lowering_upload_limit_preserves_cached_and_remote_historical_playback(cloud):
    settings, state = cloud
    staged = stage_audio(io.BytesIO(DATA), settings)
    reference = finalize_audio(staged, settings)
    settings.max_upload_bytes = 100
    cached = resolve_audio_path(reference, settings, CHECKSUM)
    assert cached.read_bytes() == DATA
    cached.unlink()
    assert resolve_audio_path(reference, settings, CHECKSUM).read_bytes() == DATA
    from app.audio import AudioTooLargeError
    with pytest.raises(AudioTooLargeError):
        stage_audio(io.BytesIO(DATA), settings)


def test_content_length_over_remaining_budget_never_consumes_audio_body(cloud):
    settings, state = cloud
    class MustNotRead(httpx.SyncByteStream):
        def __iter__(self):
            raise AssertionError("Over-budget original body must not be consumed")
            yield b""
    state.download_headers = {"content-length": str(len(DATA))}
    state.download_stream = MustNotRead()
    with pytest.raises(AudioReadLimitError):
        resolve_audio_path(REFERENCE, settings, CHECKSUM, max_bytes=len(DATA) - 1)
    assert not (settings.audio_root / REFERENCE).exists()
    assert list((settings.audio_root / ".cache-staging").iterdir()) == []


def test_exhausted_budget_does_not_contact_remote_storage(cloud):
    settings, state = cloud
    with pytest.raises(AudioReadLimitError):
        resolve_audio_path(REFERENCE, settings, CHECKSUM, max_bytes=0)
    assert state.requests == []


@pytest.mark.parametrize("backend", ["filesystem", "supabase"])
def test_cached_stat_rejects_oversized_original_before_hashing(cloud, monkeypatch, backend):
    settings, state = cloud
    settings.audio_storage_backend = backend
    cached = settings.audio_root / REFERENCE
    cached.parent.mkdir(parents=True)
    cached.write_bytes(DATA)
    real_open = Path.open
    def guarded_open(path, *args, **kwargs):
        if path == cached:
            raise AssertionError("Over-budget original must not be read")
        return real_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", guarded_open)
    with pytest.raises(AudioReadLimitError):
        resolve_audio_path(REFERENCE, settings, CHECKSUM, max_bytes=len(DATA) - 1)
    assert state.requests == []


def test_settings_default_filesystem_and_redact_storage_secret():
    base = {"_env_file": None, "database_url": "postgresql+psycopg://localhost/noise_test", "admin_token": "x" * 32}
    assert Settings(**base).audio_storage_backend == "filesystem"
    settings = Settings(**base, audio_storage_backend="supabase", supabase_url="https://example.supabase.co/",
        supabase_service_role_key=KEY, supabase_storage_bucket="original-audio")
    assert settings.supabase_url == "https://example.supabase.co"
    assert KEY not in repr(settings)
    assert KEY not in settings.model_dump_json()
    for url in ("http://example.supabase.co", "https://user:secret@example.supabase.co", "https://example.supabase.co/other",
                "https://example.supabase.co?token=value"):
        with pytest.raises(ValidationError) as failure:
            Settings(**base, audio_storage_backend="supabase", supabase_url=url,
                supabase_service_role_key=KEY, supabase_storage_bucket="original-audio")
        assert KEY not in str(failure.value)
