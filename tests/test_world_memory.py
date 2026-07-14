#!/usr/bin/env python3
"""test_world_memory.py — 월드 메모리 이슈 저장소 (FinanceAgentGUI 이식) 무네트워크."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture()
def wm(tmp_path, monkeypatch):
    from lib import world_memory as W
    monkeypatch.setattr(W, "DB_PATH", tmp_path / "world.sqlite3")
    return W


def test_log_issue_and_dedupe(wm):
    e1 = wm.log_issue("엔비디아 실적 서프라이즈", category="실적", region="US",
                      importance="high", issue_date="2026-07-10", tickers=["NVDA"])
    assert e1
    assert wm.log_issue("엔비디아 실적 서프라이즈", issue_date="2026-07-10") is None  # 같은 날 중복
    assert wm.log_issue("엔비디아 실적 서프라이즈", issue_date="2026-07-11")          # 다른 날 OK
    assert wm.stats()["issues"] == 2


def test_timeline_search_and_order(wm):
    wm.log_issue("삼성전자 HBM4 공급 계약", category="신제품기술", region="KR",
                 issue_date="2026-07-01", tickers=["005930"])
    wm.log_issue("엔비디아 신규 칩 발표", category="신제품기술", region="US",
                 issue_date="2026-07-05", tickers=["NVDA"])
    wm.log_issue("연준 금리 동결", category="거시", issue_date="2026-07-08")
    hits = wm.timeline("NVDA")
    assert len(hits) == 1 and hits[0]["title"].startswith("엔비디아")
    recent = wm.timeline("", limit=10)
    assert [i["issue_date"] for i in recent] == ["2026-07-08", "2026-07-05", "2026-07-01"]
    txt = wm.timeline_text("엔비디아")
    assert "2026-07-05" in txt and txt.strip().startswith("-")
    assert wm.timeline_text("존재하지않는키워드XYZ") == ""


def test_story_chain_supersede(wm):
    e1 = wm.log_issue("이슈1", issue_date="2026-07-01")
    wm.link_state("ticker:NVDA", "첫 상태: 호재 시작", source_event_id=e1, bias="긍정")
    wm.link_state("ticker:NVDA", "둘째 상태: 규제 리스크 부상", bias="부정")
    chain = wm.story_chain("ticker:NVDA")
    assert len(chain) == 2
    assert chain[0]["status"] == "superseded" and chain[0]["to"]      # 이전 상태 자동 종료
    assert chain[1]["status"] == "active" and chain[1]["bias"] == "부정"


def test_ingest_from_labels(wm):
    labels = [
        {"id": "a", "title_head": "MSFT 클라우드 성장", "event_type": "실적", "direction": 1,
         "strength": 4, "tickers": ["MSFT"], "published_at": "2026-07-10T10:00:00+09:00"},
        {"id": "b", "title_head": "MSFT 클라우드 성장", "event_type": "실적", "direction": 1,
         "strength": 4, "tickers": ["MSFT"], "published_at": "2026-07-10T11:00:00+09:00"},  # dedupe
        {"id": "c", "title_head": "삼성 파운드리 수주", "event_type": "기타", "direction": 0,
         "strength": 2, "tickers": ["005930"], "published_at": "2026-07-11T09:00:00+09:00"},
        {"id": "d", "title_head": "", "tickers": []},                                        # 빈 제목 스킵
    ]
    assert wm.ingest_from_labels(labels) == 2
    assert wm.ingest_from_labels(labels) == 0                          # 멱등
    hits = wm.timeline("MSFT")
    assert hits and hits[0]["importance"] == "high"                    # strength 4 → high
    chain = wm.story_chain("ticker:MSFT")
    assert chain and "호재" in chain[-1]["summary"]                    # direction=1 → 상태 체인
    assert wm.story_chain("ticker:005930") == []                       # direction=0 → 체인 없음


def test_views_world_timeline_graceful(wm, monkeypatch):
    from dashboard import views
    wm.log_issue("테스트 이슈", issue_date="2026-07-10", tickers=["NVDA"])
    out = views.world_timeline("NVDA.KS")   # 접미사 제거 확인 (base 매칭)
    assert isinstance(out.get("issues"), list) and isinstance(out.get("chain"), list)
