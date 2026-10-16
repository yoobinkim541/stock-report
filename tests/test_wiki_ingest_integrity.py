"""Synthetic second-ingest contracts; never invoke providers or live stores."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent_console import shared_memory, wiki
from reports import raw_archive, source_collector, wiki_distillation as wd
from reports.evidence_cards import event_to_evidence_card


@pytest.fixture(autouse=True)
def isolated_wiki(monkeypatch, tmp_path):
    for key, name in (
        ("AGENT_CONSOLE_SHARED_MEMORY_DIR", "memory"),
        ("AGENT_CONSOLE_WIKI_ARTIFACTS_DIR", "artifacts"),
        ("AGENT_CONSOLE_QMD_WIKI_DIR", "markdown"),
        ("STOCK_REPORT_REPORTS_DIR", "reports"),
    ):
        monkeypatch.setenv(key, str(tmp_path / name))
    monkeypatch.setenv("AGENT_CONSOLE_QMD_ENABLED", "0")
    monkeypatch.setattr(wiki, "_debounced_rebuild", lambda: None)
    monkeypatch.setattr(shared_memory, "_schedule_context_memory_summary", lambda: None)
    monkeypatch.setattr(wiki, "rebuild_artifacts", lambda: {})
    monkeypatch.setattr(wiki, "sync_qmd", lambda: {"ok": True})
    wiki._CACHE.clear()
    yield
    wiki._CACHE.clear()


def _article(eid="new", **changes):
    return {
        "evidence_id": eid, "url": f"https://example.org/{eid}",
        "text": "Complete synthetic capacity evidence.", "content_kind": "article",
        "source_text_scope": "article_fulltext", "provenance": "official",
        "text_complete": True, **changes,
    }


def _response(body="Current capacity claim [S1].", kind="concept", **changes):
    return json.dumps({
        "action": "create", "kind": kind, "title": "Capacity constraints",
        "summary": "Capacity constraints and demand", "body": body, **changes,
    })


def _refresh_page(history):
    return {
        "id": "source-history", "surface": "market", "_require_articles": True,
        "evidence_ids": ["new"], "_source_articles": [_article()],
        "_previous_knowledge": {
            "id": "knowledge-history", "kind": "concept", "body": history,
            "source_refs": ["https://example.org/old"], "evidence_ids": ["old"],
        },
    }


@pytest.mark.parametrize("fulltext", [False, True])
def test_distillation_cannot_add_model_invented_source_refs(fulltext):
    page = {
        "id": "source-provenance", "surface": "market",
        "source_refs": ["https://example.org/input"], "evidence_ids": ["input"],
    }
    if fulltext:
        page.update(_require_articles=True, _source_articles=[_article("input")])
    proposed = [
        "https://example.org/invented", "wiki:invented", "raw-sha256:" + "f" * 64,
    ]
    payload, status, reason = wd._distill_one_with_status(
        page, lambda _: _response("Capacity evidence [S1]." if fulltext else "Capacity evidence.",
                                   source_refs=proposed))
    assert status == "created", reason
    saved = wiki.upsert_page(payload)
    wiki._CACHE.clear()
    refs = wiki.get_page(saved["id"])["source_refs"]
    assert "https://example.org/input" in refs
    assert "wiki:source-provenance" in refs
    assert not set(proposed) & set(refs)


def test_distillation_retains_known_current_and_historical_source_refs():
    page = _refresh_page("Historical knowledge [S1](https://example.org/old).")
    page["source_refs"] = ["https://example.org/input-context"]
    page["_source_articles"][0]["raw_sha256"] = "a" * 64
    payload, status, reason = wd._distill_one_with_status(
        page, lambda _: _response("Historical knowledge [S1](https://example.org/old). Current [S1].",
                                   source_refs=["https://example.org/invented"]))
    assert status == "created", reason
    refs = payload["source_refs"]
    assert {"https://example.org/new", "raw-sha256:" + "a" * 64,
            "https://example.org/input-context", "https://example.org/old",
            "wiki:source-history"}.issubset(refs)
    assert "https://example.org/invented" not in refs


def test_long_generated_knowledge_reaches_lossless_child_page_storage():
    prose = "## Capacity history\n\n" + "Capacity observations and dated exceptions.\n\n" * 350
    prose += "Final counterexample preserved at the end [S1]."
    assert len(prose) > wiki.WIKI_BODY_LIMIT
    page = {"id": "source-long-prose", "surface": "market", "_require_articles": True,
            "evidence_ids": ["new"], "_source_articles": [_article()]}
    payload, status, reason = wd._distill_one_with_status(page, lambda _: _response(prose))
    assert status == "created", reason
    resolved = prose.replace("[S1]", "[S1](https://example.org/new)")
    assert payload["body"].startswith(resolved)
    assert len(payload["body"]) > wiki.WIKI_BODY_LIMIT
    parent = wiki.upsert_page(payload)
    wiki._CACHE.clear()
    children = [wiki.get_page(pid) for pid in parent["links"]]
    children = [child for child in children if child and child.get("parent_page_id") == parent["id"]]
    assert len(children) > 1
    assert "".join(child["body"] for child in children) == payload["body"]
    for child in children:
        assert parent["id"] in child["links"]
        assert "https://example.org/new" in child["source_refs"]
        assert child["evidence_ids"] == ["new"]
    assert wiki.WIKI_CONTEXT_BODY_SNIPPET == 1600


@pytest.mark.parametrize("conflicts", [None, []])
def test_no_conflict_fingerprint_preserves_legacy_format(conflicts):
    page = {"evidence_ids": ["b", "a", "a"]}
    if conflicts is not None:
        page["conflicting_evidence_ids"] = conflicts
    legacy = hashlib.sha256(json.dumps(["a", "b"]).encode()).hexdigest()
    assert wd._evidence_fingerprint(page) == legacy


def test_conflict_fingerprint_tracks_roles_but_not_order_or_duplicate_ids():
    page = {"evidence_ids": ["a", "b"], "conflicting_evidence_ids": ["a"]}
    changed_role = {**page, "conflicting_evidence_ids": ["b"]}
    assert wd._evidence_fingerprint(page) != wd._evidence_fingerprint(changed_role)
    reordered = {"evidence_ids": ["b", "a", "a"], "conflicting_evidence_ids": ["a", "a"]}
    assert wd._evidence_fingerprint(page) == wd._evidence_fingerprint(reordered)


@pytest.mark.parametrize("status", ["created", "skipped"])
@pytest.mark.parametrize("supporting", [["support"], []])
def test_adding_only_conflict_evidence_reopens_processed_digest(status, supporting):
    page = {"id": "source-conflict", "kind": "source_digest", "status": "draft",
            "evidence_ids": supporting, "links": ["knowledge"]}
    page["distillation_state"] = {
        "status": status, "last_result_id": "knowledge",
        "evidence_fingerprint": wd._evidence_fingerprint(page),
    }
    knowledge = {"id": "knowledge", "kind": "concept", "status": "draft"}
    assert wd.select_distillation_candidates([page, knowledge]) == []
    page["conflicting_evidence_ids"] = ["counter"]
    assert wd.select_distillation_candidates([page, knowledge]) == [page]


def test_removing_conflict_evidence_reopens_digest_with_unchanged_supporting_ids():
    page = {"id": "source-conflict", "kind": "source_digest", "status": "draft",
            "evidence_ids": ["support"], "conflicting_evidence_ids": ["counter"], "links": ["knowledge"]}
    page["distillation_state"] = {
        "status": "created", "last_result_id": "knowledge",
        "evidence_fingerprint": wd._evidence_fingerprint(page),
    }
    knowledge = {"id": "knowledge", "kind": "concept", "status": "draft"}
    assert wd.select_distillation_candidates([page, knowledge]) == []
    page["conflicting_evidence_ids"] = []
    assert wd.select_distillation_candidates([page, knowledge]) == [page]


@pytest.mark.parametrize("kind", ["concept", "risk"])
def test_refresh_retains_prior_conflict_ids_only_for_same_kind_target(kind):
    page = _refresh_page("Prior knowledge and dated counterevidence.")
    prior = wiki.upsert_page({
        **page["_previous_knowledge"], "surface": "market", "status": "draft",
        "conflicting_evidence_ids": ["old-counter", "shared-counter"],
    })
    page["_previous_knowledge"] = prior
    page["evidence_ids"] = ["new", "new-counter"]
    page["conflicting_evidence_ids"] = ["new-counter", "shared-counter"]
    page["_source_articles"] = [_article(), _article("new-counter", evidence_role="conflicting")]
    payload, status, reason = wd._distill_one_with_status(
        page, lambda _: _response("New evidence [S1]. New counterevidence [S2].", kind))
    assert status == "created", reason
    saved = wiki.upsert_page(payload)
    wiki._CACHE.clear()
    expected = ["new-counter", "shared-counter"] + (["old-counter"] if kind == "concept" else [])
    assert wiki.get_page(saved["id"])["conflicting_evidence_ids"] == expected
    if kind == "risk":
        assert wiki.get_page(prior["id"])["conflicting_evidence_ids"] == ["old-counter", "shared-counter"]


def test_conflict_only_refresh_reads_role_tagged_body_and_commits_new_fingerprint(tmp_path, monkeypatch):
    now = datetime(2026, 10, 8, 10, tzinfo=timezone.utc)
    events = [{
        "id": eid, "source": "official", "title": f"Capacity {eid}",
        "url": f"https://example.org/{eid}", "body_raw": body, "fulltext_status": "full",
        "classification": {"kind": "article"}, "collected_at": now.isoformat(),
    } for eid, body in [("support", "Complete report: shipments rose."),
                        ("counter", "Complete report: shipments fell on the competing measurement.")]]
    support_id, counter_id = [event_to_evidence_card(event, now=now).id for event in events]
    cache = tmp_path / "source-cache"
    path = source_collector._event_file(cache, now)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(event) + "\n" for event in events))
    sid = "source-role-refresh"
    kid = wd._distillation_id(sid, "concept")
    wiki.upsert_page({
        "id": kid, "kind": "concept", "surface": "market", "status": "draft",
        "body": "Historical knowledge with unresolved counterevidence.", "evidence_ids": [support_id],
        "conflicting_evidence_ids": ["historical-counter"],
    })
    source = {"id": sid, "kind": "source_digest", "surface": "market", "status": "draft",
              "evidence_ids": [support_id], "links": [kid]}
    source["distillation_state"] = {
        "status": "created", "last_result_id": kid,
        "evidence_fingerprint": wd._evidence_fingerprint(source),
    }
    wiki.upsert_page(source)
    wiki.upsert_page({**wiki.get_page(sid), "conflicting_evidence_ids": [counter_id]})
    attach = wd._attach_article_context
    monkeypatch.setattr(wd, "_attach_article_context", lambda pages: attach(pages, cache_dir=cache, now=now))

    def synth(prompt):
        assert events[1]["body_raw"] in prompt
        assert '"evidence_role": "conflicting"' in prompt
        return _response("Shipments rose [S1]. Competing measurement reports a decline [S2].")

    result = wd.run(llm_fn=synth, fulltext=True, page_ids=[sid])
    assert len(result["created"]) == 1
    wiki._CACHE.clear()
    knowledge = wiki.get_page(kid)
    assert "https://example.org/counter" in knowledge["body"]
    assert knowledge["conflicting_evidence_ids"] == [counter_id, "historical-counter"]
    completed = wiki.get_page(sid)
    assert completed["distillation_state"]["evidence_fingerprint"] == wd._evidence_fingerprint(completed)
    assert wd.select_distillation_candidates([completed, knowledge]) == []


@pytest.mark.parametrize("label", ["S1", "S2"])
def test_refresh_retains_known_historical_link_identity(label):
    historical = f"Prior capacity claim [{label}](https://example.org/old)."
    payload, status, reason = wd._distill_one_with_status(
        _refresh_page(historical), lambda _: _response(historical + " New claim [S1]."))
    assert status == "created", reason
    assert historical in payload["body"]
    assert "New claim [S1](https://example.org/new)." in payload["body"]
    assert payload["evidence_ids"] == ["new", "old"]


def test_validated_current_link_counts_as_current_evidence():
    payload, status, reason = wd._distill_one_with_status(
        _refresh_page("Historical prose."),
        lambda _: _response("Current claim [S1](https://example.org/new)."))
    assert status == "created", reason
    assert payload["evidence_ids"] == ["new", "old"]


def test_same_url_refresh_link_counts_as_both_historical_and_current_evidence():
    url = "https://example.org/old"
    page = _refresh_page(f"Historical claim [S1]({url}).")
    page["_source_articles"] = [_article(url=url, raw_sha256="c" * 64)]
    body = f"Refreshed capacity claim [S1]({url})."
    payload, status, reason = wd._distill_one_with_status(page, lambda _: _response(body))
    assert status == "created", reason
    assert body in payload["body"]
    assert payload["evidence_ids"] == ["new", "old"]
    assert "raw-sha256:" + "c" * 64 in payload["source_refs"]


@pytest.mark.parametrize("cite_current_pair", [False, True])
def test_same_url_at_different_index_preserves_history_without_counting_it_as_current(cite_current_pair):
    url = "https://example.org/old"
    historical = f"Historical claim [S1]({url})."
    page = _refresh_page(historical)
    page["_source_articles"] = [_article("other"), _article("same-url", url=url)]
    body = historical + (f" Refreshed claim [S2]({url})." if cite_current_pair else "")
    payload, status, reason = wd._distill_one_with_status(page, lambda _: _response(body))
    if cite_current_pair:
        assert status == "created", reason
        assert historical in payload["body"]
        assert f"Refreshed claim [S2]({url})." in payload["body"]
        assert payload["evidence_ids"] == ["same-url", "old"]
    else:
        assert payload is None and status == "failed"
        assert "citation" in reason


@pytest.mark.parametrize("citation", [
    "[S1](https://example.org/invented)",
    "[S9](https://example.org/old)",
    "[S1](https://example.org/old/other)",
    "[S1](https://example.org/new",
])
def test_refresh_rejects_invented_or_malformed_linked_citations(citation):
    payload, status, reason = wd._distill_one_with_status(
        _refresh_page("Historical claim [S2](https://example.org/old)."),
        lambda _: _response(f"Current claim [S1]. Invented claim {citation}."))
    assert payload is None and status == "failed"
    assert "citation" in reason


def test_historical_links_alone_do_not_count_as_current_input_citations():
    historical = "Prior capacity claim [S1](https://example.org/old)."
    payload, status, reason = wd._distill_one_with_status(
        _refresh_page(historical), lambda _: _response(historical))
    assert payload is None and status == "failed"
    assert "citation" in reason


def _attach_synthetic(pages):
    for page in pages:
        page["_source_articles"] = [_article(eid) for eid in page["evidence_ids"]]


def test_kind_cycle_hydrates_actual_target_and_preserves_its_prose_and_evidence(monkeypatch):
    monkeypatch.setattr(wd, "_attach_article_context", _attach_synthetic)
    sid = "source-cycle"
    old_prose = "Prior capacity constraint [S1](https://example.org/v1)."
    for n, kind in enumerate(("concept", "risk", "concept"), 1):
        previous_source = wiki.get_page(sid) or {}
        wiki.upsert_page({
            **previous_source, "id": sid, "kind": "source_digest", "surface": "market",
            "title": "Capacity evidence", "body": "Synthetic digest.",
            "status": "draft", "evidence_ids": [f"v{n}"],
        })

        def synth(prompt, n=n, kind=kind):
            if n == 3:
                assert old_prose in prompt
                assert "v1" in prompt
                return _response(old_prose + " Current limitation [S1].", kind)
            body = "Prior capacity constraint [S1]." if n == 1 else "New demand risk [S1]."
            return _response(body, kind, title=f"Capacity {kind}")

        result = wd.run(llm_fn=synth, fulltext=True, page_ids=[sid])
        assert len(result["created"]) == 1
    concept = wiki.get_page(wd._distillation_id(sid, "concept"))
    assert old_prose in concept["body"]
    assert "Current limitation [S1](https://example.org/v3)." in concept["body"]
    assert concept["evidence_ids"] == ["v3", "v1"]
    assert "https://example.org/v1" in concept["source_refs"]


@pytest.mark.parametrize("occupied_revision", [False, True])
def test_archived_same_kind_target_is_not_resurrected_or_used_as_active_context(monkeypatch, occupied_revision):
    monkeypatch.setattr(wd, "_attach_article_context", _attach_synthetic)
    sid = "source-archived-target"
    concept_id = wd._distillation_id(sid, "concept")
    archived = wiki.upsert_page({
        "id": concept_id, "kind": "concept", "surface": "market", "status": "archived",
        "title": "Historical concept", "body": "Archived knowledge must stay archived.",
    })
    if occupied_revision:
        wiki.upsert_page({**archived, "id": concept_id + "-r2", "body": "Occupied archived revision."})
    risk = wiki.upsert_page({
        "id": wd._distillation_id(sid, "risk"), "kind": "risk", "surface": "market",
        "title": "Current risk", "body": "Current demand risk.", "status": "draft",
    })
    wiki.upsert_page({
        "id": sid, "kind": "source_digest", "surface": "market", "status": "draft",
        "evidence_ids": ["new"], "links": [risk["id"]],
        "distillation_state": {"status": "created", "attempts": 1,
                               "last_result_id": risk["id"], "evidence_fingerprint": "old"},
    })

    def synth(prompt):
        assert archived["body"] not in prompt
        return _response()

    result = wd.run(llm_fn=synth, fulltext=True, page_ids=[sid])
    assert len(result["created"]) == 1
    assert result["created"][0]["id"] != concept_id
    if occupied_revision:
        assert result["created"][0]["id"] != concept_id + "-r2"
        assert wiki.get_page(concept_id + "-r2")["status"] == "archived"
        assert wiki.get_page(concept_id + "-r2")["body"] == "Occupied archived revision."
    assert wiki.get_page(concept_id)["status"] == "archived"
    assert wiki.get_page(concept_id)["body"] == archived["body"]


def test_lexical_match_persists_new_cited_draft_and_crosslinks_without_overwriting(monkeypatch):
    sid = "source-near-match"
    old = wiki.upsert_page({
        "id": "unrelated-existing", "title": "Capacity constraints", "kind": "concept",
        "surface": "market", "status": "draft", "summary": "Capacity constraints and demand",
        "body": "Distinct old explanation.", "evidence_ids": ["old"],
        "source_refs": ["https://example.org/old"], "conflicting_evidence_ids": ["old-counter"],
    })
    wiki.upsert_page({
        "id": sid, "kind": "source_digest", "surface": "market", "status": "draft",
        "evidence_ids": ["new", "counter"], "conflicting_evidence_ids": ["counter"],
    })

    def attach(pages):
        for page in pages:
            page["_source_articles"] = [_article(), _article("counter", evidence_role="conflicting")]

    monkeypatch.setattr(wd, "_attach_article_context", attach)
    result = wd.run(llm_fn=lambda _: _response("New mechanism [S1]. Counterevidence [S2]."),
                    fulltext=True, page_ids=[sid])
    assert len(result["created"]) == 1
    new_id = result["created"][0]["id"]
    assert new_id != old["id"]
    wiki._CACHE.clear()
    new = wiki.get_page(new_id)
    assert "New mechanism [S1](https://example.org/new)." in new["body"]
    assert "Counterevidence [S2](https://example.org/counter)." in new["body"]
    assert new["evidence_ids"] == ["new", "counter"]
    assert new["conflicting_evidence_ids"] == ["counter"]
    assert new["status"] == "draft" and old["id"] in new["links"]
    reread_old = wiki.get_page(old["id"])
    for field in ("body", "evidence_ids", "source_refs", "conflicting_evidence_ids"):
        assert reread_old[field] == old[field]
    assert new_id in reread_old["links"]
    state = wiki.get_page(sid)["distillation_state"]
    assert state["status"] == "created" and state["last_result_id"] == new_id


def _archive(now, title, url, text, *, complete=True, source="telegram"):
    record = raw_archive.save_raw_artifact(
        source, "html", now, title, url, f"<article>{title}: {text}</article>", ".html")
    record.update(body_coverage="full" if complete else "partial",
                  text_complete=complete, extraction_scope="public_message")
    raw_archive.save_extracted_text(record, text)
    return record


def _legacy_event_archive(tmp_path, *, replacement_url=None, replacement_text=None,
                          complete=True, extra_partial=False, source="telegram", event_url=None):
    now = datetime(2026, 10, 8, 10, tzinfo=timezone.utc)
    url = "https://t.me/channel/42"
    text = "Complete message capacity evidence, including the entire final sentence."
    legacy = _archive(now, "Legacy extraction", url, text, complete=False)
    event = {
        **legacy, "id": "legacy-event", "source": "telegram:channel",
        "body_raw": "An incomplete preview.", "fulltext_status": "partial",
        "classification": {"kind": "community_signal"}, "collected_at": now.isoformat(),
        "raw_sha256": legacy["sha256"],
    }
    if event_url:
        event["url"] = event_url
    fresh = _archive(now, "Verified new DOM", replacement_url or url,
                     replacement_text or text, complete=complete, source=source)
    # A different manifest ID must not be guessed to be the legacy event's ID.
    fresh["evidence_id"] = "unrelated-new-manifest-id"
    raw_archive.save_extracted_text(fresh, replacement_text or text)
    events = [event]
    if extra_partial:
        events.append({**event, "id": "unresolved-event", "text_path": "",
                       "url": "https://t.me/channel/99", "body_raw": "Other incomplete body."})
    cache = tmp_path / "source-cache"
    event_file = source_collector._event_file(cache, now)
    event_file.parent.mkdir(parents=True, exist_ok=True)
    event_file.write_text("".join(json.dumps(row) + "\n" for row in events))
    page = {"id": "source-legacy", "surface": "market", "_require_articles": True,
            "evidence_ids": [event_to_evidence_card(row, now=now).id for row in events],
            "source_refs": [fresh["text_path"]]}
    return now, cache, page, legacy, fresh, text


@pytest.mark.parametrize("legacy_ref_first", [False, True])
def test_verified_matching_archive_supersedes_legacy_partial_without_borrowing_identity(tmp_path, legacy_ref_first):
    now, cache, page, legacy, fresh, text = _legacy_event_archive(tmp_path)
    if legacy_ref_first:
        page["source_refs"].insert(0, legacy["text_path"])
    old_raw = Path(legacy["raw_path"]).read_bytes()
    wd._attach_article_context([page], cache_dir=cache, now=now)
    assert not page.get("_source_text_error")
    assert len(page["_source_articles"]) == 1
    article = page["_source_articles"][0]
    assert article["text"] == text and article["text_complete"] is True
    assert wd._full_body_source_class(article) == "community"
    assert article["evidence_id"] == "" and article["evidence_role"] == "unmatched"
    assert article["raw_sha256"] == fresh["sha256"] != legacy["sha256"]
    payload, status, reason = wd._distill_one_with_status(page, lambda _: _response())
    assert status == "created", reason
    assert payload["evidence_ids"] == []
    assert "raw-sha256:" + fresh["sha256"] in payload["source_refs"]
    assert "raw-sha256:" + legacy["sha256"] not in payload["source_refs"]
    assert payload["confidence"] <= 0.5
    assert Path(legacy["raw_path"]).read_bytes() == old_raw


@pytest.mark.parametrize("changes", [
    {"replacement_url": "https://t.me/channel/43"},
    {"event_url": "https://t.me/channel/43"},
    {"replacement_text": "Different complete message body."},
    {"complete": False},
    {"extra_partial": True},
    {"source": "fred"},
])
def test_nonmatching_or_unresolved_partial_archive_still_blocks_synthesis(tmp_path, changes):
    now, cache, page, *_ = _legacy_event_archive(tmp_path, **changes)
    wd._attach_article_context([page], cache_dir=cache, now=now)
    payload, status, _ = wd._distill_one_with_status(
        page, lambda _: pytest.fail("unresolved partial input invoked synthesis"))
    assert payload is None and status == "failed"


def test_matching_archive_cannot_bypass_body_budget(tmp_path, monkeypatch):
    now, cache, page, *_ = _legacy_event_archive(tmp_path)
    monkeypatch.setattr(wd, "MAX_SOURCE_ARTICLE_CHARS", 10)
    wd._attach_article_context([page], cache_dir=cache, now=now)
    payload, status, reason = wd._distill_one_with_status(
        page, lambda _: pytest.fail("overbudget input invoked synthesis"))
    assert payload is None and status == "failed"
    assert "budget" in reason
