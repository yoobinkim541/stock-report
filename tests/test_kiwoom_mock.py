#!/usr/bin/env python3
"""
test_kiwoom_mock.py — 키움 모의 페이퍼트레이딩 테스트 (무네트워크).

검증:
  - 모의 도메인 하드락 (실전 도메인 호출 차단)
  - 주문 바디 형식 (.KS 제거, 시장가 trde_tp=3, 매수 kt10000/매도 kt10001)
  - 모든 HTTP 호출이 mockapi.kiwoom.com 으로만 나감
  - 잔고(kt00018) 파싱
  - plan_rebalance 순수 로직 (목표 바스켓·매도신호·델타 리밸런스·max_positions)
  - 비활성(KIWOOM_MOCK_ENABLED 미설정) 시 주문 0
"""
import os
import sys

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "crons"))

import kiwoom_mock  # noqa: E402


# ── HTTP 레코더 (토큰+주문+잔고 응답을 가짜로) ────────────────────────────────
class _Rec:
    def __init__(self):
        self.calls = []

    def post(self, url, headers=None, json=None, timeout=None, allow_redirects=None):
        self.calls.append({"url": url, "headers": headers or {}, "json": json or {},
                           "allow_redirects": allow_redirects})

        class R:
            def __init__(s, payload):
                s._p = payload
                s.status_code = 200

            def raise_for_status(s):
                pass

            def json(s):
                return s._p

        if url.endswith("/oauth2/token"):
            return R({"token": "tok-123", "expires_in": 3600})
        if url.endswith("/api/dostk/ordr"):
            return R({"return_code": 0, "ord_no": "ORD1", "return_msg": "정상처리"})
        if url.endswith("/api/dostk/acnt"):
            return R({"return_code": 0, "acnt_evlt_remn_indv_tot": [
                {"stk_cd": "005930", "stk_nm": "삼성전자", "rmnd_qty": "10",
                 "pur_pric": "70000", "cur_prc": "75000", "evlt_amt": "750000",
                 "evltv_prft": "50000", "prft_rt": "7.14"},
            ], "prsm_dpst_aset_amt": "10000000"})
        return R({})


@pytest.fixture
def rec(monkeypatch):
    r = _Rec()
    monkeypatch.setenv("KIWOOM_API_KEY", "k")
    monkeypatch.setenv("KIWOOM_API_SECRET", "s")
    monkeypatch.setattr(kiwoom_mock.requests, "post", r.post)
    kiwoom_mock._token_cache.update(token=None, exp=0.0)   # 캐시 초기화
    return r


# ── 1. 도메인 하드락 ──────────────────────────────────────────────────────────
def test_assert_mock_url_blocks_live():
    with pytest.raises(RuntimeError):
        kiwoom_mock._assert_mock_url("https://api.kiwoom.com/api/dostk/ordr")
    # 모의 도메인은 통과
    kiwoom_mock._assert_mock_url("https://mockapi.kiwoom.com/api/dostk/ordr")


def test_all_calls_hit_mock_domain(rec):
    kiwoom_mock.get_balance()
    kiwoom_mock.place_order("005930", 1, "buy")
    assert rec.calls, "호출이 있어야 함"
    for c in rec.calls:
        assert c["url"].startswith("https://mockapi.kiwoom.com/"), f"실전 도메인 유출: {c['url']}"
        assert c["allow_redirects"] is False, "리다이렉트 차단(실전 도메인 유출 방지)"


# ── 2. 주문 바디 형식 ─────────────────────────────────────────────────────────
def test_buy_order_body(rec):
    res = kiwoom_mock.place_order("005930.KS", 5, "buy")
    assert res["ok"] and res["ord_no"] == "ORD1"
    order_call = [c for c in rec.calls if c["url"].endswith("/api/dostk/ordr")][0]
    assert order_call["headers"]["api-id"] == "kt10000"        # 매수
    b = order_call["json"]
    assert b["stk_cd"] == "005930"                              # .KS 제거
    assert b["ord_qty"] == "5"
    assert b["trde_tp"] == "3"                                  # 시장가
    assert b["ord_uv"] == ""                                    # 시장가는 단가 빈값
    assert b["dmst_stex_tp"] == "KRX"


def test_sell_order_uses_kt10001(rec):
    kiwoom_mock.place_order("000660", 3, "sell")
    order_call = [c for c in rec.calls if c["url"].endswith("/api/dostk/ordr")][0]
    assert order_call["headers"]["api-id"] == "kt10001"        # 매도


def test_limit_order_sets_price(rec):
    kiwoom_mock.place_order("005930", 2, "buy", price=70000)
    b = [c for c in rec.calls if c["url"].endswith("/api/dostk/ordr")][0]["json"]
    assert b["trde_tp"] == "0" and b["ord_uv"] == "70000"      # 지정가


def test_zero_qty_rejected(rec):
    res = kiwoom_mock.place_order("005930", 0, "buy")
    assert not res["ok"]
    assert not [c for c in rec.calls if c["url"].endswith("/api/dostk/ordr")]  # 호출 안 함


# ── 3. 잔고 파싱 ──────────────────────────────────────────────────────────────
def test_get_balance_parses_positions(rec):
    bal = kiwoom_mock.get_balance()
    assert bal["ok"] is True
    assert "005930" in bal["positions"]
    p = bal["positions"]["005930"]
    assert p["shares"] == 10 and p["cur_price"] == 75000
    # kt00018엔 순수 예수금 필드 없음 → nav=추정예탁자산, 현금 = nav - 보유평가액
    assert bal["nav"] == 10_000_000
    assert bal["cash_krw"] == 10_000_000 - 750_000   # 9,250,000


def test_get_balance_failure_sets_ok_false(rec, monkeypatch):
    monkeypatch.setattr(kiwoom_mock, "_post", lambda *a, **k: {"return_code": 1, "return_msg": "오류"})
    bal = kiwoom_mock.get_balance()
    assert bal["ok"] is False and bal["positions"] == {}


def test_token_uses_expires_dt(rec, monkeypatch):
    # expires_dt(YYYYMMDDHHMMSS) 경로가 크래시 없이 토큰 반환
    def post(url, headers=None, json=None, timeout=None, allow_redirects=None):
        class R:
            status_code = 200
            def raise_for_status(s): pass
            def json(s): return {"token": "tok-dt", "expires_dt": "20991231235959"}
        return R()
    monkeypatch.setattr(kiwoom_mock.requests, "post", post)
    kiwoom_mock._token_cache.update(token=None, exp=0.0)
    assert kiwoom_mock._get_token() == "tok-dt"


# ── 4. is_enabled 게이팅 ──────────────────────────────────────────────────────
def test_is_enabled(monkeypatch):
    monkeypatch.delenv("KIWOOM_MOCK_ENABLED", raising=False)
    assert kiwoom_mock.is_enabled() is False
    monkeypatch.setenv("KIWOOM_MOCK_ENABLED", "true")
    assert kiwoom_mock.is_enabled() is True


def test_main_skips_when_disabled(monkeypatch):
    import kiwoom_mock_track as kt
    monkeypatch.delenv("KIWOOM_MOCK_ENABLED", raising=False)
    called = {"order": False}
    monkeypatch.setattr(kiwoom_mock, "place_order", lambda *a, **k: called.__setitem__("order", True))
    assert kt.main() == 0
    assert called["order"] is False, "비활성 시 주문 절대 안 함"


# ── 5. plan_rebalance 순수 로직 ───────────────────────────────────────────────
def _sig(code, action, score, price):
    return {"code": code, "action": action, "score": score, "price": price,
            "is_buy": action in ("강한 매수후보", "관심/분할매수"),
            "is_sell": action in ("매도검토", "손절/매도검토")}


def test_plan_buys_top_n_equal_weight():
    import kiwoom_mock_track as kt
    signals = [
        _sig("005930", "강한 매수후보", 80, 70000),
        _sig("000660", "관심/분할매수", 70, 100000),
        _sig("035720", "강한 매수후보", 90, 50000),
        _sig("207940", "중립", 50, 80000),   # 매수 아님 → 제외
    ]
    orders = kt.plan_rebalance(signals, positions={}, budget_krw=3_000_000, max_positions=2)
    # 상위 2 (score 90, 80) = 035720, 005930. 균등 1.5M씩.
    codes = {o["code"] for o in orders}
    assert codes == {"035720", "005930"}
    by = {o["code"]: o for o in orders}
    assert by["035720"]["side"] == "buy" and by["035720"]["qty"] == 30   # 1.5M/50000
    assert by["005930"]["qty"] == 21                                     # floor(1.5M/70000)


def test_plan_sells_position_off_target():
    import kiwoom_mock_track as kt
    signals = [_sig("035720", "강한 매수후보", 90, 50000)]
    positions = {"005930": {"shares": 10, "cur_price": 75000}}   # 더 이상 목표 아님
    orders = kt.plan_rebalance(signals, positions, budget_krw=1_000_000, max_positions=1)
    sell = [o for o in orders if o["code"] == "005930"][0]
    assert sell["side"] == "sell" and sell["qty"] == 10 and sell["reason"] == "타깃이탈"


def test_plan_sells_on_sell_signal_even_if_held():
    import kiwoom_mock_track as kt
    signals = [_sig("005930", "손절/매도검토", 20, 75000)]
    positions = {"005930": {"shares": 8, "cur_price": 75000}}
    orders = kt.plan_rebalance(signals, positions, budget_krw=1_000_000, max_positions=5)
    assert [o for o in orders if o["code"] == "005930"][0] == {
        "code": "005930", "side": "sell", "qty": 8, "reason": "타깃이탈"}
    # is_sell 종목은 is_buy 가 아니라 애초에 타깃에서 빠짐 → 전량 매도


def test_plan_rebalances_delta_up():
    import kiwoom_mock_track as kt
    signals = [_sig("005930", "강한 매수후보", 80, 50000)]
    positions = {"005930": {"shares": 5, "cur_price": 50000}}   # 이미 5주 보유
    orders = kt.plan_rebalance(signals, positions, budget_krw=1_000_000, max_positions=1)
    # 목표 = 1.0M/50000 = 20주, 보유 5 → +15 매수
    buy = [o for o in orders if o["code"] == "005930"][0]
    assert buy["side"] == "buy" and buy["qty"] == 15


def test_plan_skips_zero_price():
    import kiwoom_mock_track as kt
    signals = [_sig("005930", "강한 매수후보", 80, 0)]   # 가격 0 → 제외
    orders = kt.plan_rebalance(signals, positions={}, budget_krw=1_000_000, max_positions=5)
    assert orders == []


def test_plan_negative_budget_no_phantom_sell():
    """음수 예산 → 매수 전면 생략, 미보유 종목 유령매도 없음 (#5)."""
    import kiwoom_mock_track as kt
    signals = [_sig("005930", "강한 매수후보", 80, 50000)]
    # 보유 없음 + 음수 예산 → 주문 0
    assert kt.plan_rebalance(signals, {}, budget_krw=-100, max_positions=3) == []
    # 보유한 off-target 은 청산하되, 매수/유령매도는 없음
    pos = {"000660": {"shares": 5, "cur_price": 100000}}
    orders = kt.plan_rebalance(signals, pos, budget_krw=-100, max_positions=3)
    assert orders == [{"code": "000660", "side": "sell", "qty": 5, "reason": "타깃이탈"}]


def test_plan_slippage_reduces_qty():
    """슬리피지 버퍼가 매수 수량을 보수적으로 줄임 (#11)."""
    import kiwoom_mock_track as kt
    signals = [_sig("005930", "강한 매수후보", 80, 50000)]
    base = kt.plan_rebalance(signals, {}, 1_000_000, 1, slippage=0.0)
    slip = kt.plan_rebalance(signals, {}, 1_000_000, 1, slippage=0.05)
    assert base[0]["qty"] == 20                 # 1.0M / 50000
    assert slip[0]["qty"] == 19                 # 1.0M / 52500 = 19.0...


def test_plan_cash_running_cap():
    """가용현금 한도까지만 매수 (#6)."""
    import kiwoom_mock_track as kt
    signals = [_sig("005930", "강한 매수후보", 80, 50000),
               _sig("000660", "강한 매수후보", 70, 50000)]
    # 예산은 넉넉(2종목×0.5M=각 10주)하지만 현금은 300k뿐 → 총 6주까지만
    orders = kt.plan_rebalance(signals, {}, 1_000_000, 2, cash_krw=300_000, slippage=0.0)
    total = sum(o["qty"] for o in orders if o["side"] == "buy")
    assert total <= 6


def test_main_dry_run_places_no_orders(monkeypatch):
    """--dry-run 은 계획만 출력하고 주문 0 (비활성 상태에서도 미리보기 허용)."""
    import kiwoom_mock_track as kt
    monkeypatch.delenv("KIWOOM_MOCK_ENABLED", raising=False)   # 비활성이어도 dry-run 동작
    monkeypatch.setattr(kiwoom_mock, "get_balance", lambda: {
        "ok": True, "positions": {}, "pos_value": 0.0, "cash_krw": 10_000_000, "nav": 10_000_000, "raw": {}})
    monkeypatch.setattr(kt, "compute_kr_signals",
                        lambda limit=20: [_sig("005930", "강한 매수후보", 80, 50000)])
    ordered = {"n": 0}
    monkeypatch.setattr(kiwoom_mock, "place_order", lambda *a, **k: ordered.__setitem__("n", ordered["n"] + 1))
    assert kt.main(["--dry-run"]) == 0
    assert ordered["n"] == 0, "dry-run 은 주문 0"


def test_main_aborts_on_balance_failure(monkeypatch):
    """잔고조회 실패 시 매수하지 않고 종료 (#8 — 블라인드 매수 방지)."""
    import kiwoom_mock_track as kt
    monkeypatch.setenv("KIWOOM_MOCK_ENABLED", "true")
    monkeypatch.setattr(kiwoom_mock, "is_enabled", lambda: True)
    monkeypatch.setattr(kiwoom_mock, "get_balance", lambda: {"ok": False, "positions": {},
                                                             "pos_value": 0.0, "cash_krw": None, "nav": None, "raw": None})
    ordered = {"n": 0}
    monkeypatch.setattr(kiwoom_mock, "place_order", lambda *a, **k: ordered.__setitem__("n", ordered["n"] + 1))
    assert kt.main() == 1
    assert ordered["n"] == 0, "잔고 실패 시 주문 0"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
