"""tests/test_agent_resolve_symbol.py — _resolve_portfolio_symbol 느슨한 substring 매칭 회귀.

짧은 티커(SO=Southern Company 등)가 다른 영단어 안에 우연히 포함돼 있으면
(예: "person" 안의 "so") 잘못 매치되던 문제 — 단어 경계 매칭으로 수정 (감사 #15).
"""


def test_resolve_portfolio_symbol_ignores_ticker_embedded_inside_other_word():
    from agent_console.agent import _resolve_portfolio_symbol

    holdings = [{"ticker": "SO", "name": "Southern Company"}]
    # "person" 안에 "so" 가 우연히 포함 — 실제 티커 언급 아님
    question = "이 person 관련 리스크는 어때"

    assert _resolve_portfolio_symbol(question, holdings) is None


def test_resolve_portfolio_symbol_matches_standalone_ticker_with_korean_particle():
    from agent_console.agent import _resolve_portfolio_symbol

    holdings = [{"ticker": "SO", "name": "Southern Company"}]
    question = "SO는 계속 들고 갈까요"

    assert _resolve_portfolio_symbol(question, holdings) == "SO"


def test_resolve_portfolio_symbol_ignores_company_name_embedded_inside_other_word():
    from agent_console.agent import _resolve_portfolio_symbol

    # 티커 자체는 질문에 전혀 등장하지 않아 1차 토큰 매칭엔 안 걸리고,
    # name="All" 이 "allow" 안에 우연히 포함되는 2차 substring 매칭만 걸림.
    holdings = [{"ticker": "XYZALLE", "name": "All"}]
    question = "이거 allow 되는지 확인해줘"

    assert _resolve_portfolio_symbol(question, holdings) is None
