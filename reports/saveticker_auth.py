"""One ordinary saved-email login attempt after an explicit human confirmation."""
from __future__ import annotations

from urllib.parse import urlsplit

ORIGIN = 'https://saveticker.com'


def _origin(page):
    parsed = urlsplit(page.url)
    return parsed.scheme + '://' + parsed.netloc


def _blocked(page):
    return page.evaluate('''() => !!document.querySelector('#challenge-running, #cf-challenge-running, [id^="cf-chl-"]') ||
        /just a moment|checking your browser|verify you are human/i.test(document.title)''')


def _authenticated(page):
    return (_origin(page)==ORIGIN and urlsplit(page.url).path.startswith('/news')
        and page.get_by_role('button',name='로그인',exact=True).count()==0
        and page.locator('input[type=password]').count()==0
        and (page.locator('[role=button][data-breaking]').count()>0 or page.locator('article').count()>0))


def _login(page,vault):
    secret = None
    try:
        page.goto(ORIGIN+'/news',wait_until='domcontentloaded',timeout=30000)
        page.bring_to_front()
        page.wait_for_timeout(700)
        if _origin(page)!=ORIGIN:
            return {'status':'reauth_required'}
        if _blocked(page):
            return {'status':'blocked'}
        if _authenticated(page):
            return {'status':'authenticated'}
        if vault is None:
            from agent.vault_backends.local import LocalLoginBackend
            vault = LocalLoginBackend()
        items = [m for m in vault.list_items() if m.origin==ORIGIN and m.identifier]
        if not items:
            return {'status':'auth_config_required'}
        credentials = [(m,vault.resolve_secret(m.id)) for m in items]
        meta,secret = credentials[0]
        if (not secret.get('password') or any(m.identifier!=meta.identifier or
                s.get('password')!=secret['password'] for m,s in credentials)):
            return {'status':'auth_config_required'}
        page.get_by_role('button',name='로그인',exact=True).click(timeout=5000)
        page.get_by_role('button',name='이메일로 로그인',exact=True).click(timeout=5000)
        if _origin(page)!=ORIGIN or _blocked(page):
            return {'status':'reauth_required'}
        page.locator('input[type=email]').fill(meta.identifier)
        page.locator('input[type=password]').fill(secret['password'])
        if _origin(page)!=ORIGIN:
            return {'status':'reauth_required'}
        page.get_by_role('button',name='로그인',exact=True).click(timeout=5000)
        page.wait_for_timeout(1500)
        if _blocked(page):
            return {'status':'blocked'}
        return {'status':'authenticated' if _authenticated(page) else 'reauth_required'}
    except Exception:
        return {'status':'reauth_required'}
    finally:
        if secret is not None:
            secret.clear()


def login_saved_email(*,page=None,vault=None):
    if page is not None:
        return _login(page,vault)
    try:
        from playwright.sync_api import sync_playwright
        from reports.saveticker_hermes_collector import HermesBrowser
        attached = HermesBrowser()
        with sync_playwright() as driver:
            browser = driver.chromium.connect_over_cdp(attached.api._cdp._get_cdp_override_raw())
            page = browser.contexts[0].new_page()
            result = _login(page,vault)
            if result['status']=='authenticated':
                page.close()
            return result
    except Exception:
        return {'status':'browser_unavailable'}
