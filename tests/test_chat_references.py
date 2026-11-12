"""답변 인용은 질문과 겹치는 출처만 붙인다."""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dashboard.chat_references import build_answer_references


def test_unrelated_latest_events_and_reports_are_not_cited(monkeypatch):
    monkeypatch.setattr(
        "agent_console.wiki.list_pages",
        lambda **kwargs: [],
    )
    pack = {
        "sources": {"events": [{
            "title": "원유 재고 증가",
            "summary": "WTI 재고",
            "published_at": "2026-11-12T00:00:00+09:00",
        }]},
        "reports": [{"title": "오늘 아침 리포트", "summary": "QQQ 국면", "name": "morning.md"}],
    }
    refs = build_answer_references("삼성전자 배당", "ticker", pack, "배당 일정만 본다")
    assert refs["sources"] == []


def test_overlapping_report_is_kept(monkeypatch):
    monkeypatch.setattr("agent_console.wiki.list_pages", lambda **kwargs: [])
    pack = {
        "sources": {"events": []},
        "reports": [
            {"title": "오늘 아침 리포트", "summary": "QQQ", "name": "morning.md"},
            {"title": "삼성전자 배당 메모", "summary": "분기 배당", "name": "samsung.md"},
        ],
    }
    refs = build_answer_references("삼성전자 배당", "ticker", pack, "")
    assert [item["title"] for item in refs["sources"]] == ["삼성전자 배당 메모"]
