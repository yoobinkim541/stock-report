from __future__ import annotations

import hashlib
import json
from pathlib import Path


def _isolate(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AGENT_CONSOLE_SHARED_MEMORY_DIR", str(tmp_path / "memory"))
    monkeypatch.setenv("AGENT_CONSOLE_WIKI_ARTIFACTS_DIR", str(tmp_path / "wiki"))
    monkeypatch.setenv("AGENT_CONSOLE_QMD_ENABLED", "0")


def test_split_blocks_preserves_every_character(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from agent_console import wiki

    body = ("  ## 첫 절\n\n인용: [원문](https://example.test/a)\n\n" + "세부 근거  " * 80
            + "\n> **리포트 인용 요약**: 끝까지 유지")

    chunks = wiki._split_blocks(body, limit=180)

    assert len(chunks) > 1
    assert all(len(chunk) <= 180 for chunk in chunks)
    assert "".join(chunks) == body


def test_long_split_titles_keep_unique_order_suffix():
    from agent_console import wiki

    seen = set()
    titles = [wiki._chunk_title('P' * 120, '# ' + 'H' * 100, index=index,
                               total=3, seen=seen) for index in range(1, 4)]
    assert len(set(titles)) == 3
    assert all(len(title) <= 160 for title in titles)
    assert [title[-6:] for title in titles] == ['[0001]', '[0002]', '[0003]']


def test_long_split_title_order_survives_shared_memory_storage(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from agent_console import wiki

    body = '# ' + 'H' * 120 + '\n' + 'Exact source text. ' * 250 + 'Last paragraph.'
    page = wiki.upsert_page({'id': 'long-title-parent', 'title': 'P' * 120,
                            'body': body, 'source_refs': ['https://example.test/source']})
    saved = wiki.split_for_readability(page['id'], max_chars=500)
    children = wiki.split_children_for_page(saved)
    assert all(child['title'].endswith(']') for child in children)
    ordered = sorted(children, key=lambda child: int(child['title'].rsplit('[', 1)[1][:4]))
    assert ''.join(child['body'] for child in ordered) == body


def test_split_for_readability_preserves_content_metadata_and_discovers_all_children(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from agent_console import wiki

    monkeypatch.setattr(wiki, "_debounced_rebuild", lambda: None)
    original_body = ("근거 [원문](https://example.test/source)\n\n" + "완전한 상세 설명  " * 520
                     + "\n\n> **리포트 인용 요약**: 마지막 출처 문장")
    page = wiki.upsert_page({
        "id": "source-backed-parent",
        "title": "긴 출처 기반 지식",
        "summary": "보존해야 하는 원래 요약",
        "body": original_body,
        "surface": "market",
        "kind": "source_digest",
        "status": "reviewed",
        "source_refs": ["https://example.test/source"],
        "links": [f"related-{index}" for index in range(wiki.MAX_LINKS)],
        "evidence_ids": ["evidence-original"],
        "merge_history": [{"event_id": "merge-old", "source_ids": ["prior"]}],
    })

    saved = wiki.split_for_readability(page["id"], max_chars=320)
    children = wiki.split_children_for_page(saved)
    children = sorted(children, key=lambda child: int(child["title"].rsplit("[", 1)[1][:4]))

    assert saved["id"] == page["id"]
    assert saved["status"] == "reviewed"
    assert saved["source_refs"] == ["https://example.test/source"]
    assert saved["evidence_ids"] == ["evidence-original"]
    assert saved["merge_history"][0]["event_id"] == "merge-old"
    assert len(children) > wiki.MAX_LINKS
    assert "".join(child["body"] for child in children) == original_body
    assert all(child["parent_page_id"] == saved["id"] for child in children)
    assert all(saved["id"] in child["links"] for child in children)
    assert all(child["status"] == "reviewed" for child in children)
    assert all(child["source_refs"] == ["https://example.test/source"] for child in children)
    assert all(len(child["links"]) <= wiki.MAX_LINKS for child in children)


def test_resplitting_archives_superseded_children_without_deleting_them(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from agent_console import wiki

    monkeypatch.setattr(wiki, "_debounced_rebuild", lambda: None)
    page = wiki.upsert_page({
        "id": "resplit-parent",
        "title": "변경되는 긴 지식",
        "body": "첫 내용 " * 500,
        "status": "reviewed",
        "source_refs": ["https://example.test/source"],
    })
    first_parent = wiki.split_for_readability(page["id"], max_chars=700)
    old_ids = {child["id"] for child in wiki.split_children_for_page(first_parent)}
    assert old_ids

    wiki.upsert_page({"id": page["id"], "title": page["title"], "body": "새 내용 " * 90,
                     "status": "reviewed", "source_refs": ["https://example.test/source"]})
    second_parent = wiki.split_for_readability(page["id"], max_chars=700)
    new_ids = {child["id"] for child in wiki.split_children_for_page(second_parent)}

    assert not new_ids
    assert old_ids
    assert all(wiki.get_page(child_id)["status"] == "archived" for child_id in old_ids)
    assert all(wiki.get_page(child_id) is not None for child_id in old_ids)


def test_log_migration_preserves_existing_bytes_and_appends_metadata_only(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from agent_console import wiki

    monkeypatch.setattr(wiki, "_debounced_rebuild", lambda: None)
    artifact_dir = wiki.wiki_artifacts_dir()
    artifact_dir.mkdir(parents=True)
    prior = b"# Existing wiki log\r\nlegacy bytes stay byte-for-byte\r\n"
    log_path = artifact_dir / "log.md"
    log_path.write_bytes(prior)

    page = wiki.upsert_page({
        "id": "metadata-only-page",
        "title": "DO_NOT_LOG_PRIVATE_TITLE",
        "body": "DO_NOT_LOG_PRIVATE_BODY",
        "summary": "DO_NOT_LOG_PRIVATE_SUMMARY",
        "status": "draft",
    })
    wiki.list_pages(query="DO_NOT_LOG_PRIVATE_QUERY", limit=5)
    wiki.lint_pages([page])
    wiki.rebuild_artifacts()
    first = log_path.read_bytes()
    wiki.rebuild_artifacts()

    assert first.startswith(prior)
    text = log_path.read_text(encoding="utf-8")
    assert "page_upsert" in text
    assert "retrieval" in text
    assert "lint" in text
    assert "rebuild" in text
    assert "DO_NOT_LOG_PRIVATE_TITLE" not in text
    assert "DO_NOT_LOG_PRIVATE_BODY" not in text
    assert "DO_NOT_LOG_PRIVATE_SUMMARY" not in text
    assert "DO_NOT_LOG_PRIVATE_QUERY" not in text
    assert log_path.read_bytes().startswith(first)


def test_auto_curation_prompt_includes_parent_maintenance_schema():
    from agent_console import wiki, wiki_contract

    schema = wiki_contract.maintenance_schema()
    prompt = wiki._build_auto_curation_prompt(
        question="질문", answer="답변", surface="market", candidates=[], pack={}, history=[]
    )

    assert schema in prompt


def test_rebuild_exports_the_same_maintenance_schema(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from agent_console import wiki, wiki_contract

    result = wiki.rebuild_artifacts()

    assert "schema.md" in result["files"]
    assert (wiki.wiki_artifacts_dir() / "schema.md").read_text(encoding="utf-8") == wiki_contract.maintenance_schema()


def test_distillation_protocol_version_survives_page_and_split_parent_roundtrip(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from agent_console import wiki

    monkeypatch.setattr(wiki, "_debounced_rebuild", lambda: None)
    state = {"status": "failed", "attempts": 3, "protocol_version": 7,
             "reason": '{"kind":"legacy","count":3}'}
    page = wiki.upsert_page({
        "id": "protocol-state-parent",
        "title": "프로토콜 보존",
        "body": "원문 " * 900,
        "status": "draft",
        "distillation_state": state,
    })

    assert page["distillation_state"]["protocol_version"] == 7
    assert wiki.get_page(page["id"])["distillation_state"]["protocol_version"] == 7
    split = wiki.split_for_readability(page["id"], max_chars=500)
    assert split["distillation_state"]["protocol_version"] == 7
    assert wiki._normalize_distillation_state(None)["protocol_version"] == 0
    assert wiki._normalize_distillation_state({"protocol_version": 140})["protocol_version"] == 100


def test_upsert_snapshots_prior_full_pages_by_hash_without_logging_content(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from agent_console import wiki

    monkeypatch.setattr(wiki, "_debounced_rebuild", lambda: None)
    page_id = "revision-history-page"
    body_v1 = "PRIVATE_REVISION_BODY_V1 " * 20
    body_v2 = "PRIVATE_REVISION_BODY_V2 " * 20
    body_v3 = "PRIVATE_REVISION_BODY_V3 " * 20
    wiki.upsert_page({"id": page_id, "title": "Private title", "body": body_v1, "summary": "prior summary"})
    wiki.upsert_page({"id": page_id, "title": "Private title", "body": body_v2, "summary": "second summary"})
    wiki.upsert_page({"id": page_id, "title": "Private title", "body": body_v3, "summary": "third summary"})

    revision_dir = wiki.wiki_artifacts_dir() / "revisions" / hashlib.sha256(page_id.encode()).hexdigest()
    revisions = sorted(revision_dir.glob("*.json"))
    snapshots = [json.loads(path.read_text(encoding="utf-8")) for path in revisions]
    saved_bodies = {snapshot["page"]["body"] for snapshot in snapshots}
    log = (wiki.wiki_artifacts_dir() / "log.md").read_text(encoding="utf-8")

    assert body_v1.strip() in saved_bodies
    assert body_v2.strip() in saved_bodies
    assert all(len(snapshot["revision_hash"]) == 64 for snapshot in snapshots)
    assert "PRIVATE_REVISION_BODY" not in log
    assert "Private title" not in log
    before = len(revisions)
    wiki._snapshot_page_revision(snapshots[0]["page"])
    assert len(list(revision_dir.glob("*.json"))) == before
    wiki.upsert_page({"id": page_id, "title": "Private title", "body": body_v3, "summary": "third summary"})
    assert len(list(revision_dir.glob("*.json"))) == before


def test_empty_page_creation_and_noop_body_update_do_not_snapshot(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from agent_console import wiki

    monkeypatch.setattr(wiki, "_debounced_rebuild", lambda: None)
    page = wiki.upsert_page({"id": "empty-revision-page", "title": "Empty", "body": ""})
    wiki.upsert_page({"id": page["id"], "title": "Empty", "body": ""})

    revisions = wiki.wiki_artifacts_dir() / "revisions" / hashlib.sha256(page["id"].encode()).hexdigest()
    assert not revisions.exists()


def test_readability_split_snapshots_body_and_preserves_parent_metadata(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from agent_console import wiki

    monkeypatch.setattr(wiki, "_debounced_rebuild", lambda: None)
    refs = [f"https://example.test/source/{index}" for index in range(16)]
    original_body = ("원문 전체를 그대로 보존합니다. " * 300).strip()
    page = wiki.upsert_page({
        "id": "metadata-split-parent",
        "title": "메타데이터 보존 분할",
        "summary": "원 요약",
        "body": original_body,
        "status": "reviewed",
        "source_refs": refs,
        "messages": [{"role": "user", "text": "기존 메시지", "createdAt": "2026-01-01"}],
        "decisions": ["기존 결정"],
        "openQuestions": ["기존 질문"],
        "feedback": {"helpful": 3, "not_helpful": 1},
        "useCount": 9,
        "lastUsedAt": "2026-10-01T00:00:00+00:00",
        "lastQuery": "기존 query metadata",
        "created_at": "2025-01-02T00:00:00+00:00",
    })

    saved = wiki.split_for_readability(page["id"], max_chars=700)
    parent_record = next(row for row in wiki._wiki_records() if row["id"] == page["id"])
    children = wiki.split_children_for_page(saved)
    children.sort(key=lambda child: int(child["title"].rsplit("[", 1)[1][:4]))
    revisions = list((wiki.wiki_artifacts_dir() / "revisions" / hashlib.sha256(page["id"].encode()).hexdigest()).glob("*.json"))

    assert parent_record["artifacts"] == refs
    assert parent_record["messages"] == page["messages"]
    assert parent_record["decisions"] == page["decisions"]
    assert parent_record["openQuestions"] == page["openQuestions"]
    assert parent_record["feedback"] == page["feedback"]
    assert parent_record["useCount"] == 9
    assert parent_record["lastUsedAt"] == "2026-10-01T00:00:00+00:00"
    assert parent_record["lastQuery"] == "기존 query metadata"
    assert parent_record["createdAt"] == "2025-01-02T00:00:00+00:00"
    assert "".join(child["body"] for child in children) == original_body
    assert len(revisions) == 1
    assert json.loads(revisions[0].read_text(encoding="utf-8"))["page"]["body"] == original_body


