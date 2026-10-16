"""Mechanical catalog pages are archived in one guarded, backed-up rewrite."""
import json
from pathlib import Path

import pytest

from agent_console import shared_memory, wiki
from reports import wiki_cleanup

CATALOG = "수집 기준: 2026-10-16\n\n요약: 종목:SPY 관련 수집 이벤트 96건\n" + "\n".join(
    ["- [yahoo_finance] SPY S&P 500 ETF: 현재 771.79, 1D +0.28%"] * 6)
KNOWLEDGE = {"claims": [{"claim_id": "c1", "text": "Orders lagged capacity.", "source_revision_id": "r1",
                         "as_of": "2026-09-30"}], "limitations": ["single source"], "relations": []}


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_CONSOLE_SHARED_MEMORY_DIR", str(tmp_path / "memory"))
    monkeypatch.setenv("AGENT_CONSOLE_QMD_ENABLED", "0")
    monkeypatch.setattr(wiki, "_debounced_rebuild", lambda: None)
    wiki._CACHE.clear()
    yield
    wiki._CACHE.clear()


def seed():
    wiki.upsert_page({"id": "source-ticker-spy", "title": "종목:SPY", "kind": "source_digest", "body": CATALOG,
                      "source_refs": ["https://finance.yahoo.com/quote/SPY"]})
    wiki.upsert_page({"id": "topic", "title": "Capacity vs orders", "kind": "concept",
                      "body": "Capacity expanded while orders lagged, so revenue attribution needs order data.",
                      "source_refs": ["https://example.com/a"], "links": ["source-ticker-spy"],
                      "source_knowledge": KNOWLEDGE})
    wiki.upsert_page({"id": "template", "title": "수집 소스 위키: 종목:GRI · 근거 정리", "kind": "risk",
                      "body": "## 해석 경계\n\n이 카드는 수집된 원문과 다이제스트를 검색 가능한 위키 초안으로 정리한 것입니다.",
                      "source_refs": ["https://example.com/b"]})
    wiki._CACHE.clear()


def test_plan_selects_catalogs_and_templates_but_never_compiled_knowledge():
    seed()
    plan = wiki_cleanup.plan_mechanical_archive(wiki._all_wiki_pages())
    assert [a["page_id"] for a in plan] == ["source-ticker-spy", "template"]


def test_apply_archives_backs_up_and_removes_dead_links_without_losing_knowledge():
    seed()
    before = (Path(shared_memory.shared_memory_dir()) / "events.jsonl").read_text(encoding="utf-8")
    plan = wiki_cleanup.plan_mechanical_archive(wiki._all_wiki_pages())
    assert wiki_cleanup.apply_mechanical_archive(plan, dry_run=True)["backup_path"] == ""

    result = wiki_cleanup.apply_mechanical_archive(plan, dry_run=False)
    wiki._CACHE.clear()
    assert result["archived"] == ["source-ticker-spy", "template"]
    assert result["unlinked_pages"] == ["topic"]
    assert Path(result["backup_path"]).read_text(encoding="utf-8").splitlines() == [
        json.dumps(json.loads(line), ensure_ascii=False, sort_keys=True) for line in before.splitlines()]
    assert wiki.get_page("source-ticker-spy")["status"] == "archived"
    assert CATALOG in wiki.get_page("source-ticker-spy")["body"]
    topic = wiki.get_page("topic")
    assert topic["status"] != "archived" and topic["links"] == []
    assert topic["source_knowledge"]["claims"] == KNOWLEDGE["claims"]
    assert topic["source_knowledge"]["limitations"] == KNOWLEDGE["limitations"]


def test_page_changed_after_planning_is_skipped():
    seed()
    plan = wiki_cleanup.plan_mechanical_archive(wiki._all_wiki_pages())
    wiki.upsert_page({**wiki.get_page("template"), "body": "Rewritten synthesis about GRI trial data and its limits."})
    wiki._CACHE.clear()
    result = wiki_cleanup.apply_mechanical_archive(plan, dry_run=False)
    assert result["skipped_stale"] == ["template"]
    wiki._CACHE.clear()
    assert wiki.get_page("template")["status"] != "archived"
