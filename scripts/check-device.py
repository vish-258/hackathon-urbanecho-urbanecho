#!/usr/bin/env python3
"""Read-only evidence for a registered physical device, without exposing credentials."""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from urllib.parse import urlencode
from uuid import UUID
from zoneinfo import ZoneInfo

from hardware_common import admin_token, api, base_url, load_config


def recording_evidence(chunk: dict, expected_device: str, now: datetime) -> dict:
    if str(chunk.get("device_id")) != expected_device:
        raise RuntimeError("This saved recording belongs to a different device.")
    capture = datetime.fromisoformat(chunk["captured_at"])
    receive = datetime.fromisoformat(chunk["received_at"])
    if capture.tzinfo is None or receive.tzinfo is None:
        raise RuntimeError("The server returned a capture or receipt time without a timezone.")
    return {
        "audio_id": chunk["id"], "location_id_at_capture": chunk["location_id"],
        "capture_time": capture.isoformat(), "received_at": receive.isoformat(),
        "capture_to_receive_seconds": (receive - capture).total_seconds(),
        "capture_age_seconds": (now - capture).total_seconds(),
        "sample_rate": chunk["sample_rate"], "duration_seconds": chunk["duration_seconds"],
        "audio_format": chunk["audio_format"], "processing_status": chunk["status"],
        "measurements": [{key: row.get(key) for key in (
            "id", "value_db", "digital_dbfs", "measurement_type", "quality_status",
            "calibration_status", "calibration_version", "is_reprocessing", "evaluation",
        )} for row in chunk.get("measurements", [])],
        "timestamp_accuracy": "Server times checked; verify device clock against an independent reference.",
        "sound_level_accuracy": "Not established by upload or checksum verification.",
    }


def inspect(args, *, now: datetime | None = None) -> dict:
    state = load_config(args.config)
    identifier = str(UUID(state["id"]))
    now = now or datetime.now(timezone.utc)
    device_only = bool(args.audio_id and args.admin_env_file is None)
    url = base_url(args.url or (state["endpoint"] if device_only else state["api_url"]))
    def get(path, **options):
        return api(url, token, path, ca_file=getattr(args, "ca_file", None), **options)
    result = {"device_id": identifier, "checked_at": now.isoformat(), "read_only": True}
    audio_id = str(args.audio_id) if args.audio_id else None
    # A known audio identifier can be inspected with the device credential alone.
    # Discovery and the location/report pages require the administrator API.
    if device_only:
        token = state.get("token")
        if not token:
            raise RuntimeError("The private file has no device credential.")
        result["scope"] = "selected recording only"
    else:
        token = admin_token(args.admin_env_file)
        device = get(f"/devices/{identifier}")
        location_id = str(UUID(device["location_id"]))
        location = get(f"/locations/{location_id}")
        result["current_assignment"] = {
            "location_id": location_id, "name": location["name"], "timezone": location["timezone"],
            "matches_private_configuration": location_id == state["location_id"],
            "device_enabled": device["enabled"], "last_contact_at": device.get("last_contact_at"),
            "calibration_configured": device.get("calibration") is not None,
        }
        readings = get(f"/measurements?{urlencode({'device_id': identifier, 'limit': 5})}")
        result["measurement_count"] = readings["total"]
        if not audio_id and readings["items"]:
            audio_id = readings["items"][0]["audio_chunk_id"]
        incidents = get(f"/incidents?{urlencode({'device_id': identifier, 'limit': 5})}")
        result["incident_count"] = incidents["total"]
        result["recent_incidents"] = [{key: row.get(key) for key in (
            "id", "location_id", "started_at", "ended_at", "status", "peak_db", "threshold_type",
        )} for row in incidents["items"]]
        selected_chunk = get(f"/audio/{UUID(str(audio_id))}") if audio_id else None
        report_location = location
        if selected_chunk and selected_chunk["location_id"] != location_id:
            report_location = get(f"/locations/{UUID(selected_chunk['location_id'])}")
        day = args.date
        if day is None and selected_chunk:
            day = datetime.fromisoformat(selected_chunk["captured_at"]).astimezone(ZoneInfo(report_location["timezone"])).date()
        day = day or now.astimezone(ZoneInfo(report_location["timezone"])).date()
        report = get(f"/daily-summaries?{urlencode({'location_id': report_location['id'], 'reporting_date': day.isoformat()})}")
        result["daily_report"] = report
        result["daily_report_note"] = "Saved location-wide report; this read-only check does not generate or recalculate it."
    if audio_id:
        chunk = get(f"/audio/{UUID(str(audio_id))}") if device_only else selected_chunk
        result["recording"] = recording_evidence(chunk, identifier, now)
        if args.verify_audio:
            original = get(f"/audio/{UUID(str(audio_id))}/file", binary=True)
            result["recording"]["stored_file_checksum_matches"] = hashlib.sha256(original).hexdigest() == chunk["checksum"]
            result["recording"]["stored_file_bytes"] = len(original)
            if not result["recording"]["stored_file_checksum_matches"]:
                raise RuntimeError("Stored audio does not match its saved SHA-256 checksum.")
    else:
        result["recording"] = None
        result["next_check"] = "No completed measurement found. If firmware logged an accepted audio ID, rerun with --audio-id to inspect pending/failed processing."
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path(".local/hardware-device.json"))
    parser.add_argument("--url", help="Override the server address; default is api_url for administrator checks or endpoint for device-only checks")
    parser.add_argument("--admin-env-file", type=Path, help="Privately read ADMIN_TOKEN for location, incident, and daily checks")
    parser.add_argument("--audio-id", type=UUID, help="Accepted server audio ID from the device log")
    parser.add_argument("--date", type=date.fromisoformat, help="Read a saved location report for YYYY-MM-DD")
    parser.add_argument("--verify-audio", action="store_true", help="Download selected original into memory and compare its saved SHA-256")
    parser.add_argument("--ca-file", type=Path, help="Trusted CA certificate for the device TLS listener; certificate and hostname checks remain enabled")
    print(json.dumps(inspect(parser.parse_args()), indent=2))


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, ValueError, KeyError):
        error = sys.exc_info()[1]
        print(str(error) if isinstance(error, RuntimeError) else "Check failed; inspect the selected configuration and server state.", file=sys.stderr)
        raise SystemExit(1)
