"""agent 의 엔진/폴백 메타데이터가 요청 로컬인지 (리뷰 D-10, 무LLM).

전역 _LAST_LLM_ENGINE 시절에는 동시 요청 B 의 폴백이 요청 A 의 context.engine 을 지우고,
race 에서 늦게 끝난 loser 가 승자 표시를 덮어썼다.
"""
import threading
import time

from agent_console import agent, laya_bridge


def _stub_answer_dependencies(monkeypatch, compose):
    monkeypatch.setattr(agent.wiki, "list_pages", lambda **kw: [])
    monkeypatch.setattr(laya_bridge, "review_wiki_candidates",
                        lambda question, pages, history=None, limit=4: (pages, {"mode": "baseline"}))
    monkeypatch.setattr(agent, "_safe_list_conversation", lambda **kw: [])
    monkeypatch.setattr(agent, "_safe_add_conversation", lambda *a, **kw: None)
    monkeypatch.setattr(agent, "_track_wiki_page_usage", lambda *a: None)
    monkeypatch.setattr(agent, "_postprocess_chat", lambda *a, **kw: {})
    monkeypatch.setattr(agent.evidence_usage, "record_answer_use", lambda *a, **kw: {})
    monkeypatch.setattr(agent, "_compose_answer", compose)


_PACK = {"sources": {"events": [], "source_counts": []}, "portfolio": {"holdings": []},
         "paper": {}, "models": {}, "memory": [], "reports": [], "ml_activity": []}


def test_concurrent_answers_keep_their_own_engine_and_fallback_reason(monkeypatch):
    a_marked = threading.Event()
    b_done = threading.Event()

    def compose(question, pack, history=None):
        if question == "A 질문":
            agent._mark_llm_engine("codex")
            a_marked.set()
            assert b_done.wait(5)                      # B 가 reset·폴백 표시를 끝낸 뒤 A 가 마무리
            return "A 답변"
        assert a_marked.wait(5)
        agent._mark_rules_fallback("llm_disabled")
        b_done.set()
        return "B 답변"

    _stub_answer_dependencies(monkeypatch, compose)
    results = {}

    def ask(question):
        results[question] = agent.answer(question, "market", context_pack_data=dict(_PACK))

    threads = [threading.Thread(target=ask, args=(q,)) for q in ("A 질문", "B 질문")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)

    assert results["A 질문"]["answer"] == "A 답변"
    assert results["A 질문"]["context"]["engine"] == "codex"
    assert results["A 질문"]["context"]["fallback_reason"] is None
    assert results["B 질문"]["context"]["engine"] == "local-rules"
    assert results["B 질문"]["context"]["fallback_reason"] == "llm_disabled"


def test_late_race_loser_cannot_overwrite_the_returned_provider(monkeypatch):
    loser_done = threading.Event()

    def slow_codex(prompt, **kwargs):
        time.sleep(0.2)
        agent._mark_llm_engine("codex")                # 승자가 반환된 뒤에 성공 표시
        loser_done.set()
        return "느린 답변"

    def fast_hermes(prompt, **kwargs):
        agent._mark_llm_engine("hermes")
        return "빠른 답변"

    monkeypatch.setattr(agent, "_try_codex_chat", slow_codex)
    monkeypatch.setattr(agent, "_try_hermes_chat", fast_hermes)

    assert agent._try_llm_prompt("테스트") == "빠른 답변"
    assert loser_done.wait(5)
    assert agent.current_llm_engine() == "hermes"


def test_engine_state_does_not_leak_between_sequential_answers(monkeypatch):
    calls = iter(["codex", None])

    def compose(question, pack, history=None):
        engine = next(calls)
        if engine:
            agent._mark_llm_engine(engine)
        return "답변"

    _stub_answer_dependencies(monkeypatch, compose)
    first = agent.answer("첫 질문", "market", context_pack_data=dict(_PACK))
    second = agent.answer("둘째 질문", "market", context_pack_data=dict(_PACK))
    assert first["context"]["engine"] == "codex"
    assert second["context"]["engine"] == "local-rules"
