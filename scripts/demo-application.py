#!/usr/bin/env python3
"""Run a bounded, explicitly simulated three-location demo through the real API."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import fcntl
import importlib.util
import json
import math
import os
from pathlib import Path
import sys
import time
import urllib.error
import urllib.request
import uuid

from seed import save_private
from simulate import pcm24_wav, upload


# Reuse the existing PCM24 numerical fixture and worker-completion check.
_spec = importlib.util.spec_from_file_location("live_fixture", Path(__file__).with_name("simulate-live.py"))
_fixture = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fixture)

STATIONS = (
    ("garden", "North garden", 12.9824, 77.5883),
    ("workshop", "Workshop", 12.9760, 77.5995),
    ("gate", "East gate", 12.9726, 77.6088),
)
THRESHOLD = {"threshold_value": 60, "threshold_type": "spl_z_leq", "weighting": "Z",
             "channel_policy": "mono", "interval_seconds": 1, "recovery_count": 3}
UTC = timezone.utc


def api(base, admin, path, payload=None, method=None):
    """Keep response bodies out of errors so credentials cannot reach the console."""
    request = urllib.request.Request(base.rstrip("/") + path,
        data=None if payload is None else json.dumps(payload).encode(),
        method=method or ("GET" if payload is None else "POST"),
        headers={"Authorization": "Bearer " + admin, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"{request.method} {path}: HTTP {exc.code}; check the server configuration.") from None


def event(message, **values):
    print(json.dumps({"demo": "SIMULATED — software test only", "message": message, **values}), flush=True)


def calibration(fixture_id, offset):
    now = datetime.now(UTC)
    return {"method": "spl_z_leq", "version": "SYNTHETIC-APPLICATION-ONLY-" + fixture_id,
            "weighting": "Z", "channel_policy": "mono", "status": "valid",
            "sample_rate": 16000, "offset_db": offset,
            "calibrated_at": (now - timedelta(days=1)).isoformat(),
            "valid_until": (now + timedelta(days=1)).isoformat()}


def prepare(args, admin, offset):
    if args.credentials.exists():
        state = json.loads(args.credentials.read_text())
        if state.get("format") != "noise-application-demo-v1" or not state.get("fixture_id"):
            raise RuntimeError("The credentials file belongs to a different fixture; choose a new filename.")
    else:
        state = {"format": "noise-application-demo-v1", "fixture_id": uuid.uuid4().hex[:8],
                 "stations": {}, "created_at": datetime.now(UTC).isoformat()}
        save_private(args.credentials, state)
    now = datetime.now(UTC)
    for key, title, lat, lon in STATIONS:
        expected_name = f"SIMULATED · {title} · {state['fixture_id']}"
        item = state["stations"].setdefault(key, {"name": expected_name})
        if item["name"] != expected_name:
            raise RuntimeError("Unexpected fixture identity; do not reuse edited credentials files.")
        if not item.get("location_id"):
            loc = api(args.url, admin, "/locations", {**THRESHOLD, "name": expected_name,
                "latitude": lat, "longitude": lon, "timezone": "Asia/Kolkata"})
            item["location_id"] = loc["id"]
            save_private(args.credentials, state)
        loc = api(args.url, admin, "/locations/" + item["location_id"])
        if loc["name"] != expected_name:
            raise RuntimeError(f"The {title} location was renamed. Restore its simulated name before replaying.")
        rule = api(args.url, admin, f"/locations/{item['location_id']}/threshold")
        current = rule["current"]
        if not current or any(current.get(k) != v for k, v in THRESHOLD.items()):
            # Only this fixture's own location is reset; its old versions and history remain.
            api(args.url, admin, f"/locations/{item['location_id']}/threshold",
                {**THRESHOLD, "expected_revision": rule["latest_revision"]}, method="PATCH")
            event("Restored this simulated location's 60 dB SPL(Z) rule", location=expected_name)
        if not item.get("id"):
            dev = api(args.url, admin, "/devices", {"location_id": item["location_id"],
                "microphone_model": "SYNTHETIC PCM24 fixture — no physical microphone",
                "calibration": calibration(state["fixture_id"], offset)})
            item.update({"id": dev["id"], "token": dev["token"]})
            save_private(args.credentials, state)
        if not item.get("token"):
            raise RuntimeError("A one-time device token is missing. Restore the private credentials file.")
        dev = api(args.url, admin, "/devices/" + item["id"])
        if dev["location_id"] != item["location_id"] or not dev["microphone_model"].startswith("SYNTHETIC PCM24 fixture"):
            raise RuntimeError(f"The {title} device was changed or reassigned. Restore it before replaying.")
        cal = dev.get("calibration") or {}
        expected = calibration(state["fixture_id"], offset)
        valid = all(cal.get(k) == v for k, v in expected.items() if k not in ("calibrated_at", "valid_until"))
        try:
            valid = valid and datetime.fromisoformat(cal["valid_until"].replace("Z", "+00:00")) > now + timedelta(seconds=args.duration + 120)
        except (KeyError, ValueError):
            valid = False
        changes = {}
        if not valid:
            changes["calibration"] = expected
        if not dev["enabled"]:
            changes["enabled"] = True
        if changes:
            api(args.url, admin, "/devices/" + item["id"],
                {"expected_revision": dev["config_revision"], **changes}, method="PATCH")
        event("Station ready", station=key, name=expected_name,
              location_id=item["location_id"], device_id=item["id"])
    return state


class Sender:
    def __init__(self, args, admin, item, audio):
        self.args, self.admin, self.item, self.audio = args, admin, item, audio
        self.session = "application-demo-" + uuid.uuid4().hex
        self.sequence = 0
        self.last_capture = None

    def send(self, level, captured=None):
        if captured is None:
            captured = datetime.now(UTC) - timedelta(seconds=1)
            if self.last_capture:
                captured = max(captured, self.last_capture + timedelta(seconds=1))
        # The entire one-second synthetic interval is in the past before upload.
        delay = (captured + timedelta(seconds=1) - datetime.now(UTC)).total_seconds()
        if delay > 0:
            time.sleep(delay)
        metadata = {"device_id": self.item["id"], "session_id": self.session,
            "chunk_id": f"{self.session}-{self.sequence}", "sequence": self.sequence,
            "captured_at": captured.isoformat()}
        status, accepted = upload(self.args.url, self.item, metadata, self.audio[level])
        if status != 202:
            raise RuntimeError(f"Unexpected upload response: HTTP {status}.")
        measured = _fixture.wait_processed(self.args.url, self.admin, accepted["id"])
        evaluation = measured.get("evaluation") or {}
        if evaluation.get("status") != "eligible_live":
            raise RuntimeError("A simulated sample was not live-eligible: " + str(evaluation.get("diagnostic")))
        if measured["value_db"] is None or abs(measured["value_db"] - level) > 0.01:
            raise RuntimeError("The synthetic calibration did not produce the expected numerical result.")
        self.sequence += 1
        self.last_capture = captured
        return {"location": self.item["name"], "nominal_spl_z_db": level,
                "measured_spl_z_db": round(measured["value_db"], 4), "audio_id": accepted["id"]}


def run(args, admin):
    snapshot = api(args.url, admin, "/locations/status?limit=1")
    stale_after = snapshot["data_stale_seconds"]
    if args.duration < stale_after + 20:
        raise RuntimeError(f"Use --duration of at least {math.ceil(stale_after + 20)} seconds for this server's stale window.")
    audio = {n: pcm24_wav(16000, 1, math.sqrt(2) * 10 ** ((n - 100) / 20))
             for n in (52, 53, 54, 55, 56, 57, 58, 59, 60, 72, 75, 77, 79)}
    offset = 60.0 - _fixture.digital_rms(audio[60])
    state = prepare(args, admin, offset)
    senders = {key: Sender(args, admin, item, audio) for key, item in state["stations"].items()}
    event("Synthetic calibration tests software only; this is not microphone calibration",
          fixture_id=state["fixture_id"], duration_seconds=args.duration, stale_after_seconds=stale_after)
    with ThreadPoolExecutor(max_workers=3) as pool:
        # A repeat run first resolves any unfinished previous incident using actual
        # contiguous samples. History stays intact and no database rows are deleted.
        baseline = datetime.now(UTC)
        for index in range(3):
            jobs = [pool.submit(sender.send, 55, baseline + timedelta(seconds=index)) for sender in senders.values()]
            for job in jobs:
                job.result()
        event("Baseline complete; all three simulated devices are normal")
        started = time.monotonic()
        gate_start = datetime.now(UTC)
        tick = 0
        workshop_breached = False
        while time.monotonic() - started < args.duration:
            elapsed = time.monotonic() - started
            excessive = elapsed >= args.breach_after
            if excessive and not workshop_breached:
                event("Workshop starts repeated excessive readings; expect one new incident notification")
                workshop_breached = True
            jobs = [pool.submit(senders["garden"].send, (52, 54, 56, 53)[tick % 4]),
                    pool.submit(senders["workshop"].send, (72, 77, 79, 75)[tick % 4] if excessive else 54)]
            if tick < 4:
                # Exact consecutive capture windows and sequence numbers are
                # deliberate: recovery requires three valid contiguous normals.
                jobs.append(pool.submit(senders["gate"].send, (75, 59, 58, 57)[tick],
                                        gate_start + timedelta(seconds=tick)))
            for job in jobs:
                event("Reading saved and processed", **job.result())
            if tick == 3:
                incidents = api(args.url, admin, "/incidents?device_id=" + senders["gate"].item["id"])
                if not incidents["items"] or incidents["items"][0]["status"] != "resolved":
                    raise RuntimeError("East gate did not resolve after three normal readings.")
                event("East gate recovered; its device now stops sending and will become stale")
            tick += 1
            delay = min(started + tick * args.tick, started + args.duration) - time.monotonic()
            if delay > 0:
                time.sleep(delay)
    expected = {"garden": ("normal", "fresh"), "workshop": ("excessive", "fresh"), "gate": ("normal", "stale")}
    for key, sender in senders.items():
        result = api(args.url, admin, "/locations/status?location_id=" + sender.item["location_id"])
        item = result["items"][0]
        actual = (item["noise_status"], item["data_status"])
        event("Final persisted status", station=key, noise_status=actual[0], data_status=actual[1],
              unresolved_incidents=len(item["unresolved_incident_ids"]))
        if actual != expected[key]:
            raise RuntimeError(f"Unexpected {key} status: {actual}; expected {expected[key]}.")
    event("PASS: normal, ongoing excessive, recovered and stale states verified through the API",
          note="The sender has stopped. Remaining devices will become stale; Workshop's incident stays unresolved. Daily reports are not generated.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--credentials", type=Path, default=Path(".local/application-demo.json"))
    parser.add_argument("--duration", type=int, default=90, help="Foreground presentation phase, 50–600 seconds; setup is additional.")
    parser.add_argument("--tick", type=float, default=3, help="Seconds between rounds, 1–5 (default: 3).")
    parser.add_argument("--breach-after", type=int, default=15, help="Seconds before Workshop starts excessive readings (default: 15).")
    args = parser.parse_args()
    if not 50 <= args.duration <= 600 or not 1 <= args.tick <= 5:
        parser.error("Use --duration between 50 and 600 and --tick between 1 and 5.")
    if not 0 <= args.breach_after <= args.duration - 10:
        parser.error("--breach-after must leave at least 10 seconds of excessive readings.")
    admin = os.environ.get("ADMIN_TOKEN")
    if not admin:
        parser.error("Set ADMIN_TOKEN privately from the server's .env file; never paste it into command arguments.")
    args.credentials.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = args.credentials.with_suffix(args.credentials.suffix + ".lock")
    lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        os.fchmod(lock_fd, 0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("This fixture is already running. Wait for it to finish before replaying.") from None
        run(args, admin)
    finally:
        os.close(lock_fd)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Demo stopped. Saved readings remain; devices will become stale. No background sender was left running.", file=sys.stderr)
        raise SystemExit(130)
    except (OSError, ValueError, KeyError, RuntimeError, urllib.error.URLError) as exc:
        # Do not print HTTP bodies, request headers or device provisioning payloads.
        message = f"HTTP {exc.code}; check server configuration" if isinstance(exc, urllib.error.HTTPError) else str(exc)
        print("Demo stopped: " + message, file=sys.stderr)
        raise SystemExit(1)
