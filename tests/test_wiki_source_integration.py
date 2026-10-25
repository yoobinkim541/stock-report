import copy
import importlib
import json

import pytest

from agent_console import shared_memory, wiki


REV = "a" * 64
OLD = "b" * 64


def record(page_id, **fields):
    return {"id": page_id, "title": "Capacity " + page_id, "body": "Old exception. Opposing evidence.",
            "tags": ["wiki", "surface:market", "kind:concept", "status:draft"],
            "kind": "concept", "artifacts": ["https://example.org/old"], **fields}


def compiled():
    return {"revision_id": REV, "body": "Capacity rose, not proven demand.", "terms": ["capacity"],
            "claims": [{"claim_id": "new-claim", "source_revision_id": REV, "text": "Capacity rose.",
                        "as_of": "2026-10-09", "scope": "capacity", "knowledge_state": "draft"}],
            "limitations": ["Demand uncertain"], "skip_reason": None}


def source():
    return {"revision_id": REV, "canonical_url": "https://example.org/new", "role": "support",
            "observed_evidence_ids": ["actual-id"], "published_at": "2026-10-09"}


def test_model_cannot_silently_omit_selected_targets(monkeypatch):
    install(monkeypatch, [record("entity"), record("topic")])
    targets = [module().hydrate_target(page_id) for page_id in ("entity", "topic")]
    with pytest.raises(ValueError, match="proposal_targets_incomplete"):
        module().propose_integration(compiled(), source(), targets, new_page_specs=[],
                                    llm_fn=lambda _: json.dumps({"targets": []}))


def test_proposal_prompt_excludes_private_metadata_but_retains_complete_knowledge(monkeypatch):
    install(monkeypatch, [record("entity", messages=[{"role": "user", "text": "PRIVATE_MESSAGE"}],
        lastQuery="PRIVATE_QUERY", contextPacket={"secret": "PRIVATE_PACKET"})])
    snap = module().hydrate_target("entity")
    def llm(prompt):
        assert "Old exception. Opposing evidence." in prompt
        assert all(marker not in prompt for marker in ("PRIVATE_MESSAGE", "PRIVATE_QUERY", "PRIVATE_PACKET"))
        payload = {"targets": [{"page_id": "entity", "before_hash": snap["revision_hash"], "additions": [{
            "claim_ids": ["new-claim"], "as_of": "2026-10-09", "scope": "capacity", "body": "Capacity rose [S1]."}],
            "relations": []}]}
        return json.dumps(payload)
    module().propose_integration(compiled(), source(), [snap], new_page_specs=[], llm_fn=llm)


def test_proposal_retains_prior_limitations_and_relations_from_parent_and_children(monkeypatch):
    rows = [record("entity", source_knowledge={
        "limitations": ["Prior uncertainty: unaudited capacity"],
        "relations": [{"type": "compete", "scope_explanation": "Historical conflict"}],
        "private_notes": "PRIVATE_EXTRA_FIELD"}),
        record("child", parent_page_id="entity", source_knowledge={
            "limitations": ["Child caveat: seasonality"],
            "relations": [{"type": "qualify", "scope_explanation": "Limited seasonal scope"}]})]
    install(monkeypatch, rows)
    snap = module().hydrate_target("entity")
    def llm(prompt):
        data = json.loads(prompt.rsplit("\n", 1)[1])
        target = data['targets'][0]
        assert target['page']['source_knowledge']['limitations'] == ["Prior uncertainty: unaudited capacity"]
        assert target['page']['source_knowledge']['relations'][0]['scope_explanation'] == "Historical conflict"
        assert target['children'][0]['source_knowledge']['limitations'] == ["Child caveat: seasonality"]
        assert "PRIVATE_EXTRA_FIELD" not in prompt
        return json.dumps({"targets": [{"page_id": "entity", "before_hash": snap["revision_hash"],
            "additions": [{"claim_ids": ["new-claim"], "as_of": "2026-10-09", "scope": "capacity",
                           "body": "Capacity rose [S1]."}], "relations": []}]})
    module().propose_integration(compiled(), source(), [snap], new_page_specs=[], llm_fn=llm)


def test_generated_child_ordinal_uses_terminal_brackets_not_parent_date():
    rows = [record("parent", title="Capacity [2026]"),
        record("z-first", title="Capacity [2026] [0001]", body="FIRST", parent_page_id="parent"),
        record("a-second", title="Capacity [2026] [0002]", body="SECOND", parent_page_id="parent")]
    assert wiki.semantic_snapshot("parent", records=rows)["body"] == "FIRSTSECOND"


def module():
    return importlib.import_module("reports.wiki_source_integration")


def install(monkeypatch, rows):
    monkeypatch.setattr(shared_memory, "inspect_records", lambda: copy.deepcopy(rows))
    monkeypatch.setattr(wiki, "list_pages", lambda **kwargs: [wiki._record_to_page(row) for row in rows])


def test_source_routes_to_two_pages_and_justified_new_page(monkeypatch):
    install(monkeypatch, [record("entity"), record("topic")])
    state = module().discover_targets(compiled(), surface="market")
    new = {"title": "Demand versus capacity", "kind": "concept", "surface": "market",
           "purpose": "Keep the measurement distinction separate"}
    result = module().route_pass(compiled(), state, llm_fn=lambda _: json.dumps({
        "selected": ["entity", "topic"], "rejected": {}, "new_page_specs": [new]}))
    assert set(result["selected"]) == {"entity", "topic"}
    assert result["new_page_specs"][0]["purpose"] == new["purpose"]
    assert result["unexamined"] == []
    assert state["cursor"] == 0


def test_router_prompt_explicitly_supplies_allowed_new_page_kinds(monkeypatch):
    install(monkeypatch, [])
    state = module().discover_targets(compiled(), surface='market')
    def llm(prompt):
        data = json.loads(prompt.rsplit('\n', 1)[1])
        assert set(data['allowed_new_page_kinds']) == module().ELIGIBLE_KINDS
        assert 'must use one of allowed_new_page_kinds' in prompt
        return json.dumps({'selected': [], 'rejected': {}, 'new_page_specs': []})
    module().route_pass(compiled(), state, llm_fn=llm)


def test_hydration_includes_ordered_children_and_exceptions(monkeypatch):
    rows = [record("parent", body="Overview", links=["second", "first"]),
            record("second", title="Capacity [0002]", body="\nSecond exception", parent_page_id="parent"),
            record("first", title="Capacity [0001]", body="First opposing evidence", parent_page_id="parent")]
    install(monkeypatch, rows)
    snapshot = module().hydrate_target("parent")
    assert snapshot["body"] == "First opposing evidence\nSecond exception"
    assert snapshot["page"]["body"] == "Overview"
    assert [child["id"] for child in snapshot["children"]] == ["first", "second"]


def test_semantic_snapshot_pure_and_ignores_usage_but_guards_child_and_role(monkeypatch):
    rows = [record("parent"), record("child", title="[0001]", parent_page_id="parent")]
    monkeypatch.setattr(shared_memory, "inspect_records", lambda: pytest.fail("unexpected store read"))
    initial = wiki.semantic_snapshot("parent", records=rows)["revision_hash"]
    updated = copy.deepcopy(rows)
    updated[0].update(useCount=99, lastUsedAt="now", updatedAt="now", backlinks=["derived"])
    assert wiki.semantic_snapshot("parent", records=updated)["revision_hash"] == initial
    updated[1]["body"] += " new exception"
    assert wiki.semantic_snapshot("parent", records=updated)["revision_hash"] != initial
    updated = copy.deepcopy(rows)
    updated[0]["conflicting_evidence_ids"] = ["old-conflict"]
    assert wiki.semantic_snapshot("parent", records=updated)["revision_hash"] != initial


def test_routing_cursor_retains_unexamined_candidates(monkeypatch):
    install(monkeypatch, [record("a"), record("b"), record("c")])
    state = module().discover_targets(compiled(), surface="market")
    first = state["candidate_ids"][0]
    result = module().route_pass(compiled(), state, limit=1, llm_fn=lambda _: json.dumps({
        "selected": [], "rejected": {first: "Different scope"}, "new_page_specs": []}))
    assert result["unexamined"] == state["candidate_ids"][1:]
    assert result["cursor"] == 1 and result["examined"] == [first]


def test_archived_cross_surface_digest_and_generic_tags_are_not_candidates(monkeypatch):
    install(monkeypatch, [record("good"), record("archived", tags=["wiki", "surface:market", "kind:concept", "status:archived"]),
        record("other", tags=["wiki", "surface:portfolio", "kind:concept"]),
        record("digest", kind="source_digest", tags=["wiki", "surface:market", "kind:source_digest"]),
        record("noise", title="Unrelated", tags=["wiki", "surface:market", "kind:concept", "속보"])])
    item = {**compiled(), "terms": ["capacity", "속보"]}
    assert module().discover_targets(item, surface="market")["candidate_ids"] == ["good"]


@pytest.mark.parametrize("payload", [
    {"selected": ["forged"], "rejected": {}, "new_page_specs": []},
    {"selected": ["a"], "rejected": {}, "new_page_specs": [], "status": "stable"},
    {"selected": [], "rejected": {}, "new_page_specs": []},
    {"selected": ["a"], "rejected": {}, "new_page_specs": [{"title": "New", "kind": "concept", "surface": "market", "purpose": ""}]},
    {"selected": ["a"], "rejected": {}, "new_page_specs": [{"title": "New", "kind": "concept", "surface": "portfolio", "purpose": "Elsewhere"}]},
])
def test_forged_or_incomplete_routing_is_rejected_without_cursor_change(monkeypatch, payload):
    install(monkeypatch, [record("a")])
    state = module().discover_targets(compiled(), surface="market")
    with pytest.raises(ValueError):
        module().route_pass(compiled(), state, llm_fn=lambda _: json.dumps(payload))
    assert state["cursor"] == 0


def proposal(snapshot):
    return {"targets": [{"page_id": snapshot["page_id"], "before_hash": snapshot["revision_hash"],
        "additions": [{"claim_ids": ["new-claim"], "as_of": "2026-10-09", "scope": "capacity",
                       "body": "Capacity rose, but this is not demand [S1]."}], "relations": [{
            "type": "compete", "from_claim_id": "new-claim", "to_claim_id": "old-claim",
            "from_revision_id": REV, "to_revision_id": OLD, "from_as_of": "2026-10-09",
            "to_as_of": "2026-09-01", "scope_explanation": "Capacity and realized orders differ"}]}]}


def test_proposal_reads_old_exceptions_and_counterclaims_without_replacing_history(monkeypatch):
    old_claim = {"claim_id": "old-claim", "source_revision_id": OLD, "as_of": "2026-09-01", "text": "Orders fell"}
    snap = wiki.semantic_snapshot("a", records=[record("a", source_knowledge={"claims": [old_claim]})])
    def model(prompt):
        assert "Old exception" in prompt and "Opposing evidence" in prompt and "Orders fell" in prompt
        assert "Demand uncertain" in prompt
        return json.dumps(proposal(snap))
    result = module().propose_integration(compiled(), source(), [snap], new_page_specs=[], llm_fn=model)
    assert result["targets"][0]["relations"][0]["type"] == "compete"
    assert "[S1](https://example.org/new)" in result["targets"][0]["additions"][0]["body"]
    assert result["role"] == "support"
    assert snap["body"] == "Old exception. Opposing evidence."


@pytest.mark.parametrize("mutation", ["target", "revision", "date", "status", "delete", "claim"])
def test_forged_integration_operations_fail_closed(mutation):
    snap = wiki.semantic_snapshot("a", records=[record("a", source_knowledge={"claims": [{
        "claim_id": "old-claim", "source_revision_id": OLD, "as_of": "2026-09-01"}]})])
    plan = proposal(snap)
    target = plan["targets"][0]
    if mutation == "target": target["page_id"] = "other"
    if mutation == "revision": target["relations"][0]["to_revision_id"] = REV
    if mutation == "date": target["relations"][0]["from_as_of"] = "2027-01-01"
    if mutation == "status": target["status"] = "stable"
    if mutation == "delete": target["delete"] = True
    if mutation == "claim": target["additions"][0]["claim_ids"] = ["unknown"]
    with pytest.raises(ValueError):
        module().propose_integration(compiled(), source(), [snap], new_page_specs=[], llm_fn=lambda _: json.dumps(plan))


# B-fix-ground-01: v1 model-proposed addition prose stays within its cited claims.
def _grounding_snapshot():
    return wiki.semantic_snapshot("a", records=[record("a", source_knowledge={"claims": [{
        "claim_id": "old-claim", "source_revision_id": OLD, "as_of": "2026-09-01"}]})])


@pytest.mark.parametrize("field,value", [
    ("body", "Capacity rose 40% this quarter [S1]."),
    ("body", "Capacity rose, so demand is guaranteed [S1]."),
    ("body", "Capacity will double [S1]."),
    ("scope", "capacity through 2030"),
])
def test_v1_addition_cannot_assert_numbers_or_certainty_absent_from_claims(field, value):
    snap = _grounding_snapshot()
    plan = proposal(snap)
    plan["targets"][0]["additions"][0][field] = value
    with pytest.raises(ValueError, match="proposal_addition_unsupported"):
        module().propose_integration(compiled(), source(), [snap], new_page_specs=[], llm_fn=lambda _: json.dumps(plan))


def test_v1_addition_may_restate_claim_date_and_negated_certainty():
    snap = _grounding_snapshot()
    plan = proposal(snap)
    plan["targets"][0]["additions"][0]["body"] = "As of 2026-10-09 capacity rose; demand is not guaranteed [S1]."
    result = module().propose_integration(compiled(), source(), [snap], new_page_specs=[],
                                          llm_fn=lambda _: json.dumps(plan))
    assert "not guaranteed" in result["targets"][0]["additions"][0]["body"]


@pytest.mark.parametrize("term,title", [
    ("엔비디아", "NVIDIA earnings"), ("NVDA", "엔비디아 실적"),
    ("연준", "Federal Reserve policy"), ("소비자물가지수", "CPI inflation"),
    ("미국채", "US Treasury yields"), ("반도체", "Semiconductor cycle"),
])
def test_financial_aliases_route_across_languages(monkeypatch, term, title):
    install(monkeypatch, [record("topic", title=title)])
    item = {**compiled(), "terms": [term]}
    assert module().discover_targets(item, surface="market")["candidate_ids"] == ["topic"]


def test_alias_matching_does_not_match_substrings_or_conflate_leverage(monkeypatch):
    install(monkeypatch, [record("noise", title="Confederation"),
                          record("leveraged", title="NVDL")])
    assert module().discover_targets({**compiled(), "terms": ["fed", "NVDA"]},
                                     surface="market")["candidate_ids"] == []


def test_topic_identity_survives_revision_and_title_case_changes():
    spec = {"title": "NVIDIA capacity", "kind": "concept",
            "surface": "market", "purpose": "topic_synthesis"}
    first = module()._new_spec(spec, REV, "market")
    later = module()._new_spec({**spec, "title": "  nvidia   capacity "}, OLD, "market")
    assert first["page_id"] == later["page_id"]
    other = module()._new_spec({**spec, "title": "NVIDIA demand"}, REV, "market")
    assert other["page_id"] != first["page_id"]
