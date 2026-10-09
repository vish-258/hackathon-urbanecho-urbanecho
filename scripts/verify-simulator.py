#!/usr/bin/env python3
"""Verify three simulated devices through the real upload, worker and report APIs.

Run from the project directory. ADMIN_TOKEN is read from the environment, or
privately from this directory's .env file; neither it nor device tokens is printed.
This foreground test adds labelled recordings and saved incident history. It
does not delete data or reset location rules. No sender remains after exit.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from zoneinfo import ZoneInfo

from simulate import pcm24_wav, upload

_spec = importlib.util.spec_from_file_location("application_demo", Path(__file__).with_name("demo-application.py"))
_application = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_application)
api = _application.api
UTC = timezone.utc


def event(phase, **values):
    print(json.dumps({"demo": "SIMULATED — software test only", "phase": phase, **values}), flush=True)


def admin_token(path=Path(".env")):
    if os.environ.get("ADMIN_TOKEN"):
        return os.environ["ADMIN_TOKEN"]
    if path.exists():
        for line in path.read_text().splitlines():
            key, separator, value = line.strip().partition("=")
            if separator and key == "ADMIN_TOKEN":
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                    value = value[1:-1]
                if value:
                    return value
    raise RuntimeError("Start the local stack first; the project .env must contain its administrator credential.")


def read_all(args, admin, path):
    items, offset = [], 0
    separator = "&" if "?" in path else "?"
    while True:
        page = api(args.url, admin, f"{path}{separator}limit=200&offset={offset}")
        items.extend(page["items"])
        offset += len(page["items"])
        if offset >= page["total"] or not page["items"]:
            return items


def fixture_audio():
    # All stations use these exact quantized PCM bytes. The offset anchors the
    # equality sample to EACH location's existing threshold, not a rounded value.
    return {delta: pcm24_wav(16000, 1, math.sqrt(2) * 10 ** ((60 + delta - 100) / 20))
            for delta in (-5, 0, 10, 20)}


def prepare(args, admin, audio):
    if not args.credentials.exists():
        # Only a missing fixture may provision new, explicitly simulated stations.
        _application.prepare(args, admin, 60 - _application._fixture.digital_rms(audio[0]))
    state = json.loads(args.credentials.read_text())
    if state.get("format") != "noise-application-demo-v1" or set(state.get("stations", {})) != {"garden", "workshop", "gate"}:
        raise RuntimeError("Use the original three-station application-demo credentials file.")
    if len({item.get("id") for item in state["stations"].values()}) != 3 or len({item.get("location_id") for item in state["stations"].values()}) != 3:
        raise RuntimeError("The demonstration requires three different registered devices and locations.")
    stations = {}
    for key, item in state["stations"].items():
        location = api(args.url, admin, "/locations/" + item["location_id"])
        device = api(args.url, admin, "/devices/" + item["id"])
        rule = api(args.url, admin, f"/locations/{item['location_id']}/threshold")["current"]
        if (not location["name"].startswith("SIMULATED · ") or location["name"] != item["name"]
                or not device.get("microphone_model", "").startswith("SYNTHETIC PCM24 fixture")
                or device["location_id"] != item["location_id"] or not device["enabled"] or not item.get("token")):
            raise RuntimeError("A simulated station was renamed, reassigned, disabled or lost its credential; review it before replaying.")
        if not rule or any(rule.get(k) != v for k, v in {
                "threshold_type": "spl_z_leq", "weighting": "Z", "channel_policy": "mono", "interval_seconds": 1}.items()):
            raise RuntimeError("This fixture needs an existing one-second SPL(Z), mono rule; it will not overwrite location settings.")
        if not -90 <= rule["threshold_value"] <= 180:
            raise RuntimeError("The fixture's relative test levels would fall outside the supported measurement range.")
        stations[key] = {**item, "rule": rule, "timezone": location["timezone"],
                         "latitude": location["latitude"], "longitude": location["longitude"], "device": device}
    # Validate all three stations before changing any synthetic calibration.
    for key, item in stations.items():
        offset = item["rule"]["threshold_value"] - _application._fixture.digital_rms(audio[0])
        expected = _application.calibration(state["fixture_id"], offset)
        expected["version"] = "SYNTHETIC-STEP6-ONLY-" + state["fixture_id"] + "-" + key
        device = item.pop("device")
        # Changing this TEST-ONLY calibration does not rewrite past recordings.
        api(args.url, admin, "/devices/" + item["id"],
            {"expected_revision": device["config_revision"], "calibration": expected}, method="PATCH")
        event("station_ready", station=key, location_id=item["location_id"], device_id=item["id"],
              name=item["name"], threshold=item["rule"]["threshold_value"], recovery_count=item["rule"]["recovery_count"])
    return stations


class Sender:
    def __init__(self, args, admin, item, audio):
        self.args, self.admin, self.item, self.audio = args, admin, item, audio
        self.session, self.sequence = "step6-" + uuid.uuid4().hex, 0
        self.records = []

    def send(self, delta, captured):
        delay = (captured + timedelta(seconds=1) - datetime.now(UTC)).total_seconds()
        if delay > 0:
            time.sleep(delay)
        metadata = {"device_id": self.item["id"], "session_id": self.session,
                    "chunk_id": f"{self.session}-{self.sequence}", "sequence": self.sequence,
                    "captured_at": captured.isoformat()}
        status, accepted = upload(self.args.url, self.item, metadata, self.audio[delta])
        if status != 202 or accepted.get("duplicate"):
            raise RuntimeError("A new fixture recording did not receive its expected acknowledgement.")
        measured = _application._fixture.wait_processed(self.args.url, self.admin, accepted["id"])
        expected = self.item["rule"]["threshold_value"] + delta
        evaluation = measured.get("evaluation") or {}
        if (evaluation.get("status") != "eligible_live" or measured["value_db"] is None
                or abs(measured["value_db"] - expected) > .01
                or bool(evaluation.get("breach")) != (delta > 0)):
            raise RuntimeError("A sample failed live eligibility, its expected numerical level or strict threshold comparison.")
        if delta == 0 and measured["value_db"] != self.item["rule"]["threshold_value"]:
            raise RuntimeError("The equality fixture was not canonically equal; rounded equality is insufficient.")
        if (measured["location_id"] != self.item["location_id"] or measured["device_id"] != self.item["id"]
                or datetime.fromisoformat(measured["captured_at"].replace("Z", "+00:00")) != captured):
            raise RuntimeError("The saved recording has an unexpected location, device or capture timestamp.")
        self.sequence += 1
        row = {"audio_id": accepted["id"], "metadata": metadata, "delta": delta, "measurement": measured}
        self.records.append(row)
        return {"device_id": self.item["id"], "audio_id": accepted["id"], "value_db": measured["value_db"],
                "threshold": self.item["rule"]["threshold_value"], "breach": evaluation["breach"]}

    def snapshot(self):
        response = api(self.args.url, self.admin, "/locations/status?device_id=" + self.item["id"])
        location = next(item for item in response["items"] if item["id"] == self.item["location_id"])
        if (location["latitude"], location["longitude"]) != (self.item["latitude"], self.item["longitude"]):
            raise RuntimeError("The map snapshot contains incorrect station coordinates.")
        streams = [row for row in location["streams"] if row["device_id"] == self.item["id"]]
        latest = max(streams, key=lambda row: row["measured_at"] or "")
        if latest["measurement_value"] != self.records[-1]["measurement"]["value_db"]:
            raise RuntimeError("The application's latest-reading snapshot differs from the persisted measurement.")
        return location, latest

    def incidents(self):
        return read_all(self.args, self.admin, "/incidents?device_id=" + self.item["id"])

    def events(self):
        return read_all(self.args, self.admin, "/events?device_id=" + self.item["id"])

    def check_duplicate(self):
        row = self.records[-1]
        before = api(self.args.url, self.admin, "/audio/" + row["audio_id"])
        counts = tuple(api(self.args.url, self.admin, "/" + name + "?device_id=" + self.item["id"] + "&limit=1")["total"]
                       for name in ("measurements", "incidents", "events"))
        status, accepted = upload(self.args.url, self.item, row["metadata"], self.audio[row["delta"]])
        after = api(self.args.url, self.admin, "/audio/" + row["audio_id"])
        after_counts = tuple(api(self.args.url, self.admin, "/" + name + "?device_id=" + self.item["id"] + "&limit=1")["total"]
                             for name in ("measurements", "incidents", "events"))
        if status != 200 or not accepted.get("duplicate") or accepted["id"] != row["audio_id"] or before != after or counts != after_counts:
            raise RuntimeError("Identical upload retry changed its recording, job, measurement, incident or event records.")
        request = urllib.request.Request(self.args.url.rstrip("/") + "/audio/" + row["audio_id"] + "/file",
                                         headers={"Authorization": "Bearer " + self.item["token"]})
        with urllib.request.urlopen(request, timeout=20) as response:
            downloaded = response.read()
        expected = hashlib.sha256(self.audio[row["delta"]]).hexdigest()
        if hashlib.sha256(downloaded).hexdigest() != expected or after["checksum"] != expected:
            raise RuntimeError("Stored/downloaded audio differs from the original uploaded bytes.")
        return {"device_id": self.item["id"], "audio_id": row["audio_id"], "unchanged_counts": counts,
                "stored_audio_checksum_verified": True}


def complete_summary(args, admin, location_id, day):
    api(args.url, admin, "/daily-summaries/generate", {"location_id": location_id, "reporting_date": str(day)})
    for _ in range(240):
        saved = api(args.url, admin, f"/daily-summaries?location_id={location_id}&reporting_date={day}")
        if saved.get("report", {}).get("status") == "completed":
            return saved
        if saved.get("report", {}).get("status") == "failed":
            raise RuntimeError("Daily report generation failed; inspect its saved error state.")
        time.sleep(.25)
    raise RuntimeError("Daily worker did not finish within 60 seconds.")


def run(args, admin):
    snapshot = api(args.url, admin, "/locations/status?limit=1")
    stale_seconds = snapshot["data_stale_seconds"]
    if stale_seconds > args.max_stale_wait:
        raise RuntimeError(f"This server requires a {stale_seconds:g}-second stale wait; raise --max-stale-wait explicitly to allow it.")
    audio = fixture_audio()
    stations = prepare(args, admin, audio)
    senders = {key: Sender(args, admin, item, audio) for key, item in stations.items()}
    focused = {args.focus_station} if getattr(args, "focus_station", None) else set(senders)
    if not focused <= set(senders):
        raise RuntimeError("Choose an existing simulated station: garden, workshop, or gate")
    recovery_count = max(item["rule"]["recovery_count"] for item in stations.values())
    event("start", note="Synthetic sound and calibration only. Original data and thresholds stay intact.",
          stale_seconds=stale_seconds)
    with ThreadPoolExecutor(max_workers=3) as pool:
        def round_at(delta, captured):
            return [job.result() for job in [pool.submit(sender.send, delta if delta <= 0 or key in focused else -5, captured) for key, sender in senders.items()]]
        def phase(name, delta, hold=True):
            rows = round_at(delta, datetime.now(UTC))
            event(name, readings=rows)
            if hold and args.hold_seconds:
                time.sleep(args.hold_seconds)
        # Settle any unfinished incident from an earlier demonstration with real
        # contiguous normal audio. Do not remove or manufacture incident history.
        started = datetime.now(UTC)
        for index in range(recovery_count):
            round_at(-5, started + timedelta(seconds=index))
        for sender in senders.values():
            if any(row["status"] in ("active", "recovering") for row in sender.incidents()):
                raise RuntimeError("The baseline failed to recover an unfinished previous incident.")
        before_ids = {key: {row["id"] for row in sender.incidents()} for key, sender in senders.items()}
        event("normal_verified", readings=[sender.records[-1]["measurement"]["value_db"] for sender in senders.values()])
        if args.hold_seconds:
            time.sleep(args.hold_seconds)
        phase("equal_to_threshold", 0)
        for key, sender in senders.items():
            if {row["id"] for row in sender.incidents()} != before_ids[key]:
                raise RuntimeError("Equality incorrectly opened an incident.")
        phase("above_threshold", 10)
        opened = {}
        for key, sender in senders.items():
            rows = [row for row in sender.incidents() if row["id"] not in before_ids[key]]
            if key not in focused:
                if rows:
                    raise RuntimeError("A normal comparison station unexpectedly opened an incident.")
                continue
            if len(rows) != 1 or rows[0]["status"] != "active" or rows[0]["breach_count"] != 1:
                raise RuntimeError("An above-threshold sample did not open exactly one active incident.")
            opened[key] = rows[0]["id"]
            sender.snapshot()
        for _ in range(2):
            phase("sustained_excessive", 20, hold=False)
        for key, sender in senders.items():
            if key not in opened:
                continue
            rows = [row for row in sender.incidents() if row["id"] not in before_ids[key]]
            if len(rows) != 1 or rows[0]["id"] != opened[key] or rows[0]["breach_count"] != 3:
                raise RuntimeError("Repeated excessive samples did not extend the same incident.")
        event("duplicate_and_audio_integrity_verified", results=[sender.check_duplicate() for sender in senders.values()])
        event("disconnect_wait", seconds=stale_seconds + 2, note="Senders pause; an unresolved incident must remain unresolved.")
        time.sleep(stale_seconds + 2)
        for key, sender in senders.items():
            location, stream = sender.snapshot()
            if stream["data_status"] != "stale" or (key in opened and opened[key] not in location["unresolved_incident_ids"]):
                raise RuntimeError("Disconnection failed to become stale or incorrectly resolved its incident.")
        event("stale_verified", unresolved_incidents=opened)
        if args.hold_seconds:
            time.sleep(args.hold_seconds)
        started = datetime.now(UTC)
        for index in range(recovery_count):
            round_at(-5, started + timedelta(seconds=index))
            for key, sender in senders.items():
                if key not in opened:
                    continue
                row = next(row for row in sender.incidents() if row["id"] == opened[key])
                expected = "resolved" if index + 1 >= sender.item["rule"]["recovery_count"] else "recovering"
                if row["status"] != expected:
                    raise RuntimeError("Recovery did not follow the location's configured count of contiguous normal samples.")
            event("recovery_reading_verified", consecutive_normal_count=index + 1)
        for key, sender in senders.items():
            location, stream = sender.snapshot()
            if stream["noise_status"] != "normal" or stream["data_status"] != "fresh" or (key in opened and opened[key] in location["unresolved_incident_ids"]):
                raise RuntimeError("The application snapshot did not return to normal after recovery.")
            if key not in opened:
                if {row["id"] for row in sender.incidents()} != before_ids[key]:
                    raise RuntimeError("A normal comparison station gained an unexpected incident.")
                continue
            events = [row for row in sender.events() if row.get("incident_id") == opened[key]]
            if sum(row["event_type"] == "incident.opened" for row in events) != 1 or sum(row["event_type"] == "incident.resolved" for row in events) != 1:
                raise RuntimeError("Expected exactly one durable opening and one resolution notification per new incident.")
        event("recovery_and_notifications_verified", incidents=opened)
    summaries = []
    for key, sender in senders.items():
        timezone_name = sender.item["timezone"]
        days = {datetime.fromisoformat(row["metadata"]["captured_at"]).astimezone(ZoneInfo(timezone_name)).date()
                for row in sender.records}
        for day in sorted(days):
            saved = complete_summary(args, admin, sender.item["location_id"], day)
            definition = [row for row in saved["summaries"] if row["definition"]["source_kind"] == "simulated"
                          and row["definition"]["measurement_type"] == "spl_z_leq"
                          and row["definition"]["processing_version"] == sender.records[-1]["measurement"]["processing_version"]]
            count = sum(datetime.fromisoformat(row["metadata"]["captured_at"]).astimezone(ZoneInfo(timezone_name)).date() == day
                        for row in sender.records)
            if len(definition) != 1 or definition[0]["statistics"]["measurement_count"] < count:
                raise RuntimeError("The saved simulated daily summary does not include this run's eligible recordings.")
            summary = {"station": key, "location_id": sender.item["location_id"], "date": str(day),
                       "report_id": saved["report"]["id"], "summary_id": definition[0]["id"],
                       "definition": definition[0]["definition"], "statistics": definition[0]["statistics"],
                       "run_measurements": count, "calculated_at": definition[0]["calculated_at"]}
            summaries.append(summary)
            event("daily_summary_verified", **summary)
    event("PASS", checks=["normal", "exact equality", "above threshold", "sustained same incident",
          "identical retry without duplicate records", "byte integrity", "correct capture time and location",
          "map/latest persisted snapshot", "stale does not resolve", "configured recovery",
          "one opening and resolution notification", "saved simulated daily summaries"],
          summary_count=len(summaries), note="Foreground sender stopped. Devices will become stale again. Physical testing and acoustic calibration are separate.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--focus-station", choices=["garden", "workshop", "gate"], help="Open excessive-noise incidents only at this simulated station; other stations do not exceed threshold.")
    parser.add_argument("--credentials", type=Path, default=Path(".local/application-demo.json"))
    parser.add_argument("--hold-seconds", type=float, default=3, help="Pause at visible phases for the application, 0–10 seconds.")
    parser.add_argument("--max-stale-wait", type=float, default=60, help="Refuse a unexpectedly long configured stale window before adding data.")
    args = parser.parse_args()
    if not 0 <= args.hold_seconds <= 10 or not 0 < args.max_stale_wait <= 300:
        parser.error("Use --hold-seconds 0–10 and --max-stale-wait above zero and at most 300.")
    args.duration = 90  # Missing application fixture's initial calibration validity.
    args.credentials.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(args.credentials.with_suffix(args.credentials.suffix + ".lock"), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        os.fchmod(fd, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("These simulated devices are already being used by another demonstration.") from None
        run(args, admin_token())
    finally:
        os.close(fd)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        raise SystemExit("Stopped. Saved data remains; no background sender was left running.")
    except (OSError, ValueError, KeyError, RuntimeError, urllib.error.URLError) as error:
        # Never print response bodies, headers, tokens or provisioning payloads.
        message = f"HTTP {error.code}; check server/device configuration" if isinstance(error, urllib.error.HTTPError) else str(error)
        print("Simulator verification failed: " + message, file=sys.stderr)
        raise SystemExit(1)
