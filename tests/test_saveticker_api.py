from providers import saveticker_api as api

NOTICE = {"id": "legacy-news-sunset-notice", "title": "새로운 세이브 뉴스로 업데이트해 주세요"}


def test_api_base_defaults_to_public_host_and_honors_override(monkeypatch):
    monkeypatch.delenv("SAVE_TICKER_API_BASE", raising=False)
    assert api.api_base() == "https://api.saveticker.com/api"
    monkeypatch.setenv("SAVE_TICKER_API_BASE", "https://mirror.example/api/")
    assert api.api_base() == "https://mirror.example/api"


def test_rows_for_tickers_matches_dollar_tags_and_ticker_objects_but_not_notice():
    rows = [
        NOTICE,
        {"id": "news_1", "title": "엔비디아 실적", "tag_names": ["정보", "$NVDA"]},
        {"id": "news_2", "title": "메타 소송", "tickers": [{"symbol": "META"}]},
        {"id": "news_3", "title": "금리 동결", "tag_names": ["속보"]},
    ]

    assert [row["id"] for row in api.rows_for_tickers(rows, ["nvda", "META"])] == ["news_1", "news_2"]
    assert api.rows_for_tickers(rows, []) == []
    assert not api.is_article_row(NOTICE)
