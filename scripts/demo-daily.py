#!/usr/bin/env python3
"""Repeatable, explicitly simulated yesterday/today recordings through the real API.

Reuses the three application-demo locations when available. Dedicated replay
devices keep the existing live devices, their calibration and streams untouched.
Never inserts measurements, incidents or reports directly into the database.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, time as day_time, timedelta, timezone
import fcntl
import importlib.util
import json
import math
import os
from pathlib import Path
import sys
import time
import urllib.error
from zoneinfo import ZoneInfo

from seed import save_private
from simulate import pcm24_wav, upload

_spec = importlib.util.spec_from_file_location("application_demo", Path(__file__).with_name("demo-application.py"))
_application = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_application)
api = _application.api
UTC = timezone.utc
ZONE = "Asia/Kolkata"
LEVELS = {"garden": (50, 60), "workshop": (60, 70, 80), "gate": (45, 55)}


def recording_plan(key: str, reporting_date: date, timezone_name: str = ZONE):
    """Stable IDs make reruns use upload deduplication, even across restarts."""
    zone = ZoneInfo(timezone_name)
    noon = datetime.combine(reporting_date, day_time(12), zone)
    next_midnight = datetime.combine(reporting_date + timedelta(days=1), day_time(), zone)
    values = [(noon + timedelta(seconds=2 * i), level) for i, level in enumerate(LEVELS[key])]
    values.append((noon + timedelta(seconds=10), None))  # actual silent WAV, excluded by quality
    if key == "gate":
        values.append((next_midnight - timedelta(seconds=.5), 65))  # half a second on each day
    values.append((next_midnight + timedelta(minutes=1), LEVELS[key][0]))
    session = f"daily-demo-v1-{key}-{reporting_date.isoformat()}"
    return [{"chunk_id": f"{session}-{i}", "session_id": session, "sequence": i,
             "captured_at": captured.isoformat(), "level": level}
            for i, (captured, level) in enumerate(values)]


def wait_measurement(base, admin, identity):
    for _ in range(300):
        audio = api(base, admin, "/audio/" + identity)
        if audio["status"] == "completed":
            return next(item for item in audio["measurements"] if item["result_version"] == "initial")
        if audio["status"] == "failed":
            raise RuntimeError("A demo recording failed processing; inspect its saved processing status.")
        time.sleep(.1)
    raise RuntimeError("Audio worker did not finish within 30 seconds.")


def validate_replay_age(plans, now, freshness_seconds):
    if (type(freshness_seconds) not in (int, float) or not math.isfinite(freshness_seconds)
            or freshness_seconds <= 0):
        raise RuntimeError("The server must disclose its live freshness window before historical replay.")
    safe_before = now - timedelta(seconds=freshness_seconds + 1)
    if any(datetime.fromisoformat(row["captured_at"]) + timedelta(seconds=1) > safe_before
           for rows in plans.values() for row in rows):
        raise RuntimeError("Replay must be older than this server's live freshness window. Wait or select an older --date.")


def completed_summary(base, admin, location, day):
    api(base, admin, "/daily-summaries/generate", {"location_id": location, "reporting_date": day.isoformat()})
    for _ in range(240):
        saved = api(base, admin, f"/daily-summaries?location_id={location}&reporting_date={day}")
        report = saved.get("report") or {}
        if report.get("status") == "completed":
            return saved
        if report.get("status") == "failed":
            raise RuntimeError("Daily calculation failed; use Recalculate in Daily reports to retry.")
        time.sleep(.25)
    raise RuntimeError("Daily worker did not finish within 60 seconds.")


def prepare(args, admin, offset):
    if args.credentials.exists():
        state = json.loads(args.credentials.read_text())
        if state.get("format") != "noise-daily-demo-v1":
            raise RuntimeError("Choose a credentials file reserved for the daily demonstration.")
    else:
        state = {"format": "noise-daily-demo-v1", "stations": {}}
        save_private(args.credentials, state)
    application_state = json.loads(args.application_credentials.read_text()) if args.application_credentials.exists() else {}
    if application_state and application_state.get("format") != "noise-application-demo-v1":
        raise RuntimeError("The application credentials file belongs to another fixture.")
    for key, title, latitude, longitude in _application.STATIONS:
        item = state["stations"].setdefault(key, {})
        prior = application_state.get("stations", {}).get(key, {})
        if not item.get("location_id"):
            if prior.get("location_id"):
                location = api(args.url, admin, "/locations/" + prior["location_id"])
                if not location["name"].startswith("SIMULATED · " + title + " · "):
                    raise RuntimeError("An application-demo location was renamed; refusing to add replay data to it.")
            else:
                location = api(args.url, admin, "/locations", {**_application.THRESHOLD,
                    "name": f"SIMULATED · Daily {title}", "latitude": latitude, "longitude": longitude,
                    "timezone": ZONE})
            item.update(location_id=location["id"], name=location["name"])
            save_private(args.credentials, state)
        location = api(args.url, admin, "/locations/" + item["location_id"])
        if location["name"] != item["name"] or location["timezone"] != ZONE:
            raise RuntimeError("A daily-demo location was edited; review it before replaying.")
        calibration = {"method": "spl_z_leq", "version": "SYNTHETIC-DAILY-REPLAY-v1",
            "weighting": "Z", "channel_policy": "mono", "status": "valid", "sample_rate": 16000,
            "offset_db": offset, "calibrated_at": "2020-01-01T00:00:00Z", "valid_until": "2100-01-01T00:00:00Z"}
        # This deliberately long validity belongs ONLY to a numerical test fixture.
        if not item.get("id"):
            device = api(args.url, admin, "/devices", {"location_id": item["location_id"],
                "microphone_model": "SYNTHETIC daily replay — no physical microphone", "calibration": calibration})
            item.update(id=device["id"], token=device["token"])
            save_private(args.credentials, state)
        device = api(args.url, admin, "/devices/" + item["id"])
        if (device["location_id"] != item["location_id"] or not device["enabled"]
                or device.get("calibration") != calibration or not item.get("token")):
            raise RuntimeError("A daily-replay device was edited; refusing to overwrite its configuration.")
    return state


def run(args, admin):
    now = datetime.now(UTC)
    day = args.date or now.astimezone(ZoneInfo(ZONE)).date() - timedelta(days=1)
    plans = {key: recording_plan(key, day) for key in LEVELS}
    capabilities = api(args.url, admin, "/capabilities")
    validate_replay_age(plans, now, capabilities.get("live_updates", {}).get("freshness_seconds"))
    levels = {row["level"] for rows in plans.values() for row in rows} | {60}
    audio = {level: pcm24_wav(16000, 1, 0 if level is None else math.sqrt(2) * 10 ** ((level - 100) / 20))
             for level in levels}
    offset = 60 - _application._fixture.digital_rms(audio[60])
    # Read the existing records before adding demo data. Output counts only.
    counts = api(args.url, admin, "/measurements?limit=1")
    print(json.dumps({"demo": "SIMULATED", "existing_measurements": counts["total"], "reporting_date": str(day)}), flush=True)
    state = prepare(args, admin, offset)
    for key, item in state["stations"].items():
        accepted_count = duplicate_count = 0
        for row in plans[key]:
            metadata = {k: v for k, v in row.items() if k != "level"}
            metadata["device_id"] = item["id"]
            status, accepted = upload(args.url, item, metadata, audio[row["level"]])
            if status not in (200, 202):
                raise RuntimeError("Unexpected upload acknowledgement.")
            measured = wait_measurement(args.url, admin, accepted["id"])
            evaluation = measured.get("evaluation") or {}
            if evaluation.get("live") or evaluation.get("status") == "eligible_live":
                raise RuntimeError("Replay unexpectedly became live; stop and inspect capture timestamps.")
            if row["level"] is None:
                if measured["value_db"] is not None or measured["quality_status"] == "good":
                    raise RuntimeError("Silent demo input was not excluded as expected.")
            elif measured["value_db"] is None or abs(measured["value_db"] - row["level"]) > .01:
                raise RuntimeError("A fixture could not produce its expected sound level; check historical measurement rules.")
            duplicate_count += int(accepted.get("duplicate", False))
            accepted_count += 1
        for selected in (day, day + timedelta(days=1)):
            saved = completed_summary(args.url, admin, item["location_id"], selected)
            print(json.dumps({"demo": "SIMULATED", "location": item["name"], "date": str(selected),
                "report_id": saved["report"]["id"], "summaries": saved["summaries"]}), flush=True)
        print(json.dumps({"station": key, "recordings_checked": accepted_count,
                          "duplicate_uploads_reused": duplicate_count}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--credentials", type=Path, default=Path(".local/daily-demo.json"))
    parser.add_argument("--application-credentials", type=Path, default=Path(".local/application-demo.json"))
    parser.add_argument("--date", type=date.fromisoformat, help="First local date; defaults to yesterday in Asia/Kolkata")
    args = parser.parse_args()
    admin = os.environ.get("ADMIN_TOKEN")
    if not admin:
        parser.error("Load the local environment first; do not paste its token into chat.")
    args.credentials.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with args.credentials.with_suffix(".lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        run(args, admin)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, RuntimeError, urllib.error.URLError) as error:
        # Never print HTTP bodies or credential-containing requests.
        print("Daily demo failed: " + (f"HTTP {error.code}" if isinstance(error, urllib.error.HTTPError)
                                      else str(error)), file=sys.stderr)
        raise SystemExit(1)
