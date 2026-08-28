from __future__ import annotations

import json


def test_prediction_prompt_preserves_provider_time_and_url_and_filters_blocked(monkeypatch):
    from agent_console import agent, context

    availability = {
        "polymarket": {"availability": "blocked", "last_error": "HTTP 451"},
        "kalshi": {"availability": "available"},
    }
    monkeypatch.setattr(context, "_source_availability", lambda source: availability[source])
    events = [
        {
            "id": "poly-event-1",
            "source": "polymarket",
            "title": "Blocked market",
            "url": "https://polymarket.com/event/blocked",
            "observed_at": "2026-08-27T08:00:00+00:00",
            "metrics": {"yes_probability": 0.61, "volume": 10},
        },
        {
            "id": "kalshi-event-1",
            "source": "kalshi",
            "title": "Fresh market",
            "url": "https://kalshi.com/markets/fresh",
            "observed_at": "2026-08-27T08:05:00+00:00",
            "metrics": {"yes_probability": 0.52, "volume": 20},
        },
    ]

    state = context.prediction_market_state(events)
    lines = agent._compact_prediction_market_context({"prediction_markets": state})

    assert state["providers"]["polymarket"]["count"] == 0
    assert state["items"][0]["event_id"] == "kalshi-event-1"
    assert "[Kalshi]" in lines[0]
    assert "2026-08-27T08:05:00+00:00" in lines[0]
    assert "https://kalshi.com/markets/fresh" in lines[0]
    assert "Blocked market" not in "\n".join(lines)


def test_llm_answer_citations_are_restricted_to_provided_context():
    from agent_console import agent

    payload = agent._normalize_answer_payload(
        json.dumps({
            "answer": "근거를 반영한 답변",
            "cited_evidence_ids": ["evidence-1", "not-provided"],
        }),
        {"evidence-1", "event-1"},
    )

    assert payload["answer"] == "근거를 반영한 답변"
    assert payload["cited_evidence_ids"] == ["evidence-1"]
    assert payload["invalid_cited_evidence_ids"] == ["not-provided"]


def test_string_only_llm_output_remains_backward_compatible():
    from agent_console import agent

    payload = agent._normalize_answer_payload("기존 문자열 응답", {"evidence-1"})

    assert payload["answer"] == "기존 문자열 응답"
    assert payload["cited_evidence_ids"] == []
    assert payload["structured"] is False


def test_answer_returns_citations_and_records_answer_use(monkeypatch, tmp_path):
    from agent_console import agent, evidence_usage

    monkeypatch.setenv("AGENT_CONSOLE_EVIDENCE_USAGE_PATH", str(tmp_path / "usage.jsonl"))
    pack = {
        "surface": "market",
        "sources": {"events": [{"id": "event-1", "title": "Event"}]},
        "memory": [],
        "paper": {},
        "market_snapshot": {},
    }
    pages = [{"id": "page-1", "evidence_ids": ["evidence-1"]}]
    monkeypatch.setattr(agent, "_safe_list_conversation", lambda **_kwargs: [])
    monkeypatch.setattr(agent, "_safe_add_conversation", lambda *_args: None)
    monkeypatch.setattr(agent, "_safe_context_pack", lambda _surface: pack)
    monkeypatch.setattr(agent.wiki, "list_pages", lambda **_kwargs: pages)
    monkeypatch.setattr(agent.wiki, "track_page_usage", lambda *_args: None)
    monkeypatch.setattr(
        agent,
        "_compose_answer",
        lambda *_args, **_kwargs: json.dumps({
            "answer": "LLM 답변",
            "cited_evidence_ids": ["evidence-1", "outside"],
        }),
    )
    monkeypatch.setattr(agent, "_humanize_generic_fallback", lambda *_args: _args[-1])
    monkeypatch.setattr(agent, "_postprocess_chat", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(agent, "_classify_question_intent", lambda *_args, **_kwargs: {"name": "general"})
    monkeypatch.setattr(agent.evidence_context, "build_usage_summary", lambda *_args, **_kwargs: {})

    result = agent.answer("근거를 보여줘", "market")
    rows = evidence_usage.read_usage_rows()
    answer_rows = [row for row in rows if row.get("kind") == "answer_use"]

    assert result["answer"] == "LLM 답변"
    assert result["cited_evidence_ids"] == ["evidence-1"]
    assert result["answer_structured"] == {
        "answer": "LLM 답변",
        "cited_evidence_ids": ["evidence-1"],
    }
    assert len(answer_rows) == 1
    assert answer_rows[0]["cited_evidence_ids"] == ["evidence-1"]
    assert answer_rows[0]["invalid_cited_evidence_ids"] == ["outside"]
    assert answer_rows[0]["provided_evidence_ids"] == ["event-1", "evidence-1", "page-1"]


def test_final_general_prompt_records_context_use_with_query_pages_and_original_evidence(monkeypatch, tmp_path):
    from agent_console import agent, evidence_usage

    monkeypatch.setenv("AGENT_CONSOLE_EVIDENCE_USAGE_PATH", str(tmp_path / "usage.jsonl"))
    pack = {
        "surface": "market",
        "_evidence_query_id": "q1",
        "_wiki_context_pages": [{"id": "page-1", "evidence_ids": ["evidence-1"]}],
        "sources": {"events": [{"id": "event-1", "title": "Event"}]},
        "memory": [],
        "paper": {},
        "market_snapshot": {},
        "prediction_markets": {},
        "portfolio": {},
    }
    monkeypatch.setattr(agent.shared_memory, "build_context_section", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(agent.wiki, "build_context_section", lambda **_kwargs: "")
    monkeypatch.setattr(agent, "_classify_question_intent", lambda *_args, **_kwargs: {"name": "general"})

    agent._build_general_chat_prompt("실제 질문", pack)
    rows = evidence_usage.read_usage_rows()
    context_rows = [row for row in rows if row.get("kind") == "context_use"]

    assert len(context_rows) == 1
    assert context_rows[0]["query_id"] == "q1"
    assert context_rows[0]["page_ids"] == ["page-1"]
    assert context_rows[0]["retrieved_page_ids"] == ["page-1"]
    assert context_rows[0]["provided_evidence_ids"] == ["event-1", "evidence-1", "page-1"]


def test_trace_source_evidence_follows_event_wiki_retrieval_context_and_answer():
    from reports.evidence_cards import event_to_evidence_card
    from scripts.trace_source_evidence import trace_source_evidence

    event = {
        "id": "event-1",
        "source": "saveticker",
        "title": "AI demand",
        "url": "https://example.com/event-1",
        "collected_at": "2026-08-27T08:00:00+00:00",
    }
    evidence_id = event_to_evidence_card(event).id
    trace = trace_source_evidence(
        [event],
        [{"id": "page-1", "evidence_ids": [evidence_id]}],
        [
            {"kind": "retrieval", "query_id": "q1", "page_ids": ["page-1"]},
            {
                "kind": "context_use",
                "query_id": "q1",
                "page_ids": ["page-1"],
                "retrieved_page_ids": ["page-1"],
                "provided_evidence_ids": [evidence_id],
            },
            {
                "kind": "answer_use",
                "query_id": "q1",
                "provided_evidence_ids": [evidence_id],
                "cited_evidence_ids": [evidence_id],
            },
        ],
        event_id="event-1",
    )

    assert trace["ok"] is True
    assert trace["chain"]["event_id"] == "event-1"
    assert trace["chain"]["evidence_id"] == evidence_id
    assert trace["chain"]["wiki_page_ids"] == ["page-1"]
    assert trace["chain"]["retrieval_query_ids"] == ["q1"]
    assert trace["chain"]["cited_evidence_ids"] == [evidence_id]


def test_trace_source_evidence_fails_without_real_context_use_row():
    from reports.evidence_cards import event_to_evidence_card
    from scripts.trace_source_evidence import trace_source_evidence

    event = {
        "id": "event-1",
        "source": "saveticker",
        "title": "AI demand",
        "url": "https://example.com/event-1",
        "collected_at": "2026-08-27T08:00:00+00:00",
    }
    evidence_id = event_to_evidence_card(event).id
    trace = trace_source_evidence(
        [event],
        [{"id": "page-1", "evidence_ids": [evidence_id]}],
        [
            {"kind": "retrieval", "query_id": "q1", "page_ids": ["page-1"]},
            {
                "kind": "answer_use",
                "query_id": "q1",
                "provided_evidence_ids": [evidence_id],
                "cited_evidence_ids": [evidence_id],
            },
        ],
        event_id="event-1",
    )

    assert trace["ok"] is False
    assert "context_use" in trace["missing"]


def test_trace_source_evidence_reports_incomplete_chain():
    from scripts.trace_source_evidence import trace_source_evidence

    trace = trace_source_evidence(
        [{"id": "event-1", "source": "saveticker", "title": "AI demand"}],
        [],
        [],
        event_id="event-1",
    )

    assert trace["ok"] is False
    assert "wiki" in trace["missing"]


def test_trace_cli_returns_failure_exit_code_for_incomplete_chain(monkeypatch):
    from scripts import trace_source_evidence as trace_cli

    monkeypatch.setattr(
        trace_cli,
        "_load_trace_inputs",
        lambda _hours, _limit: ([{"id": "event-1", "source": "saveticker", "title": "AI demand"}], [], []),
    )

    assert trace_cli.main(["--event-id", "event-1", "--json"]) == 1
