import importlib
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

NOW = datetime(2026, 10, 13, 9, tzinfo=timezone.utc)
URL = 'https://saveticker.com/news/123'
HTML = '<article><h1>GPU 수요</h1><div data-article-body><p>첫 문단.</p><p>마지막 근거 끝.</p></div></article>'


def module():
    return importlib.import_module('reports.saveticker_hermes_collector')


def snapshot(url=URL, html=HTML):
    return {'url': url, 'html': html, 'links': []}


def test_article_body_and_original_dom_survive_without_summary_or_truncation():
    record = module().extract_article(snapshot())
    assert record['body'] == '첫 문단.\n마지막 근거 끝.'
    assert record['raw_content'] == HTML
    assert record['published_at'] == ''
    assert record['body_coverage'] == 'full'
    assert record['extraction_scope'] == 'authorized_article'


MODAL_HTML = ('<div class="flex flex-col gap-6 p-6 max-mobile:p-4">'
              '<div>로이터</div><div class="bg-primary-blue/12">AI 요약 제외</div>'
              '<div class="flex flex-col"><p class="whitespace-pre-wrap">첫 원문.</p>'
              '<div class="h-6"></div><p class="whitespace-pre-wrap">마지막 원문.</p></div>'
              '<p>저작권 안내 제외</p></div>')


def modal_snapshot(html=MODAL_HTML):
    return {**snapshot(html=html), 'layout':'saveticker_modal_v1', 'title':'기사 제목'}


def test_live_modal_preserves_exact_section_and_excludes_ai_summary_footer():
    record = module().extract_article(modal_snapshot())
    assert record['title'] == '기사 제목'
    assert record['body'] == '첫 원문.\n마지막 원문.'
    assert record['raw_content'] == MODAL_HTML
    assert record['published_at'] == ''


def test_live_direct_article_h2_keeps_body_and_excludes_summary():
    html = MODAL_HTML.replace('<div class="flex flex-col gap-6 p-6 max-mobile:p-4">',
        '<article class="gap-6 p-6"><header><h2>원문 제목</h2></header>',1)
    html = html[:-6] + '</article>'
    record = module().extract_article(snapshot(html=html))
    assert record['title'] == '원문 제목'
    assert record['body'] == '첫 원문.\n마지막 원문.'
    assert record['raw_content'] == html


@pytest.mark.parametrize('html', [
    '<div><p>미리보기만 존재</p></div>',
    MODAL_HTML.replace('마지막 원문.', '미리보기...'),
    MODAL_HTML + '<div><p class="whitespace-pre-wrap">댓글</p></div>',
])
def test_live_modal_fails_closed_when_body_is_missing_partial_or_ambiguous(html):
    with pytest.raises(module().CollectionError):
        module().extract_article(modal_snapshot(html))


def test_explicit_article_collection_does_not_read_or_discover_other_articles(tmp_path):
    reads=[]
    class ExplicitBrowser:
        def read(self,url):
            reads.append(url)
            return modal_snapshot()
    result=module().collect(ExplicitBrowser(),outbox=tmp_path,
        sender=lambda p:{'ok':True},now=NOW,limit=1,article_urls=[URL])
    assert result['delivered']==1 and reads==[URL]


def test_explicit_article_urls_are_validated_before_any_io(tmp_path):
    with pytest.raises(module().CollectionError):
        module().collect(browser(),outbox=tmp_path,sender=lambda p:pytest.fail('no send'),
                         article_urls=['https://evil.example/news/1'])
    assert not list(tmp_path.iterdir())


def test_collection_uses_bounded_browser_discovery_for_click_based_listing(tmp_path):
    class ClickBrowser:
        def discover(self,limit):
            assert limit==1
            return {'url':'https://saveticker.com/','links':[URL]}
        def read(self,url):
            assert url==URL
            return modal_snapshot()
    result=module().collect(ClickBrowser(),outbox=tmp_path,sender=lambda p:{'ok':True},limit=1)
    assert result['delivered']==1


@pytest.mark.parametrize('suffix', ['', '/'])
def test_listing_metadata_survives_article_extraction_without_changing_raw(tmp_path, suffix):
    class ClickBrowser:
        def discover(self, limit):
            return {'url': 'https://saveticker.com/', 'links': [URL + suffix],
                    'article_metadata': {URL + suffix: {'category': '속보',
                        'published_at': '2026-10-13T18:00:00+09:00'}}}
        def read(self, url):
            return snapshot()
    delivered = []
    def sender(path):
        delivered.extend(json.loads(path.read_text())['records'])
        return {'ok': True}
    result = module().collect(ClickBrowser(), outbox=tmp_path, sender=sender, limit=1, now=NOW)
    assert result['delivered'] == 1
    assert delivered[0]['category'] == '속보'
    assert delivered[0]['published_at'] == '2026-10-13T18:00:00+09:00'
    assert delivered[0]['raw_content'] == HTML


def test_article_timestamp_wins_and_invalid_listing_time_is_not_invented():
    record = module().extract_article({**snapshot(html=HTML.replace('<h1>',
        '<time datetime="2026-10-12T12:00:00Z"></time><h1>', 1)),
        'published_at': '2026-10-13T18:00:00+09:00'})
    assert record['published_at'] == '2026-10-12T12:00:00Z'
    for value in ['18:00', '2026-10-13T18:00:00', 'invalid']:
        assert module().extract_article({**snapshot(), 'published_at': value})['published_at'] == ''


def test_listing_timestamp_is_used_when_article_has_no_timestamp():
    assert module().extract_article({**snapshot(),
        'published_at': '2026-10-13T18:00:00+09:00'})['published_at'] == '2026-10-13T18:00:00+09:00'


def test_article_breaking_badge_wins_over_listing_fallback():
    html = HTML.replace('<h1>', '<header><span>속보</span></header><h1>', 1)
    assert module().extract_article({**snapshot(html=html), 'category': '뉴스'})['category'] == '속보'


def test_hermes_click_discovery_handles_news_redirect_and_honors_limit():
    api=hermes_api()
    api.browser_navigate=lambda url,task_id:json.dumps({'success':True,'url':'https://saveticker.com/news'})
    calls=[]
    def console(**kw):
        calls.append(kw['expression'])
        return json.dumps({'success':True,'result':{'url':'https://saveticker.com/','links':[URL]}})
    api.browser_console=console
    value=module().HermesBrowser(api=api).discover(limit=1)
    assert value['links']==[URL]
    assert 'const limit = 1;' in calls[0]
    assert 'document.cookie' not in calls[0]


@pytest.mark.parametrize('write_delay,write_enabled,expected', [
    (500, True, '/news/clicked'),
    (0, False, None),
])
def test_discovery_javascript_never_uses_stale_clipboard(write_delay,write_enabled,expected):
    script = '''
    let clip='https://saveticker.com/news/old';
    let open=false;
    let copied=false;
    const share={click(){if(WRITE_ENABLED)setTimeout(()=>{clip='https://saveticker.com/news/clicked';copied=true},WRITE_DELAY)}};
    const close={click(){open=false}};
    const modal={matches:()=>true,querySelector:s=>s==='h2'?{}:s.includes('공유하기')?share:s.includes('뒤로가기')?close:null};
    const irrelevant={matches:()=>false,querySelector:()=>null};
    globalThis.document={title:'News',querySelector:()=>null,querySelectorAll:s=>
        s==='button'?[]:s.includes('data-breaking')?[{click(){open=true},
        getAttribute:s=>s==='data-breaking'?'true':s==='data-category'?'뉴스':null,
        querySelector:s=>s==='time[datetime]'?{getAttribute:()=> '2026-10-13T18:00:00+09:00'}:null}]:s.includes('modal-fullscreen')?(open?[modal]:[]):[]};
    Object.defineProperty(globalThis,'navigator',{value:{clipboard:{readText:async()=>clip,writeText:async value=>{clip=value}}},configurable:true});
    const limit=1;
    const result=await EXPRESSION;
    console.log(JSON.stringify({result,copied}));
    '''.replace('WRITE_ENABLED','true' if write_enabled else 'false').replace('WRITE_DELAY',str(write_delay)).replace('EXPRESSION',module().LISTING_DISCOVERY)
    run=subprocess.run(['node','--input-type=module'],input=script,text=True,capture_output=True,timeout=12)
    assert run.returncode==0,run.stderr
    result=json.loads(run.stdout)['result']
    if expected:
        assert result['links']==['https://saveticker.com'+expected]
        assert result['article_metadata']['https://saveticker.com'+expected] == {
            'category': '속보', 'published_at': '2026-10-13T18:00:00+09:00'}
    else:
        assert result.get('error')=='browser_unavailable'
        assert not result.get('links')


def test_cli_accepts_explicit_article_url_without_listing(tmp_path,capsys):
    cli=importlib.import_module('scripts.saveticker_collect')
    assert cli.main(['--url',URL,'--limit','1','--outbox',str(tmp_path)],
        browser_factory=lambda:Browser({URL:modal_snapshot()}),sender=lambda p:{'ok':True})==0
    assert json.loads(capsys.readouterr().out)['delivered']==1


@pytest.mark.parametrize('value,reason', [
    (snapshot(url='https://saveticker.com/login'), 'reauth_required'),
    (snapshot(html='<article><h1>로그인 후 열람</h1></article>'), 'reauth_required'),
    (snapshot(html='<article><input type="password"><p>기사</p></article>'), 'reauth_required'),
    ({**snapshot(), 'blocked': True}, 'blocked'),
    (snapshot(html='<article>Just a moment... cf-chl-test</article>'), 'blocked'),
    (snapshot(url='https://evil.example/news/123'), 'unsupported_url'),
    (snapshot(url=URL+'?token=secret'), 'unsupported_url'),
    (snapshot(html='<main>기사 미리보기</main>'), 'parse_failed'),
    (snapshot(html='<article><h1>기사</h1><p>짧은 미리보기...</p></article>'), 'incomplete_body'),
    (snapshot(html='<article><h1>기사</h1><p>'+ 'x'*100_001+'</p></article>'), 'oversized_body'),
])
def test_unreadable_articles_fail_explicitly(value, reason):
    with pytest.raises(module().CollectionError, match=reason):
        module().extract_article(value)


def test_discovery_accepts_only_unique_same_origin_article_links():
    page = {'url':'https://saveticker.com/', 'links':[
        URL, '/news/123', '/news/124', 'https://evil.example/news/1',
        '//evil.example/news/2', '/news/123?secret=x', '/report']}
    assert module().discover_urls(page, limit=20) == [URL, 'https://saveticker.com/news/124']


class Browser:
    def __init__(self, pages):
        self.pages = pages
    def read(self, url):
        return self.pages[url]


def browser(article=None):
    return Browser({'https://saveticker.com/': {'url':'https://saveticker.com/', 'links':[URL]},
                    URL: article or snapshot()})


def test_sent_body_is_complete_and_unchanged_versions_do_not_redeliver(tmp_path):
    sent = []
    def sender(path):
        sent.append(json.loads(path.read_text()))
        return {'ok':True}
    first = module().collect(browser(), outbox=tmp_path, sender=sender, now=NOW)
    second = module().collect(browser(), outbox=tmp_path, sender=sender, now=NOW)
    assert first['delivered'] == 1 and second['unchanged'] == 1
    assert len(sent) == 1
    assert sent[0]['records'][0]['body'].endswith('마지막 근거 끝.')
    assert first['wiki_status'] == 'pending'


def test_transport_retry_reuses_exact_delivery_file_and_timestamp(tmp_path):
    attempted = []
    def fail(path):
        attempted.append(path.read_bytes())
        raise TimeoutError('secret response must not be recorded')
    first = module().collect(browser(), outbox=tmp_path, sender=fail, now=NOW)
    assert first['status'] == 'delivery_failed' and first['delivered'] == 0
    state = json.loads((tmp_path/'collector-state.json').read_text())
    assert not state.get('last_success') and 'secret response' not in json.dumps(state)
    def success(path):
        attempted.append(path.read_bytes())
        return {'ok':True}
    second = module().collect(browser(), outbox=tmp_path, sender=success, now=NOW)
    assert second['delivered'] == 1 and attempted[0] == attempted[1]
    assert not list((tmp_path/'pending').glob('*.json'))


def test_new_source_revision_delivers_new_id(tmp_path):
    ids=[]
    def sender(path):
        ids.append(json.loads(path.read_text())['delivery_id'])
        return {'ok':True}
    module().collect(browser(), outbox=tmp_path, sender=sender, now=NOW)
    module().collect(browser(snapshot(html=HTML.replace('첫 문단.', '수정된 문단.'))),
                     outbox=tmp_path, sender=sender, now=NOW)
    assert len(ids) == 2 and ids[0] != ids[1]


def test_one_blocked_article_does_not_discard_valid_articles(tmp_path):
    pages = browser().pages
    pages['https://saveticker.com/']['links'].append('https://saveticker.com/news/124')
    pages['https://saveticker.com/news/124'] = {**snapshot('https://saveticker.com/news/124'), 'reauth_required':True}
    result = module().collect(Browser(pages), outbox=tmp_path, sender=lambda path:{'ok':True}, now=NOW)
    assert result['delivered'] == 1 and result['status'] == 'partial'
    assert result['errors'] == [{'url':'https://saveticker.com/news/124', 'reason':'reauth_required'}]


def test_inaccessible_listing_never_claims_success(tmp_path):
    page={'url':'https://saveticker.com/', 'blocked':True}
    result=module().collect(Browser({'https://saveticker.com/':page}), outbox=tmp_path,
                            sender=lambda path: pytest.fail('must not deliver'), now=NOW)
    assert result['status']=='blocked' and result['delivered']==0


def test_article_keeps_explicit_publication_timezone():
    html=HTML.replace('<h1>', '<time datetime="2026-10-12T21:30:00+09:00"></time><h1>')
    assert module().extract_article(snapshot(html=html))['published_at']=='2026-10-12T21:30:00+09:00'


def hermes_api(*, cdp='http://127.0.0.1:9222', altered=False):
    value={**snapshot(), 'html_sha256':hashlib.sha256(HTML.encode()).hexdigest()}
    if altered:
        value['html']=HTML.replace('첫 문단.', '요약으로 변경됨.')
    return SimpleNamespace(
        _cdp=SimpleNamespace(_get_cdp_override_raw=lambda:cdp),
        _is_camofox_mode=lambda:False,
        browser_navigate=lambda url, task_id:json.dumps({'success':True,'url':url}),
        browser_console=lambda **kw:json.dumps({'success':True,'result':value}))


@pytest.mark.parametrize('cdp', ['', 'https://public-debug.example', 'http://user:secret@127.0.0.1:9222'])
def test_hermes_requires_explicit_private_browser_attachment(cdp):
    with pytest.raises(module().CollectionError, match='browser_connection_required'):
        module().HermesBrowser(api=hermes_api(cdp=cdp))


def test_hermes_adapter_reads_dom_without_model_or_browser_storage():
    adapter=module().HermesBrowser(api=hermes_api())
    assert adapter.read(URL)['html']==HTML
    expression=module().DOM_CAPTURE
    assert 'document.cookie' not in expression and 'localStorage' not in expression
    assert 'subtle.digest' in expression


def test_direct_article_login_button_is_detected_as_expired_session():
    script='''
    globalThis.document={title:'News',body:{innerText:''},querySelector:s=>s==='article'?{outerHTML:__ARTICLE_HTML__}:null,
        querySelectorAll:s=>s==='button'?[{textContent:'로그인'}]:[]};
    globalThis.location={href:'https://saveticker.com/news/123'};
    const result=await EXPRESSION;
    console.log(JSON.stringify({reauth:result.reauth_required}));
    '''.replace('__ARTICLE_HTML__',json.dumps(HTML)).replace('EXPRESSION',module().DOM_CAPTURE)
    run=subprocess.run(['node','--input-type=module'],input=script,text=True,capture_output=True,timeout=5)
    assert run.returncode==0,run.stderr
    assert json.loads(run.stdout)['reauth'] is True


def test_redacted_or_shortened_browser_result_is_not_original():
    with pytest.raises(module().CollectionError, match='original_mismatch'):
        module().HermesBrowser(api=hermes_api(altered=True)).read(URL)


def test_adapter_focuses_exact_navigated_page_before_capture():
    calls=[]
    def focus(task_id,url):
        calls.append((task_id,url))
    adapter=module().HermesBrowser(api=hermes_api(),page_focus=focus)
    assert adapter.read(URL)['url']==URL
    assert calls==[('stock-report-saveticker',URL)]


def test_default_browser_context_permission_does_not_use_ungrantable_context_id(monkeypatch):
    calls=[]
    class Capture:
        page_session_id='page'
        def call(self,method,params=None,**kw):
            calls.append((method,params))
            if method=='Target.getTargetInfo':
                return {'result':{'targetInfo':{'browserContextId':'default-internal-id'}}}
            if method=='Target.getBrowserContexts':
                return {'result':{'browserContextIds':[]}}
            return {'result':{}}
    registry=SimpleNamespace(get=lambda task:SimpleNamespace(focus_page=lambda *a,**kw:{'ok':True}),
                             capture=lambda task:Capture())
    monkeypatch.setitem(sys.modules,'tools.browser_supervisor',SimpleNamespace(SUPERVISOR_REGISTRY=registry))
    module()._focus_attached_page('task',URL,clipboard=True)
    params=next(p for m,p in calls if m=='Browser.grantPermissions')
    assert 'browserContextId' not in params
    assert params['origin']=='https://saveticker.com'


def test_read_only_page_focus_does_not_grant_clipboard_permissions(monkeypatch):
    calls=[]
    class Capture:
        page_session_id='page'
        def call(self,method,params=None,**kw):
            calls.append(method)
            return {'result':{}}
    registry=SimpleNamespace(get=lambda task:SimpleNamespace(focus_page=lambda *a,**kw:{'ok':True}),
                             capture=lambda task:Capture())
    monkeypatch.setitem(sys.modules,'tools.browser_supervisor',SimpleNamespace(SUPERVISOR_REGISTRY=registry))
    module()._focus_attached_page('task',URL)
    assert calls==['Page.bringToFront']


def test_empty_or_unrecognized_listing_is_not_a_healthy_run(tmp_path):
    result=module().collect(Browser({'https://saveticker.com/':{'url':'https://saveticker.com/','links':[]}}),
                            outbox=tmp_path, sender=lambda p:pytest.fail('no delivery'), now=NOW)
    assert result['status']=='parse_failed'


def test_synthetic_article_reaches_real_archive_and_revision_discovery(tmp_path, monkeypatch):
    from reports import hermes_source_webhook as wh, raw_archive, source_collector, wiki_source_revision
    monkeypatch.setenv('STOCK_REPORT_REPORTS_DIR', str(tmp_path/'reports'))
    def sender(path):
        return wh.ingest_batch(json.loads(path.read_text()), cache_dir=tmp_path/'cache', state_dir=tmp_path/'receipts')
    now=datetime.now(timezone.utc)
    result=module().collect(browser(), outbox=tmp_path/'outbox', sender=sender, now=now)
    events=source_collector.load_recent_events(cache_dir=tmp_path/'cache')
    assert result['delivered']==1 and len(events)==1
    assert raw_archive.read_raw_artifact(events[0]['raw_path'])==HTML.encode()
    assert raw_archive.read_raw_artifact(events[0]['text_path'],encoding='utf-8').endswith('마지막 근거 끝.')
    discovery=wiki_source_revision.discover_revisions(events,now=now)
    assert len(discovery['eligible'])==1
    assert discovery['eligible'][0]['body'].endswith('마지막 근거 끝.')


def test_cli_runs_collector_and_prints_receipt_only(tmp_path, capsys):
    cli=importlib.import_module('scripts.saveticker_collect')
    assert cli.main(['--outbox',str(tmp_path)],browser_factory=browser,sender=lambda p:{'ok':True})==0
    output=capsys.readouterr().out
    assert json.loads(output)['delivered']==1
    assert '마지막 근거' not in output


def test_cli_reports_missing_browser_without_claiming_collection(tmp_path, capsys):
    cli=importlib.import_module('scripts.saveticker_collect')
    def unavailable():
        raise module().CollectionError('browser_connection_required')
    assert cli.main(['--outbox',str(tmp_path)],browser_factory=unavailable,sender=lambda p:pytest.fail('no send'))==2
    assert json.loads(capsys.readouterr().out)['status']=='browser_connection_required'
    state=json.loads((tmp_path/'collector-state.json').read_text())
    assert not state.get('last_success')


@pytest.mark.parametrize('html', [
    '<article><h1>기사</h1><p>일부 내용입니다.</p><button>Subscribe to unlock</button></article>',
    '<article><h1>기사</h1><p>짧은 미리보기입니다.</p></article>',
])
def test_unverified_preview_layout_is_never_full_text(html):
    with pytest.raises(module().CollectionError):
        module().extract_article(snapshot(html=html))


def test_gate_outside_article_prevents_extraction():
    value={**snapshot(), 'access_required':True}
    with pytest.raises(module().CollectionError, match='access_required'):
        module().extract_article(value)


def test_a_b_a_reversion_does_not_reuse_id_with_new_timestamp(tmp_path):
    sent=[]
    def sender(path):
        payload=json.loads(path.read_text())
        old=next((p for p in sent if p['delivery_id']==payload['delivery_id']),None)
        assert old is None or old==payload
        sent.append(payload)
        return {'ok':True}
    module().collect(browser(),outbox=tmp_path,sender=sender,now=NOW)
    module().collect(browser(snapshot(html=HTML.replace('첫 문단.','변경됨.'))),outbox=tmp_path,sender=sender,now=NOW)
    later=NOW.replace(hour=10)
    result=module().collect(browser(),outbox=tmp_path,sender=sender,now=later)
    assert result['unchanged']==1 and len(sent)==2


def test_retry_cursor_prevents_permanent_failure_starvation(tmp_path):
    records=[module().extract_article(snapshot(url='https://saveticker.com/news/'+str(i))) for i in [123,124]]
    pending=tmp_path/'pending'; pending.mkdir()
    files=[]
    for record in records:
        identity=module()._delivery_id(record)
        path=pending/(identity+'.json')
        path.write_text(json.dumps({'schema':'stock-report.source-batch.v1','delivery_id':identity,
                                    'collected_at':NOW.isoformat(),'records':[record]}))
        files.append(path)
    failing=min(files)
    delivered=[]
    def sender(path):
        if path==failing:
            raise TimeoutError()
        delivered.append(path.name)
        return {'ok':True}
    inaccessible=Browser({'https://saveticker.com/':{'url':'https://saveticker.com/','blocked':True}})
    module().collect(inaccessible,outbox=tmp_path,sender=sender,now=NOW,limit=1)
    module().collect(inaccessible,outbox=tmp_path,sender=sender,now=NOW,limit=1)
    assert len(delivered)==1


def test_check_connection_mode_cannot_collect_write_or_deliver(tmp_path,capsys):
    cli=importlib.import_module('scripts.saveticker_collect')
    class Readiness:
        def read(self,url):
            pytest.fail('must not navigate')
    assert cli.main(['--check-connection','--outbox',str(tmp_path/'not-created')],
                    browser_factory=Readiness,sender=lambda p:pytest.fail('must not send'))==0
    assert json.loads(capsys.readouterr().out)['status']=='browser_configured'
    assert not (tmp_path/'not-created').exists()


def test_missing_browser_state_recovers_once_connection_is_available(tmp_path,capsys):
    cli=importlib.import_module('scripts.saveticker_collect')
    def unavailable():
        raise module().CollectionError('browser_connection_required')
    cli.main(['--outbox',str(tmp_path)],browser_factory=unavailable)
    assert cli.main(['--outbox',str(tmp_path)],browser_factory=browser,sender=lambda p:{'ok':True})==0
    assert json.loads((tmp_path/'collector-state.json').read_text())['status']=='delivered'
