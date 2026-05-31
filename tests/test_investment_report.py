import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from investment_report import _judgment


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
