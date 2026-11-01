"""Raspberry Pi deployment scripts: rendered unit and autostart, and the kiosk supervisor loop."""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INSTALL = ROOT / "scripts" / "install-pi-kiosk.sh"
UPDATE = ROOT / "scripts" / "update-pi.sh"
KIOSK = ROOT / "scripts" / "airspace-kiosk"
PI_DIR = "/home/canterlot/github/airspace"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


def run(*args: str, env: dict | None = None, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(a) for a in args], capture_output=True, text=True, check=check, env={**os.environ, **(env or {})}
    )


@pytest.mark.parametrize("script", [INSTALL, UPDATE, KIOSK])
def test_scripts_parse(script):
    assert os.access(script, os.X_OK)
    run("bash", "-n", script)


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck not installed")
def test_shellcheck():
    run("shellcheck", "-x", INSTALL, UPDATE, KIOSK)


def unit(**env) -> str:
    return run(INSTALL, "--print-unit", env={"AIRSPACE_DIR": PI_DIR, "AIRSPACE_USER": "canterlot", **env}).stdout


def test_unit_runs_venv_binary_as_user():
    text = unit()
    assert "@" not in text
    assert "User=canterlot\n" in text
    assert f"WorkingDirectory={PI_DIR}\n" in text
    assert f"ExecStart={PI_DIR}/.venv/bin/airspace serve --port 8000 --no-access-log\n" in text
    assert f"Environment=AIRSPACE_CONFIG={PI_DIR}/airspace.toml\n" in text
    assert "After=network-online.target" in text
    assert "Restart=on-failure\n" in text
    assert "WantedBy=multi-user.target" in text


def test_unit_stays_local():
    text = unit()
    assert "0.0.0.0" not in text
    assert "--host" not in text
    assert "uv run" not in text
    assert "MAPBOX" not in text.upper()
    assert "latitude" not in text


def test_unit_settings():
    text = unit(AIRSPACE_PORT="8010", AIRSPACE_USER="pi", AIRSPACE_DIR="/srv/airspace")
    assert "ExecStart=/srv/airspace/.venv/bin/airspace serve --port 8010 --no-access-log\n" in text
    assert "User=pi\n" in text


@pytest.mark.parametrize(
    "env", [{"AIRSPACE_PORT": "80a"}, {"AIRSPACE_DIR": "relative/path"}, {"AIRSPACE_SERVICE": "air space"}]
)
def test_rejects_bad_settings(env):
    result = run(INSTALL, "--print-unit", env={"AIRSPACE_DIR": PI_DIR, **env}, check=False)
    assert result.returncode != 0
    assert "Error" in result.stderr


def test_autostart_entry():
    text = run(INSTALL, "--print-autostart", env={"AIRSPACE_DIR": PI_DIR}).stdout
    assert f"Exec={PI_DIR}/scripts/airspace-kiosk run --port 8000\n" in text
    assert text.startswith("[Desktop Entry]\n")


def test_chromium_flags():
    words = run(KIOSK, "print-command", "--port", "8000").stdout.split()
    for flag in ("--kiosk", "--password-store=basic", "--no-first-run", "--hide-crash-restore-bubble"):
        assert flag in words
    assert words[-1] == "http://127.0.0.1:8000"
    assert any(w.startswith("--user-data-dir=") for w in words)


# The supervisor test needs util-linux flock and a short socket path (macOS limits it to 104 bytes)


class Health(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200 if self.path == "/api/health" else 404)
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *args):
        pass


def wait_for(predicate, timeout=20.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.2)
    return False


@pytest.mark.skipif(not (shutil.which("flock") and shutil.which("curl")), reason="needs flock and curl")
def test_supervisor_waits_relaunches_and_stops():
    base = Path(tempfile.mkdtemp(prefix="ak", dir="/tmp"))
    try:
        runtime, bin_dir, home = base / "run", base / "bin", base / "home"
        for d in (runtime, bin_dir, home):
            d.mkdir()
        launches = base / "launches"
        fake = bin_dir / "chromium"
        # Stays a bash process so its command line keeps the arguments, as Chromium's does
        fake.write_text(
            f'#!/usr/bin/env bash\necho "$*" >> {launches}\ntrap \'kill $!; exit 1\' TERM\nsleep 300 &\nwait\n'
        )
        fake.chmod(0o755)
        sock = socket.socket(socket.AF_UNIX)
        sock.bind(str(runtime / "wayland-1"))

        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        env = {
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "HOME": str(home),
            "XDG_RUNTIME_DIR": str(runtime),
            "XDG_STATE_HOME": str(base / "state"),
            "WAYLAND_DISPLAY": "wayland-1",
        }
        env.pop("DISPLAY", None)
        kiosk = [str(KIOSK)]
        supervisor = subprocess.Popen([*kiosk, "run", "--port", str(port)], env=env, stderr=subprocess.PIPE, text=True)
        server = None
        try:
            time.sleep(3)
            assert not launches.exists(), "launched before the backend responded"

            server = HTTPServer(("127.0.0.1", port), Health)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            assert wait_for(lambda: launches.exists() and len(launches.read_text().splitlines()) == 1)
            assert f"http://127.0.0.1:{port}" in launches.read_text()

            # A second supervisor exits without opening another window
            second = subprocess.run([*kiosk, "run", "--port", str(port)], env=env, capture_output=True, text=True, timeout=10)
            assert second.returncode == 0
            assert "another kiosk supervisor" in second.stderr

            # A crashed browser is reopened
            subprocess.run(["pkill", "-f", "--", f"--user-data-dir={base}/state"], check=True)
            assert wait_for(lambda: len(launches.read_text().splitlines()) == 2, timeout=30)

            status = subprocess.run([*kiosk, "status", "--port", str(port)], env=env, capture_output=True, text=True)
            assert "supervisor: running" in status.stdout
            assert "backend:    responding" in status.stdout

            subprocess.run([*kiosk, "stop"], env=env, check=True, capture_output=True)
            supervisor.wait(timeout=10)
            assert wait_for(
                lambda: subprocess.run(["pgrep", "-f", "--", f"--user-data-dir={base}/state"], capture_output=True).returncode
                != 0,
                timeout=10,
            )
        finally:
            if supervisor.poll() is None:
                supervisor.kill()
            subprocess.run(["pkill", "-f", "--", f"--user-data-dir={base}/state"], capture_output=True)
            if server:
                server.shutdown()
            sock.close()
    finally:
        shutil.rmtree(base, ignore_errors=True)
