"""Real PostGIS flows with a durable HTTP object store and disposable cache.

Only the storage service is simulated; uploads, SQL, workers and playback use
the actual application. Generated recordings are synthetic fixtures.
"""
from dataclasses import dataclass, field
import shutil

import httpx
from pydantic import SecretStr
import pytest

from app import object_storage
from app.reconcile import reconcile
from tests.test_integration import accept, counts, device, location, process, upload
from tests.test_pcm_upload_api import accepted, raw_pcm, registered, wav_samples


pytestmark = pytest.mark.integration


@dataclass
class DurableBucket:
    objects: dict = field(default_factory=dict)
    requests: list = field(default_factory=list)
    fail_upload: bool = False

    def handle(self, request):
        self.requests.append((request.method, request.url.path))
        path = request.url.path
        if path == "/storage/v1/bucket/synthetic-originals":
            return httpx.Response(200, json={"id": "synthetic-originals", "public": False})
        upload_prefix = "/storage/v1/object/synthetic-originals/"
        download_prefix = "/storage/v1/object/authenticated/synthetic-originals/"
        if request.method == "POST" and path.startswith(upload_prefix):
            if self.fail_upload:
                return httpx.Response(503, json={"message": "synthetic storage outage"})
            key = path.removeprefix(upload_prefix)
            assert request.headers["x-upsert"] == "false"
            if key in self.objects:
                return httpx.Response(409)
            self.objects[key] = request.read()
            return httpx.Response(200, json={"Key": key})
        if request.method == "GET" and path.startswith(download_prefix):
            key = path.removeprefix(download_prefix)
            if key not in self.objects:
                return httpx.Response(404)
            return httpx.Response(200, content=self.objects[key], headers={"Content-Type": "audio/wav"})
        raise AssertionError("Unexpected storage request")


@pytest.fixture
def bucket(settings, monkeypatch):
    for name, value in {
        "audio_storage_backend": "supabase",
        "supabase_url": "https://synthetic.supabase.co",
        "supabase_service_role_key": SecretStr("synthetic-storage-only"),
        "supabase_storage_bucket": "synthetic-originals",
    }.items():
        monkeypatch.setattr(settings, name, value)
    store = DurableBucket()
    monkeypatch.setattr(object_storage, "_client", lambda settings: httpx.Client(
        base_url=settings.supabase_url + "/storage/v1/", transport=httpx.MockTransport(store.handle)))
    return store


def discard_cache(settings):
    shutil.rmtree(settings.audio_root / "originals", ignore_errors=True)


def test_cloud_original_restores_for_worker_download_and_duplicate(client, admin_headers, db, settings, bucket):
    _, dev = registered(client, admin_headers)
    saved = accepted(client, dev)
    assert len(bucket.objects) == 1
    original = next(iter(bucket.objects.values()))
    discard_cache(settings)
    process(settings)
    assert counts(db) == (1, 1, 1, 1)
    discard_cache(settings)
    response = client.get(f'/audio/{saved["id"]}/file', headers=admin_headers)
    assert response.status_code == 200 and response.content == original
    discard_cache(settings)
    assert wav_samples(client.get(saved["recording_url"], headers=admin_headers)) == raw_pcm()
    assert accepted(client, dev)["duplicate"] is True
    assert len(bucket.objects) == 1


def test_failed_cloud_upload_is_not_accepted_or_queued(client, admin_headers, db, settings, bucket):
    loc = location(client, admin_headers, name="SIMULATED cloud failure")
    dev = device(client, admin_headers, loc["id"])
    bucket.fail_upload = True
    response = upload(client, dev)
    assert response.status_code == 503
    assert counts(db) == (0, 0, 0, 0)
    assert not bucket.objects
    assert not list(settings.audio_root.rglob("*.wav"))
    assert not list(settings.audio_root.rglob("*.part"))


def test_reconcile_does_not_confuse_empty_cache_with_lost_originals(client, admin_headers, settings, bucket):
    dev = device(client, admin_headers, location(client, admin_headers)["id"])
    accept(client, dev)
    discard_cache(settings)
    before = list(bucket.requests)
    report = reconcile(settings, delete_orphans=True)
    assert report["missing_committed_files"] == []
    assert report["uncached_committed_files"] == list(bucket.objects)
    assert report["remote_storage_checked"] is False
    assert report["deleted_count"] == 0
    assert bucket.requests == before
    assert len(bucket.objects) == 1


def test_corrupt_cloud_original_is_never_published_or_played(client, admin_headers, settings, bucket):
    dev = device(client, admin_headers, location(client, admin_headers)["id"])
    saved = accept(client, dev)
    discard_cache(settings)
    key = next(iter(bucket.objects))
    original = bucket.objects[key]
    bucket.objects[key] = original[:-1] + bytes([original[-1] ^ 1])
    response = client.get(f'/audio/{saved["id"]}/file', headers=admin_headers)
    assert response.status_code == 503
    assert not list(settings.audio_root.rglob("*.wav"))
    bucket.objects[key] = original
    assert client.get(f'/audio/{saved["id"]}/file', headers=admin_headers).content == original


def test_group_playback_restores_ten_immutable_originals(client, admin_headers, settings, fake_clock, bucket):
    from tests.test_recording_groups import PCM, add, finish, groups
    _, dev = registered(client, admin_headers)
    add(client, dev, fake_clock, fake_clock.now())
    discard_cache(settings)
    finish(settings)
    group = groups(client, admin_headers)["items"][0]
    assert group["status"] == "ready"
    discard_cache(settings)
    response = client.get(f'/recordings/{group["id"]}/file', headers=admin_headers,
                          params={"revision": group["revision"]})
    assert wav_samples(response) == PCM * 10
    assert len(bucket.objects) == 10


def test_incident_analysis_and_playback_restore_originals(client, admin_headers, settings, fake_clock, bucket):
    from app.incident_analysis_jobs import run_once
    from tests.test_incident_analysis import fake_infer, fixture, state
    _, _, _, incident_id, _, _ = fixture(client, admin_headers, settings, fake_clock)
    discard_cache(settings)
    assert run_once(settings, infer=fake_infer)
    saved = state(client, admin_headers, incident_id)
    assert saved["status"] == "completed"
    discard_cache(settings)
    response = client.get(f"/incidents/{incident_id}/audio/file", headers=admin_headers,
                          params={"revision": saved["revision"]})
    assert response.status_code == 200, response.text
    assert response.content.startswith(b"RIFF")
    assert len(bucket.objects) == 3


def test_cold_incident_stops_fetching_when_source_budget_is_exhausted(
        client, admin_headers, settings, fake_clock, bucket, monkeypatch):
    from app import incident_audio
    from app.db import SessionLocal
    from app.models import Incident
    from tests.test_incident_analysis import fixture
    _, _, _, incident_id, originals, _ = fixture(client, admin_headers, settings, fake_clock)
    discard_cache(settings)
    monkeypatch.setattr(incident_audio, "MAX_SOURCE_BYTES", len(originals[0]) + 10)
    before = len(bucket.requests)
    with SessionLocal() as db:
        sources = incident_audio.collect_sources(db, db.get(Incident, incident_id), fake_clock.now(), settings)
        manifest, _ = incident_audio.build_manifest(sources, settings)
    fetches = [path for method, path in bucket.requests[before:]
               if method == "GET" and "/object/authenticated/" in path]
    assert len(fetches) == 2  # One accepted source and one rejected by its length.
    assert manifest["truncated"] is True
    assert len(list((settings.audio_root / "originals").rglob("*.wav"))) == 1
