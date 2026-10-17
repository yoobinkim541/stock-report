"""Authenticated browser recovery never exports cookies or accepts challenge content."""
import importlib
import json
from datetime import datetime, timezone

import pytest


def module():
    return importlib.import_module('reports.saveticker_browser')


def test_browser_api_restricts_same_origin_and_reports_challenges(monkeypatch):
    m = module()
    browser = m.CDPBrowser()
    expressions = []
    def evaluate(expression):
        expressions.append(expression)
        return {'status': 200, 'data': {'events': [{'id': 'one'}]}}
    monkeypatch.setattr(browser, '_evaluate', evaluate)
    assert browser.fetch_json('/api/calendar/events', {'start_date': '2026-10-16'})['events']
    assert "credentials:'same-origin'" in expressions[0]
    assert 'document.cookie' not in expressions[0]
    for path in ['https://evil.test/api/news/list', '/api/../auth', '/api/auth', '//evil.test/api/news/list']:
        with pytest.raises(ValueError):
            browser.fetch_json(path)
    monkeypatch.setattr(browser, '_evaluate', lambda expression: {'blocked': True})
    with pytest.raises(m.CollectionError, match='blocked'):
        browser.fetch_json('/api/news/list')


def test_browser_discovery_prioritizes_unseen_valid_ids_not_clipboard(monkeypatch):
    m = module()
    browser = m.CDPBrowser()
    browser.set_seen_urls({'https://saveticker.com/news/seen'})
    calls = []
    def fetch(path, params=None):
        calls.append((path, params))
        return {'news_list': [{'id': 'seen', 'created_at': '2026-10-16T10:00:00Z'},
                              {'id': 'fresh', 'created_at': '2026-10-16T11:00:00Z'},
                              {'id': '../invalid'}] if params['page'] == 1 else []}
    monkeypatch.setattr(browser, 'fetch_json', fetch)
    result = browser.discover(limit=1)
    assert result['links'] == ['https://saveticker.com/news/fresh']
    assert result['article_metadata'][result['links'][0]]['published_at'] == '2026-10-16T11:00:00Z'
    assert len(calls) <= 5


def test_recovery_partial_success_clears_sticky_error_without_hiding_errors(tmp_path):
    from reports.saveticker_recovery import tick, read_state
    state = tmp_path / 'state.json'
    state.write_text(json.dumps({'phase': 'error'}))
    result = tick(state_path=state, now=2000,
        collect_fn=lambda: {'status': 'partial', 'delivered': 2, 'unchanged': 0,
                            'errors': [{'reason': 'parse_failed'}]},
        login_fn=lambda: None, remote_fn=lambda **kw: None, send_fn=lambda *a, **kw: True)
    assert result['phase'] == 'degraded'
    assert read_state(state)['last_success'] == 2000
    result = tick(state_path=state, now=3000,
        collect_fn=lambda: {'status': 'delivered', 'delivered': 3, 'errors': []},
        login_fn=lambda: None, remote_fn=lambda **kw: None, send_fn=lambda *a, **kw: True)
    assert result['phase'] == 'healthy'
    assert 'reason' not in read_state(state)


def test_calendar_http_block_falls_back_to_authenticated_browser(monkeypatch):
    import requests
    from providers import econ_calendar
    m = module()
    class Response:
        ok = False
        status_code = 403
    monkeypatch.setattr(requests, 'get', lambda *a, **kw: Response())
    monkeypatch.setattr(m.CDPBrowser, 'fetch_json', lambda self, path, params=None:
        {'events': [{'title': 'CPI', 'event_date': '2026-10-20T12:30:00Z', 'color': '#EF4444'}]})
    events = econ_calendar.upcoming_events()
    assert len(events) == 1 and events[0]['title'] == 'CPI'
    assert events[0]['when'].tzinfo is not None


def test_news_http_block_fetches_browser_data_without_refetching_blocked_pages(monkeypatch, tmp_path):
    import requests
    from reports import source_collector as collector
    m = module()
    class Response:
        status_code = 403
        def raise_for_status(self):
            raise requests.HTTPError(response=self)
    monkeypatch.setattr(requests, 'get', lambda *a, **kw: Response())
    monkeypatch.setenv('STOCK_REPORT_REPORTS_DIR', str(tmp_path))
    monkeypatch.setattr(m.CDPBrowser, 'fetch_json', lambda self, path, params=None:
        {'news_list': [{'id': 'fresh', 'title': 'New item', 'content': 'A preview...'}]})
    monkeypatch.setattr(collector, '_fetch_saveticker_article_body',
                        lambda *a, **kw: pytest.fail('browser previews must not refetch blocked HTTP pages'))
    rows = collector.fetch_saveticker_events()
    assert len(rows) == 1 and rows[0]['url'] == 'https://saveticker.com/news/fresh'
    assert rows[0]['fulltext_status'] == 'preview_only'
    assert rows[0]['manifest_path']


def test_runtime_defaults_to_direct_cdp_not_shutdown_sensitive_hermes():
    import inspect
    from reports import saveticker_runtime
    assert 'CDPBrowser()' in inspect.getsource(saveticker_runtime.collect_private)
