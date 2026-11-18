from datetime import datetime, timedelta, timezone


from bot import saveticker_commands
from crons import news_spike_detector as spike
from reports import source_collector as sc


def test_save_command_only_sends_retirement_notice():
    for action in ('status', 'open', 'check', None):
        sent = []
        args = [] if action is None else [action]
        saveticker_commands.handle('owner', args, send_fn=lambda chat, text: sent.append(text))
        assert 'Investing.com' in sent[0] and 'Yahoo Finance' in sent[0]
        assert '더 이상 사용하지 않습니다' in sent[0]


def test_retirement_does_not_hide_active_provider_outages():
    now = datetime(2026, 11, 15, 8, tzinfo=timezone.utc)
    old = (now - timedelta(days=3)).isoformat()
    health = {'saveticker': {'availability': 'disabled', 'last_success': old},
              'rss:investing_kr_stock': {'availability': 'unavailable', 'last_success': old},
              'yahoo_headline': {'last_success': now.isoformat()}}
    assert [r['source'] for r in sc.stale_sources(health, now=now)] == ['rss:investing_kr_stock']


def test_cached_news_requires_explicit_breaking_marker_and_recent_publication(monkeypatch):
    now = datetime(2026, 11, 15, 8, tzinfo=timezone.utc)
    def event(key, **changes):
        return {'id': key, 'source': 'rss:investing_kr_stock', 'title': '[속보] 시장 소식',
                'published_at': now.isoformat(), 'tags': [], **changes}
    rows = [event('breaking'), event('plain', title='일반 시장 소식'),
            event('old', published_at=(now - timedelta(days=3)).isoformat()),
            event('future', published_at=(now + timedelta(hours=1)).isoformat()),
            event('retired', source='saveticker'), event('undated', published_at=''),
            event('breaking'), event('tagged', source='yahoo_headline', title='News', tags=['속보'])]
    monkeypatch.setattr(sc, 'load_recent_events', lambda **kw: rows)
    assert {r['id'] for r in spike.fetch_breaking_news(now)} == {'breaking', 'tagged'}


def test_alert_reports_actual_provider_and_structured_tickers(monkeypatch):
    monkeypatch.setattr(spike, '_realtime_tag', lambda tickers: '')
    text = spike._format_alert({'source': 'yahoo_headline', 'title': '[속보] 기업 공지',
                               'tickers': ['NVDA'], 'published_at': '2026-11-15T03:00:00+00:00'}, 8, '보유 종목')
    assert 'NVDA' in text and 'yahoo_headline' in text
    assert '12:00 KST' in text and 'saveticker' not in text


def test_korean_news_quote_uses_won(monkeypatch):
    from providers import realtime_quotes
    monkeypatch.setattr(realtime_quotes, 'enabled', lambda: True)
    monkeypatch.setattr(realtime_quotes, 'get_price', lambda *a, **kw: 61200)
    assert '₩61,200' in spike._realtime_tag(['005930.KS'])
