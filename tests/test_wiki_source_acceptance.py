"""Synthetic end-to-end evidence; these tests do not certify model reasoning."""
import hashlib
import json
from pathlib import Path

import pytest

from agent_console import shared_memory, wiki
from reports import wiki_source_pipeline, wiki_source_revision
from test_wiki_source_pipeline import pipeline_env, NOW
from test_wiki_source_revision import archived_event
from test_wiki_source_chunks import response as chunk_response

REAL_DISCOVERY = wiki_source_revision.discover_revisions
FIXTURES = Path(__file__).parent / "fixtures" / "wiki_source_semantics.json"
SAMPLES = Path(__file__).parent / "fixtures" / "wiki_source_semantic_outputs.json"


def test_verified_long_archive_compiles_into_lossless_children_and_reuses_chunks(pipeline_env, archived_event, monkeypatch):
    original = 'Capacity reported; realized orders uncertain.\n' * 1100 + 'TAIL'
    event = archived_event(body=original)
    raw_before = Path(event['raw_path']).read_bytes()
    text_before = Path(event['text_path']).read_bytes()
    monkeypatch.setattr(wiki_source_revision, 'discover_revisions', REAL_DISCOVERY)
    def llm(prompt):
        if prompt.startswith('Compile'):
            pipeline_env['calls'].append('compile')
            payload = json.loads(chunk_response(prompt))
            payload['body'] += '\nDetailed attributed discussion. ' * 400
            return json.dumps(payload)
        return pipeline_env['llm'](prompt)
    result = wiki_source_pipeline.run(events=[event], dry_run=False, llm_fn=llm, now=NOW)
    assert result['compiled'] == result['data_committed'] == 1
    revision = REAL_DISCOVERY([event], now=NOW, allow_chunking=True)['eligible'][0]
    page_id = hashlib.sha256(('compiled-source:' + revision['revision_id']).encode()).hexdigest()
    snapshot = wiki.semantic_snapshot(page_id)
    assert snapshot['children'] and 'TAIL' in snapshot['body']
    for claim in snapshot['claims']:
        anchor = claim['anchor']
        assert original[anchor['start']:anchor['end']] == anchor['quote']
        assert claim['source_revision_id'] == revision['revision_id']
    before_calls = list(pipeline_env['calls'])
    repeated = wiki_source_pipeline.run(events=[event], dry_run=False, llm_fn=llm, now=NOW)
    assert repeated['data_committed'] == 0 and pipeline_env['calls'] == before_calls
    assert Path(event['raw_path']).read_bytes() == raw_before
    assert Path(event['text_path']).read_bytes() == text_before


def test_pending_long_archive_resumes_after_leaving_recent_events(pipeline_env, archived_event, monkeypatch):
    event = archived_event(body='Reported capacity with uncertainty.\n' * 2400)
    monkeypatch.setattr(wiki_source_revision, 'discover_revisions', REAL_DISCOVERY)
    first = wiki_source_pipeline.run(events=[event], dry_run=False, llm_fn=pipeline_env['llm'], now=NOW)
    assert first['pending_chunking'] == 1 and first['data_committed'] == 0
    second = wiki_source_pipeline.run(events=[], dry_run=False, llm_fn=pipeline_env['llm'], now=NOW)
    assert second['compiled'] == second['data_committed'] == 1
    assert pipeline_env['calls'].count('compile') == 5


def test_pending_archive_resume_rechecks_raw_provenance_before_model_call(pipeline_env, archived_event, monkeypatch):
    event = archived_event(body='Reported capacity with uncertainty.\n' * 2400)
    monkeypatch.setattr(wiki_source_revision, 'discover_revisions', REAL_DISCOVERY)
    wiki_source_pipeline.run(events=[event], dry_run=False, llm_fn=pipeline_env['llm'], now=NOW)
    Path(event['raw_path']).unlink()
    result = wiki_source_pipeline.run(events=[], dry_run=False, llm_fn=pipeline_env['llm'], now=NOW)
    assert result['unavailable'] == 1 and result['reason_counts']['raw_unavailable'] == 1
    assert result['data_committed'] == result['compiled'] == 0
    assert pipeline_env['calls'] == ['compile'] * 4


def test_source_knowledge_compounds_across_pages(pipeline_env, archived_event, monkeypatch):
    cases = json.loads(FIXTURES.read_text())
    case = cases[0]
    event = archived_event(body=case["source"])
    raw_path = Path(event["raw_path"])
    before = raw_path.read_bytes()
    monkeypatch.setattr(wiki_source_revision, "discover_revisions", REAL_DISCOVERY)
    wiki.upsert_page({"id": "topic", "title": "Capacity planning", "body": case["prior_knowledge"],
                      "kind": "concept", "surface": "market"})
    wiki.upsert_page({"id": "unrelated", "title": "Currency", "body": "Unrelated unchanged.",
                      "kind": "concept", "surface": "market"})
    unrelated = wiki.semantic_snapshot("unrelated")["revision_hash"]
    original = pipeline_env["llm"]
    tail = "End of synthesis: " + case["required_caveats"][0]
    def llm(prompt):
        payload = json.loads(original(prompt))
        if prompt.startswith("Compile"):
            assert case["source"] in prompt
            payload["body"] += "\n" + ("Detailed permitted discussion. " * 500) + tail + " [S1]."
        return json.dumps(payload)
    result = wiki_source_pipeline.run(events=[event], dry_run=False, llm_fn=llm, now=NOW)
    assert result["data_committed"] == 1 and result["effects_pending"] == 0
    revision = REAL_DISCOVERY([event], now=NOW)["eligible"][0]["revision_id"]
    compiled_id = hashlib.sha256(("compiled-source:" + revision).encode()).hexdigest()
    compiled = wiki.semantic_snapshot(compiled_id)
    assert tail in compiled["body"] and len(compiled["children"]) >= 2
    for page_id in ("entity", "topic"):
        snap = wiki.semantic_snapshot(page_id)
        assert "Demand uncertain" in snap["body"] or "demand uncertain" in snap["body"]
        assert "Added capacity" in snap["body"]
        assert compiled_id in wiki.get_page(page_id)["links"]
        assert event["url"] in snap["source_refs"]
    assert wiki.semantic_snapshot("unrelated")["revision_hash"] == unrelated
    assert raw_path.read_bytes() == before
    repeated = wiki_source_pipeline.run(events=[event], dry_run=False, llm_fn=llm, now=NOW)
    assert repeated["data_committed"] == 0 and pipeline_env["calls"].count("compile") == 1
    out = wiki.wiki_artifacts_dir()
    assert all((out / name).exists() for name in ("index.md", "schema.md", "log.md"))
    assert compiled_id in (out / "log.md").read_text()


def test_query_fileback_and_excerpt_contract_remain_compatible(pipeline_env):
    wiki_source_pipeline.run(events=[{}], dry_run=False, llm_fn=pipeline_env["llm"], now=NOW)
    saved = wiki.capture_from_chat("How to validate capacity?", "Retain the demand caveat and verify orders.",
                                  surface="market", kind="playbook", source_refs=["https://example.org/approved"])
    before = wiki.semantic_snapshot(saved["id"])["revision_hash"]
    page = wiki.get_page("entity")
    page["body"] = "x" * 1600 + "NOT_READ_TAIL"
    section = wiki.build_context_section(query="capacity", surface="market", pages=[page])
    assert "NOT_READ_TAIL" not in section and wiki.WIKI_CONTEXT_BODY_SNIPPET == 1600
    excerpt = wiki._context_body_excerpt(page)
    assert len(excerpt) <= 1600
    assert "x" * 100 in excerpt and excerpt in section
    assert "Demand uncertain" in excerpt and "draft" in excerpt
    assert "전체 검토 아님" in excerpt
    assert page["body"].endswith("NOT_READ_TAIL")
    wiki_source_pipeline.run(events=[{}], dry_run=False, llm_fn=pipeline_env["llm"], now=NOW)
    assert wiki.semantic_snapshot(saved["id"])["revision_hash"] == before
    assert any(item["id"] == saved["id"] for item in wiki.list_pages(query="validate capacity", surface="market"))


def test_unavailable_sources_never_count_as_completed(pipeline_env, archived_event, monkeypatch):
    event = archived_event()
    Path(event["raw_path"]).unlink()
    monkeypatch.setattr(wiki_source_revision, "discover_revisions", REAL_DISCOVERY)
    rows = shared_memory.inspect_records()
    result = wiki_source_pipeline.run(events=[event], dry_run=False, llm_fn=pipeline_env["llm"], now=NOW)
    assert result["unavailable"] == 1 and result["eligible"] == result["data_committed"] == 0
    assert result["reason_counts"]["raw_unavailable"] == 1
    assert pipeline_env["calls"] == [] and shared_memory.inspect_records() == rows


def test_semantic_review_cases_are_unreviewed_and_cover_known_failure_modes():
    cases = json.loads(FIXTURES.read_text())
    assert {case["failure_mode"] for case in cases} >= {
        "capacity_is_not_demand", "different_horizons", "community_attribution", "ocr_uncertainty"}
    for case in cases:
        assert case["reviewer_verdict"] == "pending"
        assert case["source"] and case["required_caveats"] and case["forbidden_claims"]


@pytest.mark.parametrize("case_id", ["capacity-demand", "different-horizons", "community-claim", "ocr-number"])
def test_recorded_model_samples_remain_replayable_but_need_quote_anchors_now(case_id):
    """Recorded under the former anchor=null prompt; replay stays valid, fresh output must quote."""
    from reports.wiki_source_compiler import compile_source, validate_compilation
    case = next(case for case in json.loads(FIXTURES.read_text()) if case["id"] == case_id)
    samples = json.loads(SAMPLES.read_text())
    response = next(case["response"] for case in samples["cases"] if case["id"] == case_id)
    url = "https://example.org/synthetic/" + case_id
    source = {"revision_id": hashlib.sha256(case_id.encode()).hexdigest(), "provider": "synthetic",
              "canonical_url": url, "scope": "synthetic_text", "title": case_id, "body": case["source"],
              "provenance_class": {"community-claim": "community", "ocr-number": "ocr_report"}.get(case_id, "editorial")}
    replayed = validate_compilation(response, source)
    assert samples["model"] == "gpt-6.1-sol" and url in replayed["body"]
    assert replayed["claims"] and all(claim["knowledge_state"] == "draft" for claim in replayed["claims"])
    assert all(not claim["competing_claim_ids"] for claim in replayed["claims"])
    with pytest.raises(ValueError, match="anchor_missing"):
        compile_source(source, llm_fn=lambda _: json.dumps(response))
