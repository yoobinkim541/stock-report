"""Same-origin SaveTicker requests in the human-authenticated Hermes browser.

Only loopback CDP and existing SaveTicker tabs are used. No login state leaves the
browser; challenges are returned to the existing human recovery workflow.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import requests
import safe_io

from reports.saveticker_hermes_collector import (
    CollectionError, DOM_CAPTURE, ORIGIN, _article_url, _check_access, extract_article,
)

ACCESS_PROBE = """({blocked: /just a moment/i.test(document.title) ||
 !!document.querySelector('#challenge-running,#cf-challenge-running,[id^="cf-chl-"]'),
 reauth_required: !!document.querySelector('input[type="password"]') ||
 Array.from(document.querySelectorAll('button')).some(b=>b.textContent.trim()==='로그인')})"""


class CDPBrowser:
    def __init__(self):
        self.endpoint = os.getenv('HERMES_SOURCE_CDP_URL', 'http://127.0.0.1:9225').rstrip('/')
        parsed = urlsplit(self.endpoint)
        if (parsed.scheme != 'http' or parsed.hostname not in {'127.0.0.1', 'localhost', '::1'}
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path):
            raise ValueError('CDP requires a loopback HTTP endpoint')
        self.lock_path = Path.home() / '.cache/stock-report/saveticker-browser.lock'
        self.seen_urls = set()

    def set_seen_urls(self, urls):
        self.seen_urls = set(urls)

    def _target(self):
        try:
            targets = requests.get(self.endpoint + '/json/list', timeout=5).json()
            return next(t for t in targets if t.get('type') == 'page'
                        and urlsplit(t.get('url', '')).scheme == 'https'
                        and urlsplit(t.get('url', '')).netloc == 'saveticker.com')
        except Exception:
            raise CollectionError('browser_unavailable') from None

    def _command(self, method, params):
        from websockets.sync.client import connect
        target = self._target()
        endpoint = target.get('webSocketDebuggerUrl', '')
        parsed = urlsplit(endpoint)
        if parsed.scheme != 'ws' or parsed.hostname != urlsplit(self.endpoint).hostname:
            raise CollectionError('browser_unavailable')
        try:
            with connect(endpoint, open_timeout=5, close_timeout=2, max_size=4_000_000) as ws:
                ws.send(json.dumps({'id': 1, 'method': method, 'params': params}))
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    result = json.loads(ws.recv(timeout=max(.01, deadline-time.monotonic())))
                    if result.get('id') == 1:
                        if result.get('error'):
                            raise CollectionError('browser_unavailable')
                        return result.get('result', {})
        except CollectionError:
            raise
        except Exception:
            raise CollectionError('browser_unavailable') from None
        raise CollectionError('browser_unavailable')

    def _evaluate(self, expression):
        result = self._command('Runtime.evaluate', {'expression': expression,
            'awaitPromise': True, 'returnByValue': True, 'timeout': 18000})
        value = result.get('result', {}).get('value')
        if result.get('exceptionDetails') or not isinstance(value, dict):
            raise CollectionError('parse_failed')
        return value

    def fetch_json(self, path, params=None):
        if path not in {'/api/news/list', '/api/news/top-stories', '/api/calendar/events'}:
            raise ValueError('unsupported same-origin API')
        address = path + ('?' + urlencode(params) if params else '')
        expression = """(async()=>{
 const access=ACCESS; if(access.blocked || access.reauth_required) return access;
 const response=await fetch(ADDRESS,{credentials:'same-origin',signal:AbortSignal.timeout(12000)});
 const text=await response.text(); let data; try{data=JSON.parse(text)}catch{}
 return {status:response.status,data,blocked:response.status===403 || response.status===451 ||
 /cf-chl-|cloudflare challenge|just a moment/i.test(text),reauth_required:response.status===401};
})()""".replace('ACCESS', ACCESS_PROBE).replace('ADDRESS', json.dumps(address))
        with safe_io.file_write_lock(str(self.lock_path)):
            result = self._evaluate(expression)
        _check_access(result)
        if result.get('status') != 200 or not isinstance(result.get('data'), dict):
            raise CollectionError('parse_failed')
        return result['data']

    def discover(self, *, limit):
        if not 1 <= limit <= 20:
            raise ValueError('invalid collection limit')
        candidates = []
        metadata = {}
        # Scan a bounded backlog, instead of repeatedly clicking the newest row.
        for page in range(1, 6):
            data = self.fetch_json('/api/news/list', {'page': page, 'page_size': 30,
                                                    'sort': 'created_at_desc'})
            rows = data.get('news_list')
            if not isinstance(rows, list):
                raise CollectionError('parse_failed')
            if not rows:
                break
            for row in rows:
                if not isinstance(row, dict):
                    continue
                try:
                    url = _article_url(ORIGIN + 'news/' + str(row.get('id') or ''))
                except CollectionError:
                    continue
                if url in metadata:
                    continue
                candidates.append(url)
                labels = row.get('tag_names') or []
                metadata[url] = {'published_at': row.get('created_at') or '',
                                 'category': '속보' if '속보' in labels else ''}
            unseen = [url for url in candidates if url not in self.seen_urls]
            if len(unseen) >= limit:
                break
            time.sleep(.25)
        chosen = ([url for url in candidates if url not in self.seen_urls] or candidates)[:limit]
        if not chosen:
            raise CollectionError('parse_failed')
        return {'url': ORIGIN, 'links': chosen,
                'article_metadata': {url: metadata[url] for url in chosen}}

    def read(self, url):
        url = _article_url(url)
        with safe_io.file_write_lock(str(self.lock_path)):
            _check_access(self._evaluate(ACCESS_PROBE))
            self._command('Page.navigate', {'url': url})
            deadline = time.monotonic() + 16
            previous_digest = None
            while time.monotonic() < deadline:
                captured = self._evaluate(DOM_CAPTURE)
                _check_access(captured)
                if captured.get('url') == url and captured.get('html'):
                    raw = captured['html']
                    if hashlib.sha256(raw.encode()).hexdigest() != captured.get('html_sha256'):
                        raise CollectionError('original_mismatch')
                    # Use the extractor's supported layouts and completeness checks,
                    # then recapture instead of returning a pre-hydration snapshot.
                    try:
                        extract_article(captured)
                    except CollectionError as exc:
                        if str(exc) not in {'parse_failed', 'incomplete_body'}:
                            raise
                        previous_digest = None
                    else:
                        digest = captured['html_sha256']
                        if digest == previous_digest:
                            return captured
                        previous_digest = digest
                else:
                    previous_digest = None
                time.sleep(.4)
        raise CollectionError('parse_failed')
