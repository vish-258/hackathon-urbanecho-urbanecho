"""Offline checks for one-firmware board provisioning: chip ID registration and USB token storage."""
from copy import deepcopy
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
        self.replies, self.stored = [], None

    def write_line(self, text):
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
    def __init__(self, devices=(), rule=("dbfs_rms", 1)):
        self.devices, self.created = list(devices), []
        self.rule = {"threshold_type": rule[0], "interval_seconds": rule[1]}

    def __call__(self, base, token, path, payload=None, **kwargs):
        assert token == "private-admin-secret"
        if path.startswith("/locations?"):
            return {"items": [LOCATION], "total": 1}
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
