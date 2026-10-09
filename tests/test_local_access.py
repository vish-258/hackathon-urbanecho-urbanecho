"""The opt-in local workspace never publishes or bypasses device credentials."""
import json
import uuid

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app import auth
from app.application_api import router
from app.config import Settings


ORIGIN = "http://localhost:8000"
BROWSER_HEADERS = {"Origin": ORIGIN, "X-Soundwatch-Local": "1", "Sec-Fetch-Site": "same-origin"}
READ_HEADERS = {"X-Soundwatch-Local": "1", "Sec-Fetch-Site": "same-origin"}
SYNTHETIC_ADMIN = "synthetic-local-test-admin-" + "x" * 40


@pytest.fixture
def local_settings(monkeypatch):
    settings = Settings(_env_file=None, database_url="postgresql+psycopg://unused:unused@localhost/noise_test",
                        admin_token=SYNTHETIC_ADMIN, local_browser_access=True)
    monkeypatch.setattr(auth, "get_settings", lambda: settings)
    return settings


@pytest.fixture
def local_client(local_settings):
    app = FastAPI()
    app.include_router(router)

    @app.post("/management-probe", dependencies=[Depends(auth.require_admin)])
    def management_probe():
        return {"changed": True}

    with TestClient(app, base_url=ORIGIN) as instance:
        yield instance


def connect(client, headers=None):
    result = client.post("/app/session", headers=BROWSER_HEADERS if headers is None else headers, json={})
    assert result.status_code == 200, result.text
    return result


def test_local_access_defaults_off():
    settings = Settings(_env_file=None, database_url="postgresql+psycopg://unused:unused@localhost/noise_test",
                        admin_token=SYNTHETIC_ADMIN)
    assert settings.local_browser_access is False


def test_disabled_mode_cannot_issue_or_use_session(local_client, local_settings):
    connect(local_client)
    local_settings.local_browser_access = False
    assert local_client.post("/app/session", headers=BROWSER_HEADERS).status_code == 403
    assert local_client.get("/capabilities", headers=READ_HEADERS).status_code == 401


def test_session_automatically_opens_read_and_write_without_returning_admin_token(local_client):
    response = connect(local_client)
    assert response.json() == {"access": "local"}
    assert response.headers["cache-control"] == "no-store"
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie and "Path=/" in cookie
    assert f"Max-Age={auth.LOCAL_SESSION_SECONDS}" in cookie
    assert SYNTHETIC_ADMIN not in response.text + cookie
    assert local_client.get("/capabilities", headers=READ_HEADERS).status_code == 200
    assert local_client.post("/management-probe", headers=BROWSER_HEADERS).json() == {"changed": True}


@pytest.mark.parametrize("host", ["localhost:8000", "127.0.0.1:8000", "[::1]:8000"])
def test_exact_loopback_hosts_supported(local_client, host):
    headers = {**BROWSER_HEADERS, "Host": host, "Origin": f"http://{host}"}
    connect(local_client, headers)
    assert local_client.get("/capabilities", headers=headers).status_code == 200


@pytest.mark.parametrize("change", [
    {"Host": "example.com", "Origin": "http://example.com"},
    {"Host": "localhost.evil.test:8000", "Origin": "http://localhost.evil.test:8000"},
    {"Host": "192.168.1.25:8000", "Origin": "http://192.168.1.25:8000"},
    {"Host": "localhost:8001", "Origin": "http://localhost:8001"},
    {"Host": "localhost"},
    {"Host": "localhost.:8000"},
    {"Origin": "http://localhost:8001"},
    {"Origin": "http://127.0.0.1:8000"},
    {"Origin": "https://localhost:8000"},
    {"Origin": "null"},
    {"Origin": "https://evil.test"},
    {"Sec-Fetch-Site": "cross-site"},
    {"Sec-Fetch-Site": "same-site"},
    {"Sec-Fetch-Site": "none"},
    {"X-Soundwatch-Local": "0"},
    {"Forwarded": "for=127.0.0.1;host=localhost:8000"},
    {"X-Forwarded-For": "127.0.0.1"},
    {"X-Forwarded-Host": "localhost:8000"},
    {"X-Forwarded-Proto": "http"},
    {"X-Forwarded-Port": "8000"},
])
def test_foreign_or_proxied_requests_cannot_issue_or_use_session(local_client, change):
    connect(local_client)
    headers = {**BROWSER_HEADERS, **change}
    assert local_client.post("/app/session", headers=headers).status_code == 403
    assert local_client.get("/capabilities", headers=headers).status_code == 401
    assert local_client.post("/management-probe", headers=headers).status_code == 401


@pytest.mark.parametrize("missing", ["Origin", "X-Soundwatch-Local"])
def test_handshake_and_mutations_require_origin_and_explicit_header(local_client, missing):
    connect(local_client)
    headers = {key: value for key, value in BROWSER_HEADERS.items() if key != missing}
    assert local_client.post("/app/session", headers=headers).status_code == 403
    assert local_client.post("/management-probe", headers=headers).status_code == 401


def test_ambient_cookie_alone_never_grants_access(local_client):
    connect(local_client)
    assert local_client.get("/capabilities").status_code == 401
    assert local_client.get("/capabilities", headers={"Origin": ORIGIN}).status_code == 401


@pytest.mark.parametrize("duplicate", ["host", "origin", "x-soundwatch-local", "sec-fetch-site"])
def test_ambiguous_headers_rejected(local_client, duplicate):
    headers = {"host": "localhost:8000", **{key.lower(): value for key, value in BROWSER_HEADERS.items()}}
    rows = list(headers.items()) + [(duplicate, headers[duplicate])]
    assert local_client.post("/app/session", headers=rows).status_code == 403


def test_cookie_is_bound_to_exact_host_and_port(local_client, local_settings):
    connect(local_client)
    assert local_client.get("/capabilities", headers={**READ_HEADERS, "Host": "127.0.0.1:8000"}).status_code == 401
    local_settings.local_browser_port = 8001
    assert local_client.get("/capabilities", headers={**READ_HEADERS, "Host": "localhost:8001"}).status_code == 401


def test_expiration_and_automatic_new_session(local_client, monkeypatch):
    now = [2000000000.0]
    monkeypatch.setattr(auth.time, "time", lambda: now[0])
    connect(local_client)
    first = local_client.cookies.get(auth.LOCAL_SESSION_COOKIE)
    now[0] += auth.LOCAL_SESSION_SECONDS
    assert local_client.get("/capabilities", headers=READ_HEADERS).status_code == 401
    connect(local_client)
    assert local_client.cookies.get(auth.LOCAL_SESSION_COOKIE) != first
    assert local_client.get("/capabilities", headers=READ_HEADERS).status_code == 200


@pytest.mark.parametrize("cookie", ["", "invalid", "v1.999999999999." + "a" * 32 + "." + "a" * 64])
def test_malformed_and_unbounded_cookie_values_rejected(local_client, cookie):
    local_client.cookies.set(auth.LOCAL_SESSION_COOKIE, cookie)
    assert local_client.get("/capabilities", headers=READ_HEADERS).status_code == 401


def test_cookie_signature_tampering_and_server_key_rotation(local_client, local_settings):
    connect(local_client)
    cookie = local_client.cookies.get(auth.LOCAL_SESSION_COOKIE)
    tampered = cookie[:-1] + ("a" if cookie[-1] != "a" else "b")
    local_client.cookies.clear()
    local_client.cookies.set(auth.LOCAL_SESSION_COOKIE, tampered)
    assert local_client.get("/capabilities", headers=READ_HEADERS).status_code == 401
    local_client.cookies.clear()
    local_client.cookies.set(auth.LOCAL_SESSION_COOKIE, cookie)
    local_settings.admin_token = "a-different-synthetic-admin-token-" + "y" * 32
    assert local_client.get("/capabilities", headers=READ_HEADERS).status_code == 401


def test_bearer_clients_unchanged_and_wrong_bearer_not_masked_by_cookie(local_client, local_settings):
    connect(local_client)
    assert local_client.get("/capabilities", headers={**READ_HEADERS, "Authorization": "Bearer incorrect"}).status_code == 403
    local_settings.local_browser_access = False
    assert local_client.get("/capabilities", headers={"Authorization": f"Bearer {SYNTHETIC_ADMIN}",
                            "Host": "example.test"}).status_code == 200


@pytest.mark.integration
def test_local_session_real_management_and_sse_preflight_keep_device_upload_protected(client, settings, monkeypatch):
    from scripts.simulate import pcm24_wav
    from test_integration import device, location, metadata, upload

    monkeypatch.setattr(settings, "local_browser_access", True)
    client.base_url = ORIGIN
    connect(client)
    loc = location(client, BROWSER_HEADERS, name="Local session integration fixture")
    dev = device(client, BROWSER_HEADERS, loc["id"])
    assert client.get("/locations", headers=READ_HEADERS).json()["total"] == 1
    # Missing cursor is a deliberate finite preflight error AFTER authentication.
    stream = client.get("/events/stream", headers=READ_HEADERS)
    assert stream.status_code == 409 and stream.json()["error"]["message"]["resync_required"] is True
    files = {"file": ("synthetic.wav", pcm24_wav(16000, 1, .5), "audio/wav")}
    assert client.post("/audio", headers=BROWSER_HEADERS, files=files,
                       data={"metadata": json.dumps(metadata(dev))}).status_code == 401
    assert client.get(f"/audio/{uuid.uuid4()}", headers=READ_HEADERS).status_code == 401
    # A newly provisioned device still works with its independent credential.
    assert upload(client, dev).status_code == 202
