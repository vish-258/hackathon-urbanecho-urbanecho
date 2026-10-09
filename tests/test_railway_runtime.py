"""Railway launcher checks use temporary directories and tiny test processes."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

import pytest

from scripts import serve_railway as runtime


def test_nondefault_port_and_bounded_api_shutdown():
    port = runtime.parse_port({"PORT": "19387"})
    api, worker = runtime.service_commands(port)
    assert api[1][api[1].index("--port") + 1] == "19387"
    assert api[1][api[1].index("--timeout-graceful-shutdown") + 1] == "10"
    assert worker[1][-2:] == ["-m", "app.worker"]
    assert runtime.parse_port({}) == 8000


@pytest.mark.parametrize("value", ["", "0", "65536", "-1", "1.2", " 8000", "8000\n", "true"])
def test_invalid_port_rejected(value):
    with pytest.raises(ValueError, match="PORT"):
        runtime.parse_port({"PORT": value})


def test_railway_refuses_missing_volume(tmp_path):
    with pytest.raises(ValueError, match="persistent volume"):
        runtime.validate_storage({"RAILWAY_ENVIRONMENT_ID": "test", "AUDIO_ROOT": str(tmp_path)})


def test_railway_refuses_wrong_mount_path(tmp_path):
    with pytest.raises(ValueError, match="must match"):
        runtime.validate_storage({"RAILWAY_ENVIRONMENT_ID": "test", "AUDIO_ROOT": str(tmp_path),
                                  "RAILWAY_VOLUME_MOUNT_PATH": str(tmp_path / "elsewhere")})


def test_railway_refuses_ephemeral_directory_even_with_volume_variable(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "is_mountpoint", lambda _: False)
    with pytest.raises(ValueError, match="not mounted"):
        runtime.validate_storage({"RAILWAY_ENVIRONMENT_ID": "test", "AUDIO_ROOT": str(tmp_path),
                                  "RAILWAY_VOLUME_MOUNT_PATH": str(tmp_path)})


def test_railway_accepts_matching_real_mount(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "is_mountpoint", lambda path: path == tmp_path.resolve())
    assert runtime.validate_storage({"RAILWAY_ENVIRONMENT_ID": "test", "AUDIO_ROOT": str(tmp_path),
                                     "RAILWAY_VOLUME_MOUNT_PATH": str(tmp_path)}) == tmp_path.resolve()


def test_volume_symlink_rejected(tmp_path):
    link = tmp_path / "link"
    link.symlink_to(tmp_path)
    with pytest.raises(ValueError, match="symbolic link"):
        runtime.validate_storage({"AUDIO_ROOT": str(link)})


@pytest.mark.parametrize("path", ["/", "/..", "relative/path"])
def test_unsafe_audio_root_rejected(path):
    with pytest.raises(ValueError, match="absolute, non-root"):
        runtime.validate_storage({"AUDIO_ROOT": path})


def test_mountinfo_recognizes_bind_mount_with_escaped_space(tmp_path, monkeypatch):
    mounted = tmp_path / "audio recordings"
    monkeypatch.setattr(runtime.os.path, "ismount", lambda _: False)
    line = "36 25 0:32 / " + str(mounted).replace(" ", "\\040") + " rw - ext4 /dev/disk rw\n"
    monkeypatch.setattr(Path, "read_text", lambda _: line)
    assert runtime.is_mountpoint(mounted)
    assert not runtime.is_mountpoint(tmp_path)


def test_privilege_drop_touches_only_volume_root(tmp_path, monkeypatch):
    recording = tmp_path / "preserved.wav"
    recording.write_bytes(b"untouched")
    calls = []
    identity = {"uid": 0, "gid": 0}
    monkeypatch.setattr(runtime.os, "geteuid", lambda: identity["uid"])
    monkeypatch.setattr(runtime.os, "getegid", lambda: identity["gid"])
    monkeypatch.setattr(runtime.os, "chown", lambda *args: calls.append(("chown", *args)))
    monkeypatch.setattr(runtime.os, "setgroups", lambda groups: calls.append(("groups", groups)))

    def set_identity(key, value):
        calls.append((key, value))
        identity[key] = value

    monkeypatch.setattr(runtime.os, "setgid", lambda value: set_identity("gid", value))
    monkeypatch.setattr(runtime.os, "setuid", lambda value: set_identity("uid", value))
    monkeypatch.setattr(runtime.os, "umask", lambda value: calls.append(("umask", value)))
    runtime.prepare_audio_root(tmp_path)
    assert calls == [("chown", tmp_path, 10001, 10001), ("groups", []),
                     ("gid", 10001), ("uid", 10001), ("umask", 0o027)]
    assert recording.read_bytes() == b"untouched"


def test_unexpected_runtime_identity_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime.os, "geteuid", lambda: 20000)
    with pytest.raises(RuntimeError, match="10001"):
        runtime.prepare_audio_root(tmp_path)


def test_children_do_not_inherit_migration_credentials():
    original = {"MIGRATION_DATABASE_URL": "secret-1", "POSTGRES_PASSWORD": "secret-2",
                "POSTGRES_USER": "owner", "DATABASE_URL": "application-connection", "PORT": "8090"}
    assert runtime.child_environment(original) == {"DATABASE_URL": "application-connection", "PORT": "8090"}
    assert "MIGRATION_DATABASE_URL" in original


@pytest.mark.parametrize("exit_code", [0, 7])
def test_any_unexpected_child_exit_stops_sibling(exit_code, monkeypatch):
    real_popen = subprocess.Popen
    processes = []

    def recording_popen(*args, **kwargs):
        assert kwargs["start_new_session"] is True
        process = real_popen(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(runtime.subprocess, "Popen", recording_popen)
    result = runtime.run_services([
        ("api", [sys.executable, "-c", "import time; time.sleep(30)"]),
        ("worker", [sys.executable, "-c", f"raise SystemExit({exit_code})"]),
    ], os.environ, shutdown_seconds=0.5, poll_seconds=0.01)
    assert result == 1
    assert len(processes) == 2
    assert all(process.poll() is not None for process in processes)


def test_failed_second_spawn_stops_first_child(monkeypatch, caplog):
    real_popen = subprocess.Popen
    processes = []

    def fail_second_spawn(*args, **kwargs):
        if processes:
            raise OSError("deliberately sensitive detail must not be logged")
        process = real_popen(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(runtime.subprocess, "Popen", fail_second_spawn)
    assert runtime.run_services([
        ("api", [sys.executable, "-c", "import time; time.sleep(30)"]),
        ("worker", [sys.executable, "-c", "pass"]),
    ], os.environ, shutdown_seconds=0.5) == 1
    assert processes[0].poll() is not None
    assert "deliberately sensitive detail" not in caplog.text


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT])
def test_shutdown_signal_is_forwarded_and_handlers_restored(signum, monkeypatch):
    real_popen = subprocess.Popen
    processes = []
    timer = None
    old_handler = signal.getsignal(signum)

    def recording_popen(*args, **kwargs):
        nonlocal timer
        process = real_popen(*args, **kwargs)
        processes.append(process)
        if len(processes) == 2:
            # The supervisor has installed its signal handlers before spawning.
            timer = threading.Timer(0.1, lambda: os.kill(os.getpid(), signum))
            timer.start()
        return process

    monkeypatch.setattr(runtime.subprocess, "Popen", recording_popen)
    command = [sys.executable, "-c", "import time; time.sleep(30)"]
    assert runtime.run_services([("api", command), ("worker", command)], os.environ,
                                shutdown_seconds=0.5, poll_seconds=0.01) == 0
    timer.join()
    assert all(process.poll() is not None for process in processes)
    assert signal.getsignal(signum) == old_handler


def test_shutdown_kills_child_that_ignores_term(tmp_path):
    ready = tmp_path / "ready"
    script = ("import signal,time,pathlib; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
              f"pathlib.Path({str(ready)!r}).touch(); time.sleep(30)")
    child = subprocess.Popen([sys.executable, "-c", script], start_new_session=True)
    try:
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists()
        started = time.monotonic()
        runtime.stop_services([("worker", child)], timeout=0.1)
        assert time.monotonic() - started < 2
        assert child.returncode == -signal.SIGKILL
    finally:
        if child.poll() is None:
            os.killpg(child.pid, signal.SIGKILL)
            child.wait()
