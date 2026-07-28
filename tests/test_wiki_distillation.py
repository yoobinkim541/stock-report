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
