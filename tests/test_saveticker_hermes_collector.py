import importlib
import hashlib
import json
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


def test_redacted_or_shortened_browser_result_is_not_original():
    with pytest.raises(module().CollectionError, match='original_mismatch'):
        module().HermesBrowser(api=hermes_api(altered=True)).read(URL)


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
