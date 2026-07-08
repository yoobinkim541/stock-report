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
from types import SimpleNamespace

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


def test_plan_rebal_band_skips_small_adjust():
    """무거래 밴드: 목표 대비 band 이내 보유종목은 조정 skip (회전율↓·증권거래세 절감)."""
    import kiwoom_mock_track as kt
    sigs = [_sig("005930", "강한 매수후보", 80, 50000)]   # 목표 per=1M → 20주
    pos = {"005930": {"shares": 19, "cur_price": 50000}}  # 19주(=950k) 목표比 -5%, band 0.25 이내
    banded = kt.plan_rebalance(sigs, pos, 1_000_000, 1, rebal_band=0.25)
    assert not any(o["code"] == "005930" for o in banded)          # 무거래
    nob = kt.plan_rebalance(sigs, pos, 1_000_000, 1, rebal_band=0.0)
    assert [o for o in nob if o["code"] == "005930"][0] == {
        "code": "005930", "side": "buy", "qty": 1, "reason": "신규/추가"}


def test_plan_exit_buffer_keeps_boundary():
    """히스테리시스: 보유종목이 top-(N+buffer) 안이면 매도 안 함(경계 flip 방지)."""
    import kiwoom_mock_track as kt
    sigs = [_sig("A", "강한 매수후보", 90, 1000),
            _sig("B", "강한 매수후보", 80, 1000),
            _sig("C", "강한 매수후보", 70, 1000)]   # C = rank3
    pos = {"C": {"shares": 5, "cur_price": 1000}}
    kept = kt.plan_rebalance(sigs, pos, 1_000_000, 2, exit_buffer=2)
    assert not any(o["code"] == "C" and o["side"] == "sell" for o in kept)   # top-4 안 → 유지
    nob = kt.plan_rebalance(sigs, pos, 1_000_000, 2, exit_buffer=0)
    assert {"code": "C", "side": "sell", "qty": 5, "reason": "타깃이탈"} in nob


def test_order_blocker_classifies():
    """계좌/시장 레벨 차단 신호 분류 — 개별 주문 문제(부족 등)와 구분해 즉시 중단·명확 알림."""
    import kiwoom_mock_track as kt
    assert kt._order_blocker("[2000](RC4091:모의투자 종료된 계좌입니다. 다시 신청해주시기 바랍니다.)") == "account"
    assert kt._order_blocker("[2000](RC5006:모의투자 개인공매도이수전용 계좌입니다.)") == "account"
    assert kt._order_blocker("[2000](RC4058:모의투자 장종료)") == "market"
    assert kt._order_blocker("모의투자 주문가능금액이 부족합니다.") is None   # 개별 주문 문제 → 중단 안 함
    assert kt._order_blocker("모의투자 매수주문이 완료 되었습니다.") is None
    assert kt._order_blocker(None) is None


def test_notify_includes_company_name_and_expanded_reason(monkeypatch):
    import kiwoom_mock_track as kt
    import notify

    sent = {}
    monkeypatch.setenv("KR_MOCK_NOTIFY_LLM_ENABLED", "0")
    monkeypatch.setitem(sys.modules, "kr200_meta", SimpleNamespace(NAME={"009540": "HD한국조선해양"}))
    monkeypatch.setattr(notify, "send_telegram",
                        lambda text, **_kw: sent.__setitem__("text", text))
    signals = [{
        "code": "009540", "ticker": "009540.KS", "action": "강한 매수후보",
        "score": 68, "price": 483000, "policy_score": 0.734,
        "rationale": {"one_line_reason": "재무 68점(B) · 일일 신호 긍정",
                      "grade": "B", "financial": "B", "timing": "긍정",
                      "news": "중립", "risk": "보통"},
    }]
    results = [{"code": "009540", "side": "buy", "qty": 2, "kind": "편입",
                "ok": True, "reason": "신규/추가"}]

    kt._notify(9_664_093, results, signals)

    text = sent["text"]
    assert "추정 NAV  ₩9,664,093" in text
    assert "편입 009540 HD한국조선해양 2주" in text
    assert "사유: 재무 68점(B) · 일일 신호 긍정" in text
    assert "근거: 판단 강한 매수후보 · 재무 68점(B) · 정책점수 0.73" in text
    assert "세부: 재무 B · 타이밍 긍정 · 뉴스 중립 · 리스크 보통" in text


def test_notify_uses_llm_decision_notes(monkeypatch):
    import kiwoom_mock_track as kt
    import notify
    from lib import mock_llm_rationale

    sent, seen = {}, {}
    monkeypatch.setenv("KR_MOCK_NOTIFY_LLM_ENABLED", "1")
    monkeypatch.setitem(sys.modules, "kr200_meta", SimpleNamespace(NAME={"009540": "HD한국조선해양"}))
    monkeypatch.setattr(notify, "send_telegram",
                        lambda text, **_kw: sent.__setitem__("text", text))

    def fake_run(payload):
        seen.update(payload)
        return ({
            "summary": "재무 B와 정책점수가 함께 기준을 통과했습니다.",
            "decision_notes": ["편입은 일일 신호와 가격 축이 뒷받침합니다."],
            "risk_checks": ["모의 결과로 추적합니다."],
            "confidence": 71,
        }, "ok")

    monkeypatch.setattr(mock_llm_rationale, "run", fake_run)
    signals = [{
        "code": "009540", "ticker": "009540.KS", "action": "강한 매수후보",
        "score": 68, "price": 483000, "policy_score": 0.734,
        "rationale": {"one_line_reason": "재무 68점(B) · 일일 신호 긍정", "grade": "B"},
        "features": {"ranker": 0.8, "mom12": 0.6},
    }]
    results = [{"code": "009540", "side": "buy", "qty": 2, "kind": "편입",
                "ok": True, "reason": "신규/추가"}]

    kt._notify(9_664_093, results, signals)

    text = sent["text"]
    assert "편입 009540 HD한국조선해양 2주" in text
    assert "🧠 판단근거:" in text
    assert "재무 B와 정책점수" in text
    assert "일일 신호와 가격 축" in text
    assert seen["recent_decisions"][0]["name"] == "HD한국조선해양"
    assert seen["recent_decisions"][0]["policy_score"] == 0.734
    assert "근거: 판단 강한 매수후보" not in text       # LLM 성공 시 정량 폴백 중복 생략


def _fake_post_factory(order_codes):
    """order_codes: /ordr 호출마다 반환할 status_code 시퀀스."""
    import requests as _rq
    state = {"i": 0}

    class R:
        def __init__(s, code, payload):
            s.status_code, s._p = code, payload

        def raise_for_status(s):
            if s.status_code >= 400:
                raise _rq.HTTPError(str(s.status_code))

        def json(s):
            return s._p

    def post(url, headers=None, json=None, timeout=None, allow_redirects=None):
        if url.endswith("/oauth2/token"):
            return R(200, {"token": "t", "expires_in": 3600})
        if url.endswith("/api/dostk/ordr"):
            code = order_codes[min(state["i"], len(order_codes) - 1)]
            state["i"] += 1
            return R(code, {"return_code": 0, "ord_no": "ORD1", "return_msg": "정상처리"} if code == 200 else {})
        return R(200, {})

    return post, state


def test_order_429_retries_then_succeeds(monkeypatch):
    """429(레이트리밋)=미체결 확실 → 재시도 후 체결. 중복체결 위험 0."""
    monkeypatch.setenv("KIWOOM_API_KEY", "k"); monkeypatch.setenv("KIWOOM_API_SECRET", "s")
    kiwoom_mock._token_cache.update(token=None, exp=0.0)
    monkeypatch.setattr(kiwoom_mock.time, "sleep", lambda *_a, **_k: None)
    post, state = _fake_post_factory([429, 200])       # 1회 429 → 2회차 성공
    monkeypatch.setattr(kiwoom_mock.requests, "post", post)
    res = kiwoom_mock.place_order("005930", 1, "buy")
    assert res["ok"] is True and res["ord_no"] == "ORD1"
    assert state["i"] == 2                              # 429 + 성공 = 2회 POST


def test_order_500_not_retried(monkeypatch):
    """500 은 무재시도(주문이 이미 체결됐을 수 있어 중복 위험) — 1회 POST 후 실패."""
    monkeypatch.setenv("KIWOOM_API_KEY", "k"); monkeypatch.setenv("KIWOOM_API_SECRET", "s")
    kiwoom_mock._token_cache.update(token=None, exp=0.0)
    post, state = _fake_post_factory([500, 200])       # 500 나와도 재시도 안 함
    monkeypatch.setattr(kiwoom_mock.requests, "post", post)
    res = kiwoom_mock.place_order("005930", 1, "buy")
    assert res["ok"] is False
    assert state["i"] == 1                              # 단 1회 POST (무재시도)


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


# ── 최소 보유기간 게이트 (KR_MOCK_MIN_HOLD_DAYS · 비용 OOS 실증 반영) ──────────

def test_held_days_from_decisions_latest_entry():
    import kiwoom_mock_track as kt
    decs = [{"date": "2026-05-01", "side": "편입", "code": "005930"},
            {"date": "2026-05-10", "side": "퇴출", "code": "005930"},
            {"date": "2026-06-01", "side": "편입", "code": "005930"},   # 재편입 → 최신 기준
            {"date": "2026-06-15", "side": "편입", "ticker": "000660.KS"}]
    hd = kt.held_days_from_decisions(decs, ["005930", "000660", "099999"], "2026-06-21")
    assert hd["005930"] == 20        # 06-01 기준 (재편입)
    assert hd["000660"] == 6         # ticker→code 파생
    assert "099999" not in hd        # 편입 기록 없음 → 제외


def test_plan_min_hold_keeps_recent_entry():
    import kiwoom_mock_track as kt
    # A 는 타깃(top1), B 는 타깃이탈이나 보유 3일(<60) → 청산 보류
    signals = [{"code": "A", "is_buy": True, "price": 100, "policy_score": 0.9},
               {"code": "B", "is_buy": False, "price": 100, "policy_score": 0.1}]
    positions = {"B": {"shares": 10, "cur_price": 100}}
    hold = kt.plan_rebalance(signals, positions, 1_000_000, 1, min_hold_days=60,
                             held_days={"B": 3})
    assert not any(o["code"] == "B" and o["side"] == "sell" for o in hold)   # 보류
    # 보유 90일(≥60)이면 정상 청산
    sold = kt.plan_rebalance(signals, positions, 1_000_000, 1, min_hold_days=60,
                             held_days={"B": 90})
    assert any(o["code"] == "B" and o["side"] == "sell" for o in sold)


def test_plan_min_hold_zero_is_current_behavior():
    import kiwoom_mock_track as kt
    signals = [{"code": "A", "is_buy": True, "price": 100, "policy_score": 0.9}]
    positions = {"B": {"shares": 10, "cur_price": 100}}
    # min_hold_days=0 (기본) → 타깃이탈 즉시 청산 (현행 불변)
    orders = kt.plan_rebalance(signals, positions, 1_000_000, 1, min_hold_days=0)
    assert any(o["code"] == "B" and o["side"] == "sell" for o in orders)


def test_min_hold_default_active_60(monkeypatch):
    """KR_MOCK_MIN_HOLD_DAYS 기본값 60 = 모의 활성 (미설정 시 비용 OOS 권고값)."""
    import kiwoom_mock_track as kt
    monkeypatch.delenv("KR_MOCK_MIN_HOLD_DAYS", raising=False)
    assert kt._int_env("KR_MOCK_MIN_HOLD_DAYS", 60) == 60
    # 0 으로 명시 오버라이드 시 현행 무제한 회전 복귀
    monkeypatch.setenv("KR_MOCK_MIN_HOLD_DAYS", "0")
    assert kt._int_env("KR_MOCK_MIN_HOLD_DAYS", 60) == 0


def test_tranches_default_active_3(monkeypatch):
    """KR_MOCK_TRANCHES 기본 3 = 분할 활성 · 1 오버라이드 시 일괄 복귀."""
    import kiwoom_mock_track as kt
    monkeypatch.delenv("KR_MOCK_TRANCHES", raising=False)
    assert kt._int_env("KR_MOCK_TRANCHES", 3) == 3
    monkeypatch.setenv("KR_MOCK_TRANCHES", "1")
    assert kt._int_env("KR_MOCK_TRANCHES", 3) == 1


def test_plan_then_tranche_caps_full_entry():
    """plan_rebalance 목표 → 분할 상한 통합 (60주 신규 → 3분할 20주)."""
    import kiwoom_mock_track as kt
    from lib.tranche import plan_tranches
    sig = [{"code": "A", "is_buy": True, "price": 50_000, "policy_score": 0.9}]
    plan = kt.plan_rebalance(sig, {}, 3_000_000, 1)
    assert plan == [{"code": "A", "side": "buy", "qty": 60, "reason": "신규/추가"}]
    out = plan_tranches(plan, 3_000_000 / 1, lambda c: {"A": 50_000}.get(c), 3, id_key="code")
    assert out[0]["qty"] == 20 and "3분할" in out[0]["reason"]


def test_plan_min_hold_stub_exempt():
    """스텁(목표의 절반 미만 반쪽 포지션)은 min_hold 보호 제외 → 청산 허용 (B)."""
    import kiwoom_mock_track as kt
    signals = [{"code": "A", "is_buy": True, "price": 100, "policy_score": 0.9}]
    # per_target = 1_000_000/1 = 1M. B 보유가치 40만(<50%) = 스텁 → 3일 보유여도 청산
    stub = {"B": {"shares": 4, "cur_price": 100_000}}
    out = kt.plan_rebalance(signals, stub, 1_000_000, 1, min_hold_days=60,
                            held_days={"B": 3}, stub_frac=0.5)
    assert any(o["code"] == "B" and o["side"] == "sell" for o in out)
    # 보유가치 60만(≥50%) = 제대로 빌드 → 보호 유지
    built = {"B": {"shares": 6, "cur_price": 100_000}}
    out2 = kt.plan_rebalance(signals, built, 1_000_000, 1, min_hold_days=60,
                             held_days={"B": 3}, stub_frac=0.5)
    assert not any(o["code"] == "B" and o["side"] == "sell" for o in out2)
