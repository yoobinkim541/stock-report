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


def source_text(prompt):
    """Decode the compile prompt's JSON source literal (the only text between the markers)."""
    literal = prompt[:-len("\nEND UNTRUSTED SOURCE TEXT")].split("\nBEGIN UNTRUSTED SOURCE TEXT\n", 1)[1]
    return json.loads(literal)


@pytest.fixture(autouse=True)
def no_live_structured_provider(monkeypatch):
    from agent_console import agent
    from lib import llm_direct
    def blocked(*a, **kw):
        pytest.fail("unit test reached live structured provider")
    monkeypatch.setattr(agent, "_try_hermes_chat", blocked)
    monkeypatch.setattr(llm_direct, "direct_chat", blocked)


def build_pipeline_env(tmp_path, monkeypatch):
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
            text = source_text(prompt)
            quote = "Capacity increased" if "Capacity increased" in text else text[:60]
            # The claim restates its own quote (a chunk without the sentence is restated verbatim).
            claim = "Capacity increased" if quote == "Capacity increased" else quote
            return json.dumps({"action": "create", "kind": "concept", "title": "Capacity", "body": "Compiled capacity; demand uncertain [S1].",
                "claims": [{"text": claim, "as_of": "2026-10-09", "scope": "capacity, not realized orders",
                            "anchor": {"quote": quote}, "competing_claim_ids": []}],
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


@pytest.mark.parametrize('response,code', [(None, 'compilation_response_invalid'),
                                         ('not JSON', 'compilation_response_invalid')])
def test_compile_failure_retains_safe_reason_code(pipeline_env, response, code):
    result = module().run(events=[{}], dry_run=False, llm_fn=lambda _: response, now=NOW)
    assert result['compile_failed'] == 1
    assert result['reason_counts'][code] == 1
    receipt = module().Journal().load_source(REV)
    assert receipt['error_code'] == code


def test_compile_exception_never_persists_arbitrary_error_text(pipeline_env):
    secret = 'synthetic-private-marker'
    def failed(_):
        raise RuntimeError(secret)
    result = module().run(events=[{}], dry_run=False, llm_fn=failed, now=NOW)
    assert result['reason_counts']['compile_failed'] == 1
    assert secret not in json.dumps(result)
    for path in (pipeline_env['root'] / 'reports').rglob('*.json'):
        assert secret not in path.read_text()


def test_nonstring_exception_payload_does_not_break_failure_recording(pipeline_env):
    def failed(_):
        raise ValueError({'synthetic': 'private-marker'})
    result = module().run(events=[{}], dry_run=False, llm_fn=failed, now=NOW)
    assert result['reason_counts']['compile_failed'] == 1


def test_failed_compile_can_recover_and_clear_previous_diagnostic(pipeline_env):
    failed = module().run(events=[{}], dry_run=False, llm_fn=lambda _: None, now=NOW)
    assert failed['compile_failed'] == 1
    recovered = module().run(events=[{}], dry_run=False, llm_fn=pipeline_env['llm'], now=NOW)
    assert recovered['data_committed'] == 1
    assert recovered['compile_failed'] == 0
    assert 'compilation_response_invalid' not in recovered['reason_counts']
    assert module().Journal().load_source(REV)['error_code'] == ''


def test_compiled_note_uses_model_title_not_original_headline(pipeline_env):
    pipeline_env['source']['title'] = 'Long original headline ' * 12
    result = module().run(events=[{}], dry_run=False, llm_fn=pipeline_env['llm'], now=NOW)
    assert result['data_committed'] == 1
    identity = hashlib.sha256(('compiled-source:' + REV).encode()).hexdigest()
    assert wiki.get_page(identity)['title'] == 'Capacity'


def test_long_source_only_integrates_after_every_chunk_is_compiled(pipeline_env):
    pipeline_env['source']['body'] = 'Capacity with caveat.\n' * 4300
    first = module().run(events=[{}], dry_run=False, llm_fn=pipeline_env['llm'], now=NOW)
    assert first['pending_chunking'] == 1
    assert first['compiled'] == first['data_committed'] == first['compile_failed'] == 0
    assert pipeline_env['calls'] == ['compile'] * 4
    second = module().run(events=[{}], dry_run=False, llm_fn=pipeline_env['llm'], now=NOW)
    assert second['compiled'] == second['data_committed'] == 1
    assert pipeline_env['calls'].count('compile') == 5
    page_id = hashlib.sha256(('compiled-source:' + REV).encode()).hexdigest()
    assert len(wiki.semantic_snapshot(page_id)['claims']) == 5


def test_scoped_worker_never_compiles_a_nonselected_source(pipeline_env):
    other = copy.deepcopy(pipeline_env['source'])
    other['revision_id'] = 'b' * 64
    other['title'] = 'Excluded private context'
    pipeline_env['discovery']['eligible'].insert(0, other)
    result = module().run(events=[{}, {}], dry_run=False, llm_fn=pipeline_env['llm'], now=NOW,
                          allowed_revision_ids={REV})
    assert result['eligible'] == result['data_committed'] == 1
    assert pipeline_env['calls'].count('compile') == 1


def test_empty_source_allowlist_does_not_call_model(pipeline_env):
    result = module().run(events=[{}], dry_run=False,
                          llm_fn=lambda _: pytest.fail('unselected source reached model'),
                          now=NOW, allowed_revision_ids=set())
    assert result['eligible'] == result['data_committed'] == 0


def test_default_worker_uses_structured_provider_contract(pipeline_env, monkeypatch):
    from lib import llm_cli

    budgets = []
    def structured(prompt, *, max_timeout=45):
        budgets.append(max_timeout)
        return pipeline_env["llm"](prompt)

    monkeypatch.setenv("WIKI_SOURCE_PIPELINE_ENABLED", "1")
    monkeypatch.setattr(llm_cli, "structured_chat", structured)
    result = module().run(events=[{}], dry_run=False, now=NOW)
    assert result["data_committed"] == 1
    assert pipeline_env["calls"] == ["compile", "route", "integrate"]
    assert budgets == [180, 180, 180]


def test_compiled_page_label_does_not_claim_exhaustive_summary(pipeline_env):
    module().run(events=[{}], dry_run=False, llm_fn=pipeline_env["llm"], now=NOW)
    page_id = hashlib.sha256(("compiled-source:" + REV).encode()).hexdigest()
    page = wiki.get_page(page_id)
    assert "Source-derived summary" in page["body"]
    assert "Complete source compilation" not in page["body"]
    assert wiki.semantic_snapshot(page_id)["claims"]


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
                "scope": "demand, not capacity", "anchor": {"quote": "Demand uncertain."},
                "competing_claim_ids": []})
            return json.dumps(payload)
        return answer
    pipeline_env["source"]["title"] = "Capacity " * 30
    result = module().run(events=[{}], dry_run=False, llm_fn=llm, now=NOW)
    assert result["data_committed"] == 1
    page_id = hashlib.sha256(("compiled-source:" + REV).encode()).hexdigest()
    claims = wiki.semantic_snapshot(page_id)["claims"]
    assert {claim["as_of"] for claim in claims} == {"2026-10-09", "2026-10-08"}


# B-fix-ground-01: an invented, validly cited claim never reaches stored or searchable knowledge.
@pytest.mark.parametrize("claim,anchor,code", [
    ("Revenue is guaranteed to triple in 2027.", None, "anchor_missing"),
    ("Revenue is guaranteed to triple in 2027.", {"quote": "Capacity increased."}, "claim_number_unsupported"),
    ("Capacity is guaranteed.", {"quote": "Capacity increased."}, "claim_certainty_unsupported"),
])
def test_invented_claim_is_rejected_before_store_and_search(pipeline_env, claim, anchor, code):
    original = pipeline_env["llm"]
    def llm(prompt):
        answer = original(prompt)
        if prompt.startswith("Compile"):
            payload = json.loads(answer)
            payload["claims"][0].update(text=claim, anchor=anchor)
            return json.dumps(payload)
        return answer
    result = module().run(events=[{}], dry_run=False, llm_fn=llm, now=NOW)
    assert result["compile_failed"] == 1 and result["data_committed"] == 0
    assert result["reason_counts"][code] == 1
    assert module().Journal().load_source(REV)["error_code"] == code
    page_id = hashlib.sha256(("compiled-source:" + REV).encode()).hexdigest()
    assert wiki.get_page(page_id) is None
    assert not any("guaranteed" in (page.get("body") or "")
                   for page in wiki.list_pages(query="guaranteed triple", status="all", surface="all"))
    assert "guaranteed" not in wiki.build_context_section(query="revenue guaranteed triple", surface="market")
    assert pipeline_env["calls"] == ["compile"]


# B-fix2-02: a reversed claim or an unrelated body never reaches stored, searchable or context knowledge.
@pytest.mark.parametrize("change,code", [
    ({"claim": "Capacity fell."}, "claim_direction_unsupported"),
    ({"body": "The regulator approved the acquisition and all factories closed [S1]."}, "body_sentence_unrelated"),
])
def test_reversed_claim_or_unrelated_body_never_reaches_search_or_context(pipeline_env, change, code):
    original = pipeline_env["llm"]
    def llm(prompt):
        answer = original(prompt)
        if prompt.startswith("Compile"):
            payload = json.loads(answer)
            if "claim" in change:
                payload["claims"][0].update(text=change["claim"], anchor={"quote": "Capacity increased."})
            if "body" in change:
                payload["body"] = change["body"]
            return json.dumps(payload)
        return answer
    result = module().run(events=[{}], dry_run=False, llm_fn=llm, now=NOW)
    assert result["compile_failed"] == 1 and result["data_committed"] == 0
    assert result["reason_counts"][code] == 1
    assert wiki.get_page(hashlib.sha256(("compiled-source:" + REV).encode()).hexdigest()) is None
    pages = wiki.list_pages(query="capacity regulator acquisition", status="all", surface="all")
    assert not any("regulator" in (page.get("body") or "") or "Capacity fell" in (page.get("body") or "")
                   for page in pages)
    context = wiki.build_context_section(query="capacity regulator acquisition", surface="market")
    assert "regulator" not in context and "Capacity fell" not in context
    assert pipeline_env["calls"] == ["compile"]


# B-fix2-01: a faithful Korean claim for an English source is stored, and context labels it unverified.
def test_translated_claim_is_stored_and_labelled_unverified_in_context(pipeline_env):
    original = pipeline_env["llm"]
    def llm(prompt):
        answer = original(prompt)
        if prompt.startswith("Compile"):
            payload = json.loads(answer)
            payload["body"] = "생산능력은 증가했고 수요는 불확실하다 [S1]."
            payload["claims"][0].update(text="생산능력이 증가했다.", anchor={"quote": "Capacity increased."})
            return json.dumps(payload)
        return answer
    result = module().run(events=[{}], dry_run=False, llm_fn=llm, now=NOW)
    assert result["compile_failed"] == 0 and result["data_committed"] == 1
    page = wiki.get_page(hashlib.sha256(("compiled-source:" + REV).encode()).hexdigest())
    assert page["source_knowledge"]["claims"][0]["text"] == "생산능력이 증가했다."
    context = wiki.build_context_section(pages=[page])
    assert "- 본문(미검증 모델 합성): " in context and wiki.MODEL_SYNTHESIS_WARNING in context


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




def test_public_rollout_does_not_reauthorize_privately_changed_compiled_note(pipeline_env, monkeypatch):
    from agent_console import agent
    from reports.wiki_source_journal import Journal
    from scripts import run_public_wiki_rollout as rollout

    source = pipeline_env["source"]
    source.update(provider="telegram:public_channel", scope="public_message",
                  canonical_url="https://t.me/public_channel/1")
    journal = Journal(pipeline_env["root"] / "public-journal")
    attempt = {"id": "synthetic-attempt", "revision_id": source["revision_id"]}
    model_prompts = []

    def mocked_chat(prompt, **kwargs):
        model_prompts.append(prompt)
        return pipeline_env["llm"](prompt.split("\n", 1)[1])

    monkeypatch.setattr(agent, "_try_codex_chat", mocked_chat)

    first = rollout.process(source, [], journal, attempt)
    assert first["data_committed"] == 1

    page_id = hashlib.sha256(("compiled-source:" + REV).encode()).hexdigest()
    page = wiki.get_page(page_id)
    page["body"] += "\nPrivate marker: synthetic-secret"
    wiki.upsert_page(page)
    source["observed_evidence_ids"].append("synthetic-redelivery-id")

    second = rollout.process(source, [], journal, attempt)

    assert second["data_committed"] == 0
    assert page_id not in rollout.public_target_ids(journal, {REV})
    assert all("synthetic-secret" not in prompt for prompt in model_prompts)
