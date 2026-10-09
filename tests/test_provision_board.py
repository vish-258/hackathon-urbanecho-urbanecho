"""Offline checks for one-firmware board provisioning: chip ID registration and USB token storage."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
from types import SimpleNamespace

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import hardware_common as common

spec = importlib.util.spec_from_file_location("provision_board", SCRIPTS / "provision-board.py")
provision = importlib.util.module_from_spec(spec)
spec.loader.exec_module(provision)

BOARD = "ESP-20500D114084"
SECRET = "private-device-secret-0123456789abcdef"
LOCATION = {"id": "72feaeed-50a8-46e6-a9a9-7c9deda4c4bf", "name": "Game"}


class FakeBoard:
    def __init__(self, provisioned=False, refuse=False):
        self.provisioned, self.refuse = provisioned, refuse
        self.replies, self.stored, self.commands = [], None, []

    def write_line(self, text):
        self.commands.append(text)
        if text == "IDENTITY":
            self.replies += ["boot noise", f"IDENTITY {BOARD} {'provisioned' if self.provisioned else 'unprovisioned'}"]
        elif text.startswith("PROVISION "):
            if self.refuse:
                self.replies.append("PROVISION REFUSED: could not store token")
            else:
                self.stored = text.removeprefix("PROVISION ")
                self.replies.append(f"PROVISIONED {BOARD}: restarting")

    def read_line(self, deadline):
        return self.replies.pop(0) if self.replies else None


class FakeAPI:
    def __init__(self, devices=(), rule=("dbfs_rms", 1), location=LOCATION):
        self.devices, self.created = list(devices), []
        self.rule = {"threshold_type": rule[0], "interval_seconds": rule[1]}
        self.location = deepcopy(location)
        self.requests = []

    def __call__(self, base, token, path, payload=None, **kwargs):
        assert token == "private-admin-secret"
        self.requests.append(path)
        if path.startswith("/locations?"):
            return {"items": [self.location], "total": 1}
        if path == f"/locations/{self.location['id']}":
            return deepcopy(self.location)
        if path == f"/locations/{LOCATION['id']}/threshold":
            return {"current": self.rule}
        if path.startswith("/devices?"):
            return {"items": deepcopy(self.devices), "total": len(self.devices)}
        if path == "/devices" and payload:
            self.created.append(deepcopy(payload))
            device = {**payload, "id": "9b1c9bab-aad6-43a5-8030-f4770cea3088", "enabled": True}
            self.devices.append(device)
            return {**device, "token": SECRET}
        raise AssertionError(path)


@pytest.fixture
def args(tmp_path):
    return SimpleNamespace(url="http://localhost:8000", location="game", location_id=None,
                           microphone_model="INMP441", backup_dir=tmp_path / ".local" / "devices")


def test_new_board_is_registered_under_chip_id_and_token_goes_only_to_board_and_backup(args, monkeypatch, capsys):
    server, board = FakeAPI(), FakeBoard()
    monkeypatch.setattr(provision, "api", server)
    result = provision.provision(args, "private-admin-secret", board)
    assert result["changed"] and server.created == [{"external_id": BOARD, "location_id": LOCATION["id"], "microphone_model": "INMP441"}]
    assert board.stored == SECRET
    backup = args.backup_dir / f"{BOARD}.json"
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600
    assert json.loads(backup.read_text())["token"] == SECRET
    assert SECRET not in capsys.readouterr().out


def test_registered_and_provisioned_board_is_left_unchanged(args, monkeypatch):
    server = FakeAPI(devices=[{"id": "existing", "external_id": BOARD, "location_id": LOCATION["id"]}])
    board = FakeBoard(provisioned=True)
    monkeypatch.setattr(provision, "api", server)
    assert provision.provision(args, "private-admin-secret", board)["changed"] is False
    assert server.created == [] and board.stored is None


def test_registered_board_without_token_is_restored_from_private_backup(args, monkeypatch):
    server = FakeAPI(devices=[{"id": "existing", "external_id": BOARD, "location_id": LOCATION["id"]}])
    common.save_private(args.backup_dir / f"{BOARD}.json", {"schema": provision.BACKUP_SCHEMA, "external_id": BOARD, "token": SECRET})
    board = FakeBoard()
    monkeypatch.setattr(provision, "api", server)
    assert provision.provision(args, "private-admin-secret", board)["changed"]
    assert server.created == [] and board.stored == SECRET


def test_registered_board_without_token_or_backup_is_not_reregistered(args, monkeypatch):
    server = FakeAPI(devices=[{"id": "existing", "external_id": BOARD, "location_id": LOCATION["id"]}])
    board = FakeBoard()
    monkeypatch.setattr(provision, "api", server)
    with pytest.raises(RuntimeError, match="cannot be recovered"):
        provision.provision(args, "private-admin-secret", board)
    assert server.created == [] and board.stored is None


def test_board_refusal_keeps_registration_backup_for_retry(args, monkeypatch):
    monkeypatch.setattr(provision, "api", FakeAPI())
    with pytest.raises(RuntimeError, match="refused"):
        provision.provision(args, "private-admin-secret", FakeBoard(refuse=True))
    assert json.loads((args.backup_dir / f"{BOARD}.json").read_text())["token"] == SECRET


def test_unknown_location_stops_before_touching_board(args, monkeypatch):
    args.location = "Nowhere"
    board = FakeBoard()
    monkeypatch.setattr(provision, "api", FakeAPI())
    with pytest.raises(RuntimeError, match="No location"):
        provision.provision(args, "private-admin-secret", board)
    assert board.replies == [] and board.stored is None


@pytest.mark.parametrize("label", ["SIMULATED", "synthetic", "DEMO", "Demonstration"])
@pytest.mark.parametrize("by_id", [False, True])
def test_simulated_location_is_rejected_before_board_or_registration(args, monkeypatch, label, by_id):
    location = {**LOCATION, "name": f"{label} · Workshop"}
    args.location, args.location_id = (None, location["id"]) if by_id else (location["name"], None)
    server, board = FakeAPI(location=location), FakeBoard()
    monkeypatch.setattr(provision, "api", server)
    with pytest.raises(RuntimeError, match="physical location"):
        provision.provision(args, "private-admin-secret", board)
    assert server.created == [] and board.commands == [] and board.stored is None
    assert not args.backup_dir.exists()


@pytest.mark.parametrize("model", ["SIMULATED INMP441", "synthetic microphone", "DEMO", "Demonstration board"])
def test_simulated_microphone_is_rejected_before_server_or_board(args, monkeypatch, model):
    args.microphone_model = model
    server, board = FakeAPI(), FakeBoard()
    monkeypatch.setattr(provision, "api", server)
    with pytest.raises(RuntimeError, match="physical microphone"):
        provision.provision(args, "private-admin-secret", board)
    assert server.requests == [] and board.commands == [] and board.stored is None


def test_cli_validates_physical_location_before_opening_usb(monkeypatch):
    server = FakeAPI(location={**LOCATION, "name": "SIMULATED Workshop"})
    monkeypatch.setattr(provision, "api", server)
    monkeypatch.setattr(provision, "admin_token", lambda *_: "private-admin-secret")
    monkeypatch.setattr(sys, "argv", ["provision-board.py", "--location", "SIMULATED Workshop", "--port", "fake-usb"])
    def no_usb(*_):
        pytest.fail("A rejected location must not open the USB port")
    monkeypatch.setattr(provision, "SerialPort", no_usb)
    with pytest.raises(RuntimeError, match="physical location"):
        provision.main()


UPLOAD_ID = "46c37e5b-0548-4e80-8fe0-a0d02990bf4e"
SERVER_ID = "9b1c9bab-aad6-43a5-8030-f4770cea3088"
STARTED = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)
UPLOAD_LINE = f"seq=1 http=200 attempt=1 rms=100 peak=300 clipped=0 saved_audio_id={UPLOAD_ID}"


def uploaded_recording(**overrides):
    return {"id": UPLOAD_ID, "device_id": SERVER_ID, "status": "pending",
            "captured_at": (STARTED + timedelta(seconds=5)).isoformat(),
            "received_at": (STARTED + timedelta(seconds=7)).isoformat(), **overrides}


def test_diagnostic_contact_without_audio_does_not_confirm_upload(monkeypatch, capsys):
    board, requests = FakeBoard(), []
    board.replies = ["BOOT: board is connected", "status http=200", "MIC SILENT: check wiring"]
    def diagnostic_api(url, admin, path):
        requests.append(path)
        return {"last_contact_at": (STARTED + timedelta(seconds=2)).isoformat()}
    monkeypatch.setattr(provision, "api", diagnostic_api)
    result = provision.wait_for_upload("http://localhost:8000", "fake-admin", SERVER_ID, STARTED, 1, board)
    provision.report_upload(BOARD, result)
    assert result is None and requests == []
    assert "No new saved recording confirmed" in capsys.readouterr().out


@pytest.mark.parametrize("overrides", [
    {"device_id": "a different device"},
    {"id": "a different recording"},
    {"captured_at": (STARTED - timedelta(seconds=1)).isoformat()},
    {"received_at": (STARTED - timedelta(seconds=1)).isoformat()},
    {"captured_at": "2026-10-09T12:00:05"},
])
def test_other_device_or_stale_recording_cannot_confirm_upload(monkeypatch, overrides):
    board = FakeBoard()
    board.replies = [UPLOAD_LINE]
    monkeypatch.setattr(provision, "api", lambda *a: uploaded_recording(**overrides))
    assert provision.wait_for_upload("http://localhost:8000", "fake-admin", SERVER_ID, STARTED, 1, board) is None


@pytest.mark.parametrize("status, message", [
    ("pending", "Sound-level processing: pending"),
    ("processing", "Sound-level processing: processing"),
    ("completed", "Sound-level processing completed"),
    ("failed", "sound-level processing failed"),
])
def test_saved_audio_confirmation_separates_processing_status(monkeypatch, capsys, status, message):
    board, requests = FakeBoard(), []
    board.replies = [UPLOAD_LINE]
    def saved_audio_api(url, admin, path):
        requests.append(path)
        return uploaded_recording(status=status)
    monkeypatch.setattr(provision, "api", saved_audio_api)
    result = provision.wait_for_upload("http://localhost:8000", "fake-admin", SERVER_ID, STARTED, 1, board)
    assert result == {"audio_id": UPLOAD_ID, "processing_status": status}
    assert requests == [f"/audio/{UPLOAD_ID}"]
    provision.report_upload(BOARD, result)
    output = capsys.readouterr().out
    assert "new audio recording saved" in output and message in output
    if status != "completed":
        assert "Sound-level processing completed" not in output


def test_ack_unknown_to_selected_server_does_not_confirm_upload(monkeypatch):
    board = FakeBoard()
    board.replies = [UPLOAD_LINE]
    def absent_audio(*_):
        raise common.APIError("GET", f"/audio/{UPLOAD_ID}", 404)
    monkeypatch.setattr(provision, "api", absent_audio)
    assert provision.wait_for_upload("http://localhost:8000", "fake-admin", SERVER_ID, STARTED, 1, board) is None


def test_incompatible_location_rule_is_reported(args, monkeypatch, capsys):
    monkeypatch.setattr(provision, "api", FakeAPI(rule=("spl_z_leq", 5)))
    provision.provision(args, "private-admin-secret", FakeBoard())
    assert "spl_z_leq every 5 s" in capsys.readouterr().out


def test_backup_must_be_private(args):
    path = args.backup_dir / f"{BOARD}.json"
    common.save_private(path, {"schema": provision.BACKUP_SCHEMA, "external_id": BOARD, "token": SECRET})
    path.chmod(0o644)
    with pytest.raises(RuntimeError, match="private"):
        provision.read_backup(path, BOARD)


def test_serial_link_reads_identity_through_boot_noise():
    controller, terminal = os.openpty()
    try:
        port = provision.SerialPort(os.ttyname(terminal))
        os.write(controller, f"ets Jul 29 2019\r\nIDENTITY {BOARD} unprovisioned\r\n".encode())
        assert provision.board_identity(port, timeout=3) == (BOARD, False)
        assert os.read(controller, 64).startswith(b"IDENTITY")
        port.close()
    finally:
        os.close(controller)
        os.close(terminal)
