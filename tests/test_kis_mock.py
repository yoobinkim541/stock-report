#!/usr/bin/env python3
"""test_kis_mock.py — KIS 해외 모의 어댑터 안전·순수 단위테스트 (무네트워크).

핵심: 모의 도메인 하드락·계좌 파싱·주문 바디·fail-closed(계좌#없으면 HTTP 0).
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import kis_mock


# ── 도메인 하드락 (안전 #1) ───────────────────────────────────────────
def test_assert_mock_url_blocks_real_domain():
    with pytest.raises(RuntimeError):
        kis_mock._assert_mock_url("https://openapi.koreainvestment.com:9443/uapi/x")
    # 모의 도메인은 통과
    kis_mock._assert_mock_url(kis_mock._MOCK_BASE + "/uapi/x")


def test_mock_base_is_vts_domain():
    assert "openapivts.koreainvestment.com" in kis_mock._MOCK_BASE
    assert "openapi.koreainvestment.com:9443" not in kis_mock._MOCK_BASE   # 실전 아님


# ── 계좌 파싱 ─────────────────────────────────────────────────────────
def test_parse_account():
    assert kis_mock._parse_account("50012345-01") == ("50012345", "01")
    assert kis_mock._parse_account("50012345") == ("50012345", "01")
    assert kis_mock._parse_account("") == (None, None)
    assert kis_mock._parse_account(None) == (None, None)


# ── 주문 빌더 ─────────────────────────────────────────────────────────
def test_order_tr_id():
    assert kis_mock._order_tr_id("buy") == kis_mock._TR_BUY
    assert kis_mock._order_tr_id("sell") == kis_mock._TR_SELL


def test_build_order_body_whole_share_limit():
    b = kis_mock.build_order_body("50012345", "01", "NASD", "msft", 3.9, 100.0)
    assert b["PDNO"] == "MSFT" and b["ORD_QTY"] == "3"          # 정수주
    assert b["OVRS_ORD_UNPR"] == "100.0000" and b["ORD_DVSN"] == "00"
    assert b["CANO"] == "50012345" and b["OVRS_EXCG_CD"] == "NASD"


def test_exchange_of():
    assert kis_mock.exchange_of("ORCL") == "NYSE"
    assert kis_mock.exchange_of("SGOV") == "AMEX"
    assert kis_mock.exchange_of("TQQQ") == "NASD"
    assert kis_mock.exchange_of("SOXL") == "AMEX"
    assert kis_mock.exchange_of("SSO") == "AMEX"
    assert kis_mock.exchange_of("NVDL") == "NASD"
    assert kis_mock.exchange_of("TSLL") == "NASD"
    assert kis_mock.exchange_of("MSFT") == "NASD"               # 기본


# ── fail-closed (계좌#·수량·가격) — HTTP 0 ────────────────────────────
def test_place_order_fail_closed_no_account(monkeypatch):
    monkeypatch.delenv("KOREA_MOCK_ACCOUNT_NO", raising=False)

    class _NoHTTP:
        def post(self, *a, **k):
            raise AssertionError("계좌# 없으면 HTTP 호출 금지")

        def get(self, *a, **k):
            raise AssertionError("계좌# 없으면 HTTP 호출 금지")

    monkeypatch.setattr(kis_mock, "requests", _NoHTTP())
    r = kis_mock.place_order("MSFT", 1, "buy", 100.0)
    assert r["ok"] is False and "계좌" in r["msg"]               # HTTP 미호출


def test_place_order_rejects_zero_qty_and_no_price(monkeypatch):
    monkeypatch.setenv("KOREA_MOCK_ACCOUNT_NO", "50012345-01")

    class _NoHTTP:
        def post(self, *a, **k):
            raise AssertionError("검증 실패 주문은 HTTP 금지")

    monkeypatch.setattr(kis_mock, "requests", _NoHTTP())
    assert kis_mock.place_order("MSFT", 0, "buy", 100.0)["ok"] is False    # 수량 0
    assert kis_mock.place_order("MSFT", 1, "buy", 0.0)["ok"] is False      # 가격 0(해외 지정가 필요)
    assert kis_mock.place_order("MSFT", 1, "hold", 100.0)["ok"] is False   # 잘못된 side


# ── 주문 500/응답유실 시 재시도 안전성 (2026-07-24) ────────────────────
class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakePostSeq:
    """POST 호출을 순서대로 소비 — 첫 호출은 항상 hashkey(성공 취급), 그 뒤가 실제 주문 시도들."""

    def __init__(self, order_results):
        # order_results: 각 원소가 dict(성공 응답) 또는 Exception 인스턴스
        self._queue = [_FakeResponse({"HASH": "x"})] + list(order_results)
        self.calls = 0

    def post(self, *a, **k):
        self.calls += 1
        item = self._queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def get(self, *a, **k):
        raise AssertionError("이 테스트에서 GET 은 get_balance() monkeypatch 로 우회해야 함")


def _prep_order_env(monkeypatch, fake_requests):
    monkeypatch.setenv("KOREA_MOCK_ACCOUNT_NO", "50012345-01")
    monkeypatch.setattr(kis_mock, "_get_token", lambda: "faketoken")
    monkeypatch.setattr(kis_mock, "requests", fake_requests)


def test_place_order_confirms_fill_after_lost_response(monkeypatch):
    """500(응답유실) 후 잔고조회로 실체결(수량 변화) 확인되면 재시도 없이 성공 반환."""
    fake = _FakePostSeq([RuntimeError("500 Server Error")])
    _prep_order_env(monkeypatch, fake)
    calls = iter([{"ok": True, "positions": {"MSFT": {"shares": 0}}},
                  {"ok": True, "positions": {"MSFT": {"shares": 1}}}])
    monkeypatch.setattr(kis_mock, "get_balance", lambda: next(calls))
    r = kis_mock.place_order("MSFT", 1, "buy", 100.0)
    assert r["ok"] is True and "체결 확인됨" in r["msg"]
    assert fake.calls == 2                                   # hashkey + 주문 1회뿐 (재시도 안 함)


def test_place_order_retries_once_after_confirmed_no_fill(monkeypatch):
    """500 후 잔고조회로 미체결 확인되면 그때만 1회 재시도, 재시도 성공 시 정상 결과 반환."""
    fake = _FakePostSeq([RuntimeError("500 Server Error"),
                         _FakeResponse({"rt_cd": "0", "msg1": "체결", "output": {"ODNO": "123"}})])
    _prep_order_env(monkeypatch, fake)
    monkeypatch.setattr(kis_mock, "get_balance",
                        lambda: {"ok": True, "positions": {"MSFT": {"shares": 0}}})   # 변화 없음
    r = kis_mock.place_order("MSFT", 1, "buy", 100.0)
    assert r["ok"] is True and r["ord_no"] == "123"
    assert fake.calls == 3                                   # hashkey + 주문 시도 2회(원본+재시도)


def test_place_order_no_retry_when_fill_status_unknown(monkeypatch):
    """잔고조회 자체가 실패해 체결여부를 확인 못 하면 — 재시도 없이 실패 반환(중복체결 위험 회피)."""
    fake = _FakePostSeq([RuntimeError("500 Server Error")])
    _prep_order_env(monkeypatch, fake)
    monkeypatch.setattr(kis_mock, "get_balance", lambda: {"ok": False})
    r = kis_mock.place_order("MSFT", 1, "buy", 100.0)
    assert r["ok"] is False and "확인불가" in r["msg"]
    assert fake.calls == 2                                   # hashkey + 주문 1회뿐 — 재시도 안 함


def test_is_enabled(monkeypatch):
    monkeypatch.setenv("KOREA_MOCK_ENABLED", "true")
    assert kis_mock.is_enabled() is True
    monkeypatch.setenv("KOREA_MOCK_ENABLED", "false")
    assert kis_mock.is_enabled() is False


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
