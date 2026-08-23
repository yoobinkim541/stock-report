#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

RELEVANT_MARKERS = (
    "reports.source_pipeline",
    "reports/source_collector.py",
    "reports.source_wiki_curator",
    "wiki_archive.log",
    "reports.wiki_health_check",
    "reports.wiki_distillation",
    "news_llm_snapshot.py",
)


def relevant_cron_lines(text: str) -> set[str]:
    lines: set[str] = set()
    for raw in str(text or "").splitlines():
        command = raw.split("#", 1)[0].strip()
        if not command or not any(marker in command for marker in RELEVANT_MARKERS):
            continue
        lines.add(re.sub(r"\s+", " ", command))
    return lines


def drift_report(expected_text: str, installed_text: str) -> dict:
    expected = relevant_cron_lines(expected_text)
    installed = relevant_cron_lines(installed_text)
    missing = sorted(expected - installed)
    unexpected = sorted(installed - expected)
    return {"ok": not missing and not unexpected, "missing": missing, "unexpected": unexpected}


def _installed_crontab() -> str:
    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=10)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or "crontab -l failed").strip())
    return result.stdout


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare installed source/wiki cron lines with the repository source of truth.")
    parser.add_argument("--expected-file", default="deploy/crontab.stock-report")
    parser.add_argument("--installed-file")
    args = parser.parse_args(argv)
    expected = Path(args.expected_file).read_text(encoding="utf-8")
    installed = Path(args.installed_file).read_text(encoding="utf-8") if args.installed_file else _installed_crontab()
    report = drift_report(expected, installed)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

