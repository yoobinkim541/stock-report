import json

import pytest

from agent_console import agent, wiki

QUESTION = "재사용 규칙과 검증 기준을 위키에 정리해줘"
ANSWER = (
    "재사용 규칙: 원문 근거와 검증 결과를 함께 확인하고 예외를 보존합니다.\n"
    "- 실제 주문 여부를 확인합니다.\n- 불확실성과 반대 근거를 기록합니다."
)


@pytest.fixture(autouse=True)
def isolated_wiki(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_CONSOLE_SHARED_MEMORY_DIR", str(tmp_path / "memory"))
    monkeypatch.setenv("AGENT_MEMORY_DIR", str(tmp_path / "memory"))
    monkeypatch.setenv("AGENT_CONSOLE_DB", str(tmp_path / "agent.sqlite3"))
    monkeypatch.setenv("AGENT_CONSOLE_QMD_DOCS_DIR", str(tmp_path / "markdown"))
    monkeypatch.setenv("AGENT_CONSOLE_QMD_ENABLED", "0")
    monkeypatch.setattr(wiki, "_debounced_rebuild", lambda: None)
    wiki._CACHE.clear()
    yield
    wiki._CACHE.clear()


def _answer_pack(pack, raw_answer):
    provided = agent._provided_context_evidence_ids(pack, pack.get("_wiki_context_pages"))
    answer = agent._normalize_answer_payload(raw_answer, provided)
    packed = dict(pack)
    packed["_validated_cited_evidence_ids"] = answer["cited_evidence_ids"]
    return packed


def _curate(question, answer, pack, plan):
    return wiki.auto_curate_from_chat(
        question,
        answer,
        surface="market",
        pack=pack,
        history=[],
        llm=lambda _prompt: json.dumps(plan),
    )


def _plan(**overrides):
    result = {
        "action": "create",
        "title": "Reusable evidence rule",
        "summary": "Verify realized orders before accepting capacity claims.",
        "body": "Reusable rule: verify realized orders before accepting capacity claims.\n"
                "- Check order conversion\n- Preserve uncertainty and exceptions.",
        "kind": "playbook",
        "status": "draft",
        "tags": ["reusable"],
        "source_refs": [],
        "links": [],
        "target_id": "",
    }
    result.update(overrides)
    return result


def _event_pack(event_id="evidence-1", url="https://source.example/report"):
    return {"sources": {"events": [{"event_id": event_id, "url": url, "title": "Source evidence headline"}]},
            "_wiki_context_pages": []}


def test_long_model_plan_reaches_chat_child_storage_without_fallback():
    body = "## Reusable knowledge\n\n" + "Dated rules and exceptions remain available.\n\n" * 350
    body += "[PRESERVED_FINAL_EXCEPTION]"
    assert len(body) > wiki.WIKI_BODY_LIMIT
    result = _curate(QUESTION, ANSWER, {}, _plan(title="Long supported-rule draft", body=body))
    assert result and result["ok"] and result["source"] == "llm"
    parent = result["page"]
    children = wiki.split_children_for_page(parent)
    children.sort(key=lambda child: int(child["title"].rsplit("[", 1)[1][:4]))
    assert len(children) > 1
    expected, _ = wiki._with_report_citation(body, fallback=_plan()["summary"])
    assert "".join(child["body"] for child in children) == expected
    assert all(child["status"] == "draft" for child in children)
    assert wiki.WIKI_CONTEXT_BODY_SNIPPET == 1600


def test_oversized_model_plan_is_rejected_not_parsed_from_a_truncated_prefix():
    response = json.dumps(_plan()) + "X" * 100_001
    assert wiki._parse_curation_plan(response) is None


def test_invented_refs_and_unsupported_citations_are_not_filed_as_provenance():
    pack = _event_pack()
    raw = json.dumps({"answer": "Reusable rule: verify evidence before filing.",
                      "cited_evidence_ids": ["not-provided"]})
    packed = _answer_pack(pack, raw)

    result = _curate(
        QUESTION,
        ANSWER,
        packed,
        _plan(source_refs=["https://invented.example/fake", "source:arbitrary-id"]),
    )

    assert result and result["ok"]
    assert result["page"]["source_refs"] == [f"conversation:{wiki._page_id(QUESTION, 'market', 'playbook')}"]
    assert result["page"]["verification_status"] == "unverified"
    assert result["page"]["status"] == "draft"


def test_legacy_reply_and_status_tags_cannot_promote_chat_knowledge():
    packed = _answer_pack(_event_pack(), "Reusable rule: verify evidence before filing.\n- Check\n- Recheck.")

    result = _curate(
        QUESTION,
        ANSWER,
        packed,
        _plan(status="stable", tags=["reviewed", "status:stable", "reusable"],
              source_refs=["https://invented.example/legacy-reply"]),
    )

    assert result and result["ok"]
    assert result["page"]["status"] == "draft"
    assert not {"reviewed", "stable", "status:reviewed", "status:stable"}.intersection(result["page"]["tags"])
    assert result["page"]["verification_status"] == "unverified"
    assert "https://invented.example/legacy-reply" not in result["page"]["source_refs"]


def test_only_validly_cited_exact_source_ref_survives_without_truncation():
    long_ref = "https://source.example/report?" + "section=" + ("long-reference-" * 24)
    assert len(long_ref) > 260
    packed = _answer_pack(_event_pack(url=long_ref), json.dumps({
        "answer": "Reusable rule: verify cited evidence before filing.",
        "cited_evidence_ids": ["evidence-1"],
    }))

    result = _curate(
        QUESTION,
        ANSWER,
        packed,
        _plan(status="reviewed", source_refs=[long_ref, "https://invented.example/other"]),
    )

    assert result and result["ok"]
    assert long_ref in result["page"]["source_refs"]
    assert "https://invented.example/other" not in result["page"]["source_refs"]
    assert len(next(ref for ref in result["page"]["source_refs"] if ref.startswith("https://"))) == len(long_ref)
    assert result["page"]["status"] == "draft"


def test_cited_wiki_context_page_contributes_its_exact_source_refs():
    source_ref = "https://source.example/wiki-evidence"
    page = {"id": "wiki-source-page", "evidence_ids": ["wiki-evidence-1"],
            "source_refs": [source_ref], "title": "Supported context"}
    packed = _answer_pack({"sources": {"events": []}, "_wiki_context_pages": [page]}, json.dumps({
        "answer": "Reusable rule: rely on the cited page's original source.",
        "cited_evidence_ids": ["wiki-evidence-1"],
    }))

    result = _curate(QUESTION, ANSWER, packed, _plan(source_refs=[source_ref]))

    assert result and result["ok"]
    assert source_ref in result["page"]["source_refs"]


def test_unexposed_wiki_references_are_not_filed_as_cited_sources():
    refs = [f'https://source.example/report-{i}' for i in range(8)]
    page = {'id': 'wiki-page', 'title': 'Evidence page', 'source_refs': refs}
    pack = {'_wiki_context_pages': [page], '_validated_cited_evidence_ids': ['wiki-page']}
    assert wiki._cited_source_refs(pack) == refs[:4]


def test_artifact_only_refs_not_shown_in_context_cannot_be_cited():
    page = {'id': 'wiki-page', 'title': 'Evidence page',
            'artifacts': ['https://source.example/unexposed-artifact']}
    pack = {'_wiki_context_pages': [page], '_validated_cited_evidence_ids': ['wiki-page']}
    assert wiki._cited_source_refs(pack) == []


def test_event_citation_does_not_attach_unexposed_alternate_refs():
    event = {'event_id': 'event-visible', 'title': 'Displayed event',
             'url': 'https://source.example/displayed',
             'source_url': 'https://source.example/alternate',
             'source_refs': ['https://source.example/unexposed'],
             'artifacts': ['https://source.example/unexposed-artifact']}
    pack = {'sources': {'events': [event]}, '_validated_cited_evidence_ids': ['event-visible']}
    assert wiki._cited_source_refs(pack) == [event['url']]


def test_event_outside_citation_context_cannot_supply_fileback_refs():
    events = [{'event_id': f'e-{i}', 'title': 'Displayed event',
               'url': f'https://source.example/{i}'} for i in range(13)]
    pack = {'sources': {'events': events}, '_validated_cited_evidence_ids': ['e-12']}
    assert wiki._cited_source_refs(pack) == []


def test_unexposed_event_is_not_an_allowed_answer_citation():
    events = [{'event_id': f'e-{i}', 'title': 'Displayed event',
               'url': f'https://source.example/{i}'} for i in range(13)]
    allowed = agent._provided_context_evidence_ids({'sources': {'events': events}})
    assert 'e-11' in allowed and 'e-12' not in allowed
    assert agent._provided_context_evidence_ids({'sources': {'events': [
        {'event_id': 'not-shown', 'url': 'https://source.example/unshown'}]}}) == set()


def test_generated_update_downgrades_reviewed_target_and_keeps_old_evidence():
    old_ref = "https://source.example/old-report"
    target = wiki.upsert_page({
        "id": "reviewed-rule",
        "title": "Reusable evidence rule",
        "summary": "Old supported rule.",
        "body": "Old reviewed body.",
        "surface": "market",
        "kind": "playbook",
        "status": "reviewed",
        "tags": ["reusable"],
        "source_refs": [old_ref],
    })
    packed = _answer_pack(_event_pack("evidence-new", "https://source.example/new-report"), json.dumps({
        "answer": "Reusable rule: verify cited evidence before filing.",
        "cited_evidence_ids": ["evidence-new"],
    }))

    result = _curate(
        "재사용 규칙과 검증 기준을 위키에서 업데이트해줘",
        ANSWER,
        packed,
        _plan(action="update", target_id=target["id"], status="reviewed", tags=["stable"],
              source_refs=["https://source.example/new-report"]),
    )

    assert result and result["ok"]
    updated = wiki.get_page(target["id"])
    assert updated["status"] == "draft"
    assert old_ref in updated["source_refs"]
    assert "https://source.example/new-report" in updated["source_refs"]
    assert "stable" not in updated["tags"]


def test_manual_source_backed_reviewed_page_remains_reviewed():
    page = wiki.upsert_page({
        "id": "manual-reviewed-page",
        "title": "Manually reviewed source-backed page",
        "summary": "Reviewed through the manual page API.",
        "body": "Human-reviewed body.",
        "surface": "market",
        "kind": "concept",
        "status": "reviewed",
        "source_refs": ["https://source.example/manual-review"],
    })

    assert wiki.get_page(page["id"])["status"] == "reviewed"


def test_shared_payload_builder_default_preserves_non_chat_status_and_refs():
    source_ref = "https://source.example/distillation-input"
    payload = wiki._plan_to_page_payload(
        _plan(status="reviewed", tags=["stable"], source_refs=[source_ref]),
        question=QUESTION,
        answer=ANSWER,
        surface="market",
    )

    assert payload["status"] == "reviewed"
    assert source_ref in payload["source_refs"]
    assert "stable" in payload["tags"]


def test_agent_answer_hands_validated_citations_to_real_postprocess(monkeypatch):
    url = "https://source.example/answer-context"
    context_pack = _event_pack(url=url)
    context_pack.update({"surface": "market", "memory": [], "shared_memory": {"recordCount": 0},
                         "market_snapshot": {"quotes": [], "status": "unavailable"},
                         "paper": {}, "portfolio": {"holdings": []}, "ml_activity": [], "focus": []})
    monkeypatch.setattr(agent, "_safe_context_pack", lambda _surface: context_pack)
    monkeypatch.setattr(agent, "_compose_answer", lambda *_args, **_kwargs: json.dumps({
        "answer": "Reusable rule: verify cited orders before accepting capacity claims.\n"
                 "- Check conversion\n- Preserve uncertainty.",
        "cited_evidence_ids": ["evidence-1"],
    }))
    monkeypatch.setattr(agent.laya_bridge, "review_wiki_candidates",
                        lambda _q, pages, **_kwargs: (pages[:4], {"mode": "baseline"}))
    monkeypatch.setattr(agent, "_try_llm_prompt", lambda _prompt, **_kwargs: json.dumps(_plan(
        status="reviewed", source_refs=[url, "https://invented.example/not-cited"])))

    response = agent.answer(QUESTION, "market")

    assert response["ok"]
    saved = wiki.list_pages(query="Reusable evidence rule", surface="market", limit=4)
    assert len(saved) == 1
    assert url in saved[0]["source_refs"]
    assert "https://invented.example/not-cited" not in saved[0]["source_refs"]
    assert saved[0]["status"] == "draft"


def test_update_hydrates_the_exact_active_candidate_selected_by_the_model():
    best_match = wiki.upsert_page({
        "id": "best-match-page",
        "title": "재사용 규칙 검증 기준과 원문 정책",
        "summary": "Candidate A should rank first for this query.",
        "body": "Candidate A body.",
        "surface": "market",
        "kind": "playbook",
        "status": "draft",
        "source_refs": ["https://source.example/candidate-a"],
        "messages": [{"role": "assistant", "text": "Candidate A history marker."}],
    })
    selected = wiki.upsert_page({
        "id": "selected-page-b",
        "title": "NVDA volume observation",
        "summary": "Candidate B, selected by the model.",
        "body": "Candidate B body.",
        "surface": "market",
        "kind": "playbook",
        "status": "draft",
        "source_refs": ["https://source.example/candidate-b"],
        "messages": [{"role": "assistant", "text": "Candidate B history marker."}],
    })

    result = _curate(
        QUESTION,
        ANSWER,
        _answer_pack(_event_pack(), "Legacy uncited response."),
        _plan(action="update", target_id=selected["id"]),
    )

    assert result and result["ok"]
    updated = wiki.get_page(selected["id"])
    assert updated["id"] == selected["id"]
    assert "https://source.example/candidate-b" in updated["source_refs"]
    assert "https://source.example/candidate-a" not in updated["source_refs"]
    assert any("Candidate B history marker." in row["text"] for row in updated["messages"])
    assert not any("Candidate A history marker." in row["text"] for row in updated["messages"])
    unchanged_best_match = wiki.get_page(best_match["id"])
    assert unchanged_best_match["summary"] == "Candidate A should rank first for this query."
    assert "Candidate A body." in unchanged_best_match["body"]


def test_update_rejects_an_unknown_target_instead_of_creating_it():
    result = _curate(
        QUESTION,
        ANSWER,
        _answer_pack(_event_pack(), "Legacy uncited response."),
        _plan(action="update", target_id="unknown-target-id"),
    )

    assert result is None or not result.get("ok")
    assert wiki.get_page("unknown-target-id") is None


def test_update_rejects_an_archived_candidate():
    archived = wiki.upsert_page({
        "id": "archived-update-target",
        "title": "재사용 규칙 검증 기준과 원문 정책",
        "summary": "Archived candidate is not updateable.",
        "body": "Archived body.",
        "surface": "market",
        "kind": "playbook",
        "status": "archived",
        "source_refs": ["https://source.example/archived"],
    })

    result = _curate(
        QUESTION,
        ANSWER,
        _answer_pack(_event_pack(), "Legacy uncited response."),
        _plan(action="update", target_id=archived["id"]),
    )

    assert result and not result.get("ok")
    assert wiki.get_page(archived["id"])["status"] == "archived"


def test_create_rejects_a_target_id_that_would_overwrite_an_existing_page():
    existing = wiki.upsert_page({
        "id": "create-collision-target",
        "title": "재사용 규칙 검증 기준과 원문 정책",
        "summary": "Do not overwrite on a create action.",
        "body": "Preserve this existing body.",
        "surface": "market",
        "kind": "playbook",
        "status": "draft",
        "source_refs": ["https://source.example/collision"],
    })

    result = _curate(
        QUESTION,
        ANSWER,
        _answer_pack(_event_pack(), "Legacy uncited response."),
        _plan(action="create", target_id=existing["id"]),
    )

    assert result is None or not result.get("ok")
    assert wiki.get_page(existing["id"])["body"] == "Preserve this existing body."


def test_create_rejects_a_deterministic_page_id_collision_without_target_id():
    title = _plan()["title"]
    existing = wiki.upsert_page({
        "title": title,
        "summary": "Existing page with the deterministic ID.",
        "body": "Keep this body.",
        "surface": "market",
        "kind": "playbook",
        "status": "draft",
        "source_refs": ["https://source.example/deterministic-collision"],
    })

    result = _curate(
        QUESTION,
        ANSWER,
        _answer_pack(_event_pack(), "Legacy uncited response."),
        _plan(action="create", target_id=""),
    )

    assert result and not result.get("ok")
    assert wiki.get_page(existing["id"])["body"].startswith("Keep this body.")
