"""Offline checks for safe, repeatable physical-device registration and diagnostics."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import stat
import sys
from types import SimpleNamespace
import urllib.error
from uuid import uuid4

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import hardware_common as common


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


register = module("register_hardware", "register-hardware.py")
check = module("check_hardware", "check-device.py")


@pytest.fixture
def args(tmp_path):
    return SimpleNamespace(url="http://localhost:8000", endpoint="https://192.168.1.10:8443",
        location_id=uuid4(), microphone_model="INMP441", config=tmp_path / ".local" / "device.json")


class FakeAPI:
    def __init__(self, location_id):
        self.location_id = str(location_id)
        self.location_name = "Physical test bench"
        self.devices, self.mutations = {}, []

    def __call__(self, base, token, path, payload=None, **kwargs):
        assert token == "private-admin-secret"
        if path == f"/locations/{self.location_id}":
            return {"id": self.location_id, "name": self.location_name, "timezone": "Asia/Kolkata"}
        if path == f"/locations/{self.location_id}/threshold":
            return {"current": {"threshold_type": "dbfs_rms", "interval_seconds": 1}}
        if path == "/devices" and payload:
            self.mutations.append(deepcopy(payload))
            device = {**deepcopy(payload), "enabled": True, "token": "private-device-secret"}
            self.devices[payload["id"]] = device
            return deepcopy(device)
        if path.startswith("/devices/"):
            identity = path.split("/")[-1]
            if identity not in self.devices:
                raise common.APIError("GET", path, 404)
            return deepcopy(self.devices[identity])
        raise AssertionError(path)


def test_registration_repeat_preserves_private_token_and_never_invents_calibration(args, monkeypatch, capsys):
    backend = FakeAPI(args.location_id)
    monkeypatch.setattr(register, "api", backend)
    first = register.prepare(args, "private-admin-secret")
    original = args.config.read_bytes()
    second = register.prepare(args, "private-admin-secret")
    assert first == second
    assert args.config.read_bytes() == original
    assert len(backend.mutations) == 1
    assert backend.mutations[0]["calibration"] is None
    assert "private-admin-secret" not in original.decode()
    assert "private-device-secret" in original.decode()
    assert "private-device-secret" not in json.dumps(first)
    assert stat.S_IMODE(args.config.stat().st_mode) == 0o600
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("label", ["SIMULATED North garden", "SYNTHETIC fixture", "Demo Library"])
def test_registration_refuses_simulated_location_without_writing(args, monkeypatch, label):
    backend = FakeAPI(args.location_id)
    backend.location_name = label
    monkeypatch.setattr(register, "api", backend)
    with pytest.raises(RuntimeError, match="physical location"):
        register.prepare(args, "private-admin-secret")
    assert not args.config.exists()
    assert backend.mutations == []


def test_lost_registration_response_does_not_create_another_device(args, monkeypatch):
    backend = FakeAPI(args.location_id)

    def lost_response(*values, **kwargs):
        result = backend(*values, **kwargs)
        if values[2] == "/devices":
            raise common.APIError("POST", "/devices", None)
        return result

    monkeypatch.setattr(register, "api", lost_response)
    with pytest.raises(common.APIError):
        register.prepare(args, "private-admin-secret")
    monkeypatch.setattr(register, "api", backend)
    with pytest.raises(RuntimeError, match="one-time token is unavailable"):
        register.prepare(args, "private-admin-secret")
    assert len(backend.mutations) == 1
    assert json.loads(args.config.read_text())["token"] is None


def test_registration_refuses_reassignment_without_modification(args, monkeypatch):
    backend = FakeAPI(args.location_id)
    monkeypatch.setattr(register, "api", backend)
    first = register.prepare(args, "private-admin-secret")
    backend.devices[first["device_id"]]["location_id"] = str(uuid4())
    with pytest.raises(RuntimeError, match="reassigned"):
        register.prepare(args, "private-admin-secret")
    assert len(backend.mutations) == 1


def test_registration_refuses_unrelated_config_without_server_calls(args, monkeypatch):
    common.save_private(args.config, {"schema": "other"})
    monkeypatch.setattr(register, "api", lambda *a, **k: pytest.fail("must not call server"))
    with pytest.raises(RuntimeError, match="not an UrbanEcho"):
        register.prepare(args, "secret")


def test_explicit_confirmed_board_profile_records_gpio_numbers_without_claiming_physical_test(args, monkeypatch):
    backend = FakeAPI(args.location_id)
    monkeypatch.setattr(register, "api", backend)
    args.board_profile = "esp32-wroom-32-inmp441"
    register.prepare(args, "private-admin-secret")
    board = common.load_config(args.config)["board"]
    assert board["model"] == "ESP-WROOM-32"
    assert (board["gpio_bclk"], board["gpio_ws"], board["gpio_data_in"]) == (26, 25, 33)
    assert board["channel"] == "left"
    assert board["sample_lsb"] == 8
    assert board["wiring_verified"] is False


def test_private_file_loader_refuses_world_readable_and_symlink(tmp_path):
    path = tmp_path / "private.json"
    common.save_private(path, {"schema": common.SCHEMA})
    path.chmod(0o644)
    with pytest.raises(RuntimeError, match="permissions"):
        common.load_config(path)
    link = tmp_path / "link.json"
    link.symlink_to(path)
    with pytest.raises(RuntimeError, match="symbolic"):
        common.save_private(link, {})


def test_admin_env_file_is_read_as_data_not_executed(tmp_path, monkeypatch):
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    path = tmp_path / ".env"
    path.write_text("DATABASE_URL=private-db\nADMIN_TOKEN='$(do-not-execute)'\n")
    assert common.admin_token(path) == "$(do-not-execute)"


def test_api_sanitizes_response_body_and_refuses_redirect(monkeypatch):
    secret_body = b"password=must-not-print-this"
    def open_failure(*args, **kwargs):
        raise urllib.error.HTTPError("https://user:secret@example.invalid", 403, "private message", {}, io.BytesIO(secret_body))
    monkeypatch.setattr(common.urllib.request, "build_opener", lambda *a: SimpleNamespace(open=open_failure))
    with pytest.raises(common.APIError) as failure:
        common.api("http://localhost:8000", "private-token", "/devices")
    assert "secret" not in str(failure.value)
    assert "password" not in str(failure.value)
    assert "HTTP 403" in str(failure.value)
    assert common.NoRedirect().redirect_request(None, None, 302, None, {}, "https://other.invalid") is None


def test_device_credential_only_checker_verifies_saved_audio_without_admin(args, monkeypatch):
    identifier, audio_id = str(uuid4()), uuid4()
    original = b"synthetic transport test, not physical audio"
    common.save_private(args.config, {"schema": common.SCHEMA, "id": identifier,
        "token": "private-device-secret", "api_url": "http://localhost:8000", "endpoint": "https://192.168.1.10:8443"})
    chunk = {"id": str(audio_id), "device_id": identifier, "location_id": str(args.location_id),
        "captured_at": "2026-10-09T05:00:00+00:00", "received_at": "2026-10-09T05:00:02+00:00",
        "sample_rate": 16000, "duration_seconds": 1, "audio_format": "wav_pcm_s24le_mono",
        "status": "completed", "measurements": [{"quality_status": "good", "calibration_status": "not_required"}],
        "checksum": hashlib.sha256(original).hexdigest()}
    calls = []

    def read_only_api(url, token, path, payload=None, **kwargs):
        calls.append(path)
        assert payload is None
        assert token == "private-device-secret"
        assert url == "https://192.168.1.10:8443"
        return original if path.endswith("/file") else deepcopy(chunk)

    monkeypatch.setattr(check, "api", read_only_api)
    monkeypatch.setattr(check, "admin_token", lambda *a: pytest.fail("must not need administrator token"))
    inputs = SimpleNamespace(config=args.config, url=None, audio_id=audio_id,
        admin_env_file=None, date=None, verify_audio=True)
    result = check.inspect(inputs, now=datetime(2026, 10, 9, 5, 0, 5, tzinfo=timezone.utc))
    assert result["recording"]["stored_file_checksum_matches"]
    assert result["recording"]["capture_to_receive_seconds"] == 2
    assert result["recording"]["capture_age_seconds"] == 5
    assert "private-device-secret" not in json.dumps(result)
    assert calls == [f"/audio/{audio_id}", f"/audio/{audio_id}/file"]


def test_recording_evidence_refuses_other_device():
    with pytest.raises(RuntimeError, match="different device"):
        check.recording_evidence({"device_id": str(uuid4())}, str(uuid4()), datetime.now(timezone.utc))


def test_registration_lock_prevents_concurrent_provisioning(tmp_path):
    path = tmp_path / "hardware.json"
    with common.locked_config(path):
        with pytest.raises(RuntimeError, match="Another registration"):
            with common.locked_config(path):
                pytest.fail("must not enter concurrent registration")


def test_registration_requires_tls_for_device_credentials(args, monkeypatch):
    args.endpoint = "http://192.168.1.10:8001"
    monkeypatch.setattr(register, "api", lambda *a, **k: pytest.fail("must not contact server"))
    with pytest.raises(RuntimeError, match="TLS"):
        register.prepare(args, "private-admin-secret")


@pytest.mark.parametrize("url", ["http://user:token@localhost:8000", "http://localhost/path", "http://localhost?token=secret", "file:///tmp/test", "http://localhost:invalid"])
def test_server_urls_cannot_embed_secrets_or_unexpected_paths(url):
    with pytest.raises(RuntimeError):
        common.base_url(url)


@pytest.mark.integration
def test_hardware_tools_use_actual_registration_processing_and_read_only_api_contract(
        client, admin_headers, settings, fake_clock, tmp_path, monkeypatch):
    from datetime import timedelta
    from app.worker import run_once
    from simulate import pcm24_wav

    created = client.post("/locations", headers=admin_headers, json={
        "name": "Physical integration test bench", "latitude": 12.9, "longitude": 77.5,
        "timezone": "Asia/Kolkata", "threshold_type": "dbfs_rms", "threshold_value": -20.0,
        "interval_seconds": 1,
    })
    assert created.status_code == 201
    location_id = created.json()["id"]

    def test_api(base, token, path, payload=None, **options):
        response = client.request("GET" if payload is None else "POST", path,
            headers={"Authorization": "Bearer " + token}, json=payload)
        if response.status_code >= 400:
            raise common.APIError("GET" if payload is None else "POST", path, response.status_code)
        return response.content if options.get("binary") else response.json()

    monkeypatch.setattr(register, "api", test_api)
    monkeypatch.setattr(check, "api", test_api)
    monkeypatch.setattr(check, "admin_token", lambda _: settings.admin_token)
    inputs = SimpleNamespace(url="http://localhost:8000", endpoint="https://192.168.1.10:8443",
        location_id=location_id, microphone_model="INMP441", config=tmp_path / "physical.json")
    prepared = register.prepare(inputs, settings.admin_token)
    state = common.load_config(inputs.config)
    audio = pcm24_wav(16000, 1, .05)
    uploaded = client.post("/audio", headers={"Authorization": "Bearer " + state["token"]},
        data={"metadata": json.dumps({"device_id": prepared["device_id"], "chunk_id": "test-one",
            "session_id": "test-session", "sequence": 0,
            "captured_at": (fake_clock.now() - timedelta(seconds=1)).isoformat()})},
        files={"file": ("test.wav", audio, "audio/wav")})
    assert uploaded.status_code == 202
    assert run_once()
    diagnostic = check.inspect(SimpleNamespace(config=inputs.config, url=None, audio_id=None,
        admin_env_file=Path("unused.env"), date=None, verify_audio=True), now=fake_clock.now())
    assert diagnostic["measurement_count"] == 1
    assert diagnostic["current_assignment"]["matches_private_configuration"]
    assert diagnostic["recording"]["stored_file_checksum_matches"]
    assert diagnostic["recording"]["location_id_at_capture"] == location_id
    assert diagnostic["recording"]["measurements"][0]["calibration_status"] == "not_required"
    assert diagnostic["daily_report"]["location_id"] == location_id
    assert diagnostic["daily_report"]["report"] is None  # The check has not generated anything.
    assert state["token"] not in json.dumps(diagnostic)
