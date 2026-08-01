#!/usr/bin/env python3
"""test_us_mock_track.py — US 모의 리밸런스 순수함수 (무네트워크)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "crons"))

import us_mock_track as T


def _orders(plan):
    return {(o["symbol"], o["side"]): o["qty"] for o in plan}


def test_plan_buys_top_n_whole_shares():
    sigs = [{"ticker": "A", "price": 100, "policy_score": 0.9},
            {"ticker": "B", "price": 50, "policy_score": 0.8},
            {"ticker": "C", "price": 50, "policy_score": 0.1}]
    o = _orders(T.plan_rebalance(sigs, {}, budget_usd=2000, max_positions=2))
    assert o.get(("A", "buy")) == 10 and o.get(("B", "buy")) == 20    # per=1000, 정수주
    assert ("C", "buy") not in o                                       # 컷오프 밖


def test_plan_sells_off_target_first():
    sigs = [{"ticker": "A", "price": 100, "policy_score": 0.9}]
    o = _orders(T.plan_rebalance(sigs, {"X": {"shares": 5}}, budget_usd=1000, max_positions=1))
    assert o.get(("X", "sell")) == 5 and o.get(("A", "buy")) == 10


def test_plan_cash_cap():
    sigs = [{"ticker": "A", "price": 100, "policy_score": 0.9}]
    o = _orders(T.plan_rebalance(sigs, {}, budget_usd=10000, max_positions=1, cash_usd=300))
    assert o.get(("A", "buy")) == 3                                    # 현금 $300 한도 (버퍼 기본 1.0)


def test_plan_cash_buffer_leaves_headroom():
    """cash_buffer<1 이면 주문가능금액의 일부만 사용 — '주문가능금액 부족' 거부 방지."""
    sigs = [{"ticker": "A", "price": 100, "policy_score": 0.9}]
    # 현금 $1000, 버퍼 0.9 → $900 사용 → 9주($900), 풀(10주) 아님 → 실집행 여유 확보
    o = _orders(T.plan_rebalance(sigs, {}, budget_usd=10000, max_positions=1,
                                 cash_usd=1000, cash_buffer=0.9))
    assert o.get(("A", "buy")) == 9


def test_plan_cash_usd_zero_still_caps_buys():
    """cash_usd=0 은 '현금 없음'(캡 0)이지 '정보 없음'(캡 미적용)이 아니다.
    (2026-07-24 회귀방지: 예전엔 cash_usd>0 도 같이 요구해서 정확히 0일 때 캡이
    통째로 빠지고 예산 기준 풀사이즈 매수가 그대로 나갔음 — 실계좌에서 실제로 발생)."""
    sigs = [{"ticker": "A", "price": 100, "policy_score": 0.9}]
    o = _orders(T.plan_rebalance(sigs, {}, budget_usd=10000, max_positions=1, cash_usd=0))
    assert ("A", "buy") not in o


def test_plan_min_cash_usd_skips_buys():
    """가용현금(cash_usd*cash_buffer)이 min_cash_usd 밑이면 매수 자체를 생성 안 함
    (2026-07-24: '주문가능금액 부족' 거부의 절반이 여기서 나옴 — 소액 매수 시도 API 낭비 방지)."""
    sigs = [{"ticker": "A", "price": 100, "policy_score": 0.9}]
    o = _orders(T.plan_rebalance(sigs, {}, budget_usd=10000, max_positions=1,
                                 cash_usd=300, min_cash_usd=500))
    assert ("A", "buy") not in o                                       # 300 < 500 → 매수 생성 안 함
    o2 = _orders(T.plan_rebalance(sigs, {}, budget_usd=10000, max_positions=1,
                                  cash_usd=600, min_cash_usd=500))
    assert o2.get(("A", "buy")) == 6                                    # 600 >= 500 → 정상 사이징


def test_plan_min_cash_usd_does_not_block_sells():
    """min_cash_usd 는 매수만 막는다 — 타깃이탈 매도(현금확보)는 항상 그대로 나가야 함."""
    sigs = [{"ticker": "A", "price": 100, "policy_score": 0.9}]
    o = _orders(T.plan_rebalance(sigs, {"X": {"shares": 5}}, budget_usd=10000, max_positions=1,
                                 cash_usd=0, min_cash_usd=500))
    assert o.get(("X", "sell")) == 5
    assert ("A", "buy") not in o


def test_plan_rebal_band_skips_small_adjust():
    """무거래 밴드: 목표 대비 band 이내 보유종목은 조정 skip (회전율↓)."""
    sigs = [{"ticker": "A", "price": 100, "policy_score": 0.9}]
    pos = {"A": {"shares": 9}}   # 목표 10주(=$1000) 대비 -10%, band 0.25 이내
    banded = _orders(T.plan_rebalance(sigs, pos, budget_usd=1000, max_positions=1, rebal_band=0.25))
    assert ("A", "buy") not in banded and ("A", "sell") not in banded   # 무거래
    nob = _orders(T.plan_rebalance(sigs, pos, budget_usd=1000, max_positions=1, rebal_band=0.0))
    assert nob.get(("A", "buy")) == 1                                    # 밴드 없으면 9→10


def test_plan_exit_buffer_keeps_boundary():
    """히스테리시스: 보유종목이 top-(N+buffer) 안이면 매도 안 함(경계 flip 방지)."""
    sigs = [{"ticker": "A", "price": 100, "policy_score": 0.9},
            {"ticker": "B", "price": 100, "policy_score": 0.8},
            {"ticker": "C", "price": 100, "policy_score": 0.7}]   # C = rank3
    pos = {"C": {"shares": 5}}
    kept = _orders(T.plan_rebalance(sigs, pos, budget_usd=1000, max_positions=2, exit_buffer=2))
    assert ("C", "sell") not in kept                                    # top-4 안 → 유지
    nob = _orders(T.plan_rebalance(sigs, pos, budget_usd=1000, max_positions=2, exit_buffer=0))
    assert nob.get(("C", "sell")) == 5                                  # top-2 밖 → 전량매도


def test_plan_budget_zero_no_buys():
    sigs = [{"ticker": "A", "price": 100, "policy_score": 0.9}]
    assert T.plan_rebalance(sigs, {}, budget_usd=0, max_positions=1) == []


def test_classify_kind():
    assert T._classify_kind("buy", 3, 0) == "편입"
    assert T._classify_kind("buy", 3, 5) == "증액"
    assert T._classify_kind("sell", 5, 5) == "퇴출"
    assert T._classify_kind("sell", 2, 5) == "감액"


def test_quote_fn_sizes_at_live_ask():
    """라이브 호가(ask) 주입 시 실제 체결가로 사이징 — 신호가보다 비싸면 주수↓."""
    sigs = [{"ticker": "A", "price": 100, "policy_score": 0.9},
            {"ticker": "B", "price": 100, "policy_score": 0.8}]
    # A 는 ask $200(신호가의 2배) → per $1000 에 5주. B 는 호가 없음(None) → 신호가 100 → 10주.
    qfn = lambda sym, side: 200.0 if sym == "A" else None
    o = _orders(T.plan_rebalance(sigs, {}, budget_usd=2000, max_positions=2, quote_fn=qfn))
    assert o.get(("A", "buy")) == 5 and o.get(("B", "buy")) == 10


def test_quote_fn_none_is_baseline():
    """quote_fn=None 이면 기존 정적 사이징과 동일(회귀 보장)."""
    sigs = [{"ticker": "A", "price": 100, "policy_score": 0.9}]
    base = _orders(T.plan_rebalance(sigs, {}, budget_usd=1000, max_positions=1))
    none = _orders(T.plan_rebalance(sigs, {}, budget_usd=1000, max_positions=1, quote_fn=None))
    assert base == none == {("A", "buy"): 10}


def test_compute_us_signals_attaches_momentum_overlay_fields(monkeypatch):
    import pandas as pd
    import us_mock_track as ut
    from ml import ranker, us_policy
    import providers.market_data as md
    from providers import earnings_data, news_labels

    monkeypatch.setenv("US_MOCK_MOMENTUM_OVERLAY_ENABLED", "true")
    close = pd.Series([100 + i for i in range(260)], index=pd.bdate_range("2025-01-02", periods=260))
    monkeypatch.setattr(ut, "_safe_earnings", lambda tk: {"per": 20, "pbr": 2, "roe": 15, "confidence": 80})
    monkeypatch.setattr(ut, "_safe_signals", lambda tk: {"price_info": {"current_price": 100, "1mo_change_pct": 7}})
    monkeypatch.setattr(ut, "_safe_fund", lambda tk: {"confidence": 80})
    monkeypatch.setattr(md, "_history_cached", lambda *a, **k: pd.DataFrame({"Close": close}))
    monkeypatch.setattr(earnings_data, "earnings_history",
                        lambda tk, limit=4: [{"date": "2026-07-01", "surprise_pct": 6.0}])
    monkeypatch.setattr(news_labels, "news_axis", lambda tk: None, raising=False)
    monkeypatch.setattr(ut, "load_ohlc_close_series", lambda *a, **k: close)
    monkeypatch.setattr(ut, "_overlay_regime", lambda: ("risk_on", 0.9))
    monkeypatch.setattr(ut, "_overlay_fresh", lambda *a, **k: True)
    monkeypatch.setattr(ranker, "scores_by_ticker", lambda tks: {"AAPL": 1.0}, raising=False)
    monkeypatch.setattr(us_policy, "load_params", lambda: {})
    monkeypatch.setattr(us_policy, "score", lambda feats, params=None: 0.77)
    monkeypatch.setattr(ut, "score_momentum_overlay",
                        lambda base, feats, **kw: {
                            "base_score": base, "momentum_score": 0.94, "selection_score": 0.98,
                            "momentum_tilt": 0.21, "momentum_multiplier": 1.18,
                            "overlay_active": True, "momentum_state": "strong",
                            "reason_codes": ["regime:risk_on", "state:strong"],
                            "momentum_reason_codes": ["regime:risk_on", "state:strong"],
                            "market": "us", "regime": "risk_on", "overlay_weight": 0.50,
                        })

    sigs = ut.compute_us_signals(["AAPL"])

    assert len(sigs) == 1
    sig = sigs[0]
    assert sig["overlay_active"] is True
    assert sig["momentum_state"] == "strong"
    assert sig["selection_score"] == 0.98
    assert sig["momentum_multiplier"] == 1.18
    assert sig["base_score"] == 0.77 and sig["policy_score"] == 0.77


def test_universe_includes_leveraged_etfs_by_default(monkeypatch):
    monkeypatch.delenv("US_MOCK_UNIVERSE", raising=False)
    monkeypatch.delenv("US_MOCK_INCLUDE_LEVERAGE", raising=False)
    monkeypatch.delenv("US_MOCK_INCLUDE_SINGLE_LEVERAGE", raising=False)
    u = T._universe()
    for sym in ("QLD", "TQQQ", "SQQQ", "SOXL", "SSO", "SOXS", "NVDL", "TSLL", "MSFU"):
        assert sym in u
        assert T.is_leverage_etf(sym) is True


def test_universe_can_disable_leveraged_etfs(monkeypatch):
    monkeypatch.delenv("US_MOCK_UNIVERSE", raising=False)
    monkeypatch.setenv("US_MOCK_INCLUDE_LEVERAGE", "false")
    u = T._universe()
    assert "TQQQ" not in u and "SOXS" not in u and "NVDL" not in u


def test_leverage_universe_can_disable_single_stock_leverage(monkeypatch):
    monkeypatch.delenv("US_MOCK_LEVERAGE_UNIVERSE", raising=False)
    monkeypatch.delenv("US_MOCK_SINGLE_LEVERAGE_UNIVERSE", raising=False)
    monkeypatch.setenv("US_MOCK_INCLUDE_SINGLE_LEVERAGE", "false")
    u = T.leverage_universe()
    assert "TQQQ" in u and "SOXS" in u
    assert "NVDL" not in u and "TSLL" not in u


def test_leverage_universe_accepts_custom_single_stock_list(monkeypatch):
    monkeypatch.setenv("US_MOCK_LEVERAGE_UNIVERSE", "QLD")
    monkeypatch.setenv("US_MOCK_INCLUDE_SINGLE_LEVERAGE", "true")
    monkeypatch.setenv("US_MOCK_SINGLE_LEVERAGE_UNIVERSE", "NVDL,TSLL,NVDL")
    assert T.leverage_universe() == ["QLD", "NVDL", "TSLL"]


def test_custom_active_leverage_symbol_gets_generic_meta():
    assert T._active_leverage_meta("ZZZL", {"ZZZL"})["label"] == "레버리지 ETF"
    assert T._active_leverage_meta("ZZZL", set()) is None


def test_plan_caps_leveraged_positions_and_budget():
    sigs = [
        {"ticker": "NVDL", "price": 100, "policy_score": 0.99},
        {"ticker": "TQQQ", "price": 100, "policy_score": 0.98},
        {"ticker": "TSLL", "price": 100, "policy_score": 0.97},
        {"ticker": "A", "price": 100, "policy_score": 0.80},
        {"ticker": "B", "price": 100, "policy_score": 0.70},
        {"ticker": "C", "price": 100, "policy_score": 0.60},
    ]
    o = _orders(T.plan_rebalance(
        sigs, {}, budget_usd=100_000, max_positions=5,
        leverage_symbols={"NVDL", "TQQQ", "TSLL"},
        leverage_max_positions=2,
        leverage_budget_frac=0.30,
    ))
    assert o.get(("NVDL", "buy")) == 150
    assert o.get(("TQQQ", "buy")) == 150
    assert ("TSLL", "buy") not in o
    assert o.get(("A", "buy")) == 233
    assert o.get(("B", "buy")) == 233
    assert o.get(("C", "buy")) == 233


def test_plan_rebalances_with_momentum_multiplier_partial_trim():
    sigs = [
        {"ticker": "A", "price": 100, "policy_score": 0.90,
         "selection_score": 0.96, "momentum_multiplier": 1.20},
        {"ticker": "B", "price": 100, "policy_score": 0.85,
         "selection_score": 0.78, "momentum_multiplier": 0.50},
    ]
    orders = T.plan_rebalance(
        sigs,
        {"B": {"shares": 20}},
        budget_usd=2_000,
        max_positions=2,
        target_multipliers={"A": 1.20, "B": 0.50},
    )
    assert any(o["symbol"] == "A" and o["side"] == "buy" for o in orders)
    assert any(o["symbol"] == "B" and o["side"] == "sell" for o in orders)


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))


# ── ★Tier3 구조레버 QLD 슬리브 (게이트·사이징 — 모의 한정) ────────────────────

def test_load_lev_shadow_go_fresh(tmp_path):
    import json
    from datetime import datetime
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"reco_lev": 1.3, "verdict": "GO",
                             "_meta": {"at": datetime.now().strftime("%Y-%m-%d %H:%M")}}))
    assert T.load_lev_shadow(str(p)) == 1.3


def test_load_lev_shadow_rejects_nogo_stale_missing(tmp_path):
    import json
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"reco_lev": 1.3, "verdict": "NO-GO",
                             "_meta": {"at": "2026-01-01 00:00"}}))
    assert T.load_lev_shadow(str(p)) is None                       # NO-GO
    p.write_text(json.dumps({"reco_lev": 1.3, "verdict": "GO",
                             "_meta": {"at": "2020-01-01 00:00"}}))
    assert T.load_lev_shadow(str(p)) is None                       # stale(>21일)
    assert T.load_lev_shadow(str(tmp_path / "none.json")) is None  # 파일 없음


def test_sleeve_plan_sizes_l_minus_one():
    frac, orders = T.sleeve_plan(1.3, 100_000, {}, 50.0, symbol="QLD")
    assert frac == 0.3
    assert orders == [{"symbol": "QLD", "side": "buy", "qty": 600,
                       "reason": "Tier3 구조레버 슬리브 ×1.30", "sleeve": True}]


def test_sleeve_plan_liquidates_when_gate_off():
    frac, orders = T.sleeve_plan(None, 100_000, {"QLD": {"shares": 100}}, 50.0, symbol="QLD")
    assert frac == 0.0
    assert orders[0]["side"] == "sell" and orders[0]["qty"] == 100


def test_sleeve_plan_band_and_price_guard():
    # 밴드 내(목표 $30k vs 보유 580주×$50=$29k) → 무거래
    frac, orders = T.sleeve_plan(1.3, 100_000, {"QLD": {"shares": 580}}, 50.0,
                                 band=0.25, symbol="QLD")
    assert frac == 0.3 and orders == []
    # 가격 불명 → 주문 보류하되 비중은 유지(예산 과투자 방지)
    frac, orders = T.sleeve_plan(1.3, 100_000, {}, None, symbol="QLD")
    assert frac == 0.3 and orders == []


def test_sleeve_plan_max_frac_clamp():
    frac, _ = T.sleeve_plan(2.5, 100_000, {}, 50.0, symbol="QLD")   # 폭주 reco
    assert frac == T.LEV_SLEEVE_MAX_FRAC


def test_leverage_sleeve_mode_defaults_to_shadow_and_legacy_bool_enables_paper(monkeypatch):
    monkeypatch.delenv("US_MOCK_LEV_SLEEVE_MODE", raising=False)
    monkeypatch.delenv("US_MOCK_LEV_SLEEVE", raising=False)
    assert T.leverage_sleeve_mode() == "shadow"
    assert T.leverage_sleeve_paper_enabled() is False

    monkeypatch.setenv("US_MOCK_LEV_SLEEVE", "true")
    assert T.leverage_sleeve_mode() == "paper"
    assert T.leverage_sleeve_paper_enabled() is True


def test_leverage_sleeve_mode_accepts_off_shadow_paper(monkeypatch):
    for mode in ("off", "shadow", "paper"):
        monkeypatch.setenv("US_MOCK_LEV_SLEEVE_MODE", mode)
        monkeypatch.delenv("US_MOCK_LEV_SLEEVE", raising=False)
        assert T.leverage_sleeve_mode() == mode

    monkeypatch.setenv("US_MOCK_LEV_SLEEVE_MODE", "bad-value")
    assert T.leverage_sleeve_mode() == "shadow"
