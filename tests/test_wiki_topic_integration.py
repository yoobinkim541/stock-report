"""Synthetic-only Task 2 proposal and provenance contracts.

Task 2 review ledger, round 1 (2026-10-15):
RED: pytest -q tests/test_wiki_topic_integration.py
     -k 'unverified_current or verified_current_receipt' --tb=short
     -> 15 failed (forged/stale current evidence was accepted), 1 passed.
GREEN: same command -> 16 passed, 42 deselected; invalid cases assert zero
       injected llm_fn calls, valid metadata-filtered evidence asserts one.
Regression: topic integration/context + source integration/compiler -> 137 passed.
Ruling: reuse Task 1's archive-aware _verified_source; bind source identity/body
and projected compilation to the retained public journal before model exposure.
No public API or return-schema change, external calls, or new write ownership.
"""
import copy
import importlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent_console import wiki
from reports import raw_archive
from reports.wiki_source_compiler import validate_compilation
from reports.wiki_source_integration import _new_spec
from reports.wiki_source_journal import Journal
from reports.wiki_source_revision import build_revision
from reports.wiki_topic_context import select_topic_context

NOW = datetime(2026, 10, 15, tzinfo=timezone.utc)


def api():
    return importlib.import_module("reports.wiki_topic_integration")


@pytest.fixture
def evidence(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCK_REPORT_REPORTS_DIR", str(tmp_path / "reports"))
    journal = Journal(tmp_path / "journal")
    rows, pairs = [], []
    for index in range(3):
        url = f"https://t.me/public_channel/{index + 1}"
        raw = raw_archive.save_raw_artifact("telegram", "text", NOW, "Public", url,
                                            f"Synthetic original {index}", ".txt")
        raw.update(body_coverage="full", text_complete=True, extraction_scope="public_message")
        raw_archive.save_extracted_text(raw, f"Synthetic original {index}")
        source = build_revision({"source": "telegram:public_channel", "url": url,
            "collected_at": NOW.isoformat(), "extraction_scope": "public_message",
            **{key: raw[key] for key in ("raw_path", "text_path", "manifest_path")}}, now=NOW)
        compiled = validate_compilation({"action": "create", "title": "Shipping",
            "body": "Attributed report [S1]", "claims": [{"text": f"Shipping report {index}",
            "as_of": f"2026-10-{13 + index}", "scope": "Hormuz shipping"}],
            "terms": ["Hormuz"], "limitations": [f"Uncertainty {index}"]}, source)
        journal.save_compilation(source, compiled)
        pairs.append((compiled, source))
        if index < 2:
            page_id = f"note-{index}"
            rows.append({"id": page_id, "title": "Shipping", "body": "Read only",
                "tags": ["wiki", "surface:market", "kind:concept", "status:draft", "compiled_source"]})
            path = journal.root / "operations" / f"{page_id}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"schema_version": 1, "stage": "committed",
                "revision_id": source["revision_id"], "batch": {"target_ids": [page_id],
                "expected_after": {page_id: wiki._source_fingerprint(page_id, rows)}}}))
    compiled, source = pairs[-1]
    context = select_topic_context(compiled, journal=journal, records=rows, now=NOW, surface="market")
    return dict(compiled=compiled, source=source, context=context, targets=[]), journal, rows


def proposal(data):
    revisions = [data["source"]["revision_id"], *sorted(n["revision_id"] for n in data["context"]["notes"])]
    claims = {data["compiled"]["revision_id"]: data["compiled"]["claims"],
              **{n["revision_id"]: n["claims"] for n in data["context"]["notes"]}}
    return {"refusal": None, "targets": [{"page_id": None, "before_hash": None,
        "title": "Hormuz shipping", "kind": "concept", "surface": "market", "purpose": "topic_synthesis",
        "contributing_revision_ids": revisions, "scope_explanation": "Dated shipping accounts, not adjudicated",
        "additions": [{"claim_ids": [claims[r][0]["claim_id"]], "revision_ids": [r],
            "as_of": claims[r][0]["as_of"], "scope": claims[r][0]["scope"],
            "body": f"Attributed shipping account [S{i + 1}]."} for i, r in enumerate(revisions)],
        "relations": []}]}


def validate(data, payload):
    return api().validate_topic_integration(payload, **data)


def test_two_source_seed_preserves_identity_and_deterministic_new_spec(evidence):
    data, _, _ = evidence
    data["context"]["notes"] = data["context"]["notes"][:1]
    data["context"]["dependencies"] = data["context"]["dependencies"][:1]
    before = copy.deepcopy(data)
    result = validate(data, proposal(data))
    target = result["targets"][0]
    expected = _new_spec({k: target[k] for k in ("title", "kind", "surface", "purpose")},
                         data["source"]["revision_id"], "market")
    assert target["page_id"] == expected["page_id"]
    assert target["knowledge_state"] == "draft"
    assert result["dependencies"] == data["context"]["dependencies"]
    assert result["source_map"]["S1"]["revision_id"] == data["source"]["revision_id"]
    assert [v["revision_id"] for k, v in result["source_map"].items() if k != "S1"] == sorted(
        n["revision_id"] for n in data["context"]["notes"])
    assert {c["source_revision_id"] for c in target["original_claims"]} == set(target["contributing_revision_ids"])
    assert target["limitations"] == list(dict.fromkeys([*data["compiled"]["limitations"],
        *[x for n in sorted(data["context"]["notes"], key=lambda n: n["revision_id"]) for x in n["limitations"]]]))
    assert "https://t.me/public_channel/3" in target["additions"][0]["body"]
    assert data == before


@pytest.mark.parametrize("reason", ["Unrelated event", "Homonym with a different scope"])
def test_valid_refusal(evidence, reason):
    assert validate(evidence[0], {"targets": [], "refusal": reason})["refusal"] == reason


def test_prompt_explicitly_supplies_new_topic_surface(evidence):
    data, journal, rows = evidence
    def llm(prompt):
        envelope = json.loads(prompt.rsplit("\n", 1)[1])
        assert envelope["surface"] == "market"
        return json.dumps({"targets": [], "refusal": "Not enough related evidence"})
    api().propose_topic_integration(**data, llm_fn=llm, journal=journal, records=rows, now=NOW)


@pytest.mark.parametrize("damage", ["claim", "date", "revision", "scope", "duplicate", "url", "label",
    "missing_citation", "extra_citation", "contributor", "trust", "nested", "readonly", "purpose", "kind",
    "relation_endpoint", "relation_date", "relation_revision", "relation_metadata", "single_source"])
def test_forged_proposals_fail_closed(evidence, damage):
    data, _, _ = evidence
    payload = proposal(data)
    t = payload["targets"][0]
    a = t["additions"][0]
    if damage == "claim": a["claim_ids"] = ["forged"]
    if damage == "date": a["as_of"] = "2099-01-01"
    if damage == "revision": a["revision_ids"] = ["forged"]
    if damage == "scope": a["scope"] = "different homonym"
    if damage == "duplicate": a["claim_ids"] *= 2
    if damage == "url": a["body"] = "Forged [S1](https://evil.example/substitution)"
    if damage == "label": a["body"] = "Forged [S99]"
    if damage == "missing_citation": a["body"] = "No attribution"
    if damage == "extra_citation": a["body"] += " [S2]"
    if damage == "contributor": t["contributing_revision_ids"].pop()
    if damage == "trust": t["knowledge_state"] = "verified"
    if damage == "nested": a["metadata"] = {"private": "secret"}
    if damage == "readonly": t["page_id"] = data["context"]["notes"][0]["page_id"]
    if damage == "purpose": t["purpose"] = "compiled_source"
    if damage == "kind": t["kind"] = "note"
    if damage.startswith("relation"):
        b = t["additions"][1]
        r = {"type": "compete", "from_claim_id": a["claim_ids"][0], "to_claim_id": b["claim_ids"][0],
             "from_revision_id": a["revision_ids"][0], "to_revision_id": b["revision_ids"][0],
             "from_as_of": a["as_of"], "to_as_of": b["as_of"], "scope_explanation": "Unresolved reports"}
        if damage == "relation_endpoint": r["to_claim_id"] = "fabricated"
        if damage == "relation_date": r["to_as_of"] = "unknown"
        if damage == "relation_revision": r["to_revision_id"] = a["revision_ids"][0]
        if damage == "relation_metadata": r["resolved"] = True
        t["relations"] = [r]
    if damage == "single_source":
        t["additions"] = [a]
        t["contributing_revision_ids"] = a["revision_ids"]
    with pytest.raises(ValueError): validate(data, payload)


# B-fix-ground-01: v2 legacy addition prose stays within its cited claims.
@pytest.mark.parametrize("body", [
    "Shipping volume will reach 900 vessels [S{i}].",
    "Hormuz shipping is guaranteed to resume [S{i}].",
    "Shipping traffic will triple [S{i}].",
])
def test_v2_addition_cannot_assert_numbers_or_certainty_absent_from_claims(evidence, body):
    data, _, _ = evidence
    payload = proposal(data)
    for index, addition in enumerate(payload["targets"][0]["additions"], 1):
        addition["body"] = body.format(i=index)
    with pytest.raises(ValueError, match="topic_addition_unsupported"):
        validate(data, payload)


def test_v2_addition_may_restate_cited_claim_numbers_and_negated_certainty(evidence):
    data, journal, rows = evidence
    payload = proposal(data)
    claims = {c["claim_id"]: c for c in [*data["compiled"]["claims"],
              *[c for n in data["context"]["notes"] for c in n["claims"]]]}
    for index, addition in enumerate(payload["targets"][0]["additions"], 1):
        claim = claims[addition["claim_ids"][0]]
        addition["body"] = f"{claim['text']} as of {claim['as_of']}; resumption is not guaranteed [S{index}]."
    result = api().propose_topic_integration(**data, journal=journal, records=rows, now=NOW,
                                             llm_fn=lambda _: json.dumps(payload))
    assert all("not guaranteed" in a["body"] for a in result["targets"][0]["additions"])


# B-fix2-02: prose without numbers or certainty words still cannot leave its cited claims.
def test_v2_addition_cannot_carry_unrelated_prose(evidence):
    data, _, _ = evidence
    payload = proposal(data)
    for index, addition in enumerate(payload["targets"][0]["additions"], 1):
        addition["body"] = f"The regulator approved the acquisition and all factories closed [S{index}]."
    with pytest.raises(ValueError, match="topic_addition_unsupported"):
        validate(data, payload)


def test_duplicate_claim_id_with_differing_content_is_rejected(evidence):
    data, _, _ = evidence
    claim = copy.deepcopy(data["compiled"]["claims"][0])
    claim["text"] = "Different content under the same identity"
    data["compiled"]["claims"].append(claim)
    with pytest.raises(ValueError): validate(data, proposal(data))


def test_current_source_identity_must_match_even_on_refusal(evidence):
    data, _, _ = evidence
    data["compiled"]["claims"][0]["source_revision_id"] = "wrong"
    with pytest.raises(ValueError): validate(data, {"targets": [], "refusal": "Unrelated"})


def test_update_retains_old_claims_relations_and_limitations(evidence):
    data, _, rows = evidence
    old = copy.deepcopy(data["context"]["notes"][0]["claims"][0])
    relation = {"type": "qualify", "from_claim_id": old["claim_id"], "to_claim_id": old["claim_id"],
        "from_revision_id": old["source_revision_id"], "to_revision_id": old["source_revision_id"],
        "from_as_of": old["as_of"], "to_as_of": old["as_of"], "scope_explanation": "Historical qualification"}
    row = {"id": "topic", "title": "Shipping", "kind": "concept", "body": "Old exceptions remain",
        "tags": ["wiki", "surface:market", "kind:concept", "status:draft"],
        "source_knowledge": {"claims": [old], "limitations": ["Old caveat"], "relations": [relation]}}
    snap = wiki.semantic_snapshot("topic", records=[*rows, row])
    data["targets"] = [snap]
    payload = proposal(data)
    t = payload["targets"][0]
    for key in ("title", "kind", "surface", "purpose"): t.pop(key)
    t.update(page_id="topic", before_hash=snap["revision_hash"])
    before = copy.deepcopy(data)
    result = validate(data, payload)["targets"][0]
    assert old in result["original_claims"] and relation in result["relations"]
    assert "Old caveat" in result["limitations"]
    assert data == before


def test_prompt_is_allowlisted_and_context_revalidated_before_exposure(evidence):
    data, journal, rows = evidence
    data["context"]["omitted"].append({"page_id": "PRIVATE_SENTINEL", "reason": "private"})
    data["source"]["private"] = {"secret": "PRIVATE_SENTINEL"}
    data["compiled"]["claims"][0]["private"] = {"secret": "PRIVATE_SENTINEL"}
    data["compiled"]["private"] = "PRIVATE_SENTINEL"
    def llm(prompt):
        assert "PRIVATE_SENTINEL" not in prompt
        assert "raw_sha256" not in prompt and "dependencies" not in prompt and "omitted" not in prompt
        assert "read_only_source_context" in prompt and "editable_topics" in prompt
        return json.dumps(proposal(data))
    api().propose_topic_integration(**data, journal=journal, records=rows, now=NOW, llm_fn=llm)
    rows[0]["body"] += " changed"
    with pytest.raises(ValueError, match="topic_context_changed"):
        api().propose_topic_integration(**data, journal=journal, records=rows, now=NOW,
                                       llm_fn=lambda _: pytest.fail("stale context exposed"))


def test_read_only_context_cannot_be_supplied_as_target(evidence):
    data, _, rows = evidence
    data["targets"] = [wiki.semantic_snapshot(rows[0]["id"], records=rows)]
    with pytest.raises(ValueError): validate(data, proposal(data))


def test_entire_contributing_revision_claim_set_survives(evidence):
    data, _, _ = evidence
    note = data["context"]["notes"][0]
    sibling = copy.deepcopy(note["claims"][0])
    sibling.update(claim_id="context-counterclaim", text="Dated counterclaim")
    note["claims"][0]["competing_claim_ids"] = [sibling["claim_id"]]
    note["claims"].append(sibling)
    result = validate(data, proposal(data))["targets"][0]
    assert sibling in result["original_claims"]
    assert note["claims"][0] in result["original_claims"]


def test_grouped_sources_require_each_citation_and_preserve_valid_relation(evidence):
    data, _, _ = evidence
    for note in data["context"]["notes"]:
        note["claims"][0]["as_of"] = data["compiled"]["claims"][0]["as_of"]
    payload = proposal(data)
    target = payload["targets"][0]
    first, second, third = target["additions"]
    relation = {"type": "compete", "from_claim_id": first["claim_ids"][0],
        "to_claim_id": second["claim_ids"][0], "from_revision_id": first["revision_ids"][0],
        "to_revision_id": second["revision_ids"][0], "from_as_of": first["as_of"],
        "to_as_of": second["as_of"], "scope_explanation": "Unresolved accounts, no truth adjudication"}
    target["relations"] = [relation]
    grouped = {**first, "claim_ids": first["claim_ids"] + second["claim_ids"],
               "revision_ids": first["revision_ids"] + second["revision_ids"], "body": "Two accounts [S1] [S2]"}
    target["additions"] = [grouped, third]
    assert validate(data, payload)["targets"][0]["relations"] == [relation]
    grouped["body"] = "Two accounts [S1]"
    with pytest.raises(ValueError): validate(data, payload)


@pytest.mark.parametrize("response", ["{} trailing", '{"targets":[],"refusal":"a","refusal":"b"}',
    '{"targets":[],"refusal":NaN}', '```json\n{"targets":[],"refusal":"a"}\n```', "[]", None])
def test_strict_json_responses(evidence, response):
    data, journal, rows = evidence
    with pytest.raises(ValueError):
        api().propose_topic_integration(**data, journal=journal, records=rows, now=NOW,
                                       llm_fn=lambda _: response)


@pytest.mark.parametrize("damage", ["revision", "body", "anchor", "claim_id", "role"])
def test_current_compilation_and_source_checked_before_model(evidence, damage):
    data, journal, rows = evidence
    if damage == "revision": data["source"]["revision_id"] = "forged"
    if damage == "body": data["compiled"]["body"] = "Substituted [S1](https://evil.example)"
    if damage == "anchor": data["compiled"]["claims"][0]["anchor"] = {"start": 0, "end": 3, "quote": "bad"}
    if damage == "claim_id": data["compiled"]["claims"][0]["claim_id"] = "forged"
    if damage == "role": data["source"]["role"] = "verified"
    with pytest.raises(ValueError):
        api().propose_topic_integration(**data, journal=journal, records=rows, now=NOW,
                                       llm_fn=lambda _: pytest.fail("invalid source exposed"))


def test_target_nested_metadata_is_never_exposed(evidence):
    data, journal, rows = evidence
    old = copy.deepcopy(data["context"]["notes"][0]["claims"][0])
    old["private"] = {"secret": "PRIVATE_SENTINEL"}
    row = {"id": "topic", "title": "Shipping", "kind": "concept", "body": "Old exception",
        "tags": ["wiki", "surface:market", "kind:concept", "status:draft"],
        "source_knowledge": {"claims": [old], "private": {"secret": "PRIVATE_SENTINEL"}},
        "messages": [{"body": "PRIVATE_SENTINEL"}]}
    rows.append(row)
    data["targets"] = [wiki.semantic_snapshot("topic", records=rows)]
    def llm(prompt):
        assert "PRIVATE_SENTINEL" not in prompt
        assert "Old exception" in prompt and old["text"] in prompt
        return json.dumps({"targets": [], "refusal": "Unrelated horizon"})
    assert api().propose_topic_integration(**data, journal=journal, records=rows, now=NOW,
                                          llm_fn=llm)["refusal"] == "Unrelated horizon"


@pytest.mark.parametrize("damage", ["url", "body", "compiled_claim", "compiled_body", "compiled_title",
    "compiled_terms", "compiled_limitations", "receipt_identity", "receipt_private", "receipt_deleted",
    "receipt_pending", "expired", "raw_changed", "text_changed", "compiled_artifact"])
def test_unverified_current_source_never_calls_model(evidence, damage):
    data, journal, rows = evidence
    source, compiled = data["source"], data["compiled"]
    receipt_path = journal._path("sources", source["revision_id"])
    receipt = journal.load_source(source["revision_id"])
    if damage == "url":
        old_url = source["canonical_url"]
        source["canonical_url"] = "https://t.me/forged_public_channel/123"
        compiled["body"] = compiled["body"].replace(old_url, source["canonical_url"])
    elif damage == "body": source["body"] = "Forged immutable original"
    elif damage == "compiled_claim": compiled["claims"][0]["text"] = "Invented claim under a valid claim ID"
    elif damage == "compiled_body": compiled["body"] = "Invented synthesis [S1]"
    elif damage == "compiled_title": compiled["title"] = "Invented title"
    elif damage == "compiled_terms": compiled["terms"] = ["Invented term"]
    elif damage == "compiled_limitations": compiled["limitations"].append("Invented limitation")
    elif damage.startswith("receipt_"):
        if damage == "receipt_deleted": receipt_path.unlink()
        else:
            if damage == "receipt_identity": receipt["source"]["raw_sha256"] = "0" * 64
            if damage == "receipt_private": receipt["source"]["canonical_url"] = "https://t.me/c/123/4"
            if damage == "receipt_pending": receipt["compile_state"] = "pending"
            receipt_path.write_text(json.dumps(receipt))
    elif damage == "expired":
        path = Path(source["manifest_ref"])
        manifest = json.loads(path.read_text())
        manifest["expires_at"] = NOW.isoformat()
        path.write_text(json.dumps(manifest))
    elif damage == "compiled_artifact": Path(receipt["compile_ref"]).write_text("changed compilation")
    else: Path(source["raw_ref" if damage == "raw_changed" else "text_ref"]).write_text("changed original")
    calls = []
    def llm(prompt):
        calls.append(prompt)
        return json.dumps({"targets": [], "refusal": "Unrelated"})
    with pytest.raises(ValueError):
        api().propose_topic_integration(**data, journal=journal, records=rows, now=NOW, llm_fn=llm)
    assert len(calls) == 0


def test_verified_current_receipt_and_extra_metadata_allow_one_synthetic_call(evidence):
    data, journal, rows = evidence
    receipt_path = journal._path("sources", data["source"]["revision_id"])
    stored = journal.load_compilation(journal.load_source(data["source"]["revision_id"]))
    stored["private"] = {"secret": "STORED_PRIVATE_SENTINEL"}
    stored["claims"][0]["private"] = {"secret": "STORED_CLAIM_PRIVATE_SENTINEL"}
    receipt_path.unlink()
    journal.save_compilation(data["source"], stored)
    data["source"]["private"] = {"secret": "CALLER_PRIVATE_SENTINEL"}
    data["compiled"]["private"] = {"secret": "CALLER_COMPILED_PRIVATE_SENTINEL"}
    calls = []
    def llm(prompt):
        calls.append(prompt)
        assert "PRIVATE_SENTINEL" not in prompt
        return json.dumps(proposal(data))
    result = api().propose_topic_integration(**data, journal=journal, records=rows, now=NOW, llm_fn=llm)
    assert len(calls) == 1 and result["targets"]
