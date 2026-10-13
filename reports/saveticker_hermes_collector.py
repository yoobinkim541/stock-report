"""Collect authorized SaveTicker DOM through Hermes, then deliver immutable batches."""
from __future__ import annotations

import hashlib
import json
import ipaddress
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

import safe_io
from reports.hermes_source_webhook import MAX_BODY_CHARS, MAX_RAW_CHARS, SCHEMA, validate_batch

ORIGIN = 'https://saveticker.com/'


class CollectionError(ValueError):
    pass


def _article_url(url):
    parsed = urlsplit(str(url or ''))
    if (parsed.scheme != 'https' or parsed.netloc not in {'saveticker.com', 'www.saveticker.com'}
            or parsed.query or parsed.fragment
            or not re.fullmatch(r'/news/[A-Za-z0-9_-]+/?', parsed.path)):
        raise CollectionError('unsupported_url')
    return 'https://saveticker.com' + parsed.path.rstrip('/')


def _check_access(snapshot):
    if not isinstance(snapshot, dict):
        raise CollectionError('parse_failed')
    raw = str(snapshot.get('html') or '')
    if snapshot.get('blocked') or re.search(r'cf-chl-|just a moment|cloudflare challenge', raw, re.I):
        raise CollectionError('blocked')
    if snapshot.get('access_required') or re.search(r'subscribe\s+to\s+(?:unlock|read)|구독\s*후\s*(?:열람|이용)', raw, re.I):
        raise CollectionError('access_required')
    if (snapshot.get('reauth_required') or re.search(r'/(?:login|signin|auth)(?:/|$)',
            urlsplit(str(snapshot.get('url') or '')).path, re.I)
            or re.search(r'로그인\s*후\s*(?:열람|이용)|<input[^>]+type=["\']?password', raw, re.I)):
        raise CollectionError('reauth_required')


def extract_article(snapshot: dict) -> dict:
    _check_access(snapshot)
    url = _article_url(snapshot.get('url'))
    raw = snapshot.get('html')
    if not isinstance(raw, str) or len(raw) > MAX_RAW_CHARS:
        raise CollectionError('oversized_body')
    soup = BeautifulSoup(raw, 'html.parser')
    article = soup.find('article')
    heading = article.find('h1') if article else None
    if not article or not heading or not heading.get_text(strip=True):
        raise CollectionError('parse_failed')
    title = heading.get_text(' ', strip=True)
    published = ''
    time_node = article.find('time', datetime=True)
    if time_node:
        candidate = str(time_node.get('datetime') or '')
        try:
            timestamp = datetime.fromisoformat(candidate.replace('Z', '+00:00'))
            if timestamp.tzinfo is not None:
                published = candidate
        except ValueError:
            pass
    content = article.select_one('[data-article-body], [data-testid="article-body"], .article-content, .prose')
    if content is None:
        text = article.get_text('\n',strip=True)
        if len(text)>MAX_BODY_CHARS:
            raise CollectionError('oversized_body')
        if text.endswith(('...','…')):
            raise CollectionError('incomplete_body')
        raise CollectionError('parse_failed')
    body = content.get_text('\n', strip=True)
    if not body or body.endswith(('...', '…')):
        raise CollectionError('incomplete_body')
    if len(body) > MAX_BODY_CHARS:
        raise CollectionError('oversized_body')
    record = {'url': url, 'title': title, 'published_at': published, 'body': body,
              'body_coverage': 'full', 'extraction_scope': 'authorized_article',
              'raw_content': raw, 'raw_format': 'html'}
    # The webhook's complete credential/challenge checks are also enforced before outbox writes.
    try:
        validate_batch({'schema': SCHEMA, 'delivery_id': 'validation',
                        'collected_at': datetime.now(timezone.utc).isoformat(), 'records': [record]})
    except ValueError:
        raise CollectionError('unsafe_article') from None
    return record


def discover_urls(snapshot: dict, *, limit: int = 20) -> list[str]:
    if not 1 <= limit <= 20:
        raise ValueError('invalid collection limit')
    _check_access(snapshot)
    if snapshot.get('url') not in {ORIGIN, ORIGIN.rstrip('/')}:
        raise CollectionError('unsupported_url')
    links = snapshot.get('links')
    if not isinstance(links, list):
        raise CollectionError('parse_failed')
    urls = []
    for link in links:
        try:
            url = _article_url(urljoin(ORIGIN, link))
        except (CollectionError, TypeError, ValueError):
            continue
        if url not in urls:
            urls.append(url)
        if len(urls) >= limit:
            break
    if not urls:
        raise CollectionError('parse_failed')
    return urls


def collect(browser, *, outbox: Path, sender, now: datetime | None = None, limit: int = 10) -> dict:
    if not 1 <= limit <= 20:
        raise ValueError('invalid collection limit')
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError('collection timestamp requires timezone')
    outbox = Path(outbox)
    state_path = outbox / 'collector-state.json'
    pending = outbox / 'pending'
    result = {'status': 'no_change', 'delivered': 0, 'unchanged': 0, 'errors': [], 'wiki_status': 'pending'}
    with safe_io.file_write_lock(str(state_path)):
        state = json.loads(state_path.read_text()) if state_path.exists() else {'seen': {}}
        state.setdefault('seen', {})
        state.setdefault('accepted', {identity:True for identity in state['seen'].values()})
        pending.mkdir(parents=True, exist_ok=True)

        def deliver(path):
            try:
                if path.is_symlink():
                    raise ValueError('outbox symlink is forbidden')
                payload = json.loads(path.read_text())
                validate_batch(payload, now=now)
                if len(payload['records']) != 1:
                    raise ValueError('collector batch must contain one article')
                url = _article_url(payload['records'][0]['url'])
                expected = _delivery_id(payload['records'][0])
                if payload['delivery_id'] != expected or path.name != expected + '.json':
                    raise ValueError('outbox identity mismatch')
            except Exception:
                quarantine=outbox/'quarantine'
                quarantine.mkdir(parents=True,exist_ok=True)
                path.replace(quarantine/(path.name+'.'+str(now.timestamp())))
                result['errors'].append({'url':'','reason':'invalid_outbox'})
                return
            try:
                receipt = sender(path)
                if not isinstance(receipt, dict) or receipt.get('ok') is not True:
                    raise ValueError('unconfirmed delivery')
                state['seen'][url] = expected
                state['accepted'][expected] = True
                state['last_success'] = now.isoformat()
                safe_io.atomic_write_json(str(state_path), state)
                path.unlink()
                result['delivered'] += 1
            except Exception:
                result['errors'].append({'url': '', 'reason': 'delivery_failed'})

        queue=sorted(pending.glob('saveticker-*.json'))
        cursor=state.get('retry_cursor','')
        queue=[p for p in queue if p.name>cursor]+[p for p in queue if p.name<=cursor]
        for path in queue[:limit]:
            deliver(path)
            state['retry_cursor']=path.name
        try:
            urls = discover_urls(browser.read(ORIGIN), limit=limit)
        except CollectionError as exc:
            urls = []
            result['errors'].append({'url': ORIGIN, 'reason': str(exc)})
        except Exception:
            urls = []
            result['errors'].append({'url': ORIGIN, 'reason': 'browser_unavailable'})
        for url in urls:
            try:
                record = extract_article(browser.read(url))
                if record['url'] != url:
                    raise CollectionError('article_redirect_mismatch')
                identity = _delivery_id(record)
                if identity in state['accepted']:
                    state['seen'][url]=identity
                    result['unchanged'] += 1
                    continue
                path = pending / (identity + '.json')
                if path.exists():
                    continue  # Failed retries keep their original collection time and exact bytes.
                payload = {'schema': SCHEMA, 'delivery_id': identity,
                           'collected_at': now.isoformat(), 'records': [record]}
                safe_io.atomic_write_json(str(path), payload)
                deliver(path)
            except CollectionError as exc:
                result['errors'].append({'url': url, 'reason': str(exc)})
            except Exception:
                result['errors'].append({'url': url, 'reason': 'browser_unavailable'})
        if result['errors']:
            result['status'] = 'partial' if result['delivered'] else result['errors'][0]['reason']
        elif result['delivered']:
            result['status'] = 'delivered'
        state.update(last_attempt=now.isoformat(), status=result['status'], errors=result['errors'])
        safe_io.atomic_write_json(str(state_path), state)
    return result


def _delivery_id(record):
    content = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return 'saveticker-' + hashlib.sha256(content.encode()).hexdigest()


DOM_CAPTURE = '''(async () => {
  const article = document.querySelector('article');
  const html = article ? article.outerHTML : '';
  const bytes = new TextEncoder().encode(html);
  const digest = await crypto.subtle.digest('SHA-256', bytes);
  const html_sha256 = Array.from(new Uint8Array(digest)).map(x => x.toString(16).padStart(2,'0')).join('');
  return {
    url: location.href, html, html_sha256,
    links: Array.from(document.querySelectorAll('a[href]')).map(a => a.getAttribute('href')),
    blocked: !!document.querySelector('#challenge-running, #cf-challenge-running, [id^="cf-chl-"]') || /just a moment/i.test(document.title),
    reauth_required: !!document.querySelector('input[type="password"]') ||
      /로그인\\s*후\\s*(열람|이용)|sign in to (continue|read)/i.test(document.body ? document.body.innerText : ''),
    access_required: /subscribe\\s+to\\s+(unlock|read)|구독\\s*후\\s*(열람|이용)/i.test(document.body ? document.body.innerText : '')
  };
})()'''


class HermesBrowser:
    """Attach only to an explicitly configured private browser; never clone login state."""
    def __init__(self, *, api=None):
        if api is None:
            from tools import browser_tool as api
        cdp = urlsplit(api._cdp._get_cdp_override_raw())
        host = cdp.hostname
        try:
            address = ipaddress.ip_address(host)
            private = (address.is_loopback or (address.is_private and not address.is_unspecified)
                       or address in ipaddress.ip_network('100.64.0.0/10'))
        except ValueError:
            private = host == 'localhost'
        if (not private or cdp.scheme not in {'http', 'https', 'ws', 'wss'}
                or cdp.username or cdp.password or cdp.query or cdp.fragment
                or api._is_camofox_mode()):
            raise CollectionError('browser_connection_required')
        self.api = api
        self.task_id = 'stock-report-saveticker'

    def read(self, url):
        if url != ORIGIN:
            _article_url(url)
        navigation = json.loads(self.api.browser_navigate(url, task_id=self.task_id))
        if not navigation.get('success'):
            raise CollectionError('browser_unavailable')
        final = navigation.get('url')
        if final != url:
            if final and re.search(r'/(?:login|signin|auth)(?:/|$)', urlsplit(final).path, re.I):
                raise CollectionError('reauth_required')
            raise CollectionError('article_redirect_mismatch')
        response = json.loads(self.api.browser_console(expression=DOM_CAPTURE, task_id=self.task_id))
        value = response.get('result')
        if not response.get('success') or not isinstance(value, dict):
            raise CollectionError('browser_unavailable')
        _check_access(value)
        raw = value.get('html')
        if (not isinstance(raw, str) or len(raw) > MAX_RAW_CHARS
                or hashlib.sha256(raw.encode()).hexdigest() != value.get('html_sha256')):
            raise CollectionError('original_mismatch')
        return value
