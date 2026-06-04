import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import market_report as mr


def test_section_2_includes_recent_source_cache_digest(monkeypatch):
    monkeypatch.setattr(mr, "fetch_saveticker_json", lambda *args, **kwargs: None)
    monkeypatch.setattr(mr, "fetch_arca_stock_posts", lambda: [])
    monkeypatch.setattr(mr.requests, "get", lambda *args, **kwargs: type("Resp", (), {"status_code": 500, "text": ""})())
    monkeypatch.setattr(mr.yf, "Ticker", lambda sym: type("Ticker", (), {"news": []})())

    monkeypatch.setattr(mr, "load_cached_source_digest", lambda: "## 누적 수집 자료\n\n- saveticker 1건\n- [saveticker] AI chip demand · NVDA\n")

    text = mr.section_2_top_news()

    assert "누적 수집 자료" in text
    assert "AI chip demand" in text
