"""CDP body readiness and transport boundary regressions."""
import hashlib

import pytest

from reports.saveticker_browser import CDPBrowser, CollectionError

URL = 'https://saveticker.com/news/one'


def capture(html):
    return {'url': URL, 'html': html, 'html_sha256': hashlib.sha256(html.encode()).hexdigest()}


def test_read_waits_for_complete_stable_body_and_accepts_supported_prose(monkeypatch, tmp_path):
    browser = CDPBrowser()
    browser.lock_path = tmp_path / 'browser.lock'
    initial = capture('<article><h1>Title</h1><div class="prose">Loading...</div></article>')
    final = capture('<article><h1>Title</h1><div class="prose"><p>Complete body.</p></div></article>')
    values = iter([{}, initial, final, final])
    monkeypatch.setattr(browser, '_evaluate', lambda expression: next(values))
    monkeypatch.setattr(browser, '_command', lambda *args: {})
    monkeypatch.setattr('reports.saveticker_browser.time.sleep', lambda seconds: None)
    assert browser.read(URL) == final


def test_read_stops_immediately_at_human_challenge(monkeypatch, tmp_path):
    browser = CDPBrowser()
    browser.lock_path = tmp_path / 'browser.lock'
    monkeypatch.setattr(browser, '_evaluate', lambda expression: {'blocked': True})
    monkeypatch.setattr(browser, '_command', lambda *args: pytest.fail('must not navigate challenge'))
    with pytest.raises(CollectionError, match='blocked'):
        browser.read(URL)


@pytest.mark.parametrize('endpoint', [
    'http://203.0.113.1:9225', 'https://127.0.0.1:9225',
    'http://user:password@127.0.0.1:9225', 'http://127.0.0.1:9225/path',
])
def test_cdp_rejects_remote_or_credential_endpoints(monkeypatch, endpoint):
    monkeypatch.setenv('HERMES_SOURCE_CDP_URL', endpoint)
    with pytest.raises(ValueError):
        CDPBrowser()


@pytest.mark.parametrize('selector', [
    'class="prose"', 'class="article-content"', 'data-article-body',
    'data-testid="article-body"',
])
def test_read_recaptures_changed_complete_body_for_all_supported_layouts(monkeypatch, tmp_path, selector):
    browser = CDPBrowser()
    browser.lock_path = tmp_path / 'browser.lock'
    first = capture(f'<article><h1>Title</h1><div {selector}>First paragraph.</div></article>')
    final = capture(f'<article><h1>Title</h1><div {selector}>First paragraph. Second paragraph.</div></article>')
    values = iter([{}, first, final, final])
    monkeypatch.setattr(browser, '_evaluate', lambda expression: next(values))
    monkeypatch.setattr(browser, '_command', lambda *args: {})
    monkeypatch.setattr('reports.saveticker_browser.time.sleep', lambda seconds: None)
    assert browser.read(URL) == final


def test_read_rejects_original_digest_mismatch(monkeypatch, tmp_path):
    browser = CDPBrowser()
    browser.lock_path = tmp_path / 'browser.lock'
    invalid = capture('<article><h1>Title</h1><div class="prose">Body.</div></article>')
    invalid['html_sha256'] = '0' * 64
    values = iter([{}, invalid])
    monkeypatch.setattr(browser, '_evaluate', lambda expression: next(values))
    monkeypatch.setattr(browser, '_command', lambda *args: {})
    with pytest.raises(CollectionError, match='original_mismatch'):
        browser.read(URL)


def test_hard_block_stops_api_fetch_before_network_request(monkeypatch, tmp_path):
    import json
    import subprocess

    browser = CDPBrowser()
    browser.lock_path = tmp_path / 'browser.lock'
    def evaluate(expression):
        script = """
        const document = {
          title: 'Attention Required! | Cloudflare',
          querySelector: selectors => selectors.split(',').some(
            s => s.trim() === '#cf-error-details') ? {} : null,
          querySelectorAll: () => []
        };
        const fetch = () => { throw Error('Blocked page must not fetch'); };
        const result = await EXPRESSION;
        console.log(JSON.stringify(result));
        """.replace('EXPRESSION', expression)
        result = subprocess.run(['node', '--input-type=module'], input=script,
                                capture_output=True, text=True, timeout=5, check=True)
        return json.loads(result.stdout)
    monkeypatch.setattr(browser, '_evaluate', evaluate)
    with pytest.raises(CollectionError, match='^blocked$'):
        browser.fetch_json('/api/news/list')
