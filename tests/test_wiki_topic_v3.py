"""Synthetic v3 evidence and append-only proposal contracts."""
from copy import deepcopy
import importlib
import importlib.util
import json
from pathlib import Path

import pytest

from agent_console import wiki
from reports.wiki_source_notes import materialize_source_note
from reports.wiki_topic_snapshot_context import select_snapshot_context
from test_wiki_source_notes import NOW, encoded, inventory, rewrite_receipt, store


def api():
    assert importlib.util.find_spec("reports.wiki_topic_v3") is not None, "Task3 APIs missing"
    return importlib.import_module("reports.wiki_topic_v3")


@pytest.fixture
def evidence(store, monkeypatch):
    sources = [store(url=f"https://t.me/public_channel/{i}", body=f"Capacity report {i}")
               for i in range(1, 4)]
    journal, source, compiled = sources[-1]
    pins = [materialize_source_note(s["revision_id"], journal=j, now=NOW, apply=True)
            for j, s, _ in sources[:-1]]
    context = select_snapshot_context(compiled, note_ids=[p["note_id"] for p in pins],
                                     journal=journal, now=NOW, surface="market")
    rows = []
    monkeypatch.setattr("agent_console.shared_memory.inspect_records", lambda: rows)
    return {"compiled": compiled, "source": source, "context": context, "targets": []}, journal, rows


def payload(data):
    revisions = [data["source"]["revision_id"], *sorted(n["revision_id"] for n in data["context"]["notes"])]
    claims = {data["compiled"]["revision_id"]: data["compiled"]["claims"],
              **{n["revision_id"]: n["claims"] for n in data["context"]["notes"]}}
    return {"targets": [{"page_id": None, "before_hash": None, "title": "Capacity outlook",
        "kind": "concept", "surface": "market", "purpose": "topic_synthesis",
        "contributing_revision_ids": revisions, "scope_explanation": "Attributed forecasts",
        "additions": [{"claim_ids": [claims[r][0]["claim_id"]], "revision_ids": [r],
            "as_of": claims[r][0]["as_of"], "scope": claims[r][0]["scope"],
            "body": f"The author reports an uncertain outlook [S{i}]."}
            for i, r in enumerate(revisions, 1)], "relations": []}], "refusal": None}


def validate(data, value):
    return api().validate_topic_integration_v3(value, **data)


def propose(evidence, fn=None):
    data, journal, rows = evidence
    return api().propose_topic_integration_v3(**data, journal=journal, records=rows, now=NOW,
        llm_fn=fn or (lambda prompt, **local: json.dumps(payload(data))))


def add_target(evidence):
    data, journal, rows = evidence
    old = deepcopy(data["context"]["notes"][0]["claims"][0])
    row = {"id": "topic", "kind": "concept", "title": "Capacity outlook", "body": "Earlier synthesis and exception",
        "tags": ["wiki", "kind:concept", "surface:market", "status:draft"],
        "source_knowledge": {"claims": [old], "limitations": ["Earlier uncertainty"], "relations": []}}
    rows.append(row)
    path = journal.root / "operations" / "synthetic-topic.json"
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(encoded({"schema_version": 1, "stage": "committed",
        "revision_id": data["source"]["revision_id"], "batch": {"target_ids": ["topic"],
        "upserts": [deepcopy(row)], "expected_after": {"topic": wiki._source_fingerprint("topic", rows)}}}))
    data["targets"] = [wiki.semantic_snapshot("topic", records=rows)]
    return old


def update_payload(data):
    value = payload(data)
    target = value["targets"][0]
    for key in ("title", "kind", "surface", "purpose"):
        target.pop(key)
    target.update(page_id="topic", before_hash=data["targets"][0]["revision_hash"])
    return value


def test_genuine_seed_contract_is_page_free_and_detached(evidence, tmp_path):
    data, _, _ = evidence
    before = inventory(tmp_path)
    result = propose(evidence)
    assert set(result) == {"protocol_version", "revision_id", "role", "observed_evidence_ids",
                           "context", "dependencies", "targets", "refusal", "source_map"}
    assert result["protocol_version"] == 3
    assert result["role"] == "support"
    assert result["observed_evidence_ids"] == data["source"]["observed_evidence_ids"]
    assert result["context"] == data["context"]
    assert result["dependencies"] == data["context"]["dependencies"]
    assert all("page_id" not in n for n in result["context"]["notes"])
    assert result["targets"][0]["page_id"].startswith("source-topic-")
    assert len(result["targets"][0]["original_claims"]) == 6
    assert "Forecast uncertainty" in result["targets"][0]["limitations"]
    assert "https://t.me/public_channel/3" in result["targets"][0]["additions"][0]["body"]
    assert inventory(tmp_path) == before
    result["context"]["notes"].clear()
    assert len(data["context"]["notes"]) == 2


def test_followup_retains_prior_synthesis_dates_and_qualifications(evidence):
    data, _, _ = evidence
    old = add_target(evidence)
    value = update_payload(data)
    first, second = value["targets"][0]["additions"][:2]
    relation = {"type": "qualify", "from_claim_id": first["claim_ids"][0],
        "to_claim_id": second["claim_ids"][0], "from_revision_id": first["revision_ids"][0],
        "to_revision_id": second["revision_ids"][0], "from_as_of": first["as_of"],
        "to_as_of": second["as_of"], "scope_explanation": "Forecasts are not realized demand"}
    value["targets"][0]["relations"] = [relation]
    captured = []
    def model(prompt, **local):
        captured.append(json.loads(prompt.rsplit("\n", 1)[1]))
        return json.dumps(value)
    result = propose(evidence, model)["targets"][0]
    assert captured[0]["editable_topics"][0]["body"] == "Earlier synthesis and exception"
    assert result["before_hash"] == data["targets"][0]["revision_hash"]
    assert old in result["original_claims"]
    assert "Earlier uncertainty" in result["limitations"]
    assert relation in result["relations"]
    assert all(c["as_of"] == "unknown" for c in result["original_claims"])
    assert all(c["competing_claim_ids"] for c in result["original_claims"])


def test_refusal_still_binds_v3_evidence(evidence):
    result = propose(evidence, lambda prompt, **local: '{"targets":[],"refusal":"Different event"}')
    assert result["targets"] == [] and result["refusal"] == "Different event"
    assert result["protocol_version"] == 3 and result["dependencies"]


@pytest.mark.parametrize("damage", ["contributor", "claim", "date", "scope", "citation", "url",
    "uncited_url", "role", "observed", "dependencies", "before", "surface", "relation"])
def test_forged_payloads_are_rejected(evidence, damage):
    data, _, _ = evidence
    value = payload(data)
    target, addition = value["targets"][0], value["targets"][0]["additions"][0]
    if damage == "contributor": target["contributing_revision_ids"].append("forged")
    elif damage == "claim": addition["claim_ids"] = ["forged"]
    elif damage == "date": addition["as_of"] = "2099-01-01"
    elif damage == "scope": addition["scope"] = "different"
    elif damage == "citation": addition["body"] = "Report [S99]"
    elif damage == "url": addition["body"] = "Report [S1](https://evil.example)"
    elif damage == "uncited_url": addition["body"] += " https://evil.example"
    elif damage in {"role", "observed", "dependencies"}: value[damage] = ["forged"]
    elif damage == "before": target["before_hash"] = "forged"
    elif damage == "surface": target["surface"] = "portfolio"
    else: target["relations"] = [{"type": "compete"}]
    with pytest.raises(ValueError): validate(data, value)


# B-fix-ground-01: real claim IDs/dates/scopes cannot carry ungrounded prose.
@pytest.mark.parametrize("body", [
    "Realized demand is guaranteed and all forecast uncertainty is resolved [S{i}].",
    "The author reports capacity will reach 500 units [S{i}].",
    "The author reports demand will triple [S{i}].",
])
def test_v3_addition_prose_cannot_exceed_cited_claims(evidence, body):
    data, _, _ = evidence
    value = payload(data)
    for index, addition in enumerate(value["targets"][0]["additions"], 1):
        addition["body"] = body.format(i=index)
    with pytest.raises(ValueError, match="topic_addition_unsupported"):
        validate(data, value)


def test_v3_addition_may_restate_cited_numbers_and_negated_certainty(evidence):
    data, _, _ = evidence
    value = payload(data)
    claims = {c["claim_id"]: c for c in [*data["compiled"]["claims"],
              *[c for n in data["context"]["notes"] for c in n["claims"]]]}
    for index, addition in enumerate(value["targets"][0]["additions"], 1):
        text = claims[addition["claim_ids"][0]]["text"]
        assert any(char.isdigit() for char in text)
        addition["body"] = f"{text}; demand is not guaranteed [S{index}]."
    result = validate(data, value)
    assert all("not guaranteed" in a["body"] for a in result["targets"][0]["additions"])


# B-fix2-02: prose without numbers or certainty words still cannot leave its cited claims.
def test_v3_addition_cannot_carry_unrelated_prose(evidence):
    data, _, _ = evidence
    value = payload(data)
    for index, addition in enumerate(value["targets"][0]["additions"], 1):
        addition["body"] = f"The regulator approved the acquisition and all factories closed [S{index}]."
    with pytest.raises(ValueError, match="topic_addition_unsupported"):
        validate(data, value)


@pytest.mark.parametrize("response", ['{"targets":[],"targets":[],"refusal":"x"}',
    '{"targets":[],"refusal":NaN}', '{"targets":[],"refusal":Infinity}',
    '{"targets":[],"refusal":1e999}', '[1]', 'not json'])
def test_strict_model_json(evidence, response):
    with pytest.raises(ValueError): propose(evidence, lambda prompt, **local: response)


@pytest.mark.parametrize("damage", ["role", "observed", "compiled", "source", "note", "pin", "expiry"])
def test_invalid_evidence_is_never_exposed(evidence, damage):
    data, journal, _ = evidence
    if damage == "role": data["source"]["role"] = "conflict"
    elif damage == "observed": data["source"]["observed_evidence_ids"] = ["forged"]
    elif damage == "compiled": data["compiled"]["claims"][0]["text"] = "private"
    elif damage == "source": data["source"]["body"] = "private"
    elif damage == "note": data["context"]["notes"][0]["claims"][0]["text"] = "private"
    elif damage == "pin": data["context"]["dependencies"][0]["compile_sha256"] = "0" * 64
    else:
        path = Path(data["source"]["manifest_ref"])
        manifest = json.loads(path.read_bytes()); manifest["expires_at"] = NOW.isoformat()
        path.write_bytes(encoded(manifest))
    with pytest.raises(ValueError):
        propose(evidence, lambda *args, **kwargs: pytest.fail("invalid evidence exposed"))


@pytest.mark.parametrize("damage", ["private", "drift", "forged_snapshot", "surface", "child"])
def test_target_authority_is_independent_of_valid_notes(evidence, damage):
    data, _, rows = evidence
    add_target(evidence)
    if damage == "private": rows[0]["body"] += " PRIVATE ADDITION"
    elif damage == "drift": rows[0]["title"] = "Changed"
    elif damage == "forged_snapshot": data["targets"][0]["body"] = "PRIVATE ADDITION"
    elif damage == "surface": rows[0]["tags"].append("surface:portfolio")
    else:
        rows.append({"id": "private-child", "parent_page_id": "topic", "body": "PRIVATE CHILD",
            "tags": ["wiki", "kind:concept", "surface:market", "status:draft"]})
    with pytest.raises(ValueError):
        propose(evidence, lambda *args, **kwargs: pytest.fail("invalid target exposed"))


@pytest.mark.parametrize("scope,proof", [("authorized_article", {"visibility": "public", "private": False,
    "requires_login": False}), ("article_fulltext", {"visibility": "public", "private": True,
    "requires_login": True}), ("article_fulltext", {})])
def test_current_source_remains_strict_public_with_private_flags(store, monkeypatch, scope, proof):
    monkeypatch.setenv("WIKI_SOURCE_PIPELINE_ENABLED", "1")
    monkeypatch.setenv("WIKI_SOURCE_ALLOW_PRIVATE", "1")
    journal, source, compiled = store(provider="saveticker", archive_provider="saveticker_article",
        url="https://saveticker.com/news/synthetic", scope=scope, kind="article", public_proof=proof)
    data = {"source": source, "compiled": compiled,
            "context": {"notes": [], "dependencies": [], "omitted": []}, "targets": []}
    with pytest.raises(ValueError):
        propose((data, journal, []), lambda *args, **kwargs: pytest.fail("private current exposed"))


def test_observed_role_binding_checks_receipt_not_just_caller(evidence):
    data, journal, _ = evidence
    rewrite_receipt((journal, data["source"], data["compiled"]),
        lambda value: value["evidence_by_role"].update(support=[]))
    with pytest.raises(ValueError): propose(evidence)


def test_current_revision_cannot_be_a_support_note(evidence):
    data, journal, _ = evidence
    current = materialize_source_note(data["source"]["revision_id"], journal=journal, now=NOW, apply=True)
    from reports.wiki_topic_snapshot_context import _candidate
    from reports.wiki_source_notes import pin_source_note
    note, dep = _candidate(pin_source_note(current["note_id"], journal=journal, now=NOW), "market")
    data["context"]["notes"].append(note)
    data["context"]["dependencies"].append(dep)
    with pytest.raises(ValueError):
        propose(evidence, lambda *args, **kwargs: pytest.fail("current duplicated in support"))


def test_followup_cannot_remove_prior_relations_or_counterclaims(evidence):
    data, _, rows = evidence
    old = add_target(evidence)
    peer = deepcopy(data["context"]["notes"][0]["claims"][1])
    relation = {"type": "compete", "from_claim_id": old["claim_id"], "to_claim_id": peer["claim_id"],
        "from_revision_id": old["source_revision_id"], "to_revision_id": peer["source_revision_id"],
        "from_as_of": old["as_of"], "to_as_of": peer["as_of"], "scope_explanation": "Earlier conflict"}
    rows[0]["source_knowledge"]["claims"].append(peer)
    rows[0]["source_knowledge"]["relations"] = [relation]
    data["targets"] = [wiki.semantic_snapshot("topic", records=rows)]
    result = validate(data, update_payload(data))["targets"][0]
    assert relation in result["relations"]
    assert old in result["original_claims"] and peer in result["original_claims"]
