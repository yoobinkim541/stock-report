#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_console.qmd_generation import probe_isolation


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe local QMD generation isolation without using an existing index.")
    parser.add_argument("--executable", default=shutil.which("qmd") or "qmd")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="qmd-generation-probe-") as temp:
        result = probe_isolation(
            executable=args.executable,
            workspace=Path(temp),
            runner=subprocess.run,
        )
    print(json.dumps(result, sort_keys=True))
    return 0 if result["supported"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
