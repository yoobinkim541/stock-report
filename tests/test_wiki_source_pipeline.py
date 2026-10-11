import copy
import hashlib
import importlib
import json
from datetime import datetime, timezone

import pytest

from agent_console import shared_memory, wiki
from reports import wiki_source_revision


REV = "a" * 64
NOW = datetime(2026, 10, 10, tzinfo=timezone.utc)


def module():
    return importlib.import_module("reports.wiki_source_pipeline")


@pytest.fixture
def pipeline_env(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_CONSOLE_SHARED_MEMORY_DIR", str(tmp_path / "memory"))
    monkeypatch.setenv("STOCK_REPORT_REPORTS_DIR", str(tmp_path / "reports"))
    monkeypatch.setenv("AGENT_CONSOLE_QMD_ENABLED", "0")
    monkeypatch.delenv("WIKI_SOURCE_PIPELINE_ENABLED", raising=False)
    monkeypatch.setattr(shared_memory, "_schedule_context_memory_summary", lambda: None)
    monkeypatch.setattr(wiki, "_debounced_rebuild", lambda: None)
    wiki._CACHE.clear()
    wiki.upsert_page({"id": "entity", "title": "Capacity", "body": "Old exception. Demand uncertain.",
                      "kind": "concept", "surface": "market", "source_refs": ["https://example.org/old"]})
    source = {"revision_id": REV, "protocol_version": 1, "provider": "saveticker", "body": "Capacity increased. Demand uncertain.",
        "canonical_url": "https://saveticker.com/news/synthetic", "scope": "authorized_article", "provenance_class": "editorial",
        "raw_sha256": "b" * 64, "text_sha256": "c" * 64, "raw_ref": "raw-sha256:" + "b" * 64,
        "text_ref": "https://saveticker.com/news/synthetic", "manifest_ref": "https://saveticker.com/news/synthetic",
        "title": "Capacity", "published_at": "2026-10-09", "collected_at": NOW.isoformat(),
        "ingested_at": NOW.isoformat(), "observed_evidence_ids": ["actual-id"], "role": "support"}
    discovery = {"eligible": [source], "unavailable": [], "pending_chunking": []}
    monkeypatch.setattr(wiki_source_revision, "discover_revisions", lambda *args, **kwargs: copy.deepcopy(discovery))
    calls = []
    def llm(prompt):
        if prompt.startswith("Compile"):
            calls.append("compile")
            return json.dumps({"action": "create", "kind": "concept", "title": "Capacity", "body": "Compiled capacity; demand uncertain [S1].",
                "claims": [{"text": "Capacity increased", "as_of": "2026-10-09", "scope": "capacity, not realized orders",
                            "anchor": None, "competing_claim_ids": []}],
                "terms": ["capacity"], "limitations": ["Demand uncertain"], "skip_reason": None})
        data = json.loads(prompt.rsplit("\n", 1)[1])
        if prompt.startswith("Route"):
            calls.append("route")
            return json.dumps({"selected": [item["page_id"] for item in data["candidates"]], "rejected": {}, "new_page_specs": []})
        calls.append("integrate")
        claim = data["compiled"]["claims"][0]
        return json.dumps({"targets": [{"page_id": item["page_id"], "before_hash": item["revision_hash"], "additions": [{
            "claim_ids": [claim["claim_id"]], "as_of": claim["as_of"], "scope": claim["scope"],
            "body": "Added capacity; demand uncertain [S1]."}], "relations": []} for item in data["targets"]]})
    return {"root": tmp_path, "source": source, "discovery": discovery, "calls": calls, "llm": llm}


def test_dry_run_has_no_model_calls_or_store_changes(pipeline_env):
    root = pipeline_env["root"]
    before = {str(path): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    result = module().run(events=[{}], dry_run=True, llm_fn=pipeline_env["llm"], now=NOW)
    assert pipeline_env["calls"] == [] and result["eligible"] == 1
    assert {str(path): path.read_bytes() for path in root.rglob("*") if path.is_file()} == before


def test_complete_source_compounds_and_unavailable_source_is_independent(pipeline_env):
    pipeline_env["discovery"]["unavailable"] = [{"discovery_id": "d" * 64, "reason": "raw_unavailable"}]
    result = module().run(events=[{}, {}], dry_run=False, llm_fn=pipeline_env["llm"], now=NOW)
    assert result["compiled"] == 1 and result["data_committed"] == 1 and result["unavailable"] == 1
    assert result["effects_pending"] == 0
    assert wiki.semantic_snapshot("entity")["body"].startswith("Old exception")
    assert "Added capacity" in wiki.semantic_snapshot("entity")["body"]
    page_id = hashlib.sha256(("compiled-source:" + REV).encode()).hexdigest()
    page = wiki.semantic_snapshot(page_id)
    assert "Compiled capacity" in page["body"]
    assert page["page"]["status"] == "draft" and "compiled_source" in page["page"]["tags"]
    assert page_id in wiki.get_page("entity")["links"]
    repeated = module().run(events=[{}], dry_run=False, llm_fn=pipeline_env["llm"], now=NOW)
    assert pipeline_env["calls"].count("compile") == 1
    assert repeated["data_committed"] == 0
    assert wiki.semantic_snapshot("entity")["body"].count("Added capacity") == 1


def test_role_change_does_not_recompile_or_repeat_prose(pipeline_env):
    module().run(events=[{}], dry_run=False, llm_fn=pipeline_env["llm"], now=NOW)
    pipeline_env["source"].update(role="conflict", observed_evidence_ids=["actual-conflict"])
    result = module().run(events=[{}], dry_run=False, llm_fn=pipeline_env["llm"], now=NOW)
    assert pipeline_env["calls"].count("compile") == 1
    assert result["data_committed"] == 1
    snap = wiki.semantic_snapshot("entity")
    assert "actual-conflict" in snap["conflicting_evidence_ids"]
    assert snap["body"].count("Added capacity") == 1


def test_redelivery_associates_actual_new_id_without_repeating_prose(pipeline_env):
    module().run(events=[{}], dry_run=False, llm_fn=pipeline_env["llm"], now=NOW)
    pipeline_env["source"]["observed_evidence_ids"] = ["later-actual-id"]
    module().run(events=[{}], dry_run=False, llm_fn=pipeline_env["llm"], now=NOW)
    assert "later-actual-id" in wiki.semantic_snapshot("entity")["evidence_ids"]
    assert wiki.semantic_snapshot("entity")["body"].count("Added capacity") == 1
    assert pipeline_env["calls"].count("compile") == 1


def test_default_off_worker_prevents_unapproved_provider_call(pipeline_env):
    with pytest.raises(ValueError, match="model_activation_required"):
        module().run(events=[{}], dry_run=False, now=NOW)


def test_compiled_page_preserves_every_claim_and_long_source_title(pipeline_env):
    original = pipeline_env["llm"]
    def llm(prompt):
        answer = original(prompt)
        if prompt.startswith("Compile"):
            payload = json.loads(answer)
            payload["claims"].append({"text": "Demand uncertain", "as_of": "2026-10-08",
                "scope": "demand, not capacity", "anchor": None, "competing_claim_ids": []})
            return json.dumps(payload)
        return answer
    pipeline_env["source"]["title"] = "Capacity " * 30
    result = module().run(events=[{}], dry_run=False, llm_fn=llm, now=NOW)
    assert result["data_committed"] == 1
    page_id = hashlib.sha256(("compiled-source:" + REV).encode()).hexdigest()
    claims = wiki.semantic_snapshot(page_id)["claims"]
    assert {claim["as_of"] for claim in claims} == {"2026-10-09", "2026-10-08"}


def test_skipped_redelivery_retains_new_actual_id_without_recompile(pipeline_env):
    from reports.wiki_source_journal import Journal
    calls = []
    def skip(prompt):
        calls.append(prompt)
        return json.dumps({"action": "skip", "kind": "concept", "title": "Capacity", "body": "",
            "claims": [], "terms": [], "limitations": [], "skip_reason": "No reusable knowledge"})
    module().run(events=[{}], dry_run=False, llm_fn=skip, now=NOW)
    pipeline_env["source"].update(role="conflict", observed_evidence_ids=["actual-new-skipped"])
    module().run(events=[{}], dry_run=False, llm_fn=skip, now=NOW)
    receipt = Journal().load_source(REV)
    assert receipt["compile_state"] == "skipped"
    assert "actual-new-skipped" in receipt["evidence_by_role"]["conflict"]
    assert len(calls) == 1


def test_target_cursor_progresses_across_passes_without_repeating_prose(pipeline_env):
    wiki.upsert_page({"id": "topic", "title": "Capacity planning", "body": "Prior planning caveat.",
                      "kind": "concept", "surface": "market"})
    first = module().run(events=[{}], dry_run=False, llm_fn=pipeline_env["llm"], target_limit=1, now=NOW)
    second = module().run(events=[{}], dry_run=False, llm_fn=pipeline_env["llm"], target_limit=1, now=NOW)
    assert first["routing_pending"] == 1 and second["routing_pending"] == 0
    assert second["data_committed"] == 1 and pipeline_env["calls"].count("compile") == 1
    for page_id in ("entity", "topic"):
        assert wiki.semantic_snapshot(page_id)["body"].count("Added capacity") == 1


def test_routed_new_concept_redelivery_does_not_duplicate_prose(pipeline_env):
    original = pipeline_env["llm"]
    def llm(prompt):
        payload = json.loads(original(prompt))
        if prompt.startswith("Route"):
            payload["new_page_specs"] = [{"title": "Demand versus capacity", "kind": "concept",
                "surface": "market", "purpose": "Separate reported capacity from actual demand"}]
        elif prompt.startswith("Propose"):
            data = json.loads(prompt.rsplit("\n", 1)[1])
            claim = data["compiled"]["claims"][0]
            payload["targets"].extend({**spec, "before_hash": None, "additions": [{
                "claim_ids": [claim["claim_id"]], "as_of": claim["as_of"], "scope": claim["scope"],
                "body": "Added capacity; demand uncertain [S1]."}], "relations": []}
                for spec in data["new_page_specs"])
        return json.dumps(payload)
    first = module().run(events=[{}], dry_run=False, llm_fn=llm, now=NOW)
    assert first["data_committed"] == 1
    created = next(page for page in wiki.list_pages(surface="market", limit=20)
                   if page["title"] == "Demand versus capacity")
    pipeline_env["source"]["observed_evidence_ids"] = ["actual-redelivery"]
    module().run(events=[{}], dry_run=False, llm_fn=llm, now=NOW)
    snap = wiki.semantic_snapshot(created["id"])
    assert snap["body"].count("Added capacity") == 1
    assert "actual-redelivery" in snap["evidence_ids"]


def test_failed_final_routing_pass_is_retried_with_unchanged_evidence(pipeline_env):
    wiki.upsert_page({"id": "topic", "title": "Capacity planning", "body": "Prior caveat.",
                      "kind": "concept", "surface": "market"})
    module().run(events=[{}], dry_run=False, llm_fn=pipeline_env["llm"], target_limit=1, now=NOW)
    def fail_proposal(prompt):
        if prompt.startswith("Propose"):
            raise ValueError("temporary model failure")
        return pipeline_env["llm"](prompt)
    failed = module().run(events=[{}], dry_run=False, llm_fn=fail_proposal, target_limit=1, now=NOW)
    assert failed["routing_pending"] == 1
    retried = module().run(events=[{}], dry_run=False, llm_fn=pipeline_env["llm"], target_limit=1, now=NOW)
    assert retried["data_committed"] == 1
    assert "Added capacity" in wiki.semantic_snapshot("topic")["body"]


def test_enabled_worker_excludes_direct_legacy_source_writes(monkeypatch):
    from reports import wiki_distillation
    monkeypatch.setenv("WIKI_SOURCE_PIPELINE_ENABLED", "1")
    monkeypatch.setattr(wiki, "_all_wiki_pages", lambda: [{"id": "digest", "kind": "source_digest", "status": "draft"}])
    calls = []
    result = wiki_distillation.run(dry_run=True, llm_fn=lambda prompt: calls.append(prompt))
    assert result["candidates_considered"] == 0 and calls == []


@pytest.mark.parametrize("enabled", [False, True])
def test_legacy_cli_dispatches_exactly_one_owner(monkeypatch, enabled):
    from reports import wiki_distillation
    monkeypatch.setenv("WIKI_SOURCE_PIPELINE_ENABLED", "1" if enabled else "0")
    monkeypatch.setattr("sys.argv", ["wiki_distillation", "--dry-run"])
    calls = []
    monkeypatch.setattr(wiki_distillation, "run", lambda **kwargs: calls.append("legacy") or {
        "dry_run": True, "candidates_considered": 0, "created": []})
    monkeypatch.setattr(module(), "run", lambda **kwargs: calls.append("new") or {"data_committed": 0})
    assert wiki_distillation.main() == 0
    assert calls == ["new" if enabled else "legacy"]
