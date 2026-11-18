import urllib.error

from agent_console import decision_model


def _pages(count):
    return [{"id": f"p{index}", "title": f"Page {index}", "body": f"body {index} [S1](https://x.org)"} for index in range(count)]


def _enable(monkeypatch, answers):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("AGENT_CONSOLE_JEV", "1")

    def post(payload):
        page = payload["state"]["wiki_page"]
        assert "https://" not in page["excerpt"] and payload["model"] == decision_model.MODEL
        result = answers[page["title"]]
        if isinstance(result, Exception):
            raise result
        return {"answers": {"helps": result}}
    monkeypatch.setattr(decision_model, "_post", post)


def test_pages_are_ordered_by_the_probability_that_they_help(monkeypatch):
    pages = _pages(decision_model.MAX_PAGES + 1)
    answers = {page["title"]: {"type": "noul", "noul": index / 100} for index, page in enumerate(pages)}
    answers["Page 3"] = {"type": "noul", "noul": 0.99}
    _enable(monkeypatch, answers)

    ranked, status = decision_model.rank_wiki_pages("question", pages)

    assert [page["id"] for page in ranked[:3]] == ["p3", "p19", "p18"]
    assert ranked[-1]["id"] == f"p{decision_model.MAX_PAGES}"  # beyond the cap: kept after the ranked head
    assert status["method"] == "jev" and status["scores"]["p3"] == 0.99


def test_a_failed_or_malformed_decision_keeps_the_retrieval_order(monkeypatch):
    pages = _pages(3)
    for broken in (urllib.error.URLError("down"), {"type": "choice", "choice": "x"}):
        _enable(monkeypatch, {"Page 0": {"type": "noul", "noul": 0.1}, "Page 1": broken,
                              "Page 2": {"type": "noul", "noul": 0.9}})

        ranked, status = decision_model.rank_wiki_pages("question", pages)

        assert ranked == pages and status["method"] == "retrieval"


def test_without_a_key_or_when_switched_off_nothing_is_called(monkeypatch):
    monkeypatch.setattr(decision_model, "_post", lambda payload: (_ for _ in ()).throw(AssertionError("called")))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("AGENT_CONSOLE_JEV", "1")
    assert decision_model.rank_wiki_pages("q", _pages(3))[1]["reason"] == "disabled"
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("AGENT_CONSOLE_JEV", "0")
    assert decision_model.rank_wiki_pages("q", _pages(3))[1]["reason"] == "disabled"


def test_intent_falls_back_to_jev_only_where_the_rules_find_none(monkeypatch):
    from agent_console import agent
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("AGENT_CONSOLE_JEV", "1")
    monkeypatch.setattr(agent, "_JEV_INTENT_CACHE", {})
    monkeypatch.setattr(agent, "_extract_asset_symbol", lambda question: None)
    calls = []

    def post(payload):
        calls.append(payload["state"]["user_message"])
        assert set(payload["questions"]["intent"]["criteria"]) == set(agent._JEV_INTENTS)
        return {"answers": {"intent": {"type": "choice", "choice": "market_analysis", "confidence": 0.8}}}
    monkeypatch.setattr(decision_model, "_post", post)

    assert agent._classify_question_intent("오늘 시장 어때?")["name"] == "market_analysis"
    assert agent._classify_question_intent("왜 같은 답을 반복해?")["name"] == "meta_debug"
    assert calls == []  # a keyword rule matched: Jev is not asked
    assert agent._classify_question_intent("그럼 코스피는 어때")["name"] == "market_analysis"
    assert agent._classify_question_intent("그럼 코스피는 어때")["name"] == "market_analysis"
    assert calls == ["그럼 코스피는 어때"]  # one call per question
    agent._classify_question_intent("안녕")
    assert calls == ["그럼 코스피는 어때"]  # small talk stays general without a call


def test_intent_stays_general_when_jev_fails_or_answers_outside_the_menu(monkeypatch):
    from agent_console import agent
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("AGENT_CONSOLE_JEV", "1")
    monkeypatch.setattr(agent, "_extract_asset_symbol", lambda question: None)
    for answer in (urllib.error.URLError("down"), {"answers": {"intent": {"type": "choice", "choice": "weather"}}}):
        monkeypatch.setattr(agent, "_JEV_INTENT_CACHE", {})

        def post(payload, answer=answer):
            if isinstance(answer, Exception):
                raise answer
            return answer
        monkeypatch.setattr(decision_model, "_post", post)

        intent = agent._classify_question_intent("그럼 코스피는 어때")

        assert intent["name"] == "general" and agent._JEV_INTENT_CACHE == {}


def test_ticker_research_chosen_without_a_resolved_symbol_asks_to_identify_it(monkeypatch):
    from agent_console import agent
    contract = agent._intent_contract_for("ticker_research", "아니아니 국내 etf", None)
    assert contract["subject"] == "" and contract["required_steps"][0] == "질문이 가리키는 종목을 먼저 특정"
