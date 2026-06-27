#!/usr/bin/env python3
"""test_command_cache.py — /accum·/indicators 인프로세스 TTL 캐시 (무네트워크)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import bot.accum_commands as ac
import bot.guest_report as gr


def test_accum_cache_hit(monkeypatch):
    calls = []
    monkeypatch.setattr(ac, "rank_accumulation",
                        lambda u, limit, min_score: calls.append(1) or ["x"])
    ac._ACCUM_CACHE.clear()
    r1 = ac._cached_rank(["AAPL", "MSFT"], 10, 60)
    r2 = ac._cached_rank(["MSFT", "AAPL"], 10, 60)   # 같은 집합(정렬 키) → 캐시 적중
    assert r1 == r2 == ["x"] and len(calls) == 1


def test_accum_cache_ttl_expired(monkeypatch):
    calls = []
    monkeypatch.setattr(ac, "rank_accumulation",
                        lambda u, limit, min_score: calls.append(1) or ["y"])
    monkeypatch.setattr(ac, "_ACCUM_TTL", 0)         # 즉시 만료 → 매번 재조회
    ac._ACCUM_CACHE.clear()
    ac._cached_rank(["A"], 1, 0)
    ac._cached_rank(["A"], 1, 0)
    assert len(calls) == 2


def test_indicators_cache_hit_case_insensitive(monkeypatch):
    calls = []
    monkeypatch.setattr(gr, "_build_indicators_raw", lambda t: calls.append(1) or f"IND:{t}")
    gr._IND_CACHE.clear()
    a = gr.build_indicators("QQQ")
    b = gr.build_indicators("qqq")                   # 대소문자 무관 키 → 캐시 적중
    assert a == b == "IND:QQQ" and len(calls) == 1


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
