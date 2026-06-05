import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from investment_report import (
    _decision_v2,
    _etf_peer_group,
    _etf_period_return,
    _fmt_price,
    _format_etf_comparison,
    _mobile_pick_line,
    _mobile_pick_items,
    _judgment,
)


def test_judgment_for_etf_with_critical_signal_is_excluded():
    fund = {"total_score": 0, "notes": ["ETF/ETN — 재무 점수 불필요"]}
    signal = {"overall_signal": "Critical", "critical": ["급락"]}

    judgment, reasons, risks = _judgment(fund, signal, "N/A")

    assert judgment == "제외 검토"
    assert reasons
    assert risks


def test_judgment_for_high_score_positive_signal_is_buy_candidate():
    fund = {"total_score": 80, "notes": ["우수"]}
    signal = {"overall_signal": "Positive", "signals_found": ["모멘텀 강세"], "critical": []}

    judgment, reasons, risks = _judgment(fund, signal, "A")

    assert judgment == "분할매수 후보"
    assert any("우수" in item or "재무 건강도" in item for item in reasons)
    assert risks


def test_decision_v2_strong_buy_candidate():
    fund = {"total_score": 82, "grade": "A", "notes": ["우수"]}
    signal = {
        "overall_signal": "Positive",
        "signals_found": ["모멘텀 강세"],
        "warnings": [],
        "critical": [],
        "price_info": {"current_price": 100, "1d_change_pct": 2.1, "1mo_change_pct": 7.5},
        "news_items": [{"sentiment": "positive", "title": "호실적"}],
    }

    decision = _decision_v2(fund, signal)

    assert decision["action"] == "강한 매수후보"
    assert 0 <= decision["confidence"] <= 100
    assert decision["financial"]["status"] == "강함"
    assert "재무 82점" in decision["one_line_reason"]


def test_decision_v2_waits_after_overheated_move():
    fund = {"total_score": 78, "grade": "A"}
    signal = {
        "overall_signal": "Positive",
        "warnings": [],
        "critical": [],
        "price_info": {"current_price": 100, "1d_change_pct": 6.2},
    }

    decision = _decision_v2(fund, signal)

    assert decision["action"] == "추격 금지"
    assert decision["timing"]["status"] == "과열"


def test_decision_v2_sell_review_on_critical_signal():
    fund = {"total_score": 68, "grade": "B"}
    signal = {
        "overall_signal": "Critical",
        "warnings": [],
        "critical": ["실적 급락"],
        "price_info": {"current_price": 100, "1d_change_pct": -3.0},
    }

    decision = _decision_v2(fund, signal)

    assert decision["action"] == "매도검토"
    assert decision["risk"]["status"] == "높음"


def test_decision_v2_data_shortage():
    fund = {"total_score": 0, "grade": "N/A"}
    signal = {"overall_signal": "Neutral", "warnings": [], "critical": []}

    decision = _decision_v2(fund, signal)

    assert decision["action"] == "데이터부족"
    assert decision["financial"]["status"] == "부족"


def test_decision_v2_includes_deterministic_confidence():
    fund = {"total_score": 82, "grade": "A"}
    signal = {
        "overall_signal": "Positive",
        "warnings": [],
        "critical": [],
        "price_info": {"current_price": 100, "1d_change_pct": 2.0},
        "news_items": [{"sentiment": "positive"}],
    }

    decision = _decision_v2(fund, signal)

    assert decision["confidence"] == 94


def test_mobile_pick_line_excludes_tickers_already_shown_in_top():
    items = [
        {"ticker": "AAA", "decision_v2": {"action": "보유", "one_line_reason": "재무 70점"}},
        {"ticker": "BBB", "decision_v2": {"action": "보유", "one_line_reason": "재무 65점"}},
    ]
    top_mobile = _mobile_pick_items(items[:1])

    line = _mobile_pick_line("주의", items, exclude_tickers={r["ticker"] for r in top_mobile})

    assert "AAA" not in line
    assert "BBB" in line


# ── v3 Decision Engine 테스트 ──────────────────────────────────────────────

def test_decision_v2_confidence_breakdown_exists_and_bounded():
    fund = {"total_score": 70, "grade": "B"}
    signal = {
        "overall_signal": "Neutral",
        "warnings": [],
        "critical": [],
        "price_info": {"current_price": 50},
        "news_items": [],
    }

    decision = _decision_v2(fund, signal)

    bd = decision.get("confidence_breakdown")
    assert isinstance(bd, dict), "confidence_breakdown should be a dict"
    for key in ("data_quality", "signal_alignment", "risk_clarity", "news_support"):
        assert key in bd, f"{key} missing from confidence_breakdown"
        assert 0 <= bd[key] <= 100, f"{key} out of bounds: {bd[key]}"


def test_decision_v2_strong_overheated_is_no_chase():
    fund = {"total_score": 78, "grade": "A"}
    signal = {
        "overall_signal": "Positive",
        "warnings": [],
        "critical": [],
        "price_info": {"current_price": 200, "1d_change_pct": 6.5},
    }

    decision = _decision_v2(fund, signal)

    assert decision["action"] == "추격 금지"
    assert decision["timing"]["status"] == "과열"


def test_decision_v2_strong_neutral_timing_low_risk_is_watch():
    fund = {"total_score": 76, "grade": "A"}
    signal = {
        "overall_signal": "Neutral",
        "warnings": [],
        "critical": [],
        "price_info": {"current_price": 100, "1d_change_pct": -1.5},
    }

    decision = _decision_v2(fund, signal)

    assert decision["action"] == "관심 유지"
    assert decision["financial"]["status"] == "강함"


def test_decision_v2_etf_note_not_data_shortage():
    fund = {
        "total_score": 0,
        "grade": "N/A",
        "ticker": "SGOV",
        "notes": ["iShares 0-3 Month Treasury Bond ETF", "현금성 단기채"],
    }
    signal = {"overall_signal": "Neutral", "warnings": [], "critical": [], "price_info": {}}

    decision = _decision_v2(fund, signal, ticker="SGOV")

    assert decision["action"] != "데이터부족", f"SGOV should not be 데이터부족, got {decision['action']}"
    assert decision["financial"]["status"] != "부족"


def test_decision_v2_keeps_sgov_as_cash_even_with_rsi_warning():
    fund = {
        "total_score": 0,
        "grade": "N/A",
        "ticker": "SGOV",
        "notes": ["iShares 0-3 Month Treasury Bond ETF", "현금성 단기채"],
    }
    signal = {
        "overall_signal": "Warning",
        "warnings": ["RSI 과매수 — 매도 검토"],
        "critical": [],
        "price_info": {"current_price": 100.5, "1d_change_pct": 0.02, "1mo_change_pct": 0.4},
    }

    decision = _decision_v2(fund, signal, ticker="SGOV")

    assert decision["action"] == "현금성 유지"


def test_decision_v2_risk_types_is_list():
    fund = {"total_score": 82, "grade": "A"}
    signal = {
        "overall_signal": "Positive",
        "warnings": [],
        "critical": [],
        "price_info": {"current_price": 100, "1d_change_pct": 7.0},
    }

    decision = _decision_v2(fund, signal)

    assert isinstance(decision["risk"]["types"], list)
    assert "과열" in decision["risk"]["types"]


def test_decision_v2_today_action_exists_and_short():
    fund = {"total_score": 65, "grade": "B"}
    signal = {
        "overall_signal": "Neutral",
        "warnings": [],
        "critical": [],
        "price_info": {"current_price": 80},
    }

    decision = _decision_v2(fund, signal)

    assert "today_action" in decision
    assert isinstance(decision["today_action"], str)
    assert len(decision["today_action"]) <= 30


def test_mobile_line_length_stays_short():
    items = [
        {"ticker": "NVDA", "decision_v2": {"action": "관심 유지", "one_line_reason": "재무 76점(A)"}},
        {"ticker": "MSFT", "decision_v2": {"action": "강한 매수후보", "one_line_reason": "재무 82점(A)"}},
    ]

    line = _mobile_pick_line("상위", items)

    assert len(line) < 120, f"Mobile line too long: {len(line)} chars"


# ── ETF 비교 평가 테스트 ──────────────────────────────────────────────

def test_etf_period_return_uses_total_return_and_expense_ratio():
    hist = {
        "Close": [100.0, 110.0],
        "Dividends": [0.0, 2.0],
    }

    result = _etf_period_return(hist, years=1, expense_ratio=0.50)

    assert result == 11.5


def test_etf_peer_group_includes_peer_and_spy_qqq_benchmarks():
    peers = _etf_peer_group("SPMO")

    assert "MTUM" in peers
    assert "SPY" in peers
    assert "QQQ" in peers


def test_format_etf_comparison_reports_relative_underperformance():
    comparison = {
        "ticker": "SPMO",
        "expense_ratio": 0.13,
        "periods": [
            {
                "label": "1Y",
                "actual_label": "1Y",
                "return_pct": 8.0,
                "vs": {
                    "SPY": {"return_pct": 10.5, "diff_pct": -2.5},
                    "QQQ": {"return_pct": 12.0, "diff_pct": -4.0},
                    "MTUM": {"return_pct": 7.0, "diff_pct": 1.0},
                },
            }
        ],
    }

    lines = _format_etf_comparison(comparison)

    assert any("운영수수료 0.13%" in line for line in lines)
    assert any("1Y" in line and "SPY -2.50%p" in line and "QQQ -4.00%p" in line for line in lines)
    assert any("동종 MTUM +1.00%p" in line for line in lines)


def test_fmt_price_supports_krw_for_korea_market_values():
    assert _fmt_price(3901.234, currency="KRW") == "₩3,901.23"
