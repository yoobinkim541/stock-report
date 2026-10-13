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
    modal = snapshot.get('layout') == 'saveticker_modal_v1'
    direct = bool(article and {'gap-6','p-6'}.issubset(article.get('class', []))
                  and article.find('h2') and not heading)
    if modal or direct:
        section = article if direct else soup.select_one('div.gap-6.p-6')
        paragraphs = soup.select('p.whitespace-pre-wrap')
        supplied_title = article.find('h2').get_text(' ',strip=True) if direct else snapshot.get('title')
        if (section is None or not paragraphs or not isinstance(supplied_title, str)
                or not supplied_title.strip()
                or any(p.parent is not paragraphs[0].parent for p in paragraphs)
                or paragraphs[0].parent.parent is not section):
            raise CollectionError('parse_failed')
        article = section
        title = supplied_title.strip()
    else:
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
    content = paragraphs[0].parent if modal or direct else article.select_one('[data-article-body], [data-testid="article-body"], .article-content, .prose')
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


def collect(browser, *, outbox: Path, sender, now: datetime | None = None, limit: int = 10,
            article_urls: list[str] | None = None) -> dict:
    if not 1 <= limit <= 20:
        raise ValueError('invalid collection limit')
    explicit = None
    if article_urls is not None:
        if not isinstance(article_urls, list) or not 1 <= len(article_urls) <= limit:
            raise ValueError('invalid explicit article count')
        explicit = list(dict.fromkeys(_article_url(url) for url in article_urls))
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
            listing = (browser.discover(limit=limit) if hasattr(browser, 'discover')
                       else browser.read(ORIGIN)) if explicit is None else None
            urls = explicit if explicit is not None else discover_urls(listing, limit=limit)
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
  const modals = Array.from(document.querySelectorAll('div[class*="modal-fullscreen:"]'))
    .filter(e => e.matches('.overflow-hidden') && e.querySelector('button[aria-label="공유하기"]'));
  const modal = modals.length === 1 ? modals[0] : null;
  const sections = modal ? Array.from(modal.querySelectorAll('.overflow-y-auto > div.gap-6.p-6')) : [];
  const section = sections.length === 1 ? sections[0] : null;
  const headings = modal ? Array.from(modal.querySelectorAll('h2')) : [];
  const titles = Array.from(new Set(headings.map(h => h.textContent.trim()).filter(Boolean)));
  const layout = !article && section && titles.length === 1 ? 'saveticker_modal_v1' : '';
  const html = article ? article.outerHTML : layout ? section.outerHTML : '';
  const bytes = new TextEncoder().encode(html);
  const digest = await crypto.subtle.digest('SHA-256', bytes);
  const html_sha256 = Array.from(new Uint8Array(digest)).map(x => x.toString(16).padStart(2,'0')).join('');
  return {
    url: location.href, html, html_sha256, layout, title: layout ? titles[0] : '',
    links: Array.from(document.querySelectorAll('a[href]')).map(a => a.getAttribute('href')),
    blocked: !!document.querySelector('#challenge-running, #cf-challenge-running, [id^="cf-chl-"]') || /just a moment/i.test(document.title),
    reauth_required: !!document.querySelector('input[type="password"]') ||
      /로그인\\s*후\\s*(열람|이용)|sign in to (continue|read)/i.test(document.body ? document.body.innerText : ''),
    access_required: /subscribe\\s+to\\s+(unlock|read)|구독\\s*후\\s*(열람|이용)/i.test(document.body ? document.body.innerText : '')
  };
})()'''


LISTING_DISCOVERY = r'''(async () => {
  const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
  const blocked = () => /just a moment/i.test(document.title) ||
    !!document.querySelector('#challenge-running, #cf-challenge-running, [id^="cf-chl-"]');
  if (blocked()) return {blocked:true};
  if (document.querySelector('input[type="password"]') ||
      Array.from(document.querySelectorAll('button')).some(b => b.textContent.trim()==='로그인'))
    return {reauth_required:true};
  const links = [];
  let rows = [];
  for (let wait=0;wait<40;wait++) {
    if (blocked()) return {blocked:true};
    rows = Array.from(document.querySelectorAll('[role="button"][data-breaking]')).slice(0,limit);
    if (rows.length) break;
    await pause(100);
  }
  if (!rows.length) return {error:'parse_failed'};
  for (const row of rows) {
    row.click();
    let modal = null;
    for (let wait=0;wait<40;wait++) {
      if (blocked()) return {blocked:true};
      const candidates = Array.from(document.querySelectorAll('div[class*="modal-fullscreen:"]'))
        .filter(e => e.matches('.overflow-hidden') && e.querySelector('button[aria-label="공유하기"]'));
      if (candidates.length===1 && candidates[0].querySelector('h2')) {
        modal=candidates[0]; break;
      }
      await pause(100);
    }
    if (!modal) return {error:'parse_failed'};
    try {
      const share = modal.querySelector('button[aria-label="공유하기"]');
      if (!share) return {error:'parse_failed'};
      const sentinel = 'stock-report-share:' + crypto.randomUUID();
      await navigator.clipboard.writeText(sentinel);
      share.click();
      let value = sentinel;
      for (let wait=0;wait<40;wait++) {
        await pause(100);
        value = await navigator.clipboard.readText();
        if (value !== sentinel) break;
      }
      if (value === sentinel) return {error:'browser_unavailable'};
      const url = new URL(value);
      if (url.origin!=='https://saveticker.com' || !/^\/news\/[A-Za-z0-9_-]+\/?$/.test(url.pathname) ||
          url.search || url.hash) return {error:'unsupported_url'};
      if (!links.includes(url.href)) links.push(url.href);
    } catch (_) {
      return {error:'browser_unavailable'};
    } finally {
      const close = modal.querySelector('button[aria-label="뒤로가기"]');
      if (close) close.click();
      await pause(100);
    }
  }
  return {url:'https://saveticker.com/',links};
})()'''


def _focus_attached_page(task_id, url, *, clipboard=False):
    from tools.browser_supervisor import SUPERVISOR_REGISTRY
    supervisor = SUPERVISOR_REGISTRY.get(task_id)
    if supervisor is None or not supervisor.focus_page('https://saveticker.com',
            accept='location.href === ' + json.dumps(url)).get('ok'):
        raise CollectionError('browser_unavailable')
    captured = SUPERVISOR_REGISTRY.capture(task_id)
    if clipboard:
        info = captured.call('Target.getTargetInfo', session_id=captured.page_session_id)
        context = info.get('result', {}).get('targetInfo', {}).get('browserContextId')
        contexts = captured.call('Target.getBrowserContexts').get('result', {}).get('browserContextIds', [])
        params = {'origin':'https://saveticker.com',
                  'permissions':['clipboardReadWrite','clipboardSanitizedWrite']}
        if context and context in contexts:
            params['browserContextId'] = context
        captured.call('Browser.grantPermissions', params)
    captured.call('Page.bringToFront', session_id=captured.page_session_id)


class HermesBrowser:
    """Attach only to an explicitly configured private browser; never clone login state."""
    def __init__(self, *, api=None, page_focus=None):
        if api is None:
            from tools import browser_tool as api
            page_focus = page_focus or _focus_attached_page
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
        self.page_focus = page_focus
        self.task_id = 'stock-report-saveticker'

    def discover(self, *, limit):
        if not 1 <= limit <= 20:
            raise ValueError('invalid collection limit')
        navigation = json.loads(self.api.browser_navigate(ORIGIN, task_id=self.task_id))
        if not navigation.get('success'):
            raise CollectionError('browser_unavailable')
        final = navigation.get('url')
        if final not in {ORIGIN, ORIGIN.rstrip('/'), 'https://saveticker.com/news'}:
            if final and re.search(r'/(?:login|signin|auth)(?:/|$)', urlsplit(final).path, re.I):
                raise CollectionError('reauth_required')
            raise CollectionError('article_redirect_mismatch')
        if self.page_focus:
            self.page_focus(self.task_id, final, clipboard=True)
        expression = f'const limit = {limit};\n' + LISTING_DISCOVERY
        response = json.loads(self.api.browser_console(expression=expression, task_id=self.task_id))
        value = response.get('result')
        if not response.get('success') or not isinstance(value, dict):
            raise CollectionError('browser_unavailable')
        _check_access(value)
        if value.get('error'):
            raise CollectionError(value['error'] if value['error'] in {
                'parse_failed','unsupported_url','browser_unavailable'} else 'browser_unavailable')
        discover_urls(value, limit=limit)
        return value

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
        if self.page_focus:
            self.page_focus(self.task_id, final)
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
