#!/usr/bin/env python3
"""test_toss_api.py — 토스증권 read-only 어댑터 + 해외 동기화 (무네트워크).

핵심: 주문 경로 0(소스 grep 강제)·도메인 하드락·정규화·단일 apply 소스 원칙.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "crons"))

from providers import toss_api as T


# ── 안전: 읽기전용 강제 (grep) ────────────────────────────────────────────────

def test_source_has_no_order_paths():
    """토스 어댑터에 주문/정정/취소 API 경로가 존재하지 않는다 — 실계좌 자동매매 금지 규율.

    buying-power(예수금 조회, GET 전용)는 2026-07-25 사용자 승인으로 예외 허용 —
    체결/정정/취소로 이어지는 실제 주문 경로는 아니고 조회 전용(_get 만 사용).
    """
    src = open(os.path.join(os.path.dirname(__file__), "..", "providers", "toss_api.py"),
               encoding="utf-8").read()
    for banned in ("/api/v1/orders", "conditional-orders", "/cancel", "/modify",
                   "sellable-quantity"):
        assert banned not in src, f"주문 관련 경로 발견: {banned}"
    # buying-power 는 허용됐지만 그래도 GET(_get) 으로만 호출돼야 함 — POST 로 쓰이면 실패
    assert '_get("/api/v1/buying-power"' in src or "_get('/api/v1/buying-power'" in src
    # 쓰기 호출은 토큰 발급 딱 하나
    assert src.count("requests.post") == 1
    assert "/oauth2/token" in src


def test_kiwoom_us_sync_is_read_only():
    """키움 해외 확장도 잔고 조회 TR 만 — 주문 엔드포인트/TR 부재 (grep 강제)."""
    src = open(os.path.join(os.path.dirname(__file__), "..", "crons", "kiwoom_sync_rest.py"),
               encoding="utf-8").read()
    assert "ust21070" in src                              # 원장잔고확인 (read-only)
    for banned in ("/api/us/ordr", "kt10000", "kt10001", "ust21150"):
        assert banned not in src, f"주문 관련 경로 발견: {banned}"


def test_assert_url_blocks_foreign_domain():
    with pytest.raises(ValueError):
        T._assert_url("https://evil.example.com/api/v1/holdings")
    assert T._assert_url(f"{T.BASE_URL}/api/v1/accounts")


# ── 토큰 (형식·캐시) ─────────────────────────────────────────────────────────

class _Resp:
    def __init__(self, payload, status=200):
        self._p, self.status_code = payload, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._p


def test_get_token_form_and_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("TOSS_API_KEY", "c_test")
    monkeypatch.setenv("TOSS_API_SECRET", "s_test")
    monkeypatch.setattr(T, "TOKEN_CACHE", tmp_path / "tok.json")
    calls = []

    def fake_post(url, data=None, headers=None, timeout=0):
        calls.append((url, data))
        return _Resp({"access_token": "jwt_abc", "token_type": "Bearer", "expires_in": 86400})

    import requests
    monkeypatch.setattr(requests, "post", fake_post)
    assert T.get_token() == "jwt_abc"
    url, form = calls[0]
    assert url == f"{T.BASE_URL}/oauth2/token"
    assert form == {"grant_type": "client_credentials",
                    "client_id": "c_test", "client_secret": "s_test"}
    # 2번째 호출은 디스크 캐시 — 재발급 없음
    assert T.get_token() == "jwt_abc" and len(calls) == 1


def test_get_token_fail_closed_without_keys(monkeypatch):
    monkeypatch.delenv("TOSS_API_KEY", raising=False)
    monkeypatch.delenv("TOSS_API_SECRET", raising=False)
    assert T.get_token() is None


# ── 정규화 (스펙 형태 fixture) ───────────────────────────────────────────────

_OVERVIEW = {
    "totalPurchaseAmount": {"krw": "0", "usd": "24500.00"},
    "items": [
        {"symbol": "MSFT", "name": "마이크로소프트", "quantity": "10.5",
         "averagePurchasePrice": "400.00", "lastPrice": "450.00",
         "currency": "USD", "marketCountry": "US",
         "marketValue": {"amount": "4725.00"}, "profitLoss": {"amount": "525.00"}},
        {"symbol": "005930", "name": "삼성전자", "quantity": "100",
         "averagePurchasePrice": "65000", "lastPrice": "72000",
         "currency": "KRW", "marketCountry": "KR"},
        {"symbol": "", "name": "빈 심볼은 폐기"},
    ],
}


def test_normalize_holdings():
    out = T.normalize_holdings(_OVERVIEW)
    assert len(out) == 2
    ms = out[0]
    assert ms["symbol"] == "MSFT" and ms["market"] == "US" and ms["currency"] == "USD"
    assert ms["shares"] == 10.5 and ms["avg"] == 400.0 and ms["value"] == 4725.0
    assert ms["return_pct"] == 12.5
    ss = out[1]
    assert ss["market"] == "KR" and ss["value"] == 100 * 72000   # marketValue 없으면 계산
    assert T.normalize_holdings(None) == []
    assert T.normalize_holdings({}) == []


def test_buying_power(monkeypatch, tmp_path):
    monkeypatch.setattr(T, "get_token", lambda: "faketoken")
    calls = []

    def fake_get(url, params=None, headers=None, timeout=0, allow_redirects=True):
        calls.append((url, params, headers.get("X-Tossinvest-Account")))
        return _Resp({"result": {"currency": "KRW", "cashBuyingPower": "41011"}})

    import requests
    monkeypatch.setattr(requests, "get", fake_get)
    assert T.buying_power("KRW", account_seq=1) == 41011.0
    url, params, acct_hdr = calls[0]
    assert url == f"{T.BASE_URL}/api/v1/buying-power"
    assert params == {"currency": "KRW"} and acct_hdr == "1"


def test_buying_power_fails_closed_on_bad_response(monkeypatch):
    monkeypatch.setattr(T, "get_token", lambda: "faketoken")

    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp({"result": {}}))
    assert T.buying_power("KRW", account_seq=1) is None


def test_default_account_seq(monkeypatch):
    monkeypatch.delenv("TOSS_ACCOUNT_SEQ", raising=False)
    monkeypatch.setattr(T, "accounts", lambda: [
        {"accountNo": "1", "accountSeq": 7, "accountType": "PENSION_SAVINGS"},
        {"accountNo": "2", "accountSeq": 3, "accountType": "BROKERAGE"}])
    assert T.default_account_seq() == 3                   # 첫 BROKERAGE
    monkeypatch.setenv("TOSS_ACCOUNT_SEQ", "9")
    assert T.default_account_seq() == 9                   # env 우선


# ── 동기화 행 변환 + 단일 apply 소스 ─────────────────────────────────────────

def test_to_snapshot_rows_us_only():
    import toss_sync as TS
    rows = TS.to_snapshot_rows(T.normalize_holdings(_OVERVIEW))
    assert len(rows) == 1 and rows[0]["ticker"] == "MSFT"
    assert rows[0]["cost_usd"] == pytest.approx(4200.0)
    assert rows[0]["value_usd"] == pytest.approx(4725.0)


def test_to_domestic_rows_kr_only():
    import toss_sync as TS
    rows = TS.to_domestic_rows(T.normalize_holdings(_OVERVIEW))
    assert len(rows) == 1 and rows[0]["ticker"] == "005930"
    assert rows[0]["avg_price"] == 65000 and rows[0]["current_price"] == 72000


def test_overseas_single_apply_source(monkeypatch):
    from lib import overseas_snapshot as O
    monkeypatch.delenv("OVERSEAS_SYNC_SOURCE", raising=False)
    assert not O.can_apply("toss") and not O.can_apply("kiwoom")   # 기본 = 보고만
    monkeypatch.setenv("OVERSEAS_SYNC_SOURCE", "toss")
    assert O.can_apply("toss") and not O.can_apply("kiwoom")       # 단일 소스만


def test_domestic_single_apply_source(monkeypatch):
    from lib import domestic_snapshot as D
    monkeypatch.delenv("DOMESTIC_SYNC_SOURCE", raising=False)
    assert not D.can_apply("toss") and not D.can_apply("kiwoom")   # 기본 = 보고만
    monkeypatch.setenv("DOMESTIC_SYNC_SOURCE", "toss")
    assert D.can_apply("toss") and not D.can_apply("kiwoom")       # 단일 소스만


def test_diff_holdings():
    from lib import overseas_snapshot as O
    cur = [{"ticker": "MSFT", "shares": 10}, {"ticker": "NVDA", "shares": 5}]
    new = [{"ticker": "MSFT", "shares": 12}, {"ticker": "ORCL", "shares": 3}]
    lines = "\n".join(O.diff_holdings(cur, new))
    assert "MSFT: 10 → 12주" in lines and "➕ ORCL" in lines and "➖ NVDA" in lines
    assert O.diff_holdings(cur, cur) == []


def test_update_overseas_holdings(tmp_path, monkeypatch):
    from lib import overseas_snapshot as O
    snap_path = tmp_path / "portfolio_snapshot.json"
    snap_path.write_text(json.dumps({
        "snapshot_date": "2026-07-07",
        "overseas_general": {"holdings_usd": [
            {"ticker": "MSFT", "name": "마이크로소프트", "shares": 10,
             "avg_price_usd": 400.0, "current_price_usd": 440.0}]},
    }), encoding="utf-8")
    recorded = []
    from lib import trade_events
    monkeypatch.setattr(trade_events, "record_trade", lambda **kw: recorded.append(kw))

    rows = [{"name": "마이크로소프트", "ticker": "MSFT", "shares": 12,
             "avg_price_usd": 405.0, "current_price_usd": 450.0,
             "cost_usd": 4860.0, "value_usd": 5400.0, "pnl_usd": 540.0, "return_pct": 11.1}]
    O.update_overseas_holdings(rows, source="toss", portfolio_path=str(snap_path))

    snap = json.loads(snap_path.read_text(encoding="utf-8"))
    holdings = snap["overseas_general"]["holdings_usd"]
    assert holdings[0]["shares"] == 12 and snap["last_overseas_sync_source"] == "toss"
    assert (tmp_path / "portfolio_snapshot.json.bak").exists()      # 백업
    assert recorded == []                                           # 첫 sync — 델타 원장 없음

    # 2번째 sync: last_overseas_sync 존재 → 수량 변화가 trade_events 기록
    rows[0]["shares"] = 15
    O.update_overseas_holdings(rows, source="toss", portfolio_path=str(snap_path))
    assert len(recorded) == 1 and recorded[0]["side"] == "buy" and recorded[0]["qty"] == 3


def test_update_overseas_holdings_removes_fully_sold_ticker(tmp_path, monkeypatch):
    """전량매도(최신 조회에 없는 종목)는 스냅샷에서 제거되고 sell 로 원장 기록.

    회귀방지: 예전엔 최신 조회 목록만 순회하며 upsert 하고 기존 값은 그대로 둬서,
    전량매도한 종목이 스냅샷에 영구히 남아있었음(2026-07-25 ORCX 사례).
    """
    from lib import overseas_snapshot as O
    snap_path = tmp_path / "portfolio_snapshot.json"
    snap_path.write_text(json.dumps({
        "snapshot_date": "2026-07-07",
        "last_overseas_sync": "2026-07-24T00:00:00",
        "overseas_general": {"holdings_usd": [
            {"ticker": "MSFT", "name": "마이크로소프트", "shares": 10,
             "avg_price_usd": 400.0, "current_price_usd": 440.0},
            {"ticker": "ORCX", "name": "오라클 2배 롱 ETF", "shares": 1,
             "avg_price_usd": 100.0, "current_price_usd": 110.0},
        ]},
    }), encoding="utf-8")
    recorded = []
    from lib import trade_events
    monkeypatch.setattr(trade_events, "record_trade", lambda **kw: recorded.append(kw))

    # 최신 브로커 조회엔 MSFT만 있음 — ORCX 전량매도됨
    rows = [{"name": "마이크로소프트", "ticker": "MSFT", "shares": 10,
             "avg_price_usd": 400.0, "current_price_usd": 440.0}]
    O.update_overseas_holdings(rows, source="kiwoom", portfolio_path=str(snap_path))

    snap = json.loads(snap_path.read_text(encoding="utf-8"))
    tickers = {h["ticker"] for h in snap["overseas_general"]["holdings_usd"]}
    assert tickers == {"MSFT"}                                          # ORCX 제거됨
    sells = [r for r in recorded if r["ticker"] == "ORCX"]
    assert len(sells) == 1 and sells[0]["side"] == "sell" and sells[0]["qty"] == 1


def test_update_domestic_holdings_with_cash(tmp_path, monkeypatch):
    from lib import domestic_snapshot as D
    snap_path = tmp_path / "portfolio_snapshot.json"
    snap_path.write_text(json.dumps({
        "snapshot_date": "2026-07-07",
        "domestic": {"holdings": [
            {"ticker": "005930", "name": "삼성전자", "shares": 10,
             "avg_price": 65000.0, "current_price": 70000.0}]},
    }), encoding="utf-8")
    recorded = []
    from lib import trade_events
    monkeypatch.setattr(trade_events, "record_trade", lambda **kw: recorded.append(kw))

    rows = [{"ticker": "005930", "name": "삼성전자", "shares": 12,
             "avg_price": 66000.0, "current_price": 72000.0, "pnl_krw": 72000.0, "return_pct": 9.1}]
    summary = D.update_domestic_holdings(rows, source="toss", cash_krw=41011, portfolio_path=str(snap_path))
    assert "예수금" in summary

    snap = json.loads(snap_path.read_text(encoding="utf-8"))
    dom = snap["domestic"]
    assert dom["holdings"][0]["shares"] == 12
    assert dom["cash_krw"] == 41011.0
    assert dom["summary"]["total_cost_krw"] == pytest.approx(12 * 66000.0)
    assert dom["summary"]["total_value_krw"] == pytest.approx(12 * 72000.0)
    assert snap["last_domestic_sync_source"] == "toss"
    assert recorded == []                                           # 첫 sync — 델타 원장 없음

    # 2번째 sync: 수량 변화가 trade_events 기록, cash_krw=None 이면 기존 값 유지
    rows[0]["shares"] = 15
    D.update_domestic_holdings(rows, source="toss", cash_krw=None, portfolio_path=str(snap_path))
    assert len(recorded) == 1 and recorded[0]["side"] == "buy" and recorded[0]["qty"] == 3
    snap2 = json.loads(snap_path.read_text(encoding="utf-8"))
    assert snap2["domestic"]["cash_krw"] == 41011.0                  # 유지됨(덮어쓰지 않음)


def test_update_domestic_holdings_removes_fully_sold_ticker(tmp_path, monkeypatch):
    """overseas 와 동일 규율 — 전량매도 종목은 스냅샷에서 제거 + sell 원장 기록."""
    from lib import domestic_snapshot as D
    snap_path = tmp_path / "portfolio_snapshot.json"
    snap_path.write_text(json.dumps({
        "snapshot_date": "2026-07-07",
        "last_domestic_sync": "2026-07-24T00:00:00",
        "domestic": {"holdings": [
            {"ticker": "005930", "name": "삼성전자", "shares": 10,
             "avg_price": 65000.0, "current_price": 70000.0},
            {"ticker": "035720", "name": "카카오", "shares": 5,
             "avg_price": 40000.0, "current_price": 42000.0},
        ]},
    }), encoding="utf-8")
    recorded = []
    from lib import trade_events
    monkeypatch.setattr(trade_events, "record_trade", lambda **kw: recorded.append(kw))

    rows = [{"ticker": "005930", "name": "삼성전자", "shares": 10,
             "avg_price": 65000.0, "current_price": 70000.0}]
    D.update_domestic_holdings(rows, source="toss", portfolio_path=str(snap_path))

    snap = json.loads(snap_path.read_text(encoding="utf-8"))
    tickers = {h["ticker"] for h in snap["domestic"]["holdings"]}
    assert tickers == {"005930"}
    sells = [r for r in recorded if r["ticker"] == "035720"]
    assert len(sells) == 1 and sells[0]["side"] == "sell" and sells[0]["qty"] == 5


# ── 키움 해외 잔고 파서 (ust21070 fixture) ───────────────────────────────────

def test_kiwoom_parse_us_balance():
    import kiwoom_sync_rest as K
    result = {"return_code": 0, "crnc_code": "USD", "result_list": [
        {"stk_cd": "AAPL", "frgn_stk_nm": "애플", "qty": "20", "crnc_code": "USD",
         "frgn_stk_book_uv": "180.50", "now_pric": "210.00",
         "evlt_amt": "4200.00", "pl_amt": "590.00"},
        {"stk_cd": "7203", "frgn_stk_nm": "도요타", "qty": "5", "crnc_code": "JPY",
         "frgn_stk_book_uv": "2000", "now_pric": "2100"},                 # 타통화 제외
        {"stk_cd": "", "frgn_stk_nm": "빈 코드 폐기"},
    ]}
    rows = K._parse_us_balance(result)
    assert len(rows) == 1
    r = rows[0]
    assert r["ticker"] == "AAPL" and r["shares"] == 20.0
    assert r["avg_price_usd"] == 180.5 and r["value_usd"] == 4200.0
    assert r["return_pct"] == pytest.approx(16.34, abs=0.01)
    assert K._parse_us_balance({}) == []


def test_kiwoom_parse_us_deposit():
    import kiwoom_sync_rest as K
    result = {"return_code": 0, "result_list": [
        {"crnc_code": "USD", "fc_entra": "77.34", "fc_ord_alowa": "69.78"},
    ]}
    assert K._parse_us_deposit(result) == pytest.approx(69.78)
    assert K._parse_us_deposit({"result_list": []}) is None
    assert K._parse_us_deposit({}) is None


def test_update_overseas_holdings_writes_cash_usd(tmp_path, monkeypatch):
    from lib import overseas_snapshot as O
    snap_path = tmp_path / "portfolio_snapshot.json"
    snap_path.write_text(json.dumps({"snapshot_date": "2026-07-07"}), encoding="utf-8")
    from lib import trade_events
    monkeypatch.setattr(trade_events, "record_trade", lambda **kw: None)

    rows = [{"ticker": "MSFT", "name": "마이크로소프트", "shares": 1,
             "avg_price_usd": 400.0, "current_price_usd": 440.0}]
    summary = O.update_overseas_holdings(rows, source="kiwoom", cash_usd=69.78, portfolio_path=str(snap_path))
    assert "예수금" in summary
    snap = json.loads(snap_path.read_text(encoding="utf-8"))
    assert snap["overseas_general"]["cash_usd"] == 69.78

    # cash_usd=None 이면 기존 값 유지(덮어쓰지 않음)
    O.update_overseas_holdings(rows, source="kiwoom", cash_usd=None, portfolio_path=str(snap_path))
    snap2 = json.loads(snap_path.read_text(encoding="utf-8"))
    assert snap2["overseas_general"]["cash_usd"] == 69.78


def test_kiwoom_domestic_write_skipped_when_other_source_active(monkeypatch):
    """DOMESTIC_SYNC_SOURCE=toss 면 키움 국내 스냅샷 쓰기(update_portfolio)를 건너뜀.

    2026-07-25 추가 — 국내 권위가 kiwoom 고정이던 시절엔 게이트가 없었는데, toss 를
    도입하면서 이중 writer 충돌 방지용으로 추가(미지정 시엔 기존처럼 kiwoom 이 기본).
    """
    import kiwoom_sync_rest as K
    monkeypatch.setenv("DOMESTIC_SYNC_SOURCE", "toss")
    monkeypatch.setattr(K, "sync_us_balance", lambda: None)
    monkeypatch.setattr(K, "sync_us_transactions", lambda: None)
    monkeypatch.setattr(K, "fetch_domestic_balance",
                        lambda: [{"ticker": "005930", "name": "삼성전자", "shares": 1,
                                  "avg_price_krw": 70000, "current_price_krw": 72000,
                                  "value_krw": 72000, "return_pct": 2.9}])
    monkeypatch.setattr(K, "_touch_sync_timestamp", lambda: None)
    calls = []
    monkeypatch.setattr(K, "update_portfolio", lambda holdings: calls.append(holdings) or "unused")
    monkeypatch.setattr(K, "_notify", lambda msg: None)

    K.main()
    assert calls == []                                       # 쓰기 함수 호출 안 됨

    monkeypatch.delenv("DOMESTIC_SYNC_SOURCE", raising=False)
    K.main()
    assert len(calls) == 1                                    # 미지정 시 기존처럼 kiwoom 이 씀


# ── 키움 해외 거래내역 → trade_events (차트 마커·모으기 실행 이력) ────────────

def test_kiwoom_parse_us_transactions():
    import kiwoom_sync_rest as K
    result = {"return_code": 0, "result_list": [
        {"deal_dt": "20260713", "deal_kind_nm": "해외주식매수", "stk_cd": "AAPL",
         "stk_nm": "애플", "deal_qty": "2", "deal_amt": "420.50", "deal_no": "T001"},
        {"deal_dt": "20260713", "deal_kind_nm": "주식모으기매수", "stk_cd": "MSFT",
         "stk_nm": "마이크로소프트", "deal_qty": "0.5", "deal_amt": "225.00", "deal_no": "T002"},
        {"deal_dt": "20260712", "deal_kind_nm": "외화입금", "stk_cd": "", "deal_qty": "0",
         "deal_amt": "1000", "deal_no": "T003"},                    # 비체결 → 스킵
        {"deal_dt": "20260712", "deal_kind_nm": "해외주식매도", "stk_cd": "NVDA",
         "stk_nm": "엔비디아", "deal_qty": "1", "deal_amt": "180.00", "deal_no": "T004"},
    ]}
    rows = K._parse_us_transactions(result)
    assert [r["ticker"] for r in rows] == ["AAPL", "MSFT", "NVDA"]
    assert rows[0]["side"] == "buy" and rows[0]["price"] == 210.25   # deal_amt/qty 근사
    assert rows[1]["kind"] == "주식모으기매수" and rows[1]["qty"] == 0.5   # 모으기 = 소수점 매수
    assert rows[2]["side"] == "sell"
    assert K._parse_us_transactions({}) == []


def test_kiwoom_sync_us_transactions_idempotent(monkeypatch):
    """event_id=거래번호 — 같은 내역 재동기화해도 원장 중복 없음 (store 격리는 conftest)."""
    import kiwoom_sync_rest as K
    from lib import trade_events
    rows = [{"ticker": "AAPL", "side": "buy", "qty": 2.0, "price": 210.25,
             "date": "20260713", "deal_no": "TX9", "kind": "해외주식매수", "name": "애플"}]
    monkeypatch.setattr(K, "fetch_us_transactions", lambda days=7: rows)
    assert K.sync_us_transactions() == 1
    assert K.sync_us_transactions() == 1                             # record_trade 가 중복 스킵
    mine = [t for t in trade_events.all_trades() if t.get("broker_order_id") == "TX9"]
    assert len(mine) == 1 and mine[0]["date"] == "2026-07-13"
    assert mine[0]["note"] == "해외주식매수" and mine[0]["source"] == "kiwoom_us_sync"


# ── main() 전체 플로우 (2026-07-25: NameError 회귀방지 — 단위테스트만으론 못 잡음) ──

def test_toss_sync_main_runs_end_to_end_with_domestic_apply(tmp_path, monkeypatch):
    """DOMESTIC_SYNC_SOURCE=toss 로 main() 전체를 실행 — 예외 없이 끝까지 도는지 확인.

    개별 함수 단위테스트만으론 main() 안의 오타/미정의 변수(예: diff→us_diff/kr_diff
    리네임 누락) 를 못 잡음 — 실제로 한 번 이 사고가 났었음(2026-07-25).
    """
    import toss_sync as TS
    from providers import toss_api as T
    from lib import overseas_snapshot as O
    from lib import domestic_snapshot as D

    monkeypatch.setenv("TOSS_API_KEY", "k")
    monkeypatch.setenv("TOSS_API_SECRET", "s")
    monkeypatch.setenv("DOMESTIC_SYNC_SOURCE", "toss")
    monkeypatch.delenv("OVERSEAS_SYNC_SOURCE", raising=False)
    monkeypatch.setattr(T, "accounts", lambda: [{"accountNo": "1", "accountSeq": 1, "accountType": "BROKERAGE"}])
    monkeypatch.setattr(T, "holdings_raw", lambda account_seq=None: _OVERVIEW)
    monkeypatch.setattr(T, "buying_power", lambda currency, account_seq=None: 41011.0)
    monkeypatch.setattr(TS, "_notify", lambda msg: None)
    monkeypatch.setattr(sys, "argv", ["toss_sync.py"])   # main() 이 argparse 로 sys.argv 직접 읽음

    snap_path = tmp_path / "portfolio_snapshot.json"
    snap_path.write_text(json.dumps({"snapshot_date": "2026-07-07"}), encoding="utf-8")
    monkeypatch.setattr(O, "PORTFOLIO_PATH", str(snap_path))
    monkeypatch.setattr(D, "PORTFOLIO_PATH", str(snap_path))

    assert TS.main() == 0
    snap = json.loads(snap_path.read_text(encoding="utf-8"))
    assert snap["domestic"]["cash_krw"] == 41011.0
    assert snap["domestic"]["holdings"][0]["ticker"] == "005930"
