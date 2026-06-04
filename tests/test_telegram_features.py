import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from barbell_strategy import build_simulation_report, _holding_details_from_snapshot
from portfolio_tracker import build_benchmark_report, build_dividend_calendar


def test_build_simulation_report_includes_mode_and_phase():
    text = build_simulation_report("bull2")

    assert "시뮬레이션 모드: bull2" in text
    assert "Intelligence Barbell v2.1" in text
    assert "Phase" in text


def test_build_benchmark_report_compares_portfolio_against_benchmarks():
    perf = {
        "current": 100.0,
        "ret_1d": 1.0,
        "ret_7d": 2.0,
        "ret_30d": 3.0,
        "ret_90d": 4.0,
        "ret_all": 5.0,
    }
    benchmarks = {
        "QQQ": {
            "name": "QQQ — Invesco QQQ Trust",
            "ret_1d": 0.5,
            "ret_7d": 1.5,
            "ret_30d": 2.5,
            "ret_90d": 3.5,
            "ret_all": 4.5,
        },
        "QQQI": {
            "name": "QQQI — NEOS Nasdaq 100 High Income ETF",
            "ret_1d": 0.2,
            "ret_7d": 0.4,
            "ret_30d": 0.6,
            "ret_90d": 0.8,
            "ret_all": 1.0,
        },
    }

    text = build_benchmark_report(perf, benchmarks)

    assert "벤치마크 비교" in text
    assert "내 포트폴리오" in text
    assert "QQQ — Invesco QQQ Trust" in text
    assert "QQQI — NEOS Nasdaq 100 High Income ETF" in text


def test_build_dividend_calendar_estimates_next_payment():
    dividends = [
        {"date": "2024-01-15", "amount_usd": 1.00},
        {"date": "2024-02-14", "amount_usd": 1.10},
        {"date": "2024-03-15", "amount_usd": 1.20},
    ]

    text = build_dividend_calendar(dividends, shares=100)

    assert "배당 캘린더" in text
    assert "다음 예상" in text
    assert "평균 간격" in text


def test_holding_details_from_snapshot_includes_stocks_and_domestic():
    snap = {
        "overseas_general": {
            "holdings_usd": [
                {
                    "ticker": "MSFT",
                    "name": "마이크로소프트",
                    "shares": 2,
                    "value_usd": 900.48,
                    "return_pct": 11.86,
                }
            ]
        },
        "overseas_fractional": {
            "holdings": [
                {
                    "ticker": "NVDA",
                    "name": "엔비디아",
                    "shares": 0.5,
                    "value_usd": 105.57,
                    "return_pct": 14.66,
                }
            ]
        },
        "domestic": {
            "holdings": [
                {
                    "ticker": "SOL AI반도체TOP2",
                    "name": "SOL AI반도체TOP2플러스",
                    "shares": 20,
                    "current_price": 25200,
                    "return_pct": 6.38,
                }
            ]
        },
    }

    details = _holding_details_from_snapshot(snap)

    assert details[0]["ticker"] == "MSFT"
    assert details[0]["name"] == "마이크로소프트"
    assert details[0]["value_usd"] == 900.48
    assert details[1]["ticker"] == "NVDA"
    assert details[2]["value_krw"] == 504000


def test_dispatch_ask_fetches_market_and_sends_advice(monkeypatch):
    import telegram_bot

    calls = {"fetch_market": 0, "ask": None, "send": [], "typing_stop": 0}
    market = {"market_type": "bull", "phase_key": "bull_1"}

    def fake_fetch_market():
        calls["fetch_market"] += 1
        return market

    def fake_ask(question, data):
        calls["ask"] = (question, data)
        return "상담 답변"

    def fake_send(chat_id, text):
        calls["send"].append((chat_id, text))

    def fake_keep_typing(chat_id):
        calls["typing_chat_id"] = chat_id

        def stop():
            calls["typing_stop"] += 1

        return stop

    monkeypatch.setattr(telegram_bot, "fetch_market", fake_fetch_market)
    monkeypatch.setattr(telegram_bot, "ask_portfolio_advisor", fake_ask)
    monkeypatch.setattr(telegram_bot, "send", fake_send)
    monkeypatch.setattr(telegram_bot, "keep_typing", fake_keep_typing)

    telegram_bot.dispatch("/ask 지금 추가매수해도 돼?", "chat-1")

    assert calls["fetch_market"] == 1
    assert calls["ask"] == ("지금 추가매수해도 돼?", market)
    assert calls["typing_chat_id"] == "chat-1"
    assert calls["typing_stop"] == 1
    assert calls["send"] == [("chat-1", "상담 답변")]
