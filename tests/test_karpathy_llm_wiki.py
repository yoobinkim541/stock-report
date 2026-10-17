"""Karpathy LLM-wiki acceptance: synthetic checks per condition plus an opt-in live gate."""
import hashlib
import os
from datetime import datetime, timezone

import pytest

from reports import wiki_karpathy_audit as audit

NOW = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)
SCHEMA = "# Contract\n## Layers\n## Ingest\n## Query\n## Lint\nindex.md catalog, log.md history\n"


def claim(revision, text="Capacity rose in September.", cid=None, as_of="2026-09-30", competing=()):
    return {"claim_id": cid or f"{revision}-{text[:8]}", "text": text, "source_revision_id": revision,
            "as_of": as_of, "competing_claim_ids": list(competing)}


def page(pid, *, kind="concept", body=None, claims=(), links=(), tags=(), parent=None, status="draft", refs=None):
    return {"id": pid, "title": pid.title(), "kind": kind, "status": status, "tags": list(tags),
            "body": body if body is not None else f"{pid} synthesis paragraph explaining mechanisms and limits " * 3,
            "summary": f"{pid} one line", "links": list(links), "parent_page_id": parent,
            "source_refs": ["https://example.com/a"] if refs is None else refs,
            "source_knowledge": {"claims": list(claims)} if claims else {}}


def stem(pid):
    return audit.file_stem(pid)


def frontmatter(pid):
    return f"---\nid: {pid}\nkind: concept\nstatus: draft\ntags: []\nupdated: 2026-10-16\nsource_count: 2\n---\n# x\n"


def healthy_inputs():
    raw, text = b"<p>original</p>", b"original"
    sources = {r: {"raw_ref": f"/raw/{r}", "text_ref": f"/text/{r}", "raw_sha256": hashlib.sha256(raw).hexdigest(),
                   "text_sha256": hashlib.sha256(text).hexdigest(), "compile_state": "compiled"} for r in ("r1", "r2")}
    pages = [
        page("note-r1", kind="note", tags=["compiled_source"], claims=[claim("r1")], links=["topic"]),
        page("note-r2", kind="note", tags=["compiled_source"], claims=[claim("r2", "Orders lagged capacity.")], links=["topic"]),
        page("topic", claims=[claim("r1", cid="a", competing=["b"]), claim("r2", "Orders fell.", cid="b", competing=["a"])],
             links=["note-r1", "note-r2", "answer"]),
        page("answer", kind="playbook", tags=["query_fileback"], claims=[claim("r1")], links=["topic"]),
    ]
    index = "# Index\n\n## Concepts\n\n" + "\n".join(
        f"- [[{stem(p['id'])}|{p['title']}]] (concept · 2026-10-16 · sources: 2) — {p['summary']}" for p in pages[2:]
    ) + "\n\n## Sources\n\n" + "\n".join(
        f"- [[{stem(p['id'])}|{p['title']}]] (note · 2026-10-16 · sources: 1) — {p['summary']}" for p in pages[:2]) + "\n"
    log = ("# Log\n\n## [2026-10-16] ingest | Capacity report\n- metadata: {\"revision_id\": \"r1\"}\n\n"
           "## [2026-10-16] ingest | Orders report\n- metadata: {\"revision_id\": \"r2\"}\n\n"
           "## [2026-10-16] query | capacity vs orders\n\n## [2026-10-16] lint | daily\n")
    store = {f"/raw/{r}": raw for r in sources} | {f"/text/{r}": text for r in sources}
    return audit.AuditInputs(
        pages=pages, index_md=index, log_md=log, schema_export=SCHEMA, schema_source=SCHEMA,
        markdown={stem(p["id"]): frontmatter(p["id"]) for p in pages}, compiled_sources=sources,
        read_artifact=store.get, collection_health={"telegram": {"healthy": True}}, now=NOW)


@pytest.fixture
def thresholds(monkeypatch):
    monkeypatch.setattr(audit, "MIN_TOPIC_PAGES", 1)
    monkeypatch.setattr(audit, "MIN_COMPOUNDED_PAGES", 1)


def test_complete_synthetic_wiki_passes_every_condition(thresholds):
    report = audit.run_audit(healthy_inputs())
    assert {k: v["pass"] for k, v in report["results"].items() if not v["pass"]} == {}
    assert report["ok"] is True


def test_raw_hash_mismatch_or_missing_original_fails(thresholds):
    inp = healthy_inputs()
    inp.read_artifact = {"/raw/r1": b"tampered"}.get
    result = audit.check_raw_immutable(inp)
    assert not result["pass"]
    assert "r1" in result["evidence"]["mismatched"] and "r2" in result["evidence"]["unavailable"]


@pytest.mark.parametrize("body", [
    "요약: 종목:SPY 관련 수집 이벤트 96건\n- [yahoo] SPY",
    "## 해석 경계\n이 카드는 수집된 원문과 다이제스트를 검색 가능한 위키 초안으로 정리한 것입니다.",
    "\n".join(["- [yahoo_finance] SPY 현재 771.79 1D +0.28% 5D -0.28%"] * 8),
    "\n".join(f"- https://example.com/{i} link" for i in range(6)),
])
def test_catalog_template_repeated_or_link_pages_are_not_knowledge(body, thresholds):
    inp = healthy_inputs()
    inp.pages.append(page("digest", kind="source_digest", body=body, links=["topic"]))
    result = audit.check_knowledge_not_links(inp)
    assert not result["pass"] and result["evidence"]["mechanical_ids"] == ["digest"]


def test_raw_layer_index_pages_are_outside_the_wiki_layer(thresholds):
    inp = healthy_inputs()
    inp.pages.append(page("digest", kind="source_index", body="수집 이벤트 96건"))
    assert audit.check_knowledge_not_links(inp)["pass"]
    assert audit.check_index_md(inp)["pass"]


def test_unsourced_page_fails_knowledge_check(thresholds):
    inp = healthy_inputs()
    inp.pages.append(page("opinion", refs=[], links=["topic"]))
    assert audit.check_knowledge_not_links(inp)["evidence"]["unsourced_ids"] == ["opinion"]


def test_compiled_source_without_summary_page_fails():
    inp = healthy_inputs()
    inp.pages = [p for p in inp.pages if p["id"] != "note-r2"]
    assert audit.check_source_summaries(inp)["evidence"]["missing"] == ["r2"]


def test_overview_hub_needs_links_instead_of_sources(thresholds):
    inp = healthy_inputs()
    inp.pages.append(page("hub", kind="overview", refs=[], links=[]))
    assert audit.check_knowledge_not_links(inp)["evidence"]["unsourced_ids"] == ["hub"]
    inp.pages[-1]["body"] += f"\n- [[{stem('topic')}|Topic]] — capacity vs orders"
    assert audit.check_knowledge_not_links(inp)["pass"]


def test_topic_pages_must_link_to_source_summaries(thresholds):
    inp = healthy_inputs()
    for p in inp.pages:
        p["links"] = [link for link in p["links"] if not link.startswith("note-")]
    inp.pages[0]["links"] = inp.pages[1]["links"] = []
    assert not audit.check_entity_topic_pages(inp)["pass"]


def test_single_source_pages_do_not_count_as_compounding(thresholds):
    inp = healthy_inputs()
    inp.pages[2]["source_knowledge"]["claims"] = [claim("r1")]
    assert audit.check_compounding(inp)["metric"]["compounded_pages"] == 0


def test_contradiction_without_dates_is_malformed(thresholds):
    inp = healthy_inputs()
    inp.pages[2]["source_knowledge"]["claims"][1]["as_of"] = ""
    assert audit.check_compounding(inp)["evidence"]["malformed_contradictions"] == ["topic"]


def test_index_requires_each_page_once_with_summary_date_and_sources():
    inp = healthy_inputs()
    inp.index_md = inp.index_md.replace(" · sources: 2", "", 1)
    inp.index_md += f"- [[{stem('topic')}|dup]] (2026-10-16 · sources: 1) — again\n"
    result = audit.check_index_md(inp)
    assert not result["pass"]
    assert result["evidence"]["duplicated"] == [stem("topic")]


def test_index_must_not_omit_pages():
    inp = healthy_inputs()
    inp.pages.append(page("unlisted", links=["topic"]))
    assert audit.check_index_md(inp)["evidence"]["missing"] == [stem("unlisted")]


def test_log_headers_must_be_parseable_and_append_only():
    inp = healthy_inputs()
    inp.log_baseline = audit.record_log_baseline(inp.log_md)
    assert audit.check_log_md(inp)["pass"]
    inp.log_md += "## [2026-10-16T12:00:00+00:00] rebuild\n"
    assert audit.check_log_md(inp)["metric"]["bad_headers"] == 1
    inp.log_md = inp.log_md.replace("Capacity report", "Rewritten")
    assert audit.check_log_md(inp)["evidence"]["append_only"] is False


def test_compiled_source_without_ingest_entry_fails_log():
    inp = healthy_inputs()
    inp.log_md = inp.log_md.replace('"r2"', '"other"')
    assert audit.check_log_md(inp)["evidence"]["unlogged"] == ["r2"]


def test_schema_export_must_match_and_cover_operations():
    inp = healthy_inputs()
    inp.schema_export = SCHEMA + "drift"
    assert not audit.check_schema(inp)["pass"]
    inp.schema_export = inp.schema_source = "# Only ingest\n"
    assert "lint" in audit.check_schema(inp)["metric"]["missing_sections"]


def test_query_fileback_needs_logged_query_and_cited_page():
    inp = healthy_inputs()
    inp.pages[3]["tags"] = []
    assert not audit.check_query_fileback(inp)["pass"]


def test_broken_links_orphans_and_stale_lint_fail():
    inp = healthy_inputs()
    inp.pages[2]["links"].append("missing-page")
    inp.pages.append(page("orphan", links=["topic"]))
    inp.log_md = inp.log_md.replace("## [2026-10-16] lint", "## [2026-10-01] lint")
    result = audit.check_lint(inp)
    assert result["metric"] == {"broken_link_pages": 1, "orphans": 1, "lint_logged_24h": False}


def test_link_to_archived_page_is_broken():
    inp = healthy_inputs()
    inp.pages.append(page("gone", status="archived"))
    inp.pages[2]["links"].append("gone")
    assert audit.check_lint(inp)["metric"]["broken_link_pages"] == 1


def test_body_wikilinks_count_as_cross_references():
    inp = healthy_inputs()
    inp.pages[3]["links"] = []
    inp.pages[3]["body"] += f" See [[{stem('topic')}|Topic]]."
    assert audit.check_cross_refs(inp)["pass"]


def test_long_pages_and_unlinked_children_fail():
    inp = healthy_inputs()
    inp.pages.append(page("long", body="x" * (audit.LONG_PAGE_LIMIT + 1), links=["topic"]))
    inp.pages.append(page("child", parent="topic", links=["topic"]))
    inp.pages[2]["links"] = [link for link in inp.pages[2]["links"] if link != "child"]
    result = audit.check_long_pages(inp)
    assert result["evidence"] == {"too_long": ["long"], "unlinked_children": ["child"]}


def test_split_child_linked_from_parent_body_passes():
    inp = healthy_inputs()
    inp.pages.append(page("child", parent="topic", links=["topic"]))
    inp.pages[2]["body"] += f"\n- [[{stem('child')}|Part 1]]"
    assert audit.check_long_pages(inp)["pass"]


def test_frontmatter_requires_every_key():
    inp = healthy_inputs()
    inp.markdown[stem("topic")] = "---\nid: topic\nkind: concept\n---\n"
    del inp.markdown[stem("answer")]
    result = audit.check_frontmatter(inp)
    assert result["evidence"] == {"missing_file": ["answer"], "bad": ["topic"]}


def test_freshness_requires_recent_ingest_and_healthy_sources():
    inp = healthy_inputs()
    inp.collection_health["saveticker"] = {"healthy": False, "availability": "blocked"}
    inp.log_md = inp.log_md.replace("[2026-10-16] ingest", "[2026-10-10] ingest")
    assert audit.check_freshness(inp)["metric"] == {"ingest_24h": False, "sources": 2, "unhealthy": 1}


def test_collection_health_treats_blocked_failed_and_stale_as_unhealthy():
    rows = {"a": {"availability": "blocked"}, "b": {"fetch_success": False}, "c": {}, "d": {"fetch_success": True},
            "e": {"availability": "disabled"}}
    health = audit.collection_health_from(rows, [{"source": "c"}])
    assert {k: v["healthy"] for k, v in health.items()} == {"a": False, "b": False, "c": False, "d": True}


def test_crashing_check_is_reported_as_failure(monkeypatch):
    monkeypatch.setitem(audit.CHECKS, "C8_schema", lambda _inp: 1 / 0)
    report = audit.run_audit(healthy_inputs())
    assert report["results"]["C8_schema"]["pass"] is False and not report["ok"]


def test_title_alias_links_with_bracketed_ordinals_resolve():
    inp = healthy_inputs()
    inp.pages.append(page("child-1", parent="topic", links=["topic"]))
    inp.pages[-1]["title"] = "Capacity history [0001]"
    inp.pages[2]["body"] += "\n- [[Capacity history [0001]]]\n- [[Missing page [0002]]]"
    _, _, broken = audit.link_graph(inp.pages)
    assert broken["topic"] == {"Missing page [0002]"}


@pytest.mark.skipif(os.getenv("WIKI_LIVE_ACCEPTANCE") != "1", reason="live store gate is opt-in")
def test_live_wiki_meets_every_karpathy_condition(monkeypatch):
    from pathlib import Path
    monkeypatch.setenv("AGENT_CONSOLE_SHARED_MEMORY_DIR", str(Path.home() / ".local/share/stock-report/shared-memory"))
    monkeypatch.setenv("AGENT_CONSOLE_QMD_DOCS_DIR", str(Path.home() / ".local/share/stock-report/shared-memory/qmd-wiki"))
    report = audit.run_audit(audit.live_inputs())
    failing = {k: (v["metric"], v["threshold"]) for k, v in report["results"].items() if not v["pass"]}
    assert failing == {}
