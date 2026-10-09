"""Run the API and worker together on Railway's single persistent audio volume.

Schema migrations run separately before deployment. This launcher deliberately
does not import the application or print environment values.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Mapping, Sequence


LOGGER = logging.getLogger("noise.railway")
APP_UID = APP_GID = 10001
SHUTDOWN_SECONDS = 15.0
PRIVILEGED_VARIABLES = frozenset({
    "MIGRATION_DATABASE_URL", "POSTGRES_PASSWORD", "POSTGRES_USER",
})


def parse_port(environment: Mapping[str, str]) -> int:
    value = environment.get("PORT", "8000")
    if not re.fullmatch(r"[0-9]{1,5}", value):
        raise ValueError("PORT must be an integer between 1 and 65535")
    port = int(value)
    if not 1 <= port <= 65535:
        raise ValueError("PORT must be an integer between 1 and 65535")
    return port


def is_mountpoint(path: Path) -> bool:
    """Recognize Linux bind mounts as well as separate filesystems."""
    if os.path.ismount(path):
        return True
    try:
        for line in Path("/proc/self/mountinfo").read_text().splitlines():
            fields = line.split()
            if len(fields) < 5:
                continue
            # Linux mountinfo escapes whitespace and backslashes as octal.
            mounted = re.sub(r"\\([0-7]{3})", lambda match: chr(int(match[1], 8)), fields[4])
            if Path(mounted) == path:
                return True
    except OSError:
        pass
    return False


def validate_storage(environment: Mapping[str, str]) -> Path:
    audio_root = Path(environment.get("AUDIO_ROOT", "/data/audio"))
    if not audio_root.is_absolute() or audio_root == Path("/"):
        raise ValueError("AUDIO_ROOT must be an absolute, non-root directory")
    if audio_root.is_symlink():
        raise ValueError("AUDIO_ROOT must not be a symbolic link")
    audio_root = audio_root.resolve()
    if audio_root == Path("/"):
        raise ValueError("AUDIO_ROOT must be an absolute, non-root directory")
    if environment.get("RAILWAY_ENVIRONMENT_ID"):
        mounted_value = environment.get("RAILWAY_VOLUME_MOUNT_PATH")
        if not mounted_value:
            raise ValueError("Railway requires a persistent volume mounted at AUDIO_ROOT")
        mounted = Path(mounted_value)
        if not mounted.is_absolute() or mounted.resolve() != audio_root:
            raise ValueError("RAILWAY_VOLUME_MOUNT_PATH must match AUDIO_ROOT")
        if not audio_root.is_dir() or not is_mountpoint(audio_root):
            raise ValueError("The configured Railway audio volume is not mounted")
    return audio_root


def prepare_audio_root(audio_root: Path) -> None:
    """Prepare only the volume root, then permanently drop startup privileges."""
    if os.geteuid() == 0:
        audio_root.mkdir(parents=True, exist_ok=True)
        os.chown(audio_root, APP_UID, APP_GID)
        # Existing recordings retain their ownership and are never traversed.
        os.setgroups([])
        os.setgid(APP_GID)
        os.setuid(APP_UID)
    if os.geteuid() != APP_UID or os.getegid() != APP_GID:
        raise RuntimeError("The Railway runtime must run as application UID/GID 10001")
    if not audio_root.is_dir() or not os.access(audio_root, os.W_OK | os.X_OK):
        raise RuntimeError("The audio volume is not writable by the application user")
    os.umask(0o027)


def child_environment(environment: Mapping[str, str]) -> dict[str, str]:
    """Keep migration credentials out of both long-running child processes."""
    return {key: value for key, value in environment.items() if key not in PRIVILEGED_VARIABLES}


def service_commands(port: int) -> list[tuple[str, list[str]]]:
    return [
        ("api", [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0",
                 "--port", str(port), "--timeout-graceful-shutdown", "10"]),
        ("worker", [sys.executable, "-m", "app.worker"]),
    ]


def _signal_group(process: subprocess.Popen, signum: int) -> None:
    try:
        os.killpg(process.pid, signum)
    except ProcessLookupError:
        pass


def stop_services(children: Sequence[tuple[str, subprocess.Popen]], timeout: float) -> None:
    # Signal even an exited group leader: it may still have live descendants.
    for _, child in children:
        _signal_group(child, signal.SIGTERM)
    deadline = time.monotonic() + timeout
    for _, child in children:
        try:
            child.wait(timeout=max(0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            pass
    # Remove descendants that ignored TERM, including those whose leader exited.
    for _, child in children:
        _signal_group(child, signal.SIGKILL)
    kill_deadline = time.monotonic() + 2
    for _, child in children:
        try:
            child.wait(timeout=max(0, kill_deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            LOGGER.error("A child did not exit after forced shutdown")


def run_services(
    commands: Sequence[tuple[str, Sequence[str]]],
    environment: Mapping[str, str],
    *,
    shutdown_seconds: float = SHUTDOWN_SECONDS,
    poll_seconds: float = 0.1,
) -> int:
    """Return zero for requested shutdown; any unexpected child exit is fatal."""
    stopping = threading.Event()
    children: list[tuple[str, subprocess.Popen]] = []
    previous_handlers = {}

    def request_stop(signum: int, frame: object) -> None:
        stopping.set()

    try:
        for signum in (signal.SIGTERM, signal.SIGINT):
            previous_handlers[signum] = signal.signal(signum, request_stop)
        for name, command in commands:
            if stopping.is_set():
                return 0
            child = subprocess.Popen(list(command), env=dict(environment), start_new_session=True)
            children.append((name, child))
            LOGGER.info("Started %s", name)
        while not stopping.is_set():
            for name, child in children:
                code = child.poll()
                if code is not None:
                    LOGGER.error("%s exited unexpectedly with status %s; stopping both services", name, code)
                    return 1
            stopping.wait(poll_seconds)
        LOGGER.info("Shutdown requested; stopping both services")
        return 0
    except OSError as error:
        # Exception text can include command/environment details; log only type.
        LOGGER.error("Unable to start or supervise services (%s)", type(error).__name__)
        return 1
    finally:
        try:
            stop_services(children, shutdown_seconds)
        finally:
            for signum, previous in previous_handlers.items():
                signal.signal(signum, previous)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        port = parse_port(os.environ)
        audio_root = validate_storage(os.environ)
        prepare_audio_root(audio_root)
    except (ValueError, RuntimeError) as error:
        # These are fixed messages above, with no user-provided values.
        LOGGER.error("Runtime configuration rejected: %s", error)
        return 1
    except OSError as error:
        LOGGER.error("Unable to prepare persistent storage (%s)", type(error).__name__)
        return 1
    return run_services(service_commands(port), child_environment(os.environ))


if __name__ == "__main__":
    raise SystemExit(main())
