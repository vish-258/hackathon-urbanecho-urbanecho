#!/usr/bin/env python3
"""Register the USB-connected board under its chip ID and store its token on the board.

Flash the shared Arduino firmware first (empty UE_DEVICE_TOKEN, automatic MAC identity). The board
reports ESP-<chip MAC>; this tool registers that ID at a location, keeps a private backup,
and sends the token over USB. The token is never printed.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import glob
import json
import os
from pathlib import Path
import re
import select
import shutil
import stat
import struct
import subprocess
import sys
import termios
import time
from uuid import UUID

from hardware_common import APIError, admin_token, api, base_url, is_simulated, locked_config, save_private

IDENTITY = re.compile(r"^IDENTITY ([A-Za-z0-9_-]{1,36}) (provisioned|unprovisioned)$")
TOKEN = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
SAVED_AUDIO = re.compile(r"(?:^|\s)saved_audio_id=([0-9a-fA-F-]{36})(?:\s|$)")
BACKUP_SCHEMA = "urbanecho-board-v1"
PORT_PATTERNS = ("/dev/cu.usbserial*", "/dev/cu.SLAB_USBtoUART*", "/dev/cu.wchusbserial*",
                 "/dev/cu.usbmodem*", "/dev/ttyUSB*", "/dev/ttyACM*")


def holders(path: str) -> list[str]:
    """Other processes holding the port would split the board's replies with us."""
    if not shutil.which("lsof"):
        return []
    result = subprocess.run(["lsof", "-t", path], capture_output=True, text=True)
    return [pid for pid in result.stdout.split() if pid != str(os.getpid())]


class SerialPort:
    """Raw 115200-baud link. DTR/RTS are released so opening it does not reset the board."""

    def __init__(self, path: str):
        others = holders(path)
        if others:
            raise RuntimeError(f"{path} is in use (PID {', '.join(others)}). Close the serial monitor and retry.")
        try:
            self.fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        except OSError:
            raise RuntimeError(f"Cannot open {path}. Check the USB cable or choose --port.") from None
        _, _, cflag, _, _, _, cc = termios.tcgetattr(self.fd)
        cflag &= ~(termios.CSIZE | termios.PARENB | termios.CSTOPB | termios.HUPCL | getattr(termios, "CRTSCTS", 0))
        cflag |= termios.CS8 | termios.CREAD | termios.CLOCAL
        cc[termios.VMIN], cc[termios.VTIME] = 0, 0
        termios.tcsetattr(self.fd, termios.TCSANOW, [0, 0, cflag, 0, termios.B115200, termios.B115200, cc])
        try:
            fcntl.ioctl(self.fd, termios.TIOCMBIC, struct.pack("I", termios.TIOCM_DTR | termios.TIOCM_RTS))
        except OSError:
            pass  # Pseudo-terminals have no modem lines; a real adapter accepts this.
        termios.tcflush(self.fd, termios.TCIFLUSH)
        self.pending = b""

    def write_line(self, text: str):
        data = (text + "\n").encode()
        while data:
            try:
                data = data[os.write(self.fd, data):]
            except BlockingIOError:
                select.select([], [self.fd], [], 1)
        termios.tcdrain(self.fd)

    def read_line(self, deadline: float) -> str | None:
        while b"\n" not in self.pending:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            if not select.select([self.fd], [], [], remaining)[0]:
                continue
            try:
                chunk = os.read(self.fd, 4096)
            except BlockingIOError:
                continue
            if not chunk:
                raise RuntimeError("The board disconnected from USB.")
            self.pending += chunk
        line, _, self.pending = self.pending.partition(b"\n")
        return line.decode("utf-8", "replace").strip("\r")

    def close(self):
        os.close(self.fd)


def find_port() -> str:
    ports = sorted({path for pattern in PORT_PATTERNS for path in glob.glob(pattern)})
    if len(ports) != 1:
        found = ", ".join(ports) if ports else "none"
        raise RuntimeError(f"Connect exactly one board over USB or choose --port (found: {found}).")
    return ports[0]


def board_identity(port, timeout: float = 20.0) -> tuple[str, bool]:
    # Ask repeatedly: a board that is still booting misses the first request.
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        port.write_line("IDENTITY")
        ask_until = min(deadline, time.monotonic() + 2)
        while (line := port.read_line(ask_until)) is not None:
            if match := IDENTITY.match(line):
                return match.group(1), match.group(2) == "provisioned"
    raise RuntimeError("The board did not report an identity. Flash the shared UrbanEcho firmware, "
                       "close any serial monitor and retry.")


def store_token(port, device_id: str, token: str, timeout: float = 10.0):
    port.write_line("PROVISION " + token)
    deadline = time.monotonic() + timeout
    while (line := port.read_line(deadline)) is not None:
        if line.startswith(f"PROVISIONED {device_id}"):
            return
        if line.startswith("PROVISION REFUSED"):
            raise RuntimeError("The board refused the token: " + line.partition(":")[2].strip())
    raise RuntimeError("The board did not confirm the token. Retry: the registration and private backup are kept.")


def all_items(url: str, admin: str, path: str) -> list[dict]:
    items: list[dict] = []
    while True:
        page = api(url, admin, f"{path}?limit=200&offset={len(items)}")
        items += page["items"]
        if not page["items"] or len(items) >= page["total"]:
            return items


def resolve_location(url: str, admin: str, name: str | None, location_id: UUID | None) -> dict:
    if location_id:
        return api(url, admin, f"/locations/{location_id}")
    matches = [item for item in all_items(url, admin, "/locations")
               if item["name"].strip().casefold() == name.strip().casefold()]
    if len(matches) != 1:
        raise RuntimeError(f"{'Several locations are' if matches else 'No location is'} named {name!r}; "
                           "check Management → Locations or use --location-id.")
    return matches[0]


def rule_warning(url: str, admin: str, location: dict) -> str | None:
    rule = api(url, admin, f"/locations/{location['id']}/threshold").get("current") or {}
    if rule.get("threshold_type") == "dbfs_rms" and rule.get("interval_seconds") == 1:
        return None
    return (f"Warning: {location['name']} uses {rule.get('threshold_type')} every {rule.get('interval_seconds')} s. "
            "Uncalibrated 1-second boards need a dBFS rule with a 1-second interval, or every reading is ineligible.")


def read_backup(path: Path, device_id: str) -> dict | None:
    if not path.exists():
        return None
    if path.is_symlink() or stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise RuntimeError(f"Refusing {path}: it must be a private (600) regular file.")
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        raise RuntimeError(f"Cannot read the private backup {path}.") from None
    if value.get("schema") != BACKUP_SCHEMA or value.get("external_id") != device_id or not TOKEN.match(value.get("token", "")):
        raise RuntimeError(f"{path} does not belong to {device_id}.")
    return value


def physical_location(args, admin: str) -> dict:
    if is_simulated(args.microphone_model):
        raise RuntimeError("Use the actual physical microphone model, without demo or synthetic labels.")
    location = resolve_location(base_url(args.url), admin, args.location, args.location_id)
    if is_simulated(location.get("name", "")):
        raise RuntimeError("Create or select a physical location in Management; do not use a simulated/demo location.")
    return location


def provision(args, admin: str, port, *, location: dict | None = None) -> dict:
    url = base_url(args.url)
    location = physical_location(args, admin) if location is None else location
    if warning := rule_warning(url, admin, location):
        print(warning)
    device_id, provisioned = board_identity(port)
    print(f"Board reports {device_id} ({'provisioned' if provisioned else 'unprovisioned'}).")
    backup = args.backup_dir / f"{device_id}.json"
    with locked_config(backup):
        existing = next((item for item in all_items(url, admin, "/devices") if item.get("external_id") == device_id), None)
        if existing and provisioned:
            moved = "" if existing["location_id"] == location["id"] else (
                " It is mapped to another location; move it in Management → Devices → Edit mapping.")
            print(f"{device_id} is already registered and provisioned; nothing changed.{moved}")
            return {"device_id": device_id, "server_id": existing["id"], "changed": False}
        if existing:
            saved = read_backup(backup, device_id)
            if saved is None:
                raise RuntimeError(f"{device_id} is registered, but this board has no token and {backup} does not exist. "
                                   "Tokens cannot be recovered from the server.")
            token = saved["token"]
            print(f"Restoring {device_id}'s token from {backup}.")
        else:
            created = api(url, admin, "/devices", {"external_id": device_id, "location_id": location["id"],
                                                    "microphone_model": args.microphone_model})
            token = created.get("token") or ""
            if not TOKEN.match(token):
                raise RuntimeError("The server did not return a usable device credential.")
            # Save before sending: the server cannot show this token again.
            save_private(backup, {"schema": BACKUP_SCHEMA, "external_id": device_id, "device_id": created["id"],
                                  "location_id": location["id"], "token": token,
                                  "registered_at": datetime.now(timezone.utc).isoformat()})
            existing = created
            print(f"Registered {device_id} at {location['name']}; private backup saved to {backup}.")
        store_token(port, device_id, token)
        print("Token stored on the board; it is restarting.")
        return {"device_id": device_id, "server_id": existing["id"], "changed": True}


def wait_for_upload(url: str, admin: str, server_id: str, since: datetime, seconds: float, port) -> dict | None:
    """Confirm a USB upload acknowledgement against its saved server record.

    Diagnostic /text messages also update last_contact_at, so contact alone is
    not audio evidence. Keeping the serial link open lets us inspect accepted
    recordings even before the worker has produced a measurement.
    """
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        line = port.read_line(deadline)
        if line is None:
            return None
        match = SAVED_AUDIO.search(line)
        if not match:
            continue
        try:
            audio_id = str(UUID(match.group(1)))
        except ValueError:
            continue
        try:
            recording = api(url, admin, f"/audio/{audio_id}")
        except APIError as error:
            if error.status == 404:
                continue  # An ACK from another server cannot verify this setup.
            raise
        if recording.get("id") != audio_id or str(recording.get("device_id")) != server_id:
            continue
        try:
            capture = datetime.fromisoformat(recording["captured_at"])
            received = datetime.fromisoformat(recording["received_at"])
        except (KeyError, TypeError, ValueError):
            continue
        if capture.tzinfo is None or received.tzinfo is None or capture < since or received < since:
            continue  # Old recordings/retries do not prove this capture session.
        status = recording.get("status")
        return {"audio_id": audio_id, "processing_status": status if status in {
            "pending", "processing", "completed", "failed"} else "unconfirmed"}
    return None


def report_upload(device_id: str, recording: dict | None):
    if recording is None:
        print("No new saved recording confirmed. Open the serial monitor to check Wi-Fi, time sync and HTTP results.")
        return
    print(f"{device_id}: new audio recording saved ({recording['audio_id']}).")
    status = recording["processing_status"]
    if status == "completed":
        print("Sound-level processing completed; check the application's reading quality and calibration status.")
    elif status == "failed":
        print("Audio arrived, but sound-level processing failed; inspect the recording in the server.")
    else:
        print(f"Sound-level processing: {status}; a saved upload does not confirm a completed or calibrated measurement.")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    where = parser.add_mutually_exclusive_group(required=True)
    where.add_argument("--location", help="Exact location name from Management → Locations")
    where.add_argument("--location-id", type=UUID)
    parser.add_argument("--port", help="Serial port; detected when exactly one USB board is connected")
    parser.add_argument("--url", default="http://localhost:8000", help="Administrator API address on this Mac")
    parser.add_argument("--admin-env-file", type=Path, default=Path(".env"))
    parser.add_argument("--microphone-model", default="INMP441")
    parser.add_argument("--backup-dir", type=Path, default=Path(".local/devices"))
    parser.add_argument("--wait-seconds", type=float, default=90, help="Wait for the first upload; 0 skips")
    args = parser.parse_args()
    admin = admin_token(args.admin_env_file)
    started = datetime.now(timezone.utc)
    location = physical_location(args, admin)
    port = SerialPort(args.port or find_port())
    try:
        result = provision(args, admin, port, location=location)
        if result["changed"] and args.wait_seconds > 0:
            print(f"Waiting up to {args.wait_seconds:.0f} s for a new saved recording (Wi-Fi, time sync, upload)...")
            report_upload(result["device_id"], wait_for_upload(
                base_url(args.url), admin, result["server_id"], started, args.wait_seconds, port))
    finally:
        port.close()


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, ValueError, KeyError):
        error = sys.exc_info()[1]
        # Expected RuntimeErrors are sanitized; never echo raw server or file contents.
        print(str(error) if isinstance(error, RuntimeError) else "Provisioning failed; check the board and server.",
              file=sys.stderr)
        raise SystemExit(1)
