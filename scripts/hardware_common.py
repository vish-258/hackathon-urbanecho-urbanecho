"""Small, secret-safe standard-library helpers for physical-device setup tools."""
from __future__ import annotations

import json
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import re
import stat
import ssl
import tempfile
import urllib.error
import urllib.parse
import urllib.request

SCHEMA = "urbanecho-hardware-v1"


class APIError(RuntimeError):
    def __init__(self, method: str, path: str, status: int | None):
        self.status = status
        message = f"{method} {path}: " + (f"HTTP {status}" if status else "connection failed")
        guidance = {
            401: "Check the configured credential; do not paste it into chat.",
            403: "The credential was rejected or the device is disabled.",
            404: "The selected saved record does not exist on this server.",
            409: "The identifier is already in use; do not retry with a new identifier.",
            413: "The recording exceeds the server upload limit.",
            422: "Check the audio format, metadata, and registered assignment.",
        }.get(status, "Check that the intended server is running and reachable.")
        super().__init__(f"{message}. {guidance}")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    # Never forward a bearer credential to an unexpected redirect destination.
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def base_url(value: str) -> str:
    parsed = urllib.parse.urlsplit(value)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in {"", "/"}):
        raise RuntimeError("Use an http:// or https:// server address without credentials, path, or query.")
    try:
        parsed.port
    except ValueError:
        raise RuntimeError("The server address has an invalid port.") from None
    return value.rstrip("/")


def api(base: str, token: str, path: str, payload: dict | None = None,
        *, binary: bool = False, maximum: int = 20_000_000, ca_file: Path | None = None):
    method = "GET" if payload is None else "POST"
    req = urllib.request.Request(base_url(base) + path,
        data=None if payload is None else json.dumps(payload).encode(),
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
        method=method)
    try:
        tls = ssl.create_default_context(cafile=str(ca_file) if ca_file else None)
        with urllib.request.build_opener(NoRedirect(), urllib.request.HTTPSHandler(context=tls)).open(req, timeout=30) as response:
            data = response.read(maximum + 1)
            if len(data) > maximum:
                raise RuntimeError("Server response exceeds the configured read limit.")
            return data if binary else json.loads(data)
    except urllib.error.HTTPError as exc:
        # A response body or exception URL can contain secrets. Do not echo either.
        raise APIError(method, path, exc.code) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise APIError(method, path, None) from None
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise RuntimeError("The server returned an invalid JSON response.") from None


def admin_token(env_file: Path | None = None) -> str:
    token = os.environ.get("ADMIN_TOKEN", "").strip()
    if token:
        return token
    if env_file is not None:
        try:
            lines = env_file.read_text().splitlines()
        except OSError:
            raise RuntimeError("Cannot read the administrator environment file.") from None
        for line in lines:
            key, separator, value = line.strip().partition("=")
            if separator and key.strip() == "ADMIN_TOKEN":
                token = value.strip()
                if len(token) >= 2 and token[0] == token[-1] and token[0] in "\"'":
                    token = token[1:-1]
                if token:
                    return token
    raise RuntimeError("Use --admin-env-file .env from the project folder, or set ADMIN_TOKEN privately.")


def save_private(path: Path, value: dict):
    if path.is_symlink():
        raise RuntimeError("Refusing to write credentials through a symbolic link.")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=".hardware-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(value, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_config(path: Path) -> dict:
    if path.is_symlink():
        raise RuntimeError("Refusing to load a credential file through a symbolic link.")
    try:
        if stat.S_IMODE(path.stat().st_mode) & 0o077:
            raise RuntimeError("The credential file must be private: set its permissions to 600.")
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        raise RuntimeError("Cannot read a valid private hardware configuration file.") from None
    if not isinstance(value, dict) or value.get("schema") != SCHEMA:
        raise RuntimeError("The selected file is not an UrbanEcho hardware configuration.")
    return value


def is_simulated(value: str) -> bool:
    return bool(re.search(r"\b(?:SIMULATED|SYNTHETIC|DEMO|DEMONSTRATION)\b", value, re.IGNORECASE))


@contextmanager
def locked_config(path: Path):
    """Do not allow two local registration processes to create competing IDs."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = path.with_name(path.name + ".lock")
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Another registration is using this configuration; wait for it to finish.") from None
        yield
    finally:
        os.close(descriptor)
