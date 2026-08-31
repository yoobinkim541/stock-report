from __future__ import annotations

from pathlib import Path


def _isolate(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AGENT_CONSOLE_SHARED_MEMORY_DIR", str(tmp_path / "data" / "shared-memory"))
    monkeypatch.setenv("AGENT_CONSOLE_QMD_ENABLED", "0")


def _page(wiki, *, title, body, kind="risk", **extra):
    payload = {
        "title": title,
        "summary": "기존 요약",
        "body": body,
        "surface": "market",
        "kind": kind,
        "status": "draft",
        "source_refs": [],
    }
    payload.update(extra)
    return wiki.upsert_page(payload)


def test_select_backfill_candidates_returns_only_legacy_judgment_pages(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from agent_console import wiki
    from reports import wiki_backfill as backfill

    legacy = _page(wiki, title="기존 리스크", body="기존 자유서술")
    current = _page(
        wiki,
        title="최신 리스크",
        body="최신 문서\n\n> **리포트 인용 요약**: 최신 요약",
        report_citation="최신 요약",
    )
    _page(wiki, title="메모", body="메모", kind="note")

    candidates = backfill.select_backfill_candidates(wiki._all_wiki_pages())

    assert [page["id"] for page in candidates] == [legacy["id"]]
    assert current["id"] not in [page["id"] for page in candidates]


def test_parse_backfill_response_accepts_only_body_and_citation():
    from reports import wiki_backfill as backfill

    parsed = backfill._parse_backfill_response(
        '{"body":"정리된 본문\\n\\n> **리포트 인용 요약**: 한 줄","report_citation":"한 줄"}'
    )
    assert parsed == {"body": "정리된 본문\n\n> **리포트 인용 요약**: 한 줄", "report_citation": "한 줄"}
    assert backfill._parse_backfill_response('{"body":"본문"}') is None
    assert backfill._parse_backfill_response('{"action":"delete"}') is None


def test_run_dry_run_does_not_modify_and_live_run_preserves_provenance(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from agent_console import wiki
    from reports import wiki_backfill as backfill

    page = _page(
        wiki,
        title="기존 리스크",
        body="기존 자유서술",
        source_refs=["https://example.com/source"],
        merge_history=[{"event_id": "merge-1", "source_ids": ["old"]}],
    )
    response = lambda prompt: '{"body":"새로운 백과사전형 본문","report_citation":"리포트용 한 줄"}'

    dry = backfill.run(dry_run=True, llm_fn=response, limit=1)
    assert len(dry["updated"]) == 1
    assert wiki.get_page(page["id"])["report_citation"] == ""

    live = backfill.run(dry_run=False, llm_fn=response, limit=1)
    assert len(live["updated"]) == 1
    loaded = wiki.get_page(page["id"])
    assert loaded["report_citation"] == "리포트용 한 줄"
    assert loaded["body"].count("리포트 인용 요약") == 1
    assert loaded["source_refs"] == ["https://example.com/source"]
    assert loaded["merge_history"][0]["event_id"] == "merge-1"


def test_run_all_repeats_batches_until_candidates_are_exhausted(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from agent_console import wiki
    from reports import wiki_backfill as backfill

    _page(wiki, title="첫 번째 판단", body="기존 본문 1")
    _page(wiki, title="두 번째 판단", body="기존 본문 2")

    def llm(_prompt):
        return '{"body":"백과사전형 본문","report_citation":"판단 근거 요약"}'

    result = backfill.run_all(llm_fn=llm, batch_size=1, max_batches=5)

    assert result["batches"] == 3
    assert result["candidates_considered"] == 2
    assert len(result["updated"]) == 2
    assert result["remaining"] == 0
