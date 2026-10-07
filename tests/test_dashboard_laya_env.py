"""The optional LAYA environment is scoped to dashboard startup."""
import os
from pathlib import Path
import subprocess

import pytest


@pytest.mark.parametrize("enabled", [False, True])
def test_dashboard_optional_laya_environment(tmp_path, enabled):
    runner = Path(__file__).resolve().parents[1] / "scripts" / "run_dashboard.sh"
    binary = tmp_path / ".venv" / "bin" / "streamlit"
    binary.parent.mkdir(parents=True)
    binary.write_text('#!/bin/sh\nprintf "%s" "${LAYA_WIKI_ENABLED:-unset}"\n')
    binary.chmod(0o700)
    (tmp_path / ".env").write_text("LAYA_WIKI_ENABLED=0\n")
    if enabled:
        (tmp_path / ".env.laya").write_text("LAYA_WIKI_ENABLED=1\n")
    env = dict(os.environ, STOCK_REPORT_PROJECT_DIR=str(tmp_path))
    result = subprocess.run(["bash", str(runner)], env=env, capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr
    assert result.stdout == ("1" if enabled else "0")
