"""위키 판단카드 중복 병합 배치 테스트."""

import os
import sys

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_CONSOLE_DB", str(tmp_path / "agent_console.sqlite3"))
    monkeypatch.setenv("AGENT_CONSOLE_SHARED_MEMORY_DIR", str(tmp_path / "data" / "shared-memory"))
    monkeypatch.setenv("AGENT_CONSOLE_QMD_ENABLED", "0")


_SIGNAL_BODY_A = (
    "커뮤니티 텔레그램 단독 신호는 공식 자료와 교차확인되기 전까지 "
    "보조 근거로만 취급한다 투자 판단 근거로 삼지 않는다"
)
_SIGNAL_BODY_B = (
    "커뮤니티 텔레그램 단독 신호는 가격 재무 공식 자료와 교차 확인되기 전까지는 "
    "보조적인 근거로 활용하며 투자 판단의 주요 근거로 삼지 않는다"
)


def _page(wiki, *, title, body, kind="playbook", surface="market", status="reviewed", **extra):
    payload = {
        "title": title,
        "summary": "s",
        "body": body,
        "surface": surface,
        "kind": kind,
        "status": status,
        "source_refs": [],
    }
    payload.update(extra)
    return wiki.upsert_page(payload)


def test_jaccard_identical_sets_is_one():
    from reports import wiki_dedup_batch as wdb

    assert wdb._jaccard({"a", "b"}, {"a", "b"}) == 1.0


def test_jaccard_disjoint_sets_is_zero():
    from reports import wiki_dedup_batch as wdb

    assert wdb._jaccard({"a"}, {"b"}) == 0.0


def test_jaccard_empty_set_is_zero():
    from reports import wiki_dedup_batch as wdb

    assert wdb._jaccard(set(), {"a"}) == 0.0
    assert wdb._jaccard(set(), set()) == 0.0


def test_find_candidate_pairs_matches_same_surface_and_kind(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from agent_console import wiki
    from reports import wiki_dedup_batch as wdb

    a = _page(wiki, title="커뮤니티 단독 신호 평가 전략", body=_SIGNAL_BODY_A)
    b = _page(wiki, title="비공식 신호 교차확인 절차", body=_SIGNAL_BODY_B)

    pairs = wdb.find_candidate_pairs(wiki._all_wiki_pages())
    assert {a["id"], b["id"]} in [{p[0]["id"], p[1]["id"]} for p in pairs]


def test_find_candidate_pairs_excludes_different_kind_surface_and_archived(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from agent_console import wiki
    from reports import wiki_dedup_batch as wdb

    a = _page(wiki, title="커뮤니티 단독 신호 평가 전략", body=_SIGNAL_BODY_A)
    different_kind = _page(wiki, title="비공식 신호 개념", body=_SIGNAL_BODY_B, kind="concept")
    different_surface = _page(wiki, title="비공식 신호 종목 절차", body=_SIGNAL_BODY_B, surface="ticker")
    archived = _page(wiki, title="비공식 신호 예전 절차", body=_SIGNAL_BODY_B, status="archived")

    pairs = wdb.find_candidate_pairs(wiki._all_wiki_pages())
    ids = [{p[0]["id"], p[1]["id"]} for p in pairs]
    assert {a["id"], different_kind["id"]} not in ids
    assert {a["id"], different_surface["id"]} not in ids
    assert {a["id"], archived["id"]} not in ids


def test_find_candidate_pairs_excludes_dissimilar_pages(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from agent_console import wiki
    from reports import wiki_dedup_batch as wdb

    a = _page(wiki, title="커뮤니티 단독 신호 평가 전략", body=_SIGNAL_BODY_A)
    b = _page(
        wiki,
        title="반도체 공급망 재편과 지정학 리스크",
        body="대만 지정학 리스크가 커지면 파운드리 공급망을 다변화해야 한다",
    )

    pairs = wdb.find_candidate_pairs(wiki._all_wiki_pages())
    assert {a["id"], b["id"]} not in [{p[0]["id"], p[1]["id"]} for p in pairs]


def test_parse_dedup_judgment_plain_json_and_code_fence():
    from reports import wiki_dedup_batch as wdb

    assert wdb._parse_dedup_judgment('{"merge": true, "reason": "같은 원칙"}') == {
        "merge": True,
        "reason": "같은 원칙",
    }
    assert wdb._parse_dedup_judgment(
        '설명\n```json\n{"merge": false, "reason": "결론 상충"}\n```'
    ) == {"merge": False, "reason": "결론 상충"}


def test_parse_dedup_judgment_rejects_missing_key_and_garbage():
    from reports import wiki_dedup_batch as wdb

    assert wdb._parse_dedup_judgment('{"reason": "no merge key"}') is None
    assert wdb._parse_dedup_judgment("자연어 답변") is None
    assert wdb._parse_dedup_judgment(None) is None


def test_judge_pair_returns_true_on_merge_confirmation():
    from reports import wiki_dedup_batch as wdb

    result = wdb._judge_pair(
        {"id": "a", "title": "A", "body": "본문 A"},
        {"id": "b", "title": "B", "body": "본문 B"},
        lambda prompt: '{"merge": true, "reason": "동일 원칙"}',
    )
    assert result == (True, "동일 원칙")


def test_judge_pair_fails_safe_on_rejection_failure_and_invalid_response():
    from reports import wiki_dedup_batch as wdb

    a = {"id": "a", "title": "A", "body": "본문 A"}
    b = {"id": "b", "title": "B", "body": "본문 B"}
    assert wdb._judge_pair(a, b, lambda prompt: '{"merge": false, "reason": "결론 상충"}') == (
        False,
        "결론 상충",
    )
    assert wdb._judge_pair(a, b, lambda prompt: "자연어 답변")[0] is False

    def broken(prompt):
        raise RuntimeError("llm unavailable")

    assert wdb._judge_pair(a, b, broken)[0] is False


def test_build_dedup_prompt_includes_bodies_and_boundary_principles():
    from reports import wiki_dedup_batch as wdb

    prompt = wdb._build_dedup_prompt(
        {"id": "a", "title": "제목A", "summary": "요약A", "body": "본문 내용 A"},
        {"id": "b", "title": "제목B", "summary": "요약B", "body": "본문 내용 B"},
    )
    assert "제목A" in prompt and "제목B" in prompt
    assert "본문 내용 A" in prompt and "본문 내용 B" in prompt
    assert "잃는 정보" in prompt
    assert "결론" in prompt


def test_pick_target_source_prefers_newer_then_confidence():
    from reports import wiki_dedup_batch as wdb

    older = {"id": "old", "updated_at": "2026-08-01T00:00:00+00:00", "confidence": 0.9}
    newer = {"id": "new", "updated_at": "2026-08-31T00:00:00+00:00", "confidence": 0.5}
    assert [p["id"] for p in wdb._pick_target_source(older, newer)] == ["new", "old"]

    same_time = "2026-08-31T00:00:00+00:00"
    low = {"id": "low", "updated_at": same_time, "confidence": 0.4}
    high = {"id": "high", "updated_at": same_time, "confidence": 0.8}
    assert [p["id"] for p in wdb._pick_target_source(low, high)] == ["high", "low"]


def test_pick_target_source_handles_missing_fields():
    from reports import wiki_dedup_batch as wdb

    target, source = wdb._pick_target_source({"id": "a"}, {"id": "b"})
    assert {target["id"], source["id"]} == {"a", "b"}


def test_run_dry_run_does_not_modify_storage(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from agent_console import wiki
    from reports import wiki_dedup_batch as wdb

    a = _page(wiki, title="커뮤니티 단독 신호 평가 전략", body=_SIGNAL_BODY_A)
    b = _page(wiki, title="비공식 신호 교차확인 절차", body=_SIGNAL_BODY_B)
    result = wdb.run(dry_run=True, llm_fn=lambda prompt: '{"merge": true, "reason": "동일 원칙"}')

    assert result["dry_run"] is True
    assert len(result["merged"]) == 1
    assert wiki.get_page(a["id"])["status"] != "archived"
    assert wiki.get_page(b["id"])["status"] != "archived"


def test_run_merges_confirmed_pair_and_archives_older_source(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from agent_console import wiki
    from reports import wiki_dedup_batch as wdb

    older = _page(
        wiki,
        title="커뮤니티 단독 신호 평가 전략",
        body=_SIGNAL_BODY_A,
        updated_at="2026-08-01T00:00:00+00:00",
    )
    newer = _page(
        wiki,
        title="비공식 신호 교차확인 절차",
        body=_SIGNAL_BODY_B,
        updated_at="2026-08-31T00:00:00+00:00",
    )
    result = wdb.run(dry_run=False, llm_fn=lambda prompt: '{"merge": true, "reason": "동일 원칙"}')

    assert len(result["merged"]) == 1
    assert result["merged"][0]["target"] == newer["id"]
    assert result["merged"][0]["source"] == older["id"]
    assert wiki.get_page(older["id"])["status"] == "archived"
    assert wiki.get_page(newer["id"])["status"] != "archived"
    assert wiki.get_page(newer["id"])["merge_history"]


def test_run_skips_rejected_pairs(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from agent_console import wiki
    from reports import wiki_dedup_batch as wdb

    a = _page(wiki, title="커뮤니티 단독 신호 평가 전략", body=_SIGNAL_BODY_A)
    b = _page(wiki, title="비공식 신호 교차확인 절차", body=_SIGNAL_BODY_B)
    result = wdb.run(dry_run=False, llm_fn=lambda prompt: '{"merge": false, "reason": "결론 상충"}')

    assert result["merged"] == []
    assert wiki.get_page(a["id"])["status"] != "archived"
    assert wiki.get_page(b["id"])["status"] != "archived"


def test_run_respects_limit_and_does_not_reconsider_merged_page(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from agent_console import wiki
    from reports import wiki_dedup_batch as wdb

    pages = [
        _page(wiki, title=f"커뮤니티 단독 신호 처리 방식 {i}", body=_SIGNAL_BODY_A)
        for i in range(3)
    ]
    llm_fn = lambda prompt: '{"merge": true, "reason": "동일 원칙"}'
    assert len(wdb.run(dry_run=False, llm_fn=llm_fn, limit=1)["merged"]) == 1

    _isolate(monkeypatch, tmp_path)
    pages = [
        _page(wiki, title=f"커뮤니티 단독 신호 처리 방식 {i}", body=_SIGNAL_BODY_A)
        for i in range(3)
    ]
    result = wdb.run(dry_run=False, llm_fn=llm_fn, limit=10)
    archived_count = sum(1 for page in pages if wiki.get_page(page["id"])["status"] == "archived")
    assert len(result["merged"]) == 2
    assert archived_count == 2


def test_main_dry_run_prints_summary_and_returns_zero(monkeypatch, tmp_path, caplog):
    _isolate(monkeypatch, tmp_path)
    caplog.set_level("INFO")
    from agent_console import wiki
    from reports import wiki_dedup_batch as wdb

    _page(wiki, title="커뮤니티 단독 신호 평가 전략", body=_SIGNAL_BODY_A)
    _page(wiki, title="비공식 신호 교차확인 절차", body=_SIGNAL_BODY_B)
    monkeypatch.setattr(sys, "argv", ["wiki_dedup_batch", "--dry-run"])
    monkeypatch.setattr(
        "agent_console.agent._try_llm_prompt",
        lambda prompt, **kwargs: '{"merge": true, "reason": "동일 원칙"}',
    )

    assert wdb.main() == 0
    assert "위키 중복 병합 완료" in caplog.text
