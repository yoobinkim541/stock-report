from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from reports import source_collector as sc
from reports.source_pipeline import ProviderSpec, run_providers


def feed(article):
    return (f'<rss><channel><item><title>엔비디아 &amp; 실적</title>'
            f'<link>https://kr.investing.com/news/{article}</link>'
            '<description>공개 피드 요약</description>'
            '<pubDate>2026-11-13 02:25:18</pubDate>'
            '</item></channel></rss>').encode('utf-8')


def test_investing_feed_preserves_korean_timestamp_and_preview_scope(monkeypatch):
    calls = []
    def get(url, **kwargs):
        calls.append(url)
        return SimpleNamespace(_content=feed(str(len(calls))))
    monkeypatch.setattr(sc, '_bounded_get', get)
    monkeypatch.setattr(sc, '_LAST_ERRORS', {})
    rows = sc.fetch_investing_news_events()
    assert {r['source'] for r in rows} == {'rss:investing_kr_stock', 'rss:investing_kr_economy'}
    assert len(calls) == 2 and all('/rss/' in url for url in calls)
    for row in rows:
        assert row['title'] == '엔비디아 & 실적'
        assert row['body_raw'] == '공개 피드 요약'
        assert row['published_at'] == '2026-11-13T11:25:18+09:00'
        assert row['fulltext_status'] == 'feed_blurb'
        assert row['text_complete'] is False
        assert '속보' not in row['tags']


def test_partial_feed_failure_is_visible_without_dropping_successful_feed(monkeypatch, tmp_path):
    def get(url, **kwargs):
        if url.endswith('news_14.rss'):
            raise ValueError('invalid RSS response')
        return SimpleNamespace(_content=feed('stock'))
    monkeypatch.setattr(sc, '_bounded_get', get)
    monkeypatch.setattr(sc, '_LAST_ERRORS', {})
    monkeypatch.setattr(sc, '_SOURCE_AVAILABILITY', {})
    spec = ProviderSpec('investing_news', ('rss:investing_kr_stock', 'rss:investing_kr_economy'),
                        'news', lambda: sc.fetch_investing_news_events())
    result = run_providers(registry=[spec], cache_dir=tmp_path / 'cache',
                           now=datetime(2026, 11, 13, 3, tzinfo=timezone.utc))
    assert result['fetched'] == 1 and result['persisted'] == 1
    health = result['health']
    assert health['rss:investing_kr_stock']['fetch_success'] is True
    assert health['rss:investing_kr_economy']['fetch_success'] is False
    assert 'invalid RSS' in health['rss:investing_kr_economy']['last_error']


def test_empty_feed_is_a_failure_not_a_successful_zero(monkeypatch):
    monkeypatch.setattr(sc, '_bounded_get', lambda *a, **kw: SimpleNamespace(_content=b'<rss><channel/></rss>'))
    monkeypatch.setattr(sc, '_LAST_ERRORS', {})
    with pytest.raises(RuntimeError, match='RSS fetch failed'):
        sc.fetch_investing_news_events()
