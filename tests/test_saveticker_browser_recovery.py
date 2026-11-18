"""Authenticated browser recovery never exports cookies or accepts challenge content."""
import importlib

import pytest


def module():
    return importlib.import_module('reports.saveticker_browser')


def test_browser_api_restricts_same_origin_and_reports_challenges(monkeypatch):
    m = module()
    browser = m.CDPBrowser()
    expressions = []
    def evaluate(expression):
        expressions.append(expression)
        return {'status': 200, 'data': {'news_list': [{'id': 'one'}]}}
    monkeypatch.setattr(browser, '_evaluate', evaluate)
    assert browser.fetch_json('/api/news/list', {'page': 1})['news_list']
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
