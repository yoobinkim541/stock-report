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


def test_accum_cache_prunes_expired_entries_instead_of_growing_unbounded(monkeypatch):
    """감사 #26 — 서로 다른 universe 조합이 쌓이면 만료돼도 안 지워져 봇(장기실행
    프로세스)에서 무제한 커지던 문제. 새 조회 시 만료 엔트리를 정리해야 한다."""
    monkeypatch.setattr(ac, "rank_accumulation", lambda u, limit, min_score: ["x"])
    monkeypatch.setattr(ac, "_ACCUM_TTL", 0)          # 모든 기존 엔트리를 즉시 만료 취급
    ac._ACCUM_CACHE.clear()
    ac._ACCUM_CACHE[("STALE",), 1, 0] = (0.0, ["old"])   # 아주 오래 전에 만료된 엔트리
    assert len(ac._ACCUM_CACHE) == 1

    ac._cached_rank(["NEW"], 1, 0)

    assert ("STALE",) not in [k[0] for k in ac._ACCUM_CACHE]


def test_indicators_cache_prunes_expired_entries_instead_of_growing_unbounded(monkeypatch):
    """감사 #26 — /indicators 로 조회된 티커가 계속 쌓이면 만료돼도 안 지워져
    봇(장기실행 프로세스)에서 무제한 커지던 문제."""
    monkeypatch.setattr(gr, "_build_indicators_raw", lambda t: f"IND:{t}")
    monkeypatch.setattr(gr, "_IND_TTL", 0)            # 모든 기존 엔트리를 즉시 만료 취급
    gr._IND_CACHE.clear()
    gr._IND_CACHE["STALE"] = (0.0, "old")             # 아주 오래 전에 만료된 엔트리
    assert len(gr._IND_CACHE) == 1

    gr.build_indicators("NEW")

    assert "STALE" not in gr._IND_CACHE


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
