#!/usr/bin/env python3
"""Convert private registration/Wi-Fi settings to a private ESP-IDF header.

Never displays credentials. Generated headers and provisioned binaries are
private artifacts; do not commit or add them to the shareable source archive.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import tempfile
from urllib.parse import urlsplit
from uuid import UUID

HERE = Path(__file__).resolve().parent


class ConfigurationError(ValueError):
    """A safe field-only error message that never contains a supplied value."""


def integer(value, name, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ConfigurationError(f"{name} must be an integer from {minimum} to {maximum}")
    return value


def text(value, name, minimum=1, maximum=1024):
    if not isinstance(value, str) or not minimum <= len(value.encode()) <= maximum or "\x00" in value:
        raise ConfigurationError(f"{name} has an invalid length or type")
    return value


def milliseconds(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ConfigurationError(f"{name} must be a finite number of seconds")
    result = round(value * 1000)
    if not 1 <= result <= 86400000 or abs(result - value * 1000) > 1e-6:
        raise ConfigurationError(f"{name} must be positive, at most one day, and a whole number of milliseconds")
    return result


def generate(config: dict, ca: str) -> str:
    if not isinstance(config, dict) or any(not isinstance(config.get(key, {}), dict) for key in ("wifi", "board", "clock", "retry")):
        raise ConfigurationError("Configuration, wifi, board, clock, and retry must be JSON objects")
    if config.get("schema") != "urbanecho-hardware-v1":
        raise ConfigurationError("Expected an urbanecho-hardware-v1 registration file")
    try:
        identifier = str(UUID(text(config.get("id"), "id", 36, 36)))
    except ValueError:
        raise ConfigurationError("id must be the registered device UUID") from None
    token = text(config.get("token"), "token", 32, 299)
    if any(character.isspace() for character in token):
        raise ConfigurationError("Device token must not contain whitespace")
    wifi = config.get("wifi", {})
    ssid = text(wifi.get("ssid"), "wifi.ssid", 1, 32)
    password = text(wifi.get("password"), "wifi.password", 8, 63)
    endpoint = text(config.get("endpoint"), "endpoint", 9, 299).rstrip("/")
    try:
        parsed = urlsplit(endpoint)
        port = parsed.port
    except ValueError:
        raise ConfigurationError("endpoint must contain a valid hostname and numeric port") from None
    if any(character.isspace() for character in endpoint) or port == 0:
        raise ConfigurationError("endpoint must not contain whitespace or an invalid port")
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path:
        raise ConfigurationError("endpoint must be an HTTPS origin, without credentials, path, query, or fragment")
    if parsed.hostname in {"localhost", "127.0.0.1", "::1"}:
        raise ConfigurationError("endpoint must address the backend computer over the LAN, not the ESP itself")
    board = config.get("board", {})
    expected = {"profile": "esp32-wroom-32-inmp441", "model": "ESP-WROOM-32", "wiring_verified": True,
                "channel": "left", "sample_lsb": 8, "gpio_bclk": 26, "gpio_ws": 25, "gpio_data_in": 33}
    if any(type(board.get(key)) is not type(value) or board.get(key) != value for key, value in expected.items()):
        raise ConfigurationError("Board profile/wiring must match the confirmed ESP-WROOM-32 + INMP441 GPIO26/25/33 left-channel profile")
    rate = integer(config.get("sample_rate"), "sample_rate", 16000, 48000)
    if rate not in {16000, 32000, 44100, 48000}:
        raise ConfigurationError("sample_rate is not supported by the backend")
    capacity = integer(config.get("max_frames_per_recording", 16000), "max_frames_per_recording", 1, 16000)
    if config.get("queue_capacity", 2) != 2:
        raise ConfigurationError("The ESP-WROOM profile budgets exactly two audio buffers")
    duration = config.get("recording_duration_seconds")
    if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration <= 0:
        raise ConfigurationError("recording_duration_seconds must be positive and finite")
    if duration != int(duration):
        raise ConfigurationError("Recording duration must match an integer-second backend location interval; this RAM profile supports one second at 16 kHz")
    frames = round(duration * rate)
    if abs(duration * rate - frames) > 1e-6 or not 1 <= frames <= capacity:
        raise ConfigurationError("Recording duration does not fit the sample count or fixed buffer capacity; default is 1 second at 16 kHz")
    recording_interval = milliseconds(config.get("recording_interval_seconds"), "recording_interval_seconds")
    upload_interval = milliseconds(config.get("upload_interval_seconds"), "upload_interval_seconds")
    if recording_interval * rate < frames * 1000 or recording_interval * rate % 1000:
        raise ConfigurationError("Recording interval must cover the recording and equal an integer number of sample frames")
    if "-----BEGIN CERTIFICATE-----" not in ca or "-----END CERTIFICATE-----" not in ca or "PRIVATE KEY" in ca:
        raise ConfigurationError("--ca must contain public CA certificate PEM, never a private key")
    clock = config.get("clock", {})
    retry = config.get("retry", {})
    values = {
        "UE_WIFI_SSID": ssid, "UE_WIFI_PASSWORD": password, "UE_ENDPOINT": endpoint,
        "UE_DEVICE_ID": identifier, "UE_DEVICE_TOKEN": token, "UE_CA_CERTIFICATE": ca,
        "UE_SNTP_SERVER": text(clock.get("sntp_server", "pool.ntp.org"), "clock.sntp_server", 1, 253),
        "UE_WIRING_VERIFIED": True,
        "UE_GPIO_BCLK": 26, "UE_GPIO_WS": 25, "UE_GPIO_DATA_IN": 33, "UE_I2S_SAMPLE_LSB": 8,
        "UE_SAMPLE_RATE": rate, "UE_FRAMES_PER_RECORDING": frames, "UE_RECORDING_INTERVAL_MS": recording_interval,
        "UE_UPLOAD_INTERVAL_MS": upload_interval, "UE_MAX_FRAMES": capacity, "UE_QUEUE_CAPACITY": 2,
        "UE_MAX_CLOCK_AGE_MS": integer(clock.get("max_sync_age_seconds", 3600), "clock.max_sync_age_seconds", 60, 86400) * 1000,
        "UE_CLOCK_JUMP_TOLERANCE_MS": integer(clock.get("clock_jump_tolerance_ms", 250), "clock.clock_jump_tolerance_ms", 1, 5000),
        "UE_SNTP_UNCERTAINTY_BUDGET_MS": integer(clock.get("uncertainty_budget_ms", 100), "clock.uncertainty_budget_ms", 1, 5000),
        "UE_RETRY_MAX_ATTEMPTS": integer(retry.get("max_attempts", 8), "retry.max_attempts", 1, 100),
        "UE_RETRY_BASE_MS": integer(retry.get("base_ms", 1000), "retry.base_ms", 1, 30000),
        "UE_RETRY_CAP_MS": integer(retry.get("cap_ms", 30000), "retry.cap_ms", 1, 300000),
        "UE_RETRY_AFTER_CAP_MS": integer(retry.get("retry_after_cap_ms", 300000), "retry.retry_after_cap_ms", 1, 300000),
        "UE_HTTP_TIMEOUT_MS": integer(config.get("http_timeout_ms", 10000), "http_timeout_ms", 1000, 15000),
    }
    if values["UE_RETRY_BASE_MS"] > values["UE_RETRY_CAP_MS"] or values["UE_RETRY_CAP_MS"] > values["UE_RETRY_AFTER_CAP_MS"]:
        raise ConfigurationError("Retry base must not exceed retry cap, which must not exceed Retry-After cap")
    lines = ["#pragma once", "// PRIVATE generated configuration. Do not commit or share this header or provisioned binaries."]
    for name, value in values.items():
        literal = json.dumps(value, ensure_ascii=False) if isinstance(value, str) else str(value).lower()
        lines.append(f"#define {name} {literal}")
    return "\n".join(lines) + "\n"


def compile_check_config():
    return {
        "schema": "urbanecho-hardware-v1", "id": "00000000-0000-4000-8000-000000000006",
        "token": "COMPILE_CHECK_ONLY_NOT_A_REGISTERED_DEVICE_TOKEN",
        "endpoint": "https://192.0.2.1:8443",  # Reserved documentation address.
        "wifi": {"ssid": "URBANECHO-COMPILE-CHECK", "password": "NOT-A-REAL-WIFI-PASSWORD"},
        "board": {"profile": "esp32-wroom-32-inmp441", "model": "ESP-WROOM-32", "wiring_verified": True,
                  "channel": "left", "sample_lsb": 8, "gpio_bclk": 26, "gpio_ws": 25, "gpio_data_in": 33},
        "sample_rate": 16000, "recording_duration_seconds": 1, "recording_interval_seconds": 1,
        "upload_interval_seconds": 1, "queue_capacity": 2, "max_frames_per_recording": 16000,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sources = parser.add_mutually_exclusive_group(required=True)
    sources.add_argument("--config", type=Path, help="Private hardware-device.json registration/configuration file")
    sources.add_argument("--compile-check", action="store_true", help="Use fake credentials/reserved URL for full firmware link check only; do not flash")
    parser.add_argument("--ca", type=Path, required=True, help="Public server CA certificate PEM")
    parser.add_argument("--output", type=Path, default=HERE / "main/device_config.h")
    args = parser.parse_args()
    try:
        config = compile_check_config() if args.compile_check else json.loads(args.config.read_text(encoding="utf-8"))
        generated = generate(config, args.ca.read_text(encoding="utf-8"))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".device-config-", dir=args.output.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(generated)
            os.chmod(temporary, 0o600)
            os.replace(temporary, args.output)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    except ConfigurationError as exc:
        parser.exit(2, f"Configuration rejected: {exc}. No credentials were displayed.\n")
    except (ValueError, TypeError, KeyError, OSError):
        # Do not echo parsed values, JSON excerpts, credentials, or raw exceptions.
        parser.exit(2, "Configuration rejected. Check the private JSON fields, confirmed board/wiring, HTTPS endpoint, audio buffer limits, and public CA path; no credentials were displayed.\n")
    print("Private configuration written; credentials were not displayed.")
    if args.compile_check:
        print("COMPILE CHECK ONLY: fake credentials and reserved endpoint. Do not flash this profile.")
    else:
        print("Rebuild and flash locally. Keep the generated header and provisioned build files private.")


if __name__ == "__main__":
    main()
