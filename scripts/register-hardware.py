#!/usr/bin/env python3
"""Register one uncalibrated physical device; keep its one-time credential private."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import sys
from uuid import UUID, uuid4

from hardware_common import APIError, SCHEMA, admin_token, api, base_url, is_simulated, load_config, locked_config, save_private


def prepare(args, token: str) -> dict:
    with locked_config(args.config):
        return _prepare(args, token)


def _prepare(args, token: str) -> dict:
    url, endpoint = base_url(args.url), base_url(args.endpoint)
    board_profile = getattr(args, "board_profile", None)
    if not endpoint.startswith("https://"):
        raise RuntimeError("Use the verified https:// device listener address so device credentials and audio use TLS.")
    location_id = str(UUID(str(args.location_id)))
    if is_simulated(args.microphone_model):
        raise RuntimeError("Use the actual physical microphone model, without demo or synthetic labels.")
    if args.config.exists():
        state = load_config(args.config)
        if (state.get("location_id") != location_id or state.get("api_url") != url
                or state.get("endpoint") != endpoint or state.get("microphone_model") != args.microphone_model
                or state.get("board", {}).get("profile") != board_profile):
            raise RuntimeError("This file belongs to another setup. Do not overwrite or reassign its device.")
    else:
        state = None
    location = api(url, token, f"/locations/{location_id}")
    if is_simulated(location.get("name", "")):
        raise RuntimeError("Create or select a physical location in Management; do not use a simulated/demo location.")
    threshold = api(url, token, f"/locations/{location_id}/threshold").get("current")
    if not threshold:
        raise RuntimeError("The location has no current threshold rule; configure it in Management first.")
    if state is None:
        state = {
            "schema": SCHEMA, "id": str(uuid4()), "token": None, "location_id": location_id,
            "timezone": location["timezone"], "api_url": url, "endpoint": endpoint,
            "microphone_model": args.microphone_model, "calibration": None,
            "sample_rate": 16000, "recording_duration_seconds": threshold["interval_seconds"],
            "recording_interval_seconds": threshold["interval_seconds"],
            "upload_interval_seconds": threshold["interval_seconds"],
            "wifi": {"ssid": "", "password": ""},
            "board": {"profile": board_profile, "model": "", "wiring_verified": False, "sample_lsb": None, "channel": None,
                      "gpio_bclk": None, "gpio_ws": None, "gpio_data_in": None},
            "queue_capacity": 2, "max_frames_per_recording": 16000,
            "tls": {"ca_certificate": ""},
            "registration_status": "prepared", "prepared_at": datetime.now(timezone.utc).isoformat(),
        }
        if board_profile == "esp32-wroom-32-inmp441":
            state["board"].update(model="ESP-WROOM-32", sample_lsb=8, channel="left",
                                  gpio_bclk=26, gpio_ws=25, gpio_data_in=33)
        # Save the identifier before POST. If the response is lost, a repeat does
        # not silently register an additional physical device.
        save_private(args.config, state)
    device_id = str(UUID(state["id"]))
    try:
        device = api(url, token, f"/devices/{device_id}")
    except APIError as exc:
        if exc.status != 404:
            raise
        if state.get("token"):
            raise RuntimeError("The saved device is missing. Check the server/database before registering anything else.") from None
        device = api(url, token, "/devices", {
            "id": device_id, "location_id": location_id,
            "microphone_model": args.microphone_model, "calibration": None,
        })
        if not device.get("token"):
            raise RuntimeError("The server did not return a credential. Keep this configuration; do not register another device.")
        state["token"] = device["token"]
        state["registration_status"] = "registered"
        save_private(args.config, state)
    if device.get("location_id") != location_id or device.get("microphone_model") != args.microphone_model:
        raise RuntimeError("The saved device was reassigned or changed; inspect it in Management before continuing.")
    if not state.get("token"):
        raise RuntimeError("The device already exists but its one-time token is unavailable. Restore the private configuration; tokens cannot be recovered from their hashes.")
    if not device.get("enabled", False):
        raise RuntimeError("The saved physical device is disabled. Review it in Management before connecting.")
    return {"device_id": device_id, "location_id": location_id,
            "measurement_type": threshold["threshold_type"], "interval_seconds": threshold["interval_seconds"],
            "calibration_status": "configured; verify physically" if device.get("calibration") else "uncalibrated",
            "configuration": str(args.config)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:8000", help="Administrator API address on this Mac")
    parser.add_argument("--endpoint", required=True, help="Ingest server address reachable from the physical device")
    parser.add_argument("--location-id", required=True, type=UUID)
    parser.add_argument("--microphone-model", default="INMP441")
    parser.add_argument("--board-profile", choices=["esp32-wroom-32-inmp441"],
                        help="User-confirmed ESP-WROOM-32 with INMP441 SCK26/WS25/SD33/LR grounded; confirm physical wiring separately")
    parser.add_argument("--config", type=Path, default=Path(".local/hardware-device.json"))
    parser.add_argument("--admin-env-file", type=Path, help="Read only ADMIN_TOKEN from this file, without printing it")
    args = parser.parse_args()
    if not 1 <= len(args.microphone_model.strip()) <= 100:
        parser.error("Use a microphone model containing 1 to 100 characters")
    result = prepare(args, admin_token(args.admin_env_file))
    print(f"Physical device: {result['device_id']}; location: {result['location_id']}")
    print(f"Private configuration saved at {result['configuration']}. Credentials are not printed.")
    print(f"Record {result['interval_seconds']}-second chunks to match the current threshold rule.")
    print("Board pins, DMA alignment, Wi-Fi, and the TLS trust certificate still require verified configuration.")
    print(f"Calibration: {result['calibration_status']}.")
    if result["measurement_type"] == "spl_z_leq" and result["calibration_status"] == "uncalibrated":
        print("Audio can be uploaded, but SPL results/alerts remain ineligible until valid physical calibration is configured.")
        print("For initial uncalibrated transmission tests, use a physical location configured for digital dBFS.")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, ValueError, KeyError):
        error = sys.exc_info()[1]
        # Expected RuntimeErrors are already sanitized. Avoid raw parse values,
        # filesystem errors, or unexpected backend fields on other exceptions.
        print(str(error) if isinstance(error, RuntimeError) else "Setup failed; check the selected file and configuration.", file=sys.stderr)
        raise SystemExit(1)
