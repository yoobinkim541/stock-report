#!/usr/bin/env python3
"""test_quotes_poller.py — REST 시세 폴러 + realtime_quotes 2계층 병합 (무네트워크)."""
import json
import os
import sys
import time
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import quotes_poller as Q
import providers.realtime_quotes as rq


# ── 안전: 시세 read-only (grep) ──────────────────────────────────────────────

def test_poller_source_is_read_only():
    src = open(os.path.join(os.path.dirname(__file__), "..", "quotes_poller.py"),
               encoding="utf-8").read()
    for banned in ("/api/v1/orders", "/api/dostk/ordr", "/api/us/ordr",
                   "kt10000", "kt10001", "ust21150"):
        assert banned not in src, f"주문 경로 발견: {banned}"
    assert "ka10095" in src                       # 시세 조회 TR 만


# ── 시장 개장 창 ─────────────────────────────────────────────────────────────

def test_kr_market_open_kst_window():
    assert Q.kr_market_open(datetime(2026, 7, 13, 0, 30, tzinfo=timezone.utc))    # 월 09:30 KST
    assert not Q.kr_market_open(datetime(2026, 7, 13, 7, 0, tzinfo=timezone.utc))  # 16:00 KST 마감
    assert not Q.kr_market_open(datetime(2026, 7, 12, 1, 0, tzinfo=timezone.utc))  # 일요일
    # KST 월요일 아침 = UTC 일요일 밤 — KST 요일 기준으로 개장 판정
    assert Q.kr_market_open(datetime(2026, 7, 13, 0, 0, tzinfo=timezone.utc))


def test_us_market_open_window():
    assert Q.us_market_open(datetime(2026, 7, 13, 14, 0, tzinfo=timezone.utc))
    assert not Q.us_market_open(datetime(2026, 7, 13, 22, 0, tzinfo=timezone.utc))
    assert not Q.us_market_open(datetime(2026, 7, 11, 15, 0, tzinfo=timezone.utc))  # 토


def test_is_kr_symbol():
    assert Q.is_kr_symbol("005930") and Q.is_kr_symbol("005930.KS")
    assert not Q.is_kr_symbol("AAPL") and not Q.is_kr_symbol("BRK.B")


# ── 키움 ka10095 파서 ────────────────────────────────────────────────────────

def test_parse_kiwoom_quotes_sign_and_comma():
    res = {"return_code": 0, "atn_stk_infr": [
        {"stk_cd": "005930", "cur_prc": "+72,000"},
        {"stk_cd": "000660", "cur_prc": "-181,500"},
        {"stk_cd": "invalid", "cur_prc": "N/A"},
    ]}
    out = Q.parse_kiwoom_quotes(res)
    assert out == {"005930": 72000.0, "000660": 181500.0}
    assert Q.parse_kiwoom_quotes({}) == {} and Q.parse_kiwoom_quotes(None) == {}


# ── 폴링 사이클 (fake 소스 주입) ─────────────────────────────────────────────

def _both_open():
    return datetime(2026, 7, 13, 14, 0, tzinfo=timezone.utc)   # US 개장 (KR 23:00 KST 마감)


def test_poll_once_us_open_only_polls_us(tmp_path):
    cache = str(tmp_path / "rest_quotes.json")
    seen = {}

    def toss(symbols):
        seen["symbols"] = list(symbols)
        return {s: 100.0 for s in symbols}

    n = Q.poll_once(now=_both_open(), universe=["MSFT", "005930", "NVDA"],
                    toss_fn=toss, kiwoom_fn=lambda c: {}, cache_path=cache)
    assert n == 2 and set(seen["symbols"]) == {"MSFT", "NVDA"}   # KR 마감 → 미조회
    data = json.loads(open(cache, encoding="utf-8").read())
    assert data["MSFT"]["price"] == 100.0 and data["MSFT"]["src"] == "toss"
    assert abs(time.time() - data["__heartbeat__"]["ts"]) < 5


def test_poll_once_kiwoom_fallback_for_missing_kr(tmp_path):
    cache = str(tmp_path / "rest_quotes.json")
    kr_open_now = datetime(2026, 7, 13, 1, 0, tzinfo=timezone.utc)   # KR 10:00 KST
    n = Q.poll_once(now=kr_open_now, universe=["005930", "000660"],
                    toss_fn=lambda s: {"005930": 72000.0},           # 000660 누락
                    kiwoom_fn=lambda codes: {c: 181500.0 for c in codes},
                    cache_path=cache)
    assert n == 2
    data = json.loads(open(cache, encoding="utf-8").read())
    assert data["005930"]["src"] == "toss"
    assert data["000660"]["src"] == "kiwoom"                          # 폴백 채움


def test_poll_once_closed_market_noop(tmp_path):
    cache = str(tmp_path / "rest_quotes.json")
    closed = datetime(2026, 7, 12, 10, 0, tzinfo=timezone.utc)        # 일요일
    assert Q.poll_once(now=closed, universe=["MSFT"], toss_fn=lambda s: {"MSFT": 1.0},
                       kiwoom_fn=lambda c: {}, cache_path=cache) == 0
    assert not os.path.exists(cache)


def test_build_universe_dedupe_cap(monkeypatch):
    monkeypatch.setenv("QUOTES_POLL_EXTRA", "TSLA, tsla ,COIN")
    syms = Q.build_universe(cap=500)
    assert syms.count("TSLA") == 1 and "COIN" in syms
    assert all("." not in s for s in syms)                            # base 심볼 정규화
    assert len(syms) <= 500


# ── realtime_quotes 2계층 병합 ───────────────────────────────────────────────

def _write(path, entries):
    now = time.time()
    d = {"__heartbeat__": {"ts": now}}
    for sym, price, age in entries:
        d[sym] = {"price": price, "ts": now - age}
    path.write_text(json.dumps(d), encoding="utf-8")


def test_realtime_merge_ws_wins_then_rest(tmp_path, monkeypatch):
    ws, rest = tmp_path / "ws.json", tmp_path / "rest.json"
    _write(ws, [("MSFT", 450.0, 5)])                                  # WS 에 MSFT 만
    _write(rest, [("MSFT", 449.0, 5), ("ORCL", 220.0, 5)])            # REST 에 둘 다
    monkeypatch.setattr(rq, "CACHE_PATH", str(ws))
    monkeypatch.setattr(rq, "REST_CACHE_PATH", str(rest))
    monkeypatch.setenv("REALTIME_ENABLED", "true")
    monkeypatch.setenv("QUOTES_POLL_ENABLED", "true")
    assert rq.get_price("MSFT") == 450.0                              # WS 우선
    assert rq.get_price("ORCL") == 220.0                              # WS 미커버 → REST
    assert rq.get_price("NVDA") is None                               # 둘 다 없음 → yfinance 폴백


def test_realtime_rest_only_mode(tmp_path, monkeypatch):
    """WS(KIS) 없이 REST 폴러만 켜도 seam 활성 — 커버리지 확대의 핵심."""
    rest = tmp_path / "rest.json"
    _write(rest, [("005930", 72000.0, 3)])
    monkeypatch.setattr(rq, "CACHE_PATH", str(tmp_path / "no_ws.json"))
    monkeypatch.setattr(rq, "REST_CACHE_PATH", str(rest))
    monkeypatch.setenv("REALTIME_ENABLED", "false")
    monkeypatch.setenv("QUOTES_POLL_ENABLED", "true")
    assert rq.enabled() is True
    assert rq.get_price("005930") == 72000.0
    assert rq.get_orderbook("005930") is None                         # 호가는 WS 전용


def test_realtime_rest_stale_heartbeat_distrusted(tmp_path, monkeypatch):
    rest = tmp_path / "rest.json"
    now = time.time()
    rest.write_text(json.dumps({"__heartbeat__": {"ts": now - 600},
                                "MSFT": {"price": 450.0, "ts": now - 600}}), encoding="utf-8")
    monkeypatch.setattr(rq, "CACHE_PATH", str(tmp_path / "no_ws.json"))
    monkeypatch.setattr(rq, "REST_CACHE_PATH", str(rest))
    monkeypatch.setenv("REALTIME_ENABLED", "false")
    monkeypatch.setenv("QUOTES_POLL_ENABLED", "true")
    assert rq.get_price("MSFT") is None                               # 폴러 죽음 → 전체 불신


def test_realtime_all_disabled(monkeypatch):
    monkeypatch.setenv("REALTIME_ENABLED", "false")
    monkeypatch.setenv("QUOTES_POLL_ENABLED", "false")
    assert rq.enabled() is False and rq.get_price("MSFT") is None
