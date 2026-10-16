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


def test_archived_judgment_link_does_not_suppress_refresh(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from agent_console import wiki
    from reports import wiki_distillation as wd

    archived = wiki.upsert_page({
        "title": "보관된 판단", "summary": "s", "body": "b",
        "surface": "market", "kind": "playbook", "status": "archived", "source_refs": [],
    })
    digest = wiki.upsert_page({
        "title": "고아가 된 다이제스트", "summary": "s", "body": "충분한 근거",
        "surface": "market", "kind": "source_digest", "status": "reviewed", "source_refs": [],
        "links": [archived["id"]],
        "distillation_state": {"status": "created", "last_result_id": archived["id"]},
    })

    pages = wiki.list_pages(status="all", limit=50)
    candidates = wd.select_distillation_candidates(pages, limit=5)

    assert digest["id"] in [page["id"] for page in candidates]


def test_fulltext_distillation_rejects_digest_when_article_archive_is_missing():
    from reports import wiki_distillation as wd

    page = {
        "id": "source-digest-1",
        "title": "원문 보관이 끝난 자료",
        "summary": "요약",
        "body": "반복적으로 확인된 가격·수급·실적 변화의 관계를 설명하는 충분한 다이제스트 근거입니다. "
                "단일 기사보다 여러 시점의 자료를 비교했고 적용 조건과 반례도 기록되어 있습니다.",
        "_require_articles": True,
        "_source_articles": [],
    }
    response = '{"action":"create","kind":"concept","title":"다이제스트 기반 개념",' \
               '"summary":"s","body":"b","status":"draft"}'

    payload, outcome, reason = wd._distill_one_with_status(page, lambda prompt: response)

    assert payload is None
    assert outcome == "failed"
    assert "article" in reason


def test_changed_kind_creates_distinct_draft_without_relabeling_existing_knowledge():
    from reports import wiki_distillation as wd

    page = {
        "id": "source-digest-1",
        "_previous_knowledge": {"id": "knowledge-1", "kind": "concept", "body": "old",
                                "source_refs": ["https://old.example/source"], "evidence_ids": ["old-evidence"]},
    }
    response = '{"action":"create","kind":"risk","title":"Updated",' \
               '"summary":"s","body":"b"}'

    payload, outcome, reason = wd._distill_one_with_status(page, lambda prompt: response)

    assert outcome == "created"
    assert reason == ""
    assert payload["id"] == wd._distillation_id("source-digest-1", "risk")
    assert payload["id"] != "knowledge-1"
    assert payload["kind"] == "risk"
    assert "knowledge-1" in payload["links"]
    assert "old" not in payload["body"]
    assert "https://old.example/source" not in payload["source_refs"]
    assert "old-evidence" not in payload["evidence_ids"]
    assert page["_previous_knowledge"]["kind"] == "concept"


def test_distillation_rejects_json_embedded_in_unstructured_text():
    from reports import wiki_distillation as wd

    response = 'model commentary: {"action":"create","kind":"concept",' \
               '"title":"Invented","summary":"s","body":"b"}'

    payload, outcome, reason = wd._distill_one_with_status(
        {"id": "source-1"}, lambda prompt: response
    )

    assert payload is None
    assert outcome == "failed"
    assert reason == "invalid curation JSON"


def test_article_cache_does_not_promote_excerpt_to_fulltext(monkeypatch, tmp_path):
    from datetime import datetime, timezone
    from reports import source_collector as collector
    from reports.evidence_cards import event_to_evidence_card
    from reports import wiki_distillation as wd
    import json

    now = datetime(2026, 9, 9, 10, tzinfo=timezone.utc)
    event = {"source": "official", "title": "Capacity", "url": "https://example.org/report",
             "collected_at": now.isoformat(), "body_excerpt": "only preview"}
    collector._event_file(tmp_path, now).write_text(json.dumps(event) + "\n", encoding="utf-8")
    page = {"evidence_ids": [event_to_evidence_card(event, now=now).id]}

    wd._attach_article_context([page], cache_dir=tmp_path, now=now)

    assert page["_source_articles"] == []


def test_structured_snapshot_is_not_eligible_as_fulltext_article():
    from reports import wiki_distillation as wd

    page = {"id": "source-fred", "body": "A digest of a structured observation.",
            "_require_articles": True,
            "_source_articles": [{"evidence_id": "e1", "url": "https://fred.example/series",
                                  "text": "Latest value: 3.2", "content_kind": "macro_snapshot",
                                  "provenance": "fred"}]}

    payload, status, reason = wd._distill_one_with_status(
        page, lambda _: (_ for _ in ()).throw(AssertionError("snapshot must not enter article synthesis"))
    )

    assert payload is None
    assert status == "failed"
    assert "article" in reason


def test_long_complete_json_is_not_truncated_before_parsing():
    from reports import wiki_distillation as wd

    response = '{"action":"create","kind":"concept","title":"Long",' \
               '"summary":"s","body":"' + ("x" * 12_000) + '"}'

    payload, status, reason = wd._distill_one_with_status({"id": "source-long"}, lambda _: response)

    assert status == "created"
    assert reason == ""
    assert payload["title"] == "Long"


def test_distillation_reads_persistent_extracted_text_and_preserves_raw_hash(monkeypatch, tmp_path):
    from datetime import datetime, timezone
    from reports import raw_archive
    from reports import wiki_distillation as wd
    import hashlib

    reports_root = tmp_path / "reports"
    monkeypatch.setenv("STOCK_REPORT_REPORTS_DIR", str(reports_root))
    original = b'{"content":"immutable source payload"}'
    record = raw_archive.save_raw_artifact(
        "saveticker_article", "json", datetime(2026, 9, 1, tzinfo=timezone.utc),
        "Persistent article", "https://example.org/article", original, ".json",
    )
    extracted = "The archived article describes a persistent supply constraint." \
                " It distinguishes observed shipments from management forecasts."
    raw_archive.save_extracted_text(record, extracted)
    page = {"evidence_ids": ["e1"], "source_refs": [record["url"], record["text_path"]]}

    wd._attach_article_context([page], cache_dir=tmp_path / "empty-event-cache")

    article = page["_source_articles"][0]
    assert article["text"] == extracted
    assert article["source_text_scope"] == "extracted_text"
    assert article["raw_sha256"] == hashlib.sha256(original).hexdigest()
    assert (reports_root / "raw" / "saveticker_article").exists()
    assert raw_archive.read_raw_artifact(record["raw_path"]) == original


def test_extracted_text_reader_rejects_path_outside_configured_archive(monkeypatch, tmp_path):
    from reports import raw_archive, wiki_distillation as wd

    monkeypatch.setenv("STOCK_REPORT_REPORTS_DIR", str(tmp_path / "reports"))
    outside = tmp_path / "private.txt"
    outside.write_text("must not be read", encoding="utf-8")
    calls = []
    monkeypatch.setattr(raw_archive, "read_raw_text", lambda path: calls.append(path) or "unsafe")

    assert wd._extracted_text_record(str(outside)) is None
    assert calls == []


def test_refresh_prompt_keeps_conflicting_sources_distinct_and_payload_provenance():
    from reports import wiki_distillation as wd
    import json
    page = {
        "id": "source-conflict",
        "evidence_ids": ["e-support", "e-conflict"],
        "conflicting_evidence_ids": ["e-conflict"],
        "_source_articles": [
            {"evidence_id": "e-support", "url": "https://example.org/a", "text": "Shipments increased.",
             "content_kind": "article", "source_text_scope": "article_fulltext",
             "provenance": "saveticker", "raw_sha256": "a" * 64, "evidence_role": "supporting"},
            {"evidence_id": "e-conflict", "url": "https://example.org/b", "text": "Shipments declined.",
             "content_kind": "article", "source_text_scope": "article_fulltext",
             "provenance": "official", "raw_sha256": "b" * 64, "evidence_role": "conflicting"},
        ],
    }
    prompt = wd._build_distillation_prompt(page)
    plan = {"action": "create", "kind": "concept", "title": "Conflicting reports",
            "summary": "Conflicting shipment reports", "body": "The sources disagree [S1] [S2]."}

    payload, status, _ = wd._distill_one_with_status(page, lambda _: json.dumps(plan))

    assert "상충" in prompt or "충돌" in prompt
    assert "article_fulltext" in prompt
    assert status == "created"
    assert "e-conflict" in payload["conflicting_evidence_ids"]
    assert "raw-sha256:" + "a" * 64 in payload["source_refs"]
    assert "source_text_scope:article_fulltext" in payload["tags"]


def test_maintenance_schema_contract_is_prepended():
    from agent_console.wiki_contract import maintenance_schema
    from reports import wiki_distillation as wd

    schema = maintenance_schema()
    prompt = wd._build_distillation_prompt({"id": "source-schema"})

    assert prompt.startswith(schema.strip() + "\n")
    assert "raw" in prompt
    assert "contradiction" in prompt


def test_distillation_json_rejects_nonfinite_and_excessive_nesting():
    from reports import wiki_distillation as wd

    assert wd._parse_distillation_plan('{"action":"create","kind":"concept","title":"t","body":"b","confidence":NaN}') is None
    nested = '{"action":"create","kind":"concept","title":"t","body":"b","extra":' \
             + ("[" * 150) + '0' + ("]" * 150) + '}'
    assert wd._parse_distillation_plan(nested) is None


def test_distillation_batch_size_can_be_tuned_by_environment(monkeypatch):
    from reports import wiki_distillation as wd

    monkeypatch.setenv("WIKI_DISTILLATION_BATCH_SIZE", "12")

    assert wd._distillation_batch_size() == 12


def test_distillation_worker_count_is_bounded_by_environment(monkeypatch):
    from reports import wiki_distillation as wd

    monkeypatch.setenv("WIKI_DISTILLATION_LLM_WORKERS", "4")

    assert wd._distillation_workers() == 4

    monkeypatch.setenv("WIKI_DISTILLATION_LLM_WORKERS", "999")
    assert wd._distillation_workers() == 8


def test_legacy_protocol_failures_get_one_reason_scoped_retry():
    from reports import wiki_distillation as wd

    for reason in ("invalid curation JSON", "refresh must preserve knowledge kind"):
        legacy = {"id": reason, "kind": "source_digest", "evidence_ids": ["e1"],
                  "links": ["knowledge"],
                  "distillation_state": {"status": "failed", "attempts": 5, "reason": reason}}
        assert wd._distillation_is_eligible(legacy)
        pages = [legacy, {"id": "knowledge", "kind": "concept", "status": "reviewed"}]
        assert wd.select_distillation_candidates(pages) == [legacy]
        legacy["distillation_state"]["protocol_version"] = 2
        assert not wd._distillation_is_eligible(legacy)
        assert wd.select_distillation_candidates(pages) == []


def test_unrelated_capped_failure_stays_ineligible():
    from reports import wiki_distillation as wd

    page = {"id": "unrelated", "kind": "source_digest", "evidence_ids": ["e1"],
            "distillation_state": {"status": "failed", "attempts": 5, "reason": "provider timeout"}}

    assert not wd._distillation_is_eligible(page)


def test_protocol_version_survives_wiki_state_normalization(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from agent_console import wiki

    saved = wiki.upsert_page({
        "id": "protocol-state", "kind": "source_digest", "status": "draft", "title": "State",
        "distillation_state": {"status": "failed", "attempts": 4, "reason": "invalid curation JSON",
                               "protocol_version": 2},
    })

    assert saved["distillation_state"]["protocol_version"] == 2


def test_distillation_failure_mark_writes_current_protocol(monkeypatch):
    from reports import wiki_distillation as wd

    monkeypatch.setattr(wd.wiki, "upsert_page", lambda page: page)
    page = {"id": "legacy", "kind": "source_digest", "evidence_ids": ["e1"],
            "distillation_state": {"status": "failed", "attempts": 5,
                                   "reason": "invalid curation JSON"}}

    marked = wd._mark_distillation_attempt(page, status="failed", reason="invalid curation JSON")

    assert marked["distillation_state"]["protocol_version"] == 2
    assert not wd._distillation_is_eligible(marked)


def test_long_article_tail_reaches_model_without_truncation(monkeypatch, tmp_path):
    from datetime import datetime, timezone
    from reports import source_collector as collector
    from reports.evidence_cards import event_to_evidence_card
    from reports import wiki_distillation as wd
    import json

    now = datetime(2026, 9, 9, 10, tzinfo=timezone.utc)
    body = "ordinary article text. " * 220 + "TAIL_FACT_DO_NOT_TRUNCATE"
    event = {"source": "official", "title": "Long body", "url": "https://example.org/long",
             "collected_at": now.isoformat(), "body_raw": body,
             "fulltext_status": "full",
             "classification": {"kind": "article"}}
    collector._event_file(tmp_path, now).write_text(json.dumps(event) + "\n", encoding="utf-8")
    page = {"id": "source-long-article", "kind": "source_digest",
            "evidence_ids": [event_to_evidence_card(event, now=now).id]}
    wd._attach_article_context([page], cache_dir=tmp_path, now=now)
    seen = []
    plan = '{"action":"create","kind":"concept","title":"Tail fact",' \
           '"summary":"Tail fact","body":"Tail fact [S1]"}'

    payload, status, reason = wd._distill_one_with_status(
        {**page, "_require_articles": True}, lambda prompt: seen.append(prompt) or plan
    )

    assert status == "created", reason
    assert "TAIL_FACT_DO_NOT_TRUNCATE" in seen[0]
    assert len(page["_source_articles"][0]["text"]) == len(body)
    assert page["_source_articles"][0]["text_complete"] is True


def test_overbudget_article_fails_visibly_without_passing_partial_text(monkeypatch, tmp_path):
    from datetime import datetime, timezone
    from reports import source_collector as collector
    from reports.evidence_cards import event_to_evidence_card
    from reports import wiki_distillation as wd
    import json

    now = datetime(2026, 9, 9, 10, tzinfo=timezone.utc)
    body = "x" * 20_001
    event = {"source": "official", "title": "Oversized body", "url": "https://example.org/large",
             "collected_at": now.isoformat(), "body_raw": body,
             "classification": {"kind": "article"}}
    collector._event_file(tmp_path, now).write_text(json.dumps(event) + "\n", encoding="utf-8")
    page = {"id": "source-large-article", "kind": "source_digest",
            "evidence_ids": [event_to_evidence_card(event, now=now).id]}
    wd._attach_article_context([page], cache_dir=tmp_path, now=now)

    payload, status, reason = wd._distill_one_with_status(
        {**page, "_require_articles": True},
        lambda _: (_ for _ in ()).throw(AssertionError("partial body must not reach model")),
    )

    assert payload is None
    assert status == "failed"
    assert "budget" in reason
    assert page["_source_text_error"]


def test_collector_truncated_body_is_not_claimed_as_complete(monkeypatch, tmp_path):
    from datetime import datetime, timezone
    from reports import source_collector as collector
    from reports.evidence_cards import event_to_evidence_card
    from reports import wiki_distillation as wd
    import json

    now = datetime(2026, 9, 9, 10, tzinfo=timezone.utc)
    event = {"source": "official", "title": "Clipped body", "url": "https://example.org/clipped",
             "collected_at": now.isoformat(), "body_raw": "The beginning of a clipped article…",
             "classification": {"kind": "article"}}
    collector._event_file(tmp_path, now).write_text(json.dumps(event) + "\n", encoding="utf-8")
    page = {"id": "source-clipped-article", "kind": "source_digest",
            "evidence_ids": [event_to_evidence_card(event, now=now).id]}
    wd._attach_article_context([page], cache_dir=tmp_path, now=now)

    payload, status, reason = wd._distill_one_with_status(
        {**page, "_require_articles": True},
        lambda _: (_ for _ in ()).throw(AssertionError("truncated body must not reach model")),
    )

    assert payload is None
    assert status == "failed"
    assert "truncated" in reason or "incomplete" in reason
    assert page["_source_articles"][0]["text_complete"] is False


def test_partial_event_body_is_rejected_even_without_truncation_marker(tmp_path):
    from datetime import datetime, timezone
    from reports import source_collector as collector
    from reports.evidence_cards import event_to_evidence_card
    from reports import wiki_distillation as wd
    import json

    now = datetime(2026, 9, 9, 10, tzinfo=timezone.utc)
    event = {"source": "official", "title": "Partial body", "url": "https://example.org/partial",
             "collected_at": now.isoformat(), "body_raw": "A seemingly complete-looking excerpt.",
             "fulltext_status": "partial", "body_coverage": "partial",
             "classification": {"kind": "article"}}
    collector._event_file(tmp_path, now).write_text(json.dumps(event) + "\n", encoding="utf-8")
    page = {"id": "source-partial-article", "kind": "source_digest",
            "evidence_ids": [event_to_evidence_card(event, now=now).id]}
    wd._attach_article_context([page], cache_dir=tmp_path, now=now)

    payload, status, reason = wd._distill_one_with_status(
        {**page, "_require_articles": True},
        lambda _: (_ for _ in ()).throw(AssertionError("partial body must not reach model")),
    )

    assert payload is None
    assert status == "failed"
    assert "truncated" in reason or "incomplete" in reason
    assert page["_source_articles"][0]["text_complete"] is False


def test_unmatched_local_archive_text_is_not_assigned_evidence_ids(monkeypatch, tmp_path):
    from datetime import datetime, timezone
    from reports import raw_archive
    from reports import wiki_distillation as wd

    monkeypatch.setenv("STOCK_REPORT_REPORTS_DIR", str(tmp_path / "reports"))
    record = raw_archive.save_raw_artifact(
        "saveticker_article", "json", datetime(2026, 9, 1, tzinfo=timezone.utc),
        "Local article", "https://example.org/local", b'{"article":"body"}', ".json",
    )
    raw_archive.save_extracted_text(record, "The locally archived article body.")
    page = {"evidence_ids": ["e-first", "e-second"], "source_refs": [record["text_path"]],
            "conflicting_evidence_ids": ["e-first"]}

    wd._attach_article_context([page], cache_dir=tmp_path / "empty-event-cache")

    article = page["_source_articles"][0]
    assert article["evidence_id"] == ""
    assert article["evidence_role"] == "unmatched"
    assert article["text"] == "The locally archived article body."


def test_aggregate_article_budget_fails_before_model_call():
    from reports import wiki_distillation as wd
    articles = [{"evidence_id": f"e{i}", "url": f"https://example.org/{i}",
                 "content_kind": "article", "source_text_scope": "attached_source_body",
                 "text": "x" * (16_001 if i == 4 else 16_000)} for i in range(5)]

    payload, status, reason = wd._distill_one_with_status(
        {"id": "source-aggregate", "_require_articles": True, "_source_articles": articles},
        lambda _: (_ for _ in ()).throw(AssertionError("over-budget prompt must not be built")),
    )

    assert payload is None
    assert status == "failed"
    assert "per-page budget" in reason


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

    result = wd.run(dry_run=True, llm_fn=llm_fn, fulltext=False)

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

    result = wd.run(dry_run=False, llm_fn=llm_fn, fulltext=False)

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
