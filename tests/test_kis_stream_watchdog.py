from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "kis_stream_watchdog.sh"
DEPENDENCIES = (
    "kis_stream.py",
    "providers/kis_quote.py",
    "providers/realtime_quotes.py",
    "providers/orderflow_store.py",
    "providers/intraday_bars.py",
)


def _sandbox(tmp_path: Path) -> tuple[Path, dict[str, str], Path, Path]:
    project = tmp_path / "project"
    scripts = project / "scripts"
    providers = project / "providers"
    home = tmp_path / "home"
    scripts.mkdir(parents=True)
    providers.mkdir()
    home.mkdir()
    copied = scripts / SCRIPT.name
    shutil.copy2(SCRIPT, copied)
    (project / ".env").write_text("REALTIME_ENABLED=true\n", encoding="utf-8")
    for rel in DEPENDENCIES:
        path = project / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# dependency\n", encoding="utf-8")
    launch_log = tmp_path / "launch.log"
    fake_uv = tmp_path / "fake-uv"
    fake_uv.write_text(
        "#!/usr/bin/env bash\nprintf '%s\\n' \"$*\" >> \"$KIS_TEST_LAUNCH_LOG\"\n",
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)
    env = {
        **os.environ,
        "HOME": str(home),
        "KIS_WATCHDOG_UV": str(fake_uv),
        "KIS_WATCHDOG_LOG": str(tmp_path / "stream.log"),
        "KIS_WATCHDOG_LOCK": str(tmp_path / "watchdog.lock"),
        "KIS_TEST_LAUNCH_LOG": str(launch_log),
    }
    return copied, env, launch_log, project


def _worker(home: Path) -> subprocess.Popen:
    worker = subprocess.Popen(["sleep", "300"])
    pid_file = home / ".local" / "state" / "stock-report" / "kis_stream.pid"
    pid_file.parent.mkdir(parents=True)
    pid_file.write_text(str(worker.pid), encoding="utf-8")
    return worker


def test_watchdog_restarts_exact_worker_when_dependency_is_newer(tmp_path):
    script, env, launch_log, project = _sandbox(tmp_path)
    worker = _worker(Path(env["HOME"]))
    try:
        future = time.time() + 10
        os.utime(project / "providers" / "orderflow_store.py", (future, future))

        result = subprocess.run(
            ["bash", str(script)], env=env, text=True, capture_output=True, timeout=20,
        )

        assert result.returncode == 0, result.stderr
        assert "코드 변경 감지(stale)" in result.stdout
        assert worker.poll() is not None
        deadline = time.time() + 2
        while not launch_log.exists() and time.time() < deadline:
            time.sleep(0.01)
        assert "run python" in launch_log.read_text(encoding="utf-8")
    finally:
        if worker.poll() is None:
            worker.terminate()
            worker.wait(timeout=5)


def test_watchdog_keeps_healthy_worker_when_sources_are_older(tmp_path):
    script, env, launch_log, project = _sandbox(tmp_path)
    old = time.time() - 120
    for rel in DEPENDENCIES:
        os.utime(project / rel, (old, old))
    worker = _worker(Path(env["HOME"]))
    try:
        result = subprocess.run(
            ["bash", str(script)], env=env, text=True, capture_output=True, timeout=20,
        )

        assert result.returncode == 0, result.stderr
        assert worker.poll() is None
        assert not launch_log.exists()
    finally:
        worker.terminate()
        worker.wait(timeout=5)


def test_watchdog_tracks_direct_stream_dependencies_and_grace_window():
    body = SCRIPT.read_text(encoding="utf-8")
    for rel in DEPENDENCIES:
        assert rel in body
    assert "START_EPOCH + 5" in body
