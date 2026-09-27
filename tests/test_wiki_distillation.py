"""
test_wiki_distillation.py — source_digest → playbook/risk/concept 증류 크론 테스트.

검증:
  - select_distillation_candidates(): source_digest 만·이미 판단카드 링크된 건 제외·
    evidence 많은 순·limit 적용
  - _distill_one(): LLM이 create 를 주면 payload 생성(links 에 원본 digest 포함),
    skip/쓰레기 출력이면 None
  - run(): dry_run 은 저장 안 함, 실제 실행은 upsert_page 로 draft 페이지 생성
"""
import os
import sys

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_CONSOLE_DB", str(tmp_path / "agent_console.sqlite3"))
    monkeypatch.setenv("AGENT_CONSOLE_SHARED_MEMORY_DIR", str(tmp_path / "data" / "shared-memory"))
    monkeypatch.setenv("AGENT_CONSOLE_QMD_ENABLED", "0")


def test_select_distillation_candidates_filters_and_sorts(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from agent_console import wiki
    from reports import wiki_distillation as wd

    linked_playbook = wiki.upsert_page({
        "title": "이미 있는 판단", "summary": "s", "body": "b",
        "surface": "market", "kind": "playbook", "status": "reviewed", "source_refs": [],
    })
    digest_linked = wiki.upsert_page({
        "title": "이미 연결된 다이제스트", "summary": "s", "body": "b",
        "surface": "market", "kind": "source_digest", "status": "reviewed", "source_refs": [],
        "links": [linked_playbook["id"]],
    })
    digest_unlinked_few = wiki.upsert_page({
        "title": "근거 적은 다이제스트", "summary": "s", "body": "b",
        "surface": "market", "kind": "source_digest", "status": "reviewed", "source_refs": [],
    })
    digest_unlinked_many = {**wiki.get_page(
        wiki.upsert_page({
            "title": "근거 많은 다이제스트", "summary": "s", "body": "b",
            "surface": "market", "kind": "source_digest", "status": "reviewed", "source_refs": [],
        })["id"]
    ), "evidence_ids": ["e1", "e2", "e3"]}

    pages = [digest_linked, digest_unlinked_few, digest_unlinked_many, wiki.get_page(linked_playbook["id"])]
    candidates = wd.select_distillation_candidates(pages, limit=5)

    ids = [p["id"] for p in candidates]
    assert digest_linked["id"] not in ids  # 이미 판단카드로 연결됨 — 제외
    assert linked_playbook["id"] not in ids  # source_digest 가 아님 — 제외
    assert ids[0] == digest_unlinked_many["id"]  # evidence 많은 게 먼저
    assert digest_unlinked_few["id"] in ids


def test_select_distillation_candidates_respects_limit():
    from reports import wiki_distillation as wd

    pages = [
        {"id": f"d{i}", "kind": "source_digest", "status": "reviewed", "links": [], "backlinks": []}
        for i in range(10)
    ]
    candidates = wd.select_distillation_candidates(pages, limit=2)

    assert len(candidates) == 2


def test_select_distillation_candidates_can_recover_exhausted_failure():
    from reports import wiki_distillation as wd

    page = {
        "id": "failed-source",
        "kind": "source_digest",
        "status": "reviewed",
        "links": [],
        "backlinks": [],
        "body": "cached source body",
        "distillation_state": {"status": "failed", "attempts": 3},
    }

    assert wd.select_distillation_candidates([page]) == []
    assert wd.select_distillation_candidates([page], include_exhausted=True) == [page]


def test_distillation_batch_size_can_be_tuned_by_environment(monkeypatch):
    from reports import wiki_distillation as wd

    monkeypatch.setenv("WIKI_DISTILLATION_BATCH_SIZE", "12")

    assert wd._distillation_batch_size() == 12


def test_institution_digest_is_distillable(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from agent_console import wiki
    from reports import wiki_distillation as wd

    digest = wiki.upsert_page({
        "title": "기관 공통 패턴: 현금 확대",
        "summary": "s",
        "body": "b",
        "surface": "market",
        "kind": "source_digest",
        "status": "reviewed",
        "source_refs": ["wiki:institution-watch-1"],
        "tags": ["wiki", "market", "source_digest", "institution_watch"],
    })

    candidates = wd.select_distillation_candidates(wiki.list_pages(status="all", limit=50))

    assert digest["id"] in [page["id"] for page in candidates]


def test_distill_one_creates_payload_linked_to_source(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from agent_console import wiki
    from reports import wiki_distillation as wd

    digest = wiki.upsert_page({
        "title": "수집 소스 위키: 금리/거시", "summary": "요약", "body": "본문 다이제스트",
        "surface": "market", "kind": "source_digest", "status": "reviewed", "source_refs": [],
    })

    llm_fn = lambda prompt: (
        '{"action":"create","kind":"playbook","title":"금리 국면 대응 원칙",'
        '"summary":"금리 상승기 대응","body":"금리 상승기엔 듀레이션을 줄인다.",'
        '"status":"draft","confidence":0.7,"reason":"패턴 반복 확인"}'
    )

    payload = wd._distill_one(wiki.get_page(digest["id"]), llm_fn)

    assert payload["kind"] == "playbook"
    assert payload["title"] == "금리 국면 대응 원칙"
    assert digest["id"] in payload["links"]
    assert f"wiki:{digest['id']}" in payload["source_refs"]


def test_distill_one_inherits_source_evidence_and_uses_stable_id(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from agent_console import wiki
    from reports import wiki_distillation as wd

    digest = wiki.upsert_page({
        "title": "수집 소스 위키: AI 수요", "summary": "요약", "body": "본문",
        "surface": "market", "kind": "source_digest", "status": "draft",
        "source_refs": ["https://example.com/source"],
        "evidence_ids": ["e1", "e2"],
        "conflicting_evidence_ids": ["e3"],
        "staleness_policy": "refresh_after_12h",
        "answer_hints": ["교차확인"],
    })
    response = '{"action":"create","kind":"risk","title":"AI 수요 리스크","summary":"s","body":"b","status":"draft"}'

    first = wd._distill_one(wiki.get_page(digest["id"]), lambda prompt: response)
    second = wd._distill_one(wiki.get_page(digest["id"]), lambda prompt: response)

    assert first["id"] == second["id"]
    assert first["source_refs"] == ["https://example.com/source", f"wiki:{digest['id']}"]
    assert first["evidence_ids"] == ["e1", "e2"]
    assert first["conflicting_evidence_ids"] == ["e3"]
    assert first["staleness_policy"] == "refresh_after_12h"
    assert first["answer_hints"] == ["교차확인"]


def test_distill_one_returns_none_on_skip():
    from reports import wiki_distillation as wd

    llm_fn = lambda prompt: '{"action":"skip","reason":"단순 사실 나열"}'

    payload = wd._distill_one({"id": "d1", "title": "t", "summary": "s", "body": "b"}, llm_fn)

    assert payload is None


def test_distill_one_returns_none_on_invalid_kind():
    from reports import wiki_distillation as wd

    llm_fn = lambda prompt: '{"action":"create","kind":"source_digest","title":"t","summary":"s","body":"b"}'

    payload = wd._distill_one({"id": "d1", "title": "t", "summary": "s", "body": "b"}, llm_fn)

    assert payload is None  # source_digest 는 이 경로에서 생성 금지


def test_distill_one_returns_none_on_llm_failure():
    from reports import wiki_distillation as wd

    def broken_llm(prompt):
        raise RuntimeError("llm down")

    payload = wd._distill_one({"id": "d1", "title": "t", "summary": "s", "body": "b"}, broken_llm)

    assert payload is None


def test_previous_result_guard_rejects_a_different_ticker_card():
    from reports import wiki_distillation as wd

    previous = {
        "id": "distill-collision",
        "title": "수집 소스 위키: 종목:LPSN · 근거 정리",
        "tags": ["wiki", "ticker:lpsn"],
        "links": ["source-ticker-lpsn"],
        "source_refs": ["wiki:source-ticker-lpsn"],
    }
    page = {
        "id": "source-ticker-soun",
        "title": "수집 소스 위키: 종목:SOUN",
        "tags": ["wiki", "ticker:soun"],
    }

    assert wd._previous_belongs_to_page(previous, page) is False


def test_previous_result_guard_does_not_match_short_ticker_as_substring():
    from reports import wiki_distillation as wd

    previous = {
        "id": "distill-collision",
        "title": "수집 소스 위키: 종목:TEAM · 근거 정리",
        "tags": ["wiki", "ticker:team"],
    }
    page = {
        "id": "source-ticker-e",
        "title": "수집 소스 위키: 종목:E",
        "tags": ["wiki", "ticker:e"],
    }

    assert wd._previous_belongs_to_page(previous, page) is False


def test_collision_safe_result_id_does_not_reuse_another_ticker_card():
    from reports import wiki_distillation as wd

    payload = {"id": "distill-collision", "title": "수집 소스 위키: 종목:SOUN · 근거 정리"}
    page = {"id": "source-ticker-soun", "title": "수집 소스 위키: 종목:SOUN"}
    existing = {
        "distill-collision": {
            "id": "distill-collision",
            "title": "수집 소스 위키: 종목:LPSN · 근거 정리",
            "tags": ["ticker:lpsn"],
        }
    }

    repaired = wd._collision_safe_result_id(payload, page, existing)

    assert repaired.startswith("distill-collision-r")
    assert repaired != payload["id"]


def test_local_only_distillation_uses_source_evidence_without_llm(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from agent_console import wiki
    from reports import wiki_distillation as wd

    digest = wiki.upsert_page({
        "title": "수집 소스 위키: 원유 공급 위험",
        "summary": "원유 공급 차질 가능성이 관찰됨",
        "body": "원문 다이제스트 본문",
        "surface": "market",
        "kind": "source_digest",
        "status": "draft",
        "source_refs": ["https://example.com/article"],
        "evidence_ids": ["e1"],
        "tags": ["wiki", "source_digest", "risk"],
    })
    page = wiki.get_page(digest["id"])
    page["_source_articles"] = [{
        "title": "원문 제목",
        "text": "원문에 실제로 보관된 사실 문장입니다.",
        "evidence_id": "e1",
    }]

    def fail_llm(_prompt):
        raise AssertionError("local-only 경로에서는 LLM을 호출하면 안 됨")

    payload, status, reason = wd._distill_one_with_status(page, fail_llm, local_only=True)

    assert status == "created"
    assert reason == ""
    assert payload["kind"] == "risk"
    assert "원문에 실제로 보관된 사실 문장" in payload["body"]
    assert payload["report_citation"]


def test_local_only_distillation_can_use_digest_when_article_is_unavailable():
    from reports import wiki_distillation as wd

    page = {
        "id": "digest-1",
        "title": "수집 소스 위키: 단순 관찰",
        "summary": "원문 캐시가 없는 다이제스트 관찰",
        "body": "다이제스트에 보관된 관찰 문장",
        "surface": "market",
        "kind": "source_digest",
        "status": "draft",
        "source_refs": ["https://example.com/source"],
        "evidence_ids": ["e1"],
        "tags": ["wiki", "source_digest", "source:saveticker"],
        "_require_articles": True,
        "_source_articles": [],
    }

    payload, status, _reason = wd._distill_one_with_status(page, lambda _: "", local_only=True)

    assert status == "created"
    assert payload["source_refs"] == ["https://example.com/source", "wiki:digest-1"]


def test_run_dry_run_does_not_persist(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from agent_console import wiki
    from reports import wiki_distillation as wd

    wiki.upsert_page({
        "title": "다이제스트", "summary": "s", "body": "b",
        "surface": "market", "kind": "source_digest", "status": "reviewed", "source_refs": [],
    })
    llm_fn = lambda prompt: (
        '{"action":"create","kind":"risk","title":"위험 신호","summary":"s","body":"b","status":"draft"}'
    )

    result = wd.run(dry_run=True, llm_fn=llm_fn)

    assert result["candidates_considered"] == 1
    assert len(result["created"]) == 1
    assert not any(p.get("kind") == "risk" for p in wiki.list_pages(status="all", limit=50))


def test_run_persists_created_pages(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from agent_console import wiki
    from reports import wiki_distillation as wd

    wiki.upsert_page({
        "title": "다이제스트", "summary": "s", "body": "b",
        "surface": "market", "kind": "source_digest", "status": "reviewed", "source_refs": [],
    })
    llm_fn = lambda prompt: (
        '{"action":"create","kind":"risk","title":"위험 신호","summary":"s","body":"b","status":"draft"}'
    )

    result = wd.run(dry_run=False, llm_fn=llm_fn)

    assert len(result["created"]) == 1
    saved_pages = wiki.list_pages(status="all", limit=50)
    assert any(p.get("kind") == "risk" and p.get("title") == "위험 신호" for p in saved_pages)


def test_run_batches_failed_distillation_state_updates(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from agent_console import wiki
    from reports import wiki_distillation as wd

    for title in ("첫 다이제스트", "두 번째 다이제스트"):
        wiki.upsert_page({
            "title": title, "summary": "s", "body": "b",
            "surface": "market", "kind": "source_digest", "status": "reviewed", "source_refs": [],
        })

    original_batch_upsert_pages = wiki.batch_upsert_pages
    batches = []

    def capture_batch(pages):
        pages = list(pages)
        batches.append(pages)
        return original_batch_upsert_pages(pages)

    monkeypatch.setattr(wiki, "batch_upsert_pages", capture_batch)

    result = wd.run(
        dry_run=False,
        fulltext=False,
        limit=10,
        llm_fn=lambda _prompt: "not json",
        rebuild_artifacts=False,
    )

    assert result["candidates_considered"] == 2
    assert result["created"] == []
    assert len(batches) == 1
    assert len(batches[0]) == 2
    assert {page["distillation_state"]["status"] for page in batches[0]} == {"failed"}


def test_run_skips_already_linked_digests(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from agent_console import wiki
    from reports import wiki_distillation as wd

    playbook = wiki.upsert_page({
        "title": "기존 판단", "summary": "s", "body": "b",
        "surface": "market", "kind": "playbook", "status": "reviewed", "source_refs": [],
    })
    wiki.upsert_page({
        "title": "이미 연결된 다이제스트", "summary": "s", "body": "b",
        "surface": "market", "kind": "source_digest", "status": "reviewed", "source_refs": [],
        "links": [playbook["id"]],
    })
    calls = []
    llm_fn = lambda prompt: calls.append(prompt) or '{"action":"skip"}'

    result = wd.run(dry_run=True, llm_fn=llm_fn)

    assert result["candidates_considered"] == 0
    assert calls == []


def test_distillation_notification_is_coalesced_during_cooldown(monkeypatch, tmp_path):
    from reports import wiki_distillation as wd

    state_path = tmp_path / "wiki-distillation-notify.json"
    monkeypatch.setenv("WIKI_DISTILLATION_NOTIFY_STATE_FILE", str(state_path))
    monkeypatch.setenv("WIKI_DISTILLATION_NOTIFY_COOLDOWN_HOURS", "24")
    monkeypatch.setattr(wd, "_notification_now", lambda: "2026-08-30T00:00:00+00:00")
    sent = []
    monkeypatch.setattr(wd.notify, "send_telegram", lambda text, **kwargs: sent.append(text) or True)

    first = [{"id": "p1", "kind": "risk", "status": "draft", "title": "첫 리스크"}]
    second = [{"id": "p2", "kind": "playbook", "status": "draft", "title": "두 번째 플레이북"}]

    assert wd._notify_created_pages(first) is True
    assert wd._notify_created_pages(second) is False
    assert len(sent) == 1
    assert "첫 리스크" in sent[0]
    assert "두 번째 플레이북" not in sent[0]

    monkeypatch.setattr(wd, "_notification_now", lambda: "2026-08-31T00:00:01+00:00")
    assert wd._notify_created_pages([]) is True
    assert len(sent) == 2
    assert "두 번째 플레이북" in sent[1]
