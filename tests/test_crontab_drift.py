from __future__ import annotations

from pathlib import Path


def test_relevant_cron_lines_normalizes_whitespace_and_comments():
    from scripts.check_crontab_drift import relevant_cron_lines

    left = """
5,35 * * * * cd /repo && uv run python -m reports.source_pipeline --group news >> /tmp/source_news.log 2>&1 # comment
8,38 * * * * cd /repo && uv run python -m reports.source_wiki_curator --hours 48 --limit 0 >> /tmp/wiki.log 2>&1
"""
    right = """
5,35  *  * * *   cd /repo && uv run python -m reports.source_pipeline --group news >> /tmp/source_news.log 2>&1
8,38 * * * * cd /repo && uv run python -m reports.source_wiki_curator --hours 48 --limit 0 >> /tmp/wiki.log 2>&1 # another comment
"""

    assert relevant_cron_lines(left) == relevant_cron_lines(right)


def test_drift_report_detects_wiki_limit_and_missing_source_group():
    from scripts.check_crontab_drift import drift_report

    expected = """
5,35 * * * * uv run python -m reports.source_pipeline --group news
9,39 * * * * uv run python -m reports.source_pipeline --group prediction
8,38 * * * * uv run python -m reports.source_wiki_curator --hours 48 --limit 0
"""
    installed = """
5,35 * * * * uv run python -m reports.source_pipeline --group news
8,38 * * * * uv run python -m reports.source_wiki_curator --hours 48 --limit 8
0 0 * * * uv run python tests/bot_smoke_test.py
"""

    report = drift_report(expected, installed)

    assert report["ok"] is False
    assert any("--group prediction" in line for line in report["missing"])
    assert any("--limit 0" in line for line in report["missing"])
    assert any("--limit 8" in line for line in report["unexpected"])
    assert all("bot_smoke_test" not in line for line in report["unexpected"])


def test_drift_report_ignores_unrelated_user_cron_lines():
    from scripts.check_crontab_drift import drift_report

    expected = "5,35 * * * * uv run python -m reports.source_pipeline --group news\n"
    installed = expected + "* * * * * bash $HOME/projects/myWiki/watchdog.sh\n"

    assert drift_report(expected, installed) == {"ok": True, "missing": [], "unexpected": []}


def test_deployed_cron_path_includes_qmd_install_dir():
    crontab = (Path(__file__).parents[1] / "deploy" / "crontab.stock-report").read_text(encoding="utf-8")

    assert "PATH=/home/ubuntu/.hermes/node/bin:/home/ubuntu/.local/bin:" in crontab


def test_markers_cover_every_wiki_and_source_job_in_deployed_crontab():
    """crontab 의 reports.wiki_* / source_* / institution_watch 작업이 모두 RELEVANT_MARKERS 에
    걸려야 한다 — 빠지면 그 작업의 drift 는 영원히 탐지되지 않는다."""
    import re
    from scripts.check_crontab_drift import RELEVANT_MARKERS

    crontab = (Path(__file__).parents[1] / "deploy" / "crontab.stock-report").read_text(encoding="utf-8")
    active = [ln.split("#", 1)[0] for ln in crontab.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    modules = {m for ln in active for m in re.findall(r"reports\.(?:wiki_\w+|source_\w+|institution_watch)", ln)}
    assert modules, "크론에 위키/소스 작업이 없다?"
    uncovered = {m for m in modules if not any(marker in m or m in marker for marker in RELEVANT_MARKERS)}
    assert not uncovered, f"RELEVANT_MARKERS 누락: {sorted(uncovered)}"
