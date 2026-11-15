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

def _us_tx(date, no, kind, remark, ticker, qty, *, price=0.0, amount=0.0, name=""):
    """ust21100 실제 응답 행 모양 — 종류는 deal_kind_nm, 방향은 rmrk_nm."""
    return {"deal_dt": date, "deal_no": str(no), "deal_kind_nm": kind, "rmrk_nm": remark,
            "stk_cd": ticker, "stk_nm": name or ticker, "deal_qty": str(qty),
            "uv_exrt": str(price), "fc_deal_amt": str(amount)}


def test_kiwoom_parse_us_transactions():
    """실측(2026-11-15): 방향이 rmrk_nm 에만 와서 예전 파서는 실제 체결을 한 건도 못 잡았다."""
    import kiwoom_sync_rest as K
    result = {"return_code": 0, "result_list": [
        _us_tx("20261113", 3, "매매", "매수", "AAPL", 2, price=210.25, amount=420.50),
        _us_tx("20261113", 4, "소수점매매", "매수", "MSFT", "0.5", amount=225.00),
        _us_tx("20261112", 1, "입출금", "배당금입금", "", 0, amount=12.3),     # 수량 변화 없음 → 스킵
        _us_tx("20261112", 2, "소수점매매", "매도", "NVDA", "0.25", price=180.0, amount=45.0),
        _us_tx("20261111", 5, "입출고", "대체온주전환출고", "ORCL", 1),
        _us_tx("20261111", 6, "입출고", "대체온주전환입고", "ORCL", 1),
        _us_tx("20261110", 7, "입출고", "대체출고", "QLD", 1),
    ]}
    rows = K._parse_us_transactions(result)
    got = [(r["ticker"], r["side"], r["book"], r["move"]) for r in rows]
    assert got == [
        ("AAPL", "buy", "whole", "trade"),
        ("MSFT", "buy", "fractional", "trade"),
        ("NVDA", "sell", "fractional", "trade"),
        ("ORCL", "sell", "fractional", "convert"),      # 소수점 1주가 빠지고
        ("ORCL", "buy", "whole", "convert"),            # 온주 1주로 들어온다
        ("QLD", "sell", "", "transfer"),                # "입출고" 의 "출고" 가 아니라 적요로 방향 판정
    ]
    assert rows[0]["price"] == 210.25 and rows[0]["amount"] == 420.50
    assert rows[1]["price"] == 450.0 and rows[1]["qty"] == 0.5        # 단가 없으면 금액/수량
    assert rows[1]["kind"] == "소수점매매 매수"
    assert K._parse_us_transactions({}) == []


def _us_row(date, no, side, ticker, qty, *, book="whole", move="trade", price=100.0):
    return {"ticker": ticker, "side": side, "qty": qty, "price": price, "amount": price * qty,
            "date": date, "deal_no": str(no), "kind": "", "name": ticker, "book": book, "move": move}


def test_kiwoom_reconstruct_us_positions_from_history():
    """잔고 조회는 온주만 주므로 소수점 보유는 거래내역 누적 — 매도·전환이 빠지면 과대 표시된다."""
    import kiwoom_sync_rest as K
    history = [
        _us_row("20260901", 1, "buy", "ORCL", 0.6, book="fractional", price=100.0),
        _us_row("20260902", 1, "buy", "ORCL", 0.6, book="fractional", price=200.0),
        # 옛 표기: 같은 날·같은 수량 대체출고/입고 짝 = 소수점 1주 → 온주 전환
        _us_row("20260903", 1, "sell", "ORCL", 1.0, book="", move="transfer"),
        _us_row("20260903", 2, "buy", "ORCL", 1.0, book="", move="transfer"),
        # 거래번호는 체결 순서가 아니다 — 당일 매수분을 앞 번호로 매도
        _us_row("20260904", 1, "sell", "NOW", 0.4, book="fractional"),
        _us_row("20260904", 2, "buy", "NOW", 0.4, book="fractional"),
        _us_row("20260905", 1, "buy", "QLD", 3.0),
        _us_row("20260906", 1, "sell", "QLD", 1.0, book="", move="transfer"),   # 짝 없음 = 온주 출고
    ]
    pos = K.reconstruct_us_positions(history)
    assert pos["ORCL"]["whole"] == 1.0
    assert pos["ORCL"]["fractional"] == pytest.approx(0.2)
    assert pos["ORCL"]["fractional_cost"] == pytest.approx(30.0)      # 평균 150 × 0.2
    assert pos["NOW"]["fractional"] == pytest.approx(0.0)             # 전량 매도
    assert pos["QLD"]["whole"] == 2.0 and pos["QLD"]["fractional"] == 0.0

    balance = [{"ticker": "ORCL", "shares": 1, "name": "오라클", "current_price_usd": 160.0},
               {"ticker": "QLD", "shares": 2, "current_price_usd": 90.0}]
    assert K.whole_share_mismatches(pos, balance) == []
    assert K.whole_share_mismatches(pos, balance[:1]) == ["QLD: 거래내역 2주 ≠ 잔고 0주"]

    held = K.fractional_holdings(pos, balance)
    assert [h["ticker"] for h in held] == ["ORCL"]                    # 전량 매도한 NOW 는 빠진다
    assert held[0]["shares"] == 0.2 and held[0]["avg_price_usd"] == 150.0
    assert held[0]["value_usd"] == 32.0 and held[0]["return_pct"] == 6.67


def test_kiwoom_sync_us_transactions_idempotent(monkeypatch):
    """event_id=날짜+거래번호 — 거래번호는 날짜별 일련번호라 번호만으로는 다른 날 체결과 겹친다."""
    import kiwoom_sync_rest as K
    from lib import trade_events
    history = [
        _us_row("20260101", 1, "buy", "AAPL", 1.0),                              # 14일보다 오래됨 → 미기록
        _us_row("20260712", 1, "sell", "ZZKW", 0.5, book="fractional", price=180.0),
        _us_row("20260713", 1, "buy", "ZZKW", 2.0, price=210.25),
        _us_row("20260713", 2, "sell", "ZZKW", 1.0, book="fractional", move="convert"),   # 체결 아님
    ]
    monkeypatch.setattr(K, "refresh_us_transaction_history", lambda **kw: history)
    assert K.sync_us_transactions() == 2
    assert K.sync_us_transactions() == 0                              # 재동기화 — 새 기록 없음
    mine = sorted((t for t in trade_events.all_trades() if t.get("ticker") == "ZZKW"),
                  key=lambda t: t["date"])
    assert [(t["date"], t["side"], t["qty"]) for t in mine] == [
        ("2026-07-12", "sell", 0.5), ("2026-07-13", "buy", 2.0)]
    assert {t["source"] for t in mine} == {"kiwoom_us_sync"}


def test_kiwoom_us_trades_skip_buys_already_logged_by_accumulate_cron(monkeypatch):
    """모으기 크론이 종가 기준으로 먼저 적은 매수(manual_holding)와 같은 체결은 다시 적지 않는다."""
    import kiwoom_sync_rest as K
    from lib import trade_events
    trade_events.record_trade(ticker="ZZPL", side="buy", qty=0.03, price=100.0, account="overseas_fractional",
                              source="manual_holding", market="US", currency="USD",
                              timestamp="2026-08-12T06:10:00", event_id="manual:zzpl:20260812")
    history = [
        _us_row("20260813", 1, "buy", "ZZPL", 0.031, book="fractional"),    # 크론 기록(전날)과 같은 체결
        _us_row("20260813", 2, "sell", "ZZPL", 0.5, book="fractional"),     # 매도는 크론이 모른다
        _us_row("20260813", 3, "buy", "ZZNP", 0.2, book="fractional"),      # 플랜에 없는 종목
        _us_row("20260813", 4, "buy", "ZZPL", 1.0),                         # 온주 매수
    ]
    assert K._record_us_trades(history) == 3
    synced = [(t["ticker"], t["side"], t["qty"]) for t in trade_events.all_trades()
              if t.get("source") == "kiwoom_us_sync" and t["ticker"].startswith("ZZ") and t["date"] == "2026-08-13"]
    assert sorted(synced) == [("ZZNP", "buy", 0.2), ("ZZPL", "buy", 1.0), ("ZZPL", "sell", 0.5)]


def test_kiwoom_us_history_refresh_replaces_recent_window(monkeypatch):
    """증분 갱신은 마지막 캐시 날짜 14일 전부터 다시 받아 그 구간을 통째로 교체한다."""
    import kiwoom_sync_rest as K
    import store
    from datetime import datetime, timedelta
    day = lambda n: (datetime.now() - timedelta(days=n)).strftime("%Y%m%d")
    old = _us_row(day(60), 1, "buy", "MSFT", 1.0)
    cancelled = _us_row(day(3), 1, "buy", "MSFT", 9.0)                # 브로커가 취소·정정한 행
    store.replace_all(K._US_TX_COLLECTION, [old, cancelled])
    asked = []

    def fake_fetch(days):
        asked.append(days)
        return [_us_row(day(2), 1, "buy", "MSFT", 0.3, book="fractional")]

    monkeypatch.setattr(K, "fetch_us_transactions", fake_fetch)
    history = K.refresh_us_transaction_history()
    assert asked == [18]                                              # 3 + 14일 전부터 오늘까지
    assert [(r["date"], r["qty"]) for r in history] == [(day(60), 1.0), (day(2), 0.3)]
    assert store.all(K._US_TX_COLLECTION) == history

    monkeypatch.setattr(K, "fetch_us_transactions", lambda days: None)   # 조회 실패 → 캐시 유지
    assert K.refresh_us_transaction_history() is None
    assert store.all(K._US_TX_COLLECTION) == history


def test_kiwoom_sync_us_fractional_skips_when_history_disagrees_with_balance(tmp_path, monkeypatch):
    """온주 수량이 잔고와 다르면 거래내역이 불완전한 것 — 소수점 보유를 덮어쓰지 않는다."""
    import kiwoom_sync_rest as K
    from lib import overseas_snapshot as O
    snap_path = tmp_path / "portfolio_snapshot.json"
    snap_path.write_text(json.dumps({"overseas_fractional": {"holdings": [
        {"ticker": "NOW", "shares": 0.9354}, {"ticker": "ORCL", "shares": 2.8839}]}}), encoding="utf-8")
    monkeypatch.setattr(O, "PORTFOLIO_PATH", str(snap_path))
    monkeypatch.setenv("OVERSEAS_SYNC_SOURCE", "kiwoom")
    monkeypatch.setattr(K, "_us_quote_prices", lambda tickers: {})
    notices = []
    monkeypatch.setattr(K, "_notify", notices.append)
    history = [_us_row("20260901", 1, "buy", "ORCL", 0.2, book="fractional", price=150.0),
               _us_row("20260902", 1, "buy", "ORCL", 1.0, price=150.0)]

    monkeypatch.setattr(K, "refresh_us_transaction_history", lambda **kw: history)
    assert K.sync_us_fractional(history, [{"ticker": "ORCL", "shares": 5}]) is False
    assert "미반영" in notices[0] and "ORCL: 거래내역 1주 ≠ 잔고 5주" in notices[0]
    held = json.loads(snap_path.read_text(encoding="utf-8"))["overseas_fractional"]["holdings"]
    assert [h["ticker"] for h in held] == ["NOW", "ORCL"]             # 그대로

    assert K.sync_us_fractional(history, [{"ticker": "ORCL", "shares": 1,
                                           "current_price_usd": 160.0}]) is True
    snap = json.loads(snap_path.read_text(encoding="utf-8"))
    held = snap["overseas_fractional"]["holdings"]
    assert [(h["ticker"], h["shares"]) for h in held] == [("ORCL", 0.2)]   # 매도한 NOW 제거
    assert snap["overseas_fractional"]["summary"]["total_value_usd"] == 32.0
    assert "➖ NOW" in notices[1] and "ORCL" not in notices[1]        # 수량만 바뀐 종목은 알리지 않음


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
