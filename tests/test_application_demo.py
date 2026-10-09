"""Offline checks for explicit demo provisioning and safe repeat behavior."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import stat
import sys
from types import SimpleNamespace

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
try:
    spec = importlib.util.spec_from_file_location("application_demo", SCRIPTS / "demo-application.py")
    demo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(demo)
finally:
    sys.path.pop(0)


@pytest.fixture
def args(tmp_path):
    return SimpleNamespace(credentials=tmp_path / ".local" / "fixture.json", url="http://example.invalid",
                           duration=90, tick=3, breach_after=15)


class FakeAPI:
    def __init__(self):
        self.locations, self.devices, self.mutations = {}, {}, []

    def __call__(self, base, admin, path, payload=None, method=None):
        method = method or ("GET" if payload is None else "POST")
        if method != "GET":
            self.mutations.append((method, path, deepcopy(payload)))
        if path == "/locations" and method == "POST":
            identity = "location-" + str(len(self.locations))
            self.locations[identity] = {"id": identity, **deepcopy(payload)}
            return deepcopy(self.locations[identity])
        if path.startswith("/locations/"):
            identity = path.split("/")[2]
            if path.endswith("/threshold"):
                return {"current": {**demo.THRESHOLD}, "latest_revision": 1}
            return deepcopy(self.locations[identity])
        if path == "/devices" and method == "POST":
            identity = "device-" + str(len(self.devices))
            self.devices[identity] = {"id": identity, "token": "private-device-token-" + identity,
                                     "enabled": True, "config_revision": 1, **deepcopy(payload)}
            return deepcopy(self.devices[identity])
        if path.startswith("/devices/"):
            identity = path.split("/")[2]
            if method == "PATCH":
                assert payload["expected_revision"] == self.devices[identity]["config_revision"]
                self.devices[identity].update({k: deepcopy(v) for k, v in payload.items() if k != "expected_revision"})
                self.devices[identity]["config_revision"] += 1
            return deepcopy(self.devices[identity])
        raise AssertionError((method, path))


def test_repeat_reuses_three_devices_without_exposing_tokens(args, monkeypatch, capsys):
    backend = FakeAPI()
    monkeypatch.setattr(demo, "api", backend)
    first = demo.prepare(args, "private-admin-token", 100)
    assert len(backend.locations) == len(backend.devices) == 3
    assert all(row["name"].startswith("SIMULATED") for row in backend.locations.values())
    assert stat.S_IMODE(args.credentials.stat().st_mode) == 0o600
    second = demo.prepare(args, "private-admin-token", 100)
    assert second == first
    assert len(backend.mutations) == 6  # No extra provisioning or configuration edits.
    output = capsys.readouterr().out
    assert "private-admin-token" not in output
    for device in backend.devices.values():
        assert device["token"] in args.credentials.read_text()
    assert "private-device-token" not in output


def test_expired_fixture_calibration_is_refreshed_without_reprovisioning(args, monkeypatch):
    backend = FakeAPI()
    monkeypatch.setattr(demo, "api", backend)
    state = demo.prepare(args, "admin", 100)
    identity = state["stations"]["garden"]["id"]
    backend.devices[identity]["calibration"]["valid_until"] = "2020-01-01T00:00:00+00:00"
    demo.prepare(args, "admin", 100)
    assert len(backend.devices) == len(backend.locations) == 3
    assert len(backend.mutations) == 7
    assert backend.mutations[-1][0:2] == ("PATCH", "/devices/" + identity)
    assert backend.devices[identity]["config_revision"] == 2


def test_reassigned_fixture_device_is_not_silently_overwritten(args, monkeypatch):
    backend = FakeAPI()
    monkeypatch.setattr(demo, "api", backend)
    state = demo.prepare(args, "admin", 100)
    identity = state["stations"]["garden"]["id"]
    backend.devices[identity]["location_id"] = "real-other-location"
    with pytest.raises(RuntimeError, match="changed or reassigned"):
        demo.prepare(args, "admin", 100)
    assert len(backend.mutations) == 6


def test_unrelated_credentials_file_is_rejected_before_any_server_write(args, monkeypatch):
    args.credentials.parent.mkdir()
    args.credentials.write_text(json.dumps({"devices": []}))
    backend = FakeAPI()
    monkeypatch.setattr(demo, "api", backend)
    with pytest.raises(RuntimeError, match="different fixture"):
        demo.prepare(args, "admin", 100)
    assert backend.mutations == []


def test_stale_window_must_fit_before_provisioning(args, monkeypatch):
    calls = []
    def api(*values):
        calls.append(values[2])
        return {"data_stale_seconds": 120}
    monkeypatch.setattr(demo, "api", api)
    with pytest.raises(RuntimeError, match="at least 140"):
        demo.run(args, "admin")
    assert calls == ["/locations/status?limit=1"]
    assert not args.credentials.exists()


def test_sender_does_not_claim_historical_measurement_as_live(args, monkeypatch):
    item = {"id": "fixture-device", "token": "private", "name": "SIMULATED"}
    sender = demo.Sender(args, "admin", item, {55: b"test-only"})
    monkeypatch.setattr(demo, "upload", lambda *unused: (202, {"id": "chunk"}))
    monkeypatch.setattr(demo._fixture, "wait_processed", lambda *unused: {
        "value_db": 55, "evaluation": {"status": "eligible_historical", "diagnostic": "old_capture"}})
    with pytest.raises(RuntimeError, match="not live-eligible: old_capture"):
        sender.send(55, datetime.now(timezone.utc) - timedelta(seconds=2))
    assert sender.sequence == 0
