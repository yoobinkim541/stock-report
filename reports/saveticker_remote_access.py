"""Bounded, private remote access to the existing source browser session."""

from __future__ import annotations

import ipaddress
import socket
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable, Sequence


_DISPLAY_UNIT = "hermes-source-display"
_BROWSER_UNIT = "hermes-source-browser"
_VNC_UNIT = "hermes-source-vnc-local"
_NOVNC_UNIT = "hermes-source-novnc-private"
_CDP_PORT = 9225
_VNC_PORT = 5905
_NOVNC_PORT = 6081


def _run_command(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True, check=False, timeout=15)


def _probe(kind: str, host: str, port: int) -> int | bytes:
    if kind == "cdp":
        with urllib.request.urlopen(f"http://{host}:{port}/json/version", timeout=2) as response:
            return response.status
    if kind == "http":
        try:
            with urllib.request.urlopen(f"http://{host}:{port}/", timeout=2) as response:
                return response.status
        except urllib.error.HTTPError as exc:
            return exc.code
    if kind == "vnc":
        with socket.create_connection((host, port), timeout=2) as connection:
            connection.settimeout(2)
            return connection.recv(12)
    raise ValueError(f"unknown probe kind: {kind}")


def _status(run_command: Callable[[Sequence[str]], object], unit: str) -> bool:
    try:
        result = run_command(["systemctl", "--user", "is-active", unit])
    except (OSError, subprocess.SubprocessError):
        return False
    return getattr(result, "returncode", 1) == 0 and getattr(result, "stdout", "").strip() == "active"


def _start_unit(
    run_command: Callable[[Sequence[str]], object],
    unit: str,
    command: Sequence[str],
    max_seconds: int,
) -> None:
    runtime = "2h" if max_seconds == 7200 else f"{max_seconds}s"
    argv = [
        "systemd-run", "--user", f"--unit={unit}", "--collect",
        f"--property=RuntimeMaxSec={runtime}", *command,
    ]
    result = run_command(argv)
    if getattr(result, "returncode", 1) != 0:
        error = getattr(result, "stderr", "").strip()
        raise RuntimeError(f"Could not start {unit}" + (f": {error}" if error else ""))


def _reachable(probe: Callable[[str, str, int], int | bytes], kind: str,
               host: str, port: int) -> bool:
    try:
        answer = probe(kind, host, port)
    except (OSError, urllib.error.URLError, TimeoutError):
        return False
    if kind == "vnc":
        return isinstance(answer, bytes) and answer.startswith(b"RFB ")
    return answer == 200


def ensure_remote_access(
    max_seconds: int = 7200,
    *,
    run_command: Callable[[Sequence[str]], object] = _run_command,
    probe: Callable[[str, str, int], int | bytes] = _probe,
    wait: Callable[[float], None] = time.sleep,
) -> str:
    """Return a Tailscale-only noVNC URL, starting missing remote units if needed."""
    if isinstance(max_seconds, bool) or not isinstance(max_seconds, int) or not 1 <= max_seconds <= 7200:
        raise ValueError("max_seconds must be an integer from 1 to 7200")

    result = run_command(["tailscale", "ip", "-4"])
    if getattr(result, "returncode", 1) != 0:
        raise RuntimeError("Could not resolve Tailscale IPv4 address")
    address = getattr(result, "stdout", "").strip()
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError as exc:
        raise RuntimeError("Tailscale IPv4 address is missing or invalid") from exc
    if not isinstance(parsed, ipaddress.IPv4Address) or parsed not in ipaddress.ip_network("100.64.0.0/10"):
        raise RuntimeError("Tailscale IPv4 address must be within 100.64.0.0/10")
    host = str(parsed)

    for unit in (_DISPLAY_UNIT, _BROWSER_UNIT):
        if not _status(run_command, unit):
            raise RuntimeError(f"Required existing service is not active: {unit}; refusing process recovery")
    if not _reachable(probe, "cdp", "127.0.0.1", _CDP_PORT):
        raise RuntimeError("Existing Chromium CDP endpoint is not reachable on loopback port 9225")

    vnc_healthy = _reachable(probe, "vnc", "127.0.0.1", _VNC_PORT)
    if not vnc_healthy and not _status(run_command, _VNC_UNIT):
        _start_unit(run_command, f"{_VNC_UNIT}-recovery-{uuid.uuid4().hex[:12]}", [
            "/usr/bin/x11vnc", "-display", ":97", "-localhost", "-rfbport", str(_VNC_PORT),
            "-forever", "-shared", "-nopw", "-no6", "-noipv6",
        ], max_seconds)
    http_healthy = _reachable(probe, "http", host, _NOVNC_PORT)
    if not http_healthy and not _status(run_command, _NOVNC_UNIT):
        _start_unit(run_command, f"{_NOVNC_UNIT}-recovery-{uuid.uuid4().hex[:12]}", [
            "/usr/bin/websockify", "--web=/usr/share/novnc",
            f"{host}:{_NOVNC_PORT}", f"127.0.0.1:{_VNC_PORT}",
        ], max_seconds)

    for _ in range(10):
        if (_reachable(probe, "vnc", "127.0.0.1", _VNC_PORT)
                and _reachable(probe, "http", host, _NOVNC_PORT)):
            return f"http://{host}:{_NOVNC_PORT}/vnc.html?autoconnect=true&resize=scale"
        wait(0.5)
    raise RuntimeError("Private VNC/noVNC health check failed (VNC RFB or noVNC HTTP)")
