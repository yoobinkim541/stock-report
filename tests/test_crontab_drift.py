from __future__ import annotations


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
