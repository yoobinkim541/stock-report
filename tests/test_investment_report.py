import math
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from investment_report import (
    _build_llm_analysis_payload,
    _build_llm_overlay_prompt,
    _build_mobile_summary,
    _decision_v2,
    _etf_peer_group,
    _etf_period_return,
    _etf_period_returns,
    _fx_timing_mobile_line,
    _fmt_index_value,
    _fmt_price,
    _format_etf_comparison,
    _generate_llm_overlay,
    _llm_overlay_mobile_lines,
    _mobile_pick_block,
    _mobile_pick_items,
    _news_title_relevant,
    _portfolio_action_plan,
    _select_top_buy_candidates,
    _select_watch_candidates,
    _validate_llm_overlay,
    _judgment,
)
from llm_decision import (
    build_context_decision,
    merge_llm_decision,
    validate_llm_decisions,
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


def test_mobile_pick_block_excludes_tickers_already_shown_in_top():
    items = [
        {"ticker": "AAA", "decision_v2": {"action": "보유", "one_line_reason": "재무 70점"}},
        {"ticker": "BBB", "decision_v2": {"action": "보유", "one_line_reason": "재무 65점"}},
    ]
    top_mobile = _mobile_pick_items(items[:1])

    block = "\n".join(_mobile_pick_block("주의", items, exclude_tickers={r["ticker"] for r in top_mobile}))

    assert "AAA" not in block
    assert "BBB" in block


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


def test_context_decision_treats_unh_like_case_as_partial_trim_not_true_risk():
    result = {
        "ticker": "UNH",
        "fundamental": {"total_score": 49, "grade": "C", "notes": []},
        "signal": {
            "overall_signal": "Positive",
            "price_info": {"current_price": 425.36, "1d_change_pct": -0.28, "1mo_change_pct": 13.47},
        },
        "decision_v2": {
            "action": "비중축소 검토",
            "confidence": 74,
            "risk": {"status": "낮음", "reason": "특이 위험 제한적"},
        },
        "risks": ["ROIC 시계열 데이터 부족"],
    }
    holding = {"weight_pct": 16.5, "return_pct": 36.4}
    earnings = {"days_until": 11, "revision_momentum": -0.2, "target_upside_pct": -3.2}

    decision = build_context_decision(result, holding=holding, earnings=earnings)

    assert decision["portfolio_action"] == "일부축소"
    assert decision["risk_level"] == "주의"
    assert "20~30%" in decision["execution_plan"]
    assert "전량매도" in decision["do_not_do"]


def test_mobile_summary_separates_position_review_from_risk_bucket():
    unh = {
        "ticker": "UNH",
        "company_name": "UnitedHealth",
        "decision_v2": {"action": "비중축소 검토"},
        "decision_context": {
            "risk_level": "주의",
            "portfolio_action": "일부축소",
            "execution_plan": "실적 전 20~30% 일부축소, 나머지 보유",
        },
        "signal": {"overall_signal": "Positive"},
    }

    text = _build_mobile_summary(
        "2026-07-07",
        -0.1,
        {"qqq_change": -1.7},
        "N/A",
        43,
        1,
        0,
        0,
        0,
        [unh],
        [],
        [],
        [],
        [],
        [],
        lambda t: t,
        None,
        "disabled",
        1.0,
        "",
        phase=None,
    )

    assert "📌 오늘 할 일: 비중점검" in text
    assert "오늘 결론" in text
    assert "💼 내 포트 43/100 · 주의 · 신규매수는 소액/선별" in text
    assert "✅ 실행 우선순위" in text
    assert "1. 🟠 UnitedHealth (UNH) · 비중점검" in text
    assert "행동: 실적 전 20~30% 일부축소, 나머지 보유" in text
    assert "UnitedHealth (UNH)" in text
    assert "위험관리 · UnitedHealth (UNH)" not in text


def test_portfolio_action_plan_prioritizes_actions_with_context():
    buy = {
        "ticker": "MSFT",
        "company_name": "Microsoft",
        "decision_v2": {"action": "강한 매수후보", "today_action": "분할매수 후보"},
        "signal": {"price_info": {"1d_change_pct": 1.2}},
    }
    review = {
        "ticker": "UNH",
        "company_name": "UnitedHealth",
        "decision_v2": {"action": "비중축소 검토"},
        "decision_context": {"portfolio_action": "일부축소", "execution_plan": "실적 전 20~30% 일부축소"},
        "holding_context": {"weight_pct": 16.5},
        "earnings_context": {"days_until": 11},
        "signal": {"price_info": {"1d_change_pct": -0.3}},
    }
    risk = {
        "ticker": "RISK",
        "company_name": "Risk Co",
        "decision_v2": {"action": "매도검토", "today_action": "급락 원인 확인"},
        "decision_context": {"risk_level": "높음"},
    }

    plan = _portfolio_action_plan([buy, review, risk])

    assert [row["bucket"] for row in plan] == ["위험관리", "비중점검", "매수관심"]
    assert plan[1]["label"] == "UnitedHealth (UNH)"
    assert "실적 전" in plan[1]["detail"]
    assert "비중 16.5%" in plan[1]["extras"]
    assert "실적 D-11" in plan[1]["extras"]


def test_fx_timing_mobile_line_is_compact_and_honest():
    line = _fx_timing_mobile_line({
        "ok": True,
        "rate": 1325.4,
        "pct_display": 18,
        "multiplier": 1.5,
        "emoji": "🟢",
        "verdict": "환전 유리",
    })

    assert "환전 유리" in line
    assert "1.5×" in line
    assert "예측 아님" in line
    assert len(line) < 100


def test_llm_decision_schema_shadow_and_apply_merge():
    raw = {
        "decisions": [
            {
                "ticker": "UNH",
                "risk_level": "주의",
                "portfolio_action": "비중점검",
                "execution_plan": "실적 확인 전 추가매수 금지",
                "reasoning_summary": ["실적 D-11"],
                "do_not_do": ["실적 전 추가매수"],
                "recheck_triggers": ["가이던스 유지 여부"],
                "confidence": 71,
            }
        ]
    }

    llm = validate_llm_decisions(raw, {"UNH"})["UNH"]
    context = {
        "ticker": "UNH",
        "risk_level": "주의",
        "portfolio_action": "일부축소",
        "execution_plan": "실적 전 20~30% 일부축소, 나머지 보유",
        "confidence": 79,
    }

    shadow = merge_llm_decision(context, llm, mode="shadow")
    applied = merge_llm_decision(context, llm, mode="apply")

    assert shadow["portfolio_action"] == "일부축소"
    assert shadow["llm_shadow"]["portfolio_action"] == "비중점검"
    assert applied["portfolio_action"] == "비중점검"
    assert applied["source"] == "llm_apply"


def test_mobile_block_lines_stay_short():
    items = [
        {"ticker": "NVDA", "decision_v2": {"action": "관심 유지", "one_line_reason": "재무 76점(A)"}},
        {"ticker": "MSFT", "decision_v2": {"action": "강한 매수후보", "one_line_reason": "재무 82점(A)"}},
    ]

    for line in _mobile_pick_block("상위", items):
        assert len(line) < 120, f"Mobile line too long: {len(line)} chars"


def test_mobile_pick_block_cleans_company_suffix_and_dedupes_price_reason():
    items = [
        {
            "ticker": "DXCM",
            "company_name": "DexCom, Inc.",
            "total_score": 79,
            "grade": "A",
            "decision_v2": {"action": "강한 매수후보", "one_line_reason": "재무 79점(B) · 일일 신호 긍정"},
        },
        {
            "ticker": "005930.KS",
            "company_name": "삼성전자",
            "total_score": 73,
            "grade": "B",
            "decision_v2": {"action": "보유", "one_line_reason": "재무 73점(B) · 1일 -6.92% 급락 · 일일 -6.92% 하락"},
        },
    ]

    block = "\n".join(_mobile_pick_block("상위", items, limit=2))

    assert "DexCom (DXCM)" in block
    assert "DexCom, Inc." not in block
    assert "1일 -6.92% 급락" in block
    assert "일일 -6.92% 하락" not in block


# ── ETF 비교 평가 테스트 ──────────────────────────────────────────────

def test_etf_period_return_uses_total_return_and_expense_ratio():
    hist = {
        "Close": [100.0, 110.0],
        "Dividends": [0.0, 2.0],
    }

    result = _etf_period_return(hist, years=1, expense_ratio=0.50)

    assert result == 11.5


def test_etf_period_returns_include_total_return_and_price_return():
    hist = {
        "Close": [100.0, 110.0],
        "Dividends": [0.0, 2.0],
    }

    result = _etf_period_returns(hist, years=1, expense_ratio=0.50)

    assert result == {"tr_return_pct": 11.5, "pr_return_pct": 9.5}


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
                "tr_return_pct": 8.0,
                "pr_return_pct": 7.5,
                "vs": {
                    "SPY": {"return_pct": 10.5, "tr_diff_pct": -2.5, "pr_diff_pct": -1.5},
                    "QQQ": {"return_pct": 12.0, "tr_diff_pct": -4.0, "pr_diff_pct": -3.0},
                    "MTUM": {"return_pct": 7.0, "tr_diff_pct": 1.0, "pr_diff_pct": 0.5},
                },
            }
        ],
    }

    lines = _format_etf_comparison(comparison)

    assert any("ETF 비교 요약" in line and "운영수수료 0.13%" in line for line in lines)
    assert any("최장기간 주식형 대비 SPY TR -2.50%p, QQQ TR -4.00%p" in line for line in lines)
    assert any("해석:" in line and "동종 ETF가 핵심 비교대상" in line for line in lines)
    assert any("1Y" in line and "주식형 대비 SPY TR -2.50%p/PR -1.50%p, QQQ TR -4.00%p/PR -3.00%p" in line for line in lines)
    assert any("동종 대비 MTUM TR +1.00%p/PR +0.50%p" in line for line in lines)


def test_format_etf_comparison_reports_income_etf_context():
    comparison = {
        "ticker": "QQQI",
        "expense_ratio": 0.0,
        "periods": [
            {
                "label": "1Y",
                "actual_label": "1Y",
                "return_pct": 27.77,
                "vs": {
                    "SPY": {"return_pct": 28.28, "diff_pct": -0.51},
                    "QQQ": {"return_pct": 40.59, "diff_pct": -12.82},
                    "JEPQ": {"return_pct": 27.39, "diff_pct": 0.38},
                    "QYLD": {"return_pct": 22.26, "diff_pct": 5.51},
                },
            }
        ],
    }

    lines = _format_etf_comparison(comparison)

    assert any("동종 인컴 ETF 대비" in line for line in lines)
    assert any("QQQI는 나스닥 인컴/커버드콜 ETF" in line for line in lines)
    assert any("QQQ는 상승장 기회비용" in line for line in lines)


def test_fmt_price_supports_krw_for_korea_market_values():
    assert _fmt_price(3901.234, currency="KRW") == "₩3,901.23"


def test_fmt_index_value_hides_nan_for_korea_indices():
    assert _fmt_index_value(float("nan")) == "N/A"
    assert _fmt_index_value(None) == "N/A"
    assert _fmt_index_value(3901.234) == "3,901.23"


def test_select_watch_candidates_excludes_top_buy_duplicates():
    results = [
        {"ticker": "A", "total_score": 90, "signal": "Positive"},
        {"ticker": "B", "total_score": 80, "signal": "Neutral"},
        {"ticker": "C", "total_score": 30, "signal": "Warning"},
    ]
    top = _select_top_buy_candidates(results, limit=2)
    watch = _select_watch_candidates(results, limit=2, exclude_tickers={r["ticker"] for r in top})

    assert [r["ticker"] for r in top] == ["A", "B"]
    assert "A" not in [r["ticker"] for r in watch]
    assert "B" not in [r["ticker"] for r in watch]
    assert [r["ticker"] for r in watch] == ["C"]


def test_select_watch_candidates_prefers_real_risk_over_low_score_fillers():
    results = [
        {"ticker": "SAFE1", "total_score": 88, "signal": "Positive"},
        {"ticker": "SAFE2", "total_score": 76, "signal": "Neutral"},
        {"ticker": "RISK", "total_score": 44, "signal": "Neutral"},
        {"ticker": "WARN", "total_score": 70, "signal": "Warning"},
    ]

    watch = _select_watch_candidates(results, limit=5)

    assert [r["ticker"] for r in watch] == ["RISK", "WARN"]
    assert "SAFE1" not in [r["ticker"] for r in watch]
    assert "SAFE2" not in [r["ticker"] for r in watch]


def test_news_title_relevant_rejects_unrelated_global_news():
    assert _news_title_relevant("MSFT", "Microsoft launches new Copilot features")
    assert _news_title_relevant("005930.KS", "삼성전자 반도체 실적 개선 기대")
    assert not _news_title_relevant("MSFT", "Nvidia stock rises as AI demand grows")
    assert not _news_title_relevant("005930.KS", "현대차 미국 판매 증가")


# ── LLM overlay fact-guard 테스트 ─────────────────────────────────────────

def _sample_clean_data():
    return {
        "date": "2026-06-05",
        "market_summary": {"spy_change_pct": 1.23, "spy_price": 650.0, "nasdaq_change_pct": -0.5},
        "portfolio_summary": [
            {"ticker": "MSFT", "company": "Microsoft", "score": 82, "judgment": "분할매수 후보"},
            {"ticker": "QQQI", "company": "NEOS Nasdaq-100 High Income ETF", "score": 0, "judgment": "현금흐름 유지"},
        ],
        "nasdaq_top_buy": [{"ticker": "NVDA", "score": 88, "signal": "Positive"}],
        "nasdaq_warnings": [],
        "kospi_top_buy": [],
        "kospi_warnings": [],
    }


def test_llm_overlay_guard_accepts_only_source_numbers_and_tickers():
    text = "## LLM 애널리스트 코멘트\n### 오늘의 해석\n- MSFT는 82점, SPY 1.23% 수치 기준으로 확인 필요"

    assert _validate_llm_overlay(text, _sample_clean_data()) == []


def test_llm_overlay_guard_allows_negative_known_facts_and_korean_company_acronyms():
    data = _sample_clean_data()
    data["market_summary"]["spy_change_pct"] = -0.94
    data["market_summary"]["nasdaq_change_pct"] = -1.92
    data["kospi_warnings"] = [{"ticker": "373220.KS", "company": "LG에너지솔루션"}, {"ticker": "000660.KS", "company": "SK하이닉스"}]
    text = "## LLM 애널리스트 코멘트\n- SPY -0.94%, NASDAQ -1.92%, LG에너지솔루션과 SK하이닉스 확인 필요"

    assert _validate_llm_overlay(text, data) == []


def test_llm_overlay_guard_allows_compact_display_of_known_string_numbers():
    data = _sample_clean_data()
    data["market_summary"]["kospi"] = "8,160.59"
    data["kospi_top_buy"] = [{"ticker": "000660.KS", "summary": "1일 -9.92% 급락"}]
    text = "## LLM 애널리스트 코멘트\n- KOSPI 8,160 부근, 000660.KS는 -9.9% 급락 확인 필요"

    assert _validate_llm_overlay(text, data) == []


def test_llm_overlay_guard_rejects_new_numeric_claims():
    text = "## LLM 애널리스트 코멘트\n### 오늘의 해석\n- MSFT는 내일 10% 상승 가능"

    issues = _validate_llm_overlay(text, _sample_clean_data())

    assert issues
    assert "10" in issues[0]


def test_llm_overlay_guard_rejects_unknown_tickers():
    text = "## LLM 애널리스트 코멘트\n### 오늘의 해석\n- TSLA는 확인 필요"

    issues = _validate_llm_overlay(text, _sample_clean_data())

    assert issues
    assert "TSLA" in " ".join(issues)


def test_generate_llm_overlay_rejects_runner_hallucinations():
    def runner(cmd, **kwargs):
        return SimpleNamespace(returncode=0, stdout="## LLM 애널리스트 코멘트\n- TSLA 10% 상승 가능")

    overlay, status = _generate_llm_overlay(_sample_clean_data(), runner=runner)

    assert overlay is None
    assert "fact guard rejected" in status


def test_generate_llm_overlay_accepts_guarded_runner_output():
    def runner(cmd, **kwargs):
        return SimpleNamespace(returncode=0, stdout="## LLM 애널리스트 코멘트\n- MSFT는 82점 기준으로 확인 필요")

    overlay, status = _generate_llm_overlay(_sample_clean_data(), runner=runner)

    assert status == "ok"
    assert "MSFT" in overlay


def test_llm_overlay_prompt_includes_all_portfolio_items():
    data = _sample_clean_data()
    data["portfolio_summary"] = [
        {"ticker": f"T{i}", "score": i, "judgment": "관심 유지"} for i in range(20)
    ]
    digest = "뉴스 요약 " * 100  # 600 chars — well under 8000 cap

    prompt = _build_llm_overlay_prompt(data, digest)

    assert "### 오늘의 해석" in prompt
    assert "### 오늘 할 일" in prompt
    assert "### 리스크 확인" in prompt
    assert "### 추가 확인" in prompt
    # All 20 portfolio items must be present (no hard cap on portfolio)
    assert "T7" in prompt
    assert "T19" in prompt
    assert "수집 정보의 compact 전체 요약" in prompt


def test_llm_overlay_prompt_caps_source_digest():
    data = _sample_clean_data()
    digest = "X" * 10000

    prompt = _build_llm_overlay_prompt(data, digest)

    # Default cap is 8000 chars — the 10000-char digest must be truncated
    assert "X" * 8001 not in prompt
    assert "X" * 100 in prompt  # some digest content remains


def test_llm_overlay_mobile_lines_preserves_sections():
    overlay = """## LLM 애널리스트 코멘트
### 오늘의 해석
- MSFT는 82점 기준으로 확인 필요
### 오늘 할 일
- QQQI 현금흐름 유지 여부 확인
### 리스크 확인
- NASDAQ -0.5% 구간에서 위험 종목 확인
### 추가 확인
- 뉴스 원인은 확인 필요"""

    lines = _llm_overlay_mobile_lines(overlay)

    assert lines == [
        "🧠 오늘의 해석",
        "- MSFT는 82점 기준으로 확인 필요",
        "✅ 오늘 할 일",
        "- QQQI 현금흐름 유지 여부 확인",
        "⚠️ 리스크 확인",
        "- NASDAQ -0.5% 구간에서 위험 종목 확인",
        "🔎 추가 확인",
        "- 뉴스 원인은 확인 필요",
    ]


def test_llm_overlay_mobile_lines_falls_back_to_bullets():
    overlay = "## LLM 애널리스트 코멘트\n- MSFT는 82점 기준으로 확인 필요"

    assert _llm_overlay_mobile_lines(overlay) == ["- MSFT는 82점 기준으로 확인 필요"]


def test_generate_llm_overlay_uses_short_timeout_and_status():
    calls = []

    def runner(cmd, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(returncode=1, stderr="에러" * 200)

    overlay, status = _generate_llm_overlay(_sample_clean_data(), runner=runner)

    assert overlay is None
    assert calls[0]["timeout"] == 120
    assert len(status) < 180


# ── _build_llm_analysis_payload 테스트 ───────────────────────────────────────

def test_build_llm_analysis_payload_has_meta_token_estimate():
    data = _sample_clean_data()
    payload = _build_llm_analysis_payload(data, "test digest")

    meta = payload["_meta"]
    assert meta["char_count"] > 0
    assert meta["estimated_tokens"] > 0
    assert meta["estimated_tokens"] == math.ceil(meta["char_count"] / 3.7)
    assert "model" in meta
    assert "provider" in meta
    assert "section_sizes" in meta
    assert "list_cap" in meta
    assert "digest_chars_cap" in meta


def test_build_llm_analysis_payload_includes_all_portfolio():
    data = _sample_clean_data()
    data["portfolio_summary"] = [
        {"ticker": f"T{i}", "score": i * 5, "judgment": "관심 유지", "decision_v2": {}}
        for i in range(15)
    ]
    payload = _build_llm_analysis_payload(data)

    tickers = [item["ticker"] for item in payload["portfolio_summary"]]
    assert "T0" in tickers
    assert "T14" in tickers
    assert len(payload["portfolio_summary"]) == 15


def test_build_llm_analysis_payload_caps_digest():
    data = _sample_clean_data()
    long_digest = "A" * 10000

    payload = _build_llm_analysis_payload(data, long_digest)

    # Default cap: 8000 chars
    assert len(payload["source_digest"]) <= 8000
    assert payload["source_digest"].endswith("…")
    assert payload["_meta"]["section_sizes"]["source_digest"] <= 8000


def test_build_llm_analysis_payload_caps_list_by_env(monkeypatch):
    monkeypatch.setenv("INVESTMENT_REPORT_LLM_LIST_CAP", "3")
    data = _sample_clean_data()
    data["nasdaq_top_buy"] = [
        {"ticker": f"N{i}", "score": 70, "grade": "B", "signal": "Positive", "decision_v2": {}}
        for i in range(10)
    ]

    payload = _build_llm_analysis_payload(data)

    assert len(payload["nasdaq_top_buy"]) == 3
    assert payload["_meta"]["list_cap"] == 3


def test_build_llm_analysis_payload_meta_char_count_matches_payload():
    data = _sample_clean_data()
    payload = _build_llm_analysis_payload(data, "짧은 digest")

    meta = payload.pop("_meta")
    import json as _json
    actual_chars = len(_json.dumps(payload, ensure_ascii=False, default=str))
    assert meta["char_count"] == actual_chars


def test_build_llm_analysis_payload_includes_perf_section():
    data = _sample_clean_data()
    data["performance"] = {"1m_return_pct": 3.5, "ytd_return_pct": 12.0}
    data["barbell_phase"] = "Phase-2"

    payload = _build_llm_analysis_payload(data)

    assert "performance" in payload
    assert payload["performance"]["barbell_phase"] == "Phase-2"
    assert payload["performance"]["1m_return_pct"] == 3.5


def test_build_llm_analysis_payload_no_meta_in_llm_prompt():
    data = _sample_clean_data()
    prompt = _build_llm_overlay_prompt(data, "digest")

    # _meta should not appear in the prompt sent to LLM
    assert "_meta" not in prompt
    assert "char_count" not in prompt


# ── LLM overlay 관측 계기 (LLM-3) ────────────────────────────────────────────

def test_llm_overlay_log_and_stats():
    """overlay 결과 store 축적 → 최근 30일 성공/거부 집계 (conftest 가 tmp DB 격리)."""
    from investment_report import _log_llm_overlay, llm_overlay_stats

    _log_llm_overlay("ok", {"estimated_tokens": 1234})
    _log_llm_overlay("fact guard rejected output: unknown numeric claims: 42", {})
    _log_llm_overlay("call failed: non-zero exit", {})
    _log_llm_overlay("disabled", {})

    stats = llm_overlay_stats(days=30)
    assert stats is not None
    assert stats["n"] >= 4
    assert stats["ok"] >= 1
    assert stats["guard_rejected"] >= 1
    assert stats["call_failed"] >= 1
    assert stats["disabled"] >= 1
    assert 0.0 <= stats["ok_rate"] <= 1.0
