#!/usr/bin/env python3
"""test_kis_quote.py — KIS 실계좌 시세 어댑터 (무네트워크·폐형해). 읽기전용 불변 강제."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import providers.kis_quote as kq


# ── 도메인 하드락 ─────────────────────────────────────────────────────────────

def test_assert_quote_url_accepts_real_rejects_others():
    kq._assert_quote_url(kq._QUOTE_BASE + "/uapi/domestic-stock/v1/quotations/inquire-price")
    with pytest.raises(RuntimeError):
        kq._assert_quote_url("https://openapivts.koreainvestment.com:29443/x")   # 모의 거부
    with pytest.raises(RuntimeError):
        kq._assert_quote_url("https://evil.example.com/uapi/x")


# ── 순수 파서 ─────────────────────────────────────────────────────────────────

def test_parse_kr_price():
    p = kq.parse_kr_price({"stck_prpr": "71,000", "acml_vol": "12345678"})
    assert p["price"] == 71000.0 and p["volume"] == 12345678.0


def test_parse_kr_orderbook_10_levels_and_best():
    out = {}
    for i in range(1, 11):
        out[f"askp{i}"] = str(71000 + i * 100)
        out[f"askp_rsqn{i}"] = str(i * 10)
        out[f"bidp{i}"] = str(70900 - i * 100)
        out[f"bidp_rsqn{i}"] = str(i * 5)
    ob = kq.parse_kr_orderbook(out)
    assert len(ob["asks"]) == 10 and len(ob["bids"]) == 10
    assert ob["best_ask"] == 71100.0 and ob["best_bid"] == 70800.0
    assert ob["asks"][0] == (71100.0, 10.0) and ob["bids"][0] == (70800.0, 5.0)


def test_parse_kr_orderbook_skips_empty_levels():
    ob = kq.parse_kr_orderbook({"askp1": "100", "askp_rsqn1": "5", "bidp1": "0", "bidp_rsqn1": "0"})
    assert ob["best_ask"] == 100.0 and ob["best_bid"] is None and ob["bids"] == []


def test_parse_overseas_price():
    assert kq.parse_overseas_price({"last": "283.78", "tvol": "1000"})["price"] == 283.78
    assert kq.parse_overseas_price({"last": "0"})["price"] is None     # 0/빈값 → None
    assert kq.parse_overseas_price({})["price"] is None


# ── 게이트 / fail-closed ──────────────────────────────────────────────────────

def test_is_enabled_honors_env(monkeypatch):
    monkeypatch.setenv("REALTIME_ENABLED", "true")
    assert kq.is_enabled() is True
    monkeypatch.setenv("REALTIME_ENABLED", "false")
    assert kq.is_enabled() is False


def test_disabled_returns_none_no_work(monkeypatch):
    monkeypatch.delenv("REALTIME_ENABLED", raising=False)
    assert kq.get_quote("005930") is None
    assert kq.get_orderbook("005930") is None


def test_fail_closed_no_key_makes_no_http(monkeypatch, tmp_path):
    """REALTIME on 이어도 실전 키 없으면 HTTP 0 + None (fail-closed)."""
    monkeypatch.setenv("REALTIME_ENABLED", "true")
    monkeypatch.delenv("KOREA_API_KEY", raising=False)
    monkeypatch.delenv("KOREA_API_SECRET", raising=False)
    monkeypatch.setattr(kq, "_TOKEN_FILE", str(tmp_path / "none.json"))
    monkeypatch.setattr(kq, "_token_cache", {"token": None, "exp": 0.0})

    def _boom(*a, **k):
        raise AssertionError("네트워크 호출 발생 — fail-closed 위반")
    monkeypatch.setattr(kq.requests, "get", _boom)
    monkeypatch.setattr(kq.requests, "post", _boom)

    assert kq.get_quote("005930", market="KR") is None
    assert kq.get_quote("AAPL", market="US") is None
    assert kq.get_orderbook("005930") is None


# ── 읽기전용 구조 불변 (grep) ─────────────────────────────────────────────────

def test_module_has_no_order_path():
    """주문 경로가 소스에 존재하지 않음을 강제 — read-only 보장."""
    src = open(kq.__file__, encoding="utf-8").read()
    for forbidden in ("place_order", "/trading/order", "ORD_QTY", "OVRS_ORD_UNPR", "hashkey",
                      "VTTT", "TTTC", "kt10000", "kt10001"):
        assert forbidden not in src, f"읽기전용 위반: '{forbidden}' 발견"
    # 유일한 POST 는 토큰 발급뿐
    assert src.count("requests.post") == 1
    assert "_TOKEN_URL" in src and "/oauth2/tokenP" in src


def test_http_get_circuit_breaker_opens_and_self_heals(monkeypatch):
    """full-실패 시 서킷 개방 → 이후 호출 즉시 None(요청 미발생), 성공 시 리셋 (Batch P 회귀).

    대시보드 8초 프래그먼트가 KIS 장애 시 매 틱 ~수십초 블로킹되던 것을 차단.
    """
    kq._CB["open_until"] = 0.0    # 상태 초기화(결정성)
    calls = {"n": 0}

    def _fail(*a, **k):
        calls["n"] += 1
        raise RuntimeError("kis down")
    monkeypatch.setattr(kq.requests, "get", _fail)

    url = kq._QUOTE_BASE + "/uapi/domestic-stock/v1/quotations/inquire-price"
    assert kq._http_get(url, {}, {}, retries=2) is None    # full 실패 → 서킷 개방
    n1 = calls["n"]
    assert kq._http_get(url, {}, {}, retries=2) is None     # 개방 중 → 즉시 폴백
    assert calls["n"] == n1                                 # 서킷 개방 중 요청 0

    # 성공 응답 → 서킷 닫힘(self-heal)
    kq._CB["open_until"] = 0.0
    class _OK:
        def raise_for_status(self): pass
        def json(self): return {"rt_cd": "0"}
    monkeypatch.setattr(kq.requests, "get", lambda *a, **k: _OK())
    assert kq._http_get(url, {}, {}, retries=2) == {"rt_cd": "0"}
    assert kq._CB["open_until"] == 0.0


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
