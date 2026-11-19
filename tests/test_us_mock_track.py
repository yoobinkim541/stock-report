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
    """타깃이탈 매도는 나가되, 주문 전 보유가 한도를 채우면 대체 신규편입은 하지 않는다.
    현금이 있어도 매도 체결과 매도대금을 가정하지 않는다."""
    sigs = [{"ticker": "A", "price": 100, "policy_score": 0.9}]
    o = _orders(T.plan_rebalance(sigs, {"X": {"shares": 5}}, budget_usd=1000, max_positions=1,
                                 cash_usd=1000))
    assert o.get(("X", "sell")) == 5
    assert ("A", "buy") not in o


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


def test_paper_sleeve_cannot_buy_more_than_confirmed_cash():
    frac, orders = T.sleeve_plan(1.3, 100_000, {}, 50., buy_cash=149.)
    assert frac == 0.3 and _orders(orders) == {("QLD", "buy"): 2}
    _, orders = T.sleeve_plan(None, 100_000, {"QLD": {"shares": 5}}, 50., buy_cash=0.)
    assert _orders(orders) == {("QLD", "sell"): 5}


def test_dry_run_stock_and_sleeve_share_one_cash_balance(monkeypatch, caplog):
    monkeypatch.setenv("US_MOCK_LEV_SLEEVE_MODE", "paper")
    monkeypatch.setenv("MOCK_SWEET_SPOT", "0")   # 주식+슬리브 경로 (스위트스팟 ETF 경로 아님)
    for key, value in {"SEED_USD": 1000., "CASH_BUFFER": 0.35, "MIN_CASH_USD": 0.,
                       "INVEST": 1., "TRANCHES": 1, "LEV_SLEEVE_SYMBOL": "QLD"}.items():
        monkeypatch.setattr(T, key, value)
    monkeypatch.setattr(T, "_rt_best", lambda *args: 50.)
    monkeypatch.setattr(T, "load_lev_shadow", lambda: 1.3)
    monkeypatch.setattr(T, "compute_us_signals", lambda: [{"ticker": "A", "price": 50.}])
    with caplog.at_level("INFO"):
        assert T.main(["--dry-run"]) == 0
    orders = [r.args[:3] for r in caplog.records if "[DRY] " in r.msg and r.args]
    assert orders == [("buy", "QLD", 6), ("buy", "A", 1)]  # $300 + $50, not $300 + $350


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


def test_order_blocker_classifies():
    """감사 #27 — KR(kiwoom_mock_track._order_blocker)엔 있던 계좌/시장 레벨
    실패 분류가 US 미러엔 없어, 전 주문 공통 차단인지 개별 문제인지 구분이
    안 됐던 문제."""
    assert T._order_blocker("계좌# 미설정(fail-closed)") == "account"
    assert T._order_blocker("토큰 없음") == "account"
    assert T._order_blocker("장운영시간이 아닙니다") == "market"
    assert T._order_blocker("수량 0 이하") is None            # 개별 주문 문제 → 중단 안 함
    assert T._order_blocker("지정가 필요(해외 정수주)") is None
    assert T._order_blocker(None) is None


def test_notify_shows_single_account_blocker_message_instead_of_spamming(monkeypatch):
    """계좌 레벨 차단이면 개별 실패를 나열하지 않고 명확 안내 1건만 보내야 한다."""
    sent = {}

    class _FakeNotify:
        @staticmethod
        def send_telegram(text, **kwargs):
            sent["text"] = text

    monkeypatch.setitem(sys.modules, "notify", _FakeNotify)

    results = [
        {"symbol": "MSFT", "qty": 1, "kind": "편입", "ok": False, "msg": "계좌# 미설정(fail-closed)"},
        {"symbol": "NVDA", "qty": 1, "kind": "편입", "ok": False, "msg": "계좌# 미설정(fail-closed)"},
    ]
    T._notify(1000.0, results)

    text = sent["text"]
    assert "계좌 문제" in text or "계좌#" in text
    assert "MSFT" not in text and "NVDA" not in text


def test_a_fix_exec_notify_reports_llm_review_fallback(monkeypatch):
    """A-11: guarded_apply 검토 실패로 매수를 보류했다면 알림에 fallback 사실이 남아야 한다."""
    from lib import mock_llm_execution as llm_exec

    sent = {}

    class _FakeNotify:
        @staticmethod
        def send_telegram(text, **kwargs):
            sent["text"] = text

    monkeypatch.setitem(sys.modules, "notify", _FakeNotify)
    plan = [{"symbol": "TSLA", "side": "sell", "qty": 4}, {"symbol": "AAPL", "side": "buy", "qty": 10}]
    new_plan, applied = llm_exec.apply_reviews(plan, {}, apply_mode="guarded_apply",
                                               review_status="call failed: fake unavailable")
    record = llm_exec.review_execution_record(apply_mode="guarded_apply",
                                              review_status="call failed: fake unavailable",
                                              applied=applied)
    results = [{**o, "kind": "퇴출", "ok": True} for o in new_plan]

    T._notify(1000.0, results, llm_note=llm_exec.fallback_note(record))

    text = sent["text"]
    assert "TSLA" in text and "AAPL" not in text
    assert "LLM 주문검토 fallback(call failed: fake unavailable)" in text
    assert "미검증 매수 1건 보류" in text and "매도 1건 검토 없이 진행" in text


def _fake_history(monkeypatch):
    import store
    rows: dict[str, list] = {}

    def append(name, item, **kwargs):
        rows.setdefault(name, []).append(dict(item))
        return len(rows[name])

    monkeypatch.setattr(store, "append", append)
    monkeypatch.setattr(store, "all", lambda name, **kwargs: list(rows.get(name, [])))
    return rows


def test_a_exec_fix2_broker_rejected_balance_holds_orders_and_records_failure(monkeypatch):
    """KIS 가 '모의투자 신청내역' 으로 거부(rt_cd≠0)하면 시드 $100,000 으로 NAV 를 메워
    snapshot·주문을 내지 않고, 실패 이벤트만 남긴 뒤 주문 보류한다."""
    rows = _fake_history(monkeypatch)
    monkeypatch.setattr(T.kis_mock, "is_enabled", lambda: True)
    monkeypatch.setattr(T.kis_mock, "get_balance", lambda: {
        "ok": True, "positions": {}, "pos_value": 0.0, "cash_usd": None,
        "nav": None, "raw": {"rt_cd": "1", "msg1": "모의투자 신청내역을 확인하세요"}})

    def _no_signals():
        raise AssertionError("잔고 실패 시 신호·주문 단계로 진행하면 안 된다")

    monkeypatch.setattr(T, "compute_us_signals", _no_signals)
    monkeypatch.setattr(T.kis_mock, "place_order", lambda *a, **k: _no_signals())

    assert T.main([]) == 1

    hist = rows["us_mock_history"]
    assert [r["kind"] for r in hist] == ["snapshot_failed"]
    assert hist[0]["reason"] == "broker_rejected: 모의투자 신청내역을 확인하세요"
    assert hist[0]["nav"] is None


def test_a_exec_fix2_unknown_nav_with_holdings_holds_orders(monkeypatch):
    """00시대 환율 미고시로 NAV 미확인(보유는 있음) → 보유평가액/시드로 예산을 잡아 주문하지 않고,
    반쪽 NAV 를 시계열에 넣지 않는다."""
    rows = _fake_history(monkeypatch)
    rows["us_mock_history"] = [{"date": "2026-08-24 06:40", "kind": "snapshot", "nav": 255_000.0,
                                "cash": 120_000.0, "positions": 5}]
    monkeypatch.setattr(T.kis_mock, "is_enabled", lambda: True)
    calls = []

    def _balance():
        calls.append(1)
        return {"ok": True, "positions": {"AAPL": {"shares": 10, "value": 128_000.0}},
                "pos_value": 128_000.0, "cash_usd": None, "nav": None, "raw": {"rt_cd": "0"}}

    monkeypatch.setattr(T.kis_mock, "get_balance", _balance)
    sleeps = []
    monkeypatch.setattr(T.time, "sleep", sleeps.append)

    def _no_signals():
        raise AssertionError("NAV 미확인 시 신호·주문 단계로 진행하면 안 된다")

    monkeypatch.setattr(T, "compute_us_signals", _no_signals)

    assert T.main([]) == 1
    assert [r["kind"] for r in rows["us_mock_history"]] == ["snapshot", "snapshot_failed"]
    assert rows["us_mock_history"][-1]["reason"] == "nav_missing"
    assert len(calls) == T.BALANCE_RETRIES + 1                 # 재조회 후에도 미확인이면 보류
    assert sleeps == [T.BALANCE_RETRY_S] * T.BALANCE_RETRIES


def _ok_balance(nav=250_000.0, cash=250_000.0, positions=None):
    return {"ok": True, "positions": positions or {}, "pos_value": nav - cash, "cash_usd": cash,
            "nav": nav, "fx": 1400.0, "raw": {"rt_cd": "0"}}


def test_fetch_balance_retries_kis_delay_then_succeeds(monkeypatch):
    """2026-11-19 00시: 체결기준현재잔고가 '모의투자 서비스가 지연' 으로 실패 → 그날 리밸런스가
    통째로 빠졌다. 일시 장애는 재조회해서 성공한 잔고를 써야 한다."""
    delayed = {"ok": False, "positions": {}, "nav": None,
               "raw": {"rt_cd": "1", "msg1": "모의투자 서비스가 지연되고 있습니다. 잠시후 재시도 바랍니다."}}
    seq = [delayed, delayed, _ok_balance()]
    monkeypatch.setattr(T.kis_mock, "get_balance", lambda: seq.pop(0))
    sleeps = []
    monkeypatch.setattr(T.time, "sleep", sleeps.append)

    bal, problem = T._fetch_balance(retries=3, wait_s=60)

    assert problem is None and bal["nav"] == 250_000.0
    assert sleeps == [60, 60]


def test_fetch_balance_does_not_retry_account_rejection(monkeypatch):
    rejected = {"ok": False, "positions": {}, "nav": None,
                "raw": {"rt_cd": "1", "msg1": "모의투자 신청내역이 없습니다"}}
    calls = []
    monkeypatch.setattr(T.kis_mock, "get_balance", lambda: calls.append(1) or rejected)
    monkeypatch.setattr(T.time, "sleep", lambda s: (_ for _ in ()).throw(AssertionError("no retry")))

    _, problem = T._fetch_balance(retries=3, wait_s=60)

    assert problem.startswith("broker_rejected") and len(calls) == 1


class _FakeLedger:
    decisions: list = []

    def __init__(self, surface, *a, **k):
        self.surface = surface

    def log_decision(self, row):
        _FakeLedger.decisions.append((self.surface, dict(row)))

    def __getattr__(self, name):
        return lambda *a, **k: None


def test_sweet_spot_etf_path_places_cash_capped_orders_with_real_basis(monkeypatch):
    """스위트스팟 ETF 경로가 budget 미정의로 주문 전에 죽던 버그 회귀 + 현금 상한 + 지정가 여유 +
    실제 근거 기록. 바구니 밖 보유(QQQ)는 팔고, 매도 대금은 매수 현금에 세지 않는다."""
    import pandas as pd
    import ml.adaptive
    import ml.data_pipeline

    rows = _fake_history(monkeypatch)
    monkeypatch.setenv("MOCK_SWEET_SPOT", "1")
    monkeypatch.setenv("MOCK_ORDER_LLM_ENABLED", "0")
    monkeypatch.setattr(T.kis_mock, "is_enabled", lambda: True)
    held = {"QQQ": {"shares": 250, "value": 150_000.0}}
    monkeypatch.setattr(T.kis_mock, "get_balance", lambda: _ok_balance(cash=100_000.0, positions=held))
    monkeypatch.setattr(T, "compute_us_signals", lambda: [])
    monkeypatch.setattr(T, "_rt_best", lambda sym, side: None)
    monkeypatch.setattr(T.kis_mock, "get_price", lambda sym: {"SGOV": 100.0, "GLD": 400.0, "QQQ": 600.0}.get(sym))
    placed = []
    monkeypatch.setattr(T.kis_mock, "place_order",
                        lambda sym, qty, side, price=None: placed.append((sym, side, qty, price)) or {"ok": True})
    monkeypatch.setattr(T.trade_events, "record_trade", lambda **k: None)
    monkeypatch.setattr(T.time, "sleep", lambda s: None)
    notes = {}
    monkeypatch.setattr(T, "_notify", lambda nav, results, llm_note=None, basis=None: notes.update(basis=basis))
    _FakeLedger.decisions = []
    monkeypatch.setattr(ml.adaptive, "Ledger", _FakeLedger)

    idx = pd.bdate_range("2025-01-01", periods=280)
    down = pd.Series(range(380, 100, -1), index=idx, dtype=float)     # 하락 추세 → 위험자산 0
    flat = lambda v: pd.Series(v, index=idx, dtype=float)
    frames = {"QQQ": down, "QLD": down, "SGOV": flat(100.0), "GLD": flat(400.0),
              "^VIX": flat(20.0), "^VIX3M": flat(21.0)}
    monkeypatch.setattr(ml.data_pipeline, "fetch_prices",
                        lambda tickers, days=400: {t: pd.DataFrame({"Close": frames[t]}) for t in tickers})

    assert T.main([]) == 0

    buys = {sym: qty * px for sym, side, qty, px in placed if side == "buy"}
    assert set(buys) == {"SGOV", "GLD"}
    assert sum(buys.values()) <= 100_000                                # 매수는 USD 현금 안에서
    assert ("QQQ", "sell", 250, 597.0) in placed                        # 매도 지정가 = 현재가 −0.5%
    assert {px for sym, side, qty, px in placed if sym == "SGOV"} == {100.5}   # 매수 지정가 +0.5%
    assert notes["basis"] and "SGOV" in notes["basis"]
    us = [row for surface, row in _FakeLedger.decisions if surface == "us_mock"]
    assert len(us) == 3
    assert all((r.get("selection_basis") or "").startswith(notes["basis"]) for r in us)
    assert rows["us_mock_history"][-1]["kind"] == "snapshot"


def test_plan_residual_holdings_block_new_entries():
    """exit buffer 잔존 + 청산 예정이 주문 전 슬롯을 채우면 신규편입은 없다.
    3분할로 매도가 부분만 나가도 슬롯은 비지 않는다."""
    from lib.tranche import plan_tranches
    sigs = [
        {"ticker": "A", "price": 100, "policy_score": 0.9},
        {"ticker": "B", "price": 100, "policy_score": 0.8},
        {"ticker": "C", "price": 100, "policy_score": 0.7},
    ]
    pos = {"C": {"shares": 5}, "F": {"shares": 30}}
    plan = T.plan_rebalance(sigs, pos, budget_usd=10_000, max_positions=2,
                            cash_usd=10_000, exit_buffer=2)
    o = _orders(plan)
    assert o.get(("F", "sell")) == 30
    assert ("C", "sell") not in o
    assert ("A", "buy") not in o and ("B", "buy") not in o
    partial = plan_tranches(plan, 10_000 / 2, lambda s: 100, 3, id_key="symbol")
    assert not any(x["side"] == "buy" for x in partial)
    assert any(x["symbol"] == "F" and x["side"] == "sell" and x["qty"] < 30 for x in partial)


def test_plan_open_slot_admits_only_best_new_entry():
    """빈 슬롯은 최상위 신규 1개만. 버퍼 보유는 유지하고 차순위 신규는 넣지 않는다."""
    sigs = [
        {"ticker": "A", "price": 100, "policy_score": 0.9},
        {"ticker": "B", "price": 100, "policy_score": 0.8},
        {"ticker": "C", "price": 100, "policy_score": 0.7},
    ]
    o = _orders(T.plan_rebalance(sigs, {"C": {"shares": 5}}, budget_usd=10_000,
                                 max_positions=2, cash_usd=10_000, exit_buffer=2))
    assert o.get(("A", "buy")) == 50
    assert ("B", "buy") not in o
    assert ("C", "sell") not in o


def test_plan_preorder_leverage_hold_blocks_new_leverage():
    """청산 예정 레버리지도 주문 전 한도를 점유한다. 주식 신규는 빈 슬롯만큼 허용."""
    sigs = [
        {"ticker": "NVDL", "price": 100, "policy_score": 0.99},
        {"ticker": "A", "price": 100, "policy_score": 0.80},
        {"ticker": "B", "price": 100, "policy_score": 0.70},
    ]
    o = _orders(T.plan_rebalance(
        sigs, {"SOXL": {"shares": 10}},
        budget_usd=10_000, max_positions=3, cash_usd=10_000,
        leverage_symbols={"NVDL", "SOXL"}, leverage_max_positions=1,
    ))
    assert o.get(("SOXL", "sell")) == 10
    assert ("NVDL", "buy") not in o
    assert o.get(("A", "buy")) == 33 and o.get(("B", "buy")) == 33


def test_stock_book_counts_sleeve_symbol_unless_paper_owns_it():
    pos = {"QLD": {"shares": 4}, "A": {"shares": 1}}
    assert set(T.stock_book_positions(pos, "shadow", "QLD")) == {"QLD", "A"}
    assert set(T.stock_book_positions(pos, "off", "QLD")) == {"QLD", "A"}
    assert set(T.stock_book_positions(pos, "paper", "QLD")) == {"A"}
