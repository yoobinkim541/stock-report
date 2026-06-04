import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


class FakeCompleted:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def sample_market():
    return {
        "fetched_at": "2026-06-03 01:00",
        "market_type": "bull",
        "phase_key": "bull_1",
        "rsi": 68.2,
        "vix": 17.5,
        "exchange_rate": 1370.0,
        "qqq": {"current": 540.12, "drawdown_pct": -1.2},
        "portfolio": {
            "total_usd": 10000.0,
            "sgov_usd": 1200.0,
            "qqqi_usd": 3000.0,
        },
    }


def test_build_advisor_prompt_contains_grounding_and_safety():
    from stock_advisor import build_advisor_prompt

    prompt = build_advisor_prompt("지금 추가매수해도 돼?", sample_market())

    assert "지금 추가매수해도 돼?" in prompt
    assert "bull/bull_1" in prompt
    assert "RSI: 68.2" in prompt
    assert "실제 데이터만" in prompt
    assert "투자 조언은 참고용" in prompt


def test_build_advisor_prompt_includes_individual_stock_holdings():
    from stock_advisor import build_advisor_prompt

    market = sample_market()
    market["portfolio"]["holdings_detail"] = [
        {
            "ticker": "NVDA",
            "name": "엔비디아",
            "shares": 2,
            "value_usd": 422.28,
            "return_pct": 14.66,
        },
        {
            "ticker": "MSFT",
            "name": "마이크로소프트",
            "shares": 2,
            "value_usd": 900.48,
            "return_pct": 11.86,
        },
    ]

    prompt = build_advisor_prompt("개별주 점검해줘", market)

    assert "[개별 보유 종목]" in prompt
    assert "NVDA — 엔비디아" in prompt
    assert "MSFT — 마이크로소프트" in prompt
    assert "$422.28" in prompt
    assert "14.66%" in prompt


def test_ask_portfolio_advisor_uses_codex55_runner():
    from stock_advisor import ask_portfolio_advisor

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return FakeCompleted(stdout="결론: DCA 유지\n근거: RSI 68")

    answer = ask_portfolio_advisor("지금 추가매수해도 돼?", sample_market(), runner=fake_run)

    assert "결론: DCA 유지" in answer
    cmd = calls[0][0]
    assert cmd[:4] == ["hermes", "chat", "-q", calls[0][0][3]]
    assert "--provider" in cmd and "openai-codex" in cmd
    assert "--model" in cmd and "gpt-5.5" in cmd


def test_ask_portfolio_advisor_falls_back_when_codex_fails():
    from stock_advisor import ask_portfolio_advisor

    def fake_run(cmd, **kwargs):
        return FakeCompleted(stdout="", stderr="boom", returncode=1)

    answer = ask_portfolio_advisor("지금 추가매수해도 돼?", sample_market(), runner=fake_run)

    assert "Codex 5.5 상담 호출 실패" in answer
    assert "로컬 안전 요약" in answer
    assert "bull/bull_1" in answer
