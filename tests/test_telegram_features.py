import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from barbell_strategy import build_simulation_report
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
