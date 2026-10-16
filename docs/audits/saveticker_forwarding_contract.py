"""Offline expected-behavior audit; run explicitly, not a production test suite.

Uses synthetic source text only. Failures document gaps in the current pipeline.
"""
import importlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
N=importlib.import_module('crons.news_spike_detector')

BODY='검증용 가상 본문 첫 문단.\n마지막 문단까지 전송되어야 합니다.'
URL='https://saveticker.com/news/synthetic_audit'


def event():
    return {'id':'synthetic-audit','source':'saveticker','title':'검증용 가상 속보',
        'tags':['속보'],'published_at':datetime.now(timezone.utc).isoformat(),
        'body':'미리보기','body_raw':BODY,'body_coverage':'full','fulltext_status':'full',
        'transport':'hermes_webhook','url':URL}


def setup_sender(monkeypatch,tmp_path,send):
    monkeypatch.setattr(N,'STATE_FILE',tmp_path/'sent.json')
    monkeypatch.setattr(N,'fetch_breaking_news',lambda now:[event()])
    monkeypatch.setattr(N,'judge_importance',lambda *a,**kw:(8,'합성 테스트',False))
    monkeypatch.setattr(N,'_send_telegram',send)
    from lib import world_memory
    monkeypatch.setattr(world_memory,'log_issue',lambda *a,**kw:None)


def test_full_source_body_must_be_in_stockbot_message(monkeypatch):
    monkeypatch.setattr(N,'_realtime_tag',lambda tags:'')
    message=N._format_alert(event(),8,'합성 테스트')
    assert event()['title'] in message
    assert BODY in message


def test_article_link_must_be_in_stockbot_message(monkeypatch):
    monkeypatch.setattr(N,'_realtime_tag',lambda tags:'')
    assert URL in N._format_alert(event(),8,'합성 테스트')


def test_publication_timestamp_must_be_converted_before_labeling_kst(monkeypatch):
    monkeypatch.setattr(N,'_realtime_tag',lambda tags:'')
    row=event()
    row['published_at']='2026-10-13T04:00:00+00:00'
    assert '2026-10-13 13:00 KST' in N._format_alert(row,8,'합성 테스트')


def test_failed_send_must_retry_in_next_run(monkeypatch,tmp_path):
    sends=[]
    setup_sender(monkeypatch,tmp_path,lambda text:(sends.append(text),len(sends)>1)[1])
    N.main()
    N.main()
    assert len(sends)==2


def test_authenticated_cache_must_remain_readable_when_public_fetch_is_blocked(monkeypatch,tmp_path):
    from reports import source_collector as sources
    row=event()
    sources.append_events([row],cache_dir=tmp_path)
    monkeypatch.setattr(N,'CACHE_DIR',tmp_path)
    def blocked(): raise RuntimeError('synthetic public provider access failure')
    monkeypatch.setattr(sources,'fetch_saveticker_events',blocked)
    rows=N.fetch_breaking_news(datetime.now(timezone.utc))
    # append_events assigns the canonical identity rather than keeping caller IDs.
    assert any(r['url']==row['url'] and r['body_raw']==BODY for r in rows)


def test_authenticated_article_extraction_must_preserve_known_breaking_category():
    from reports.saveticker_hermes_collector import extract_article
    raw='<article><header><span>속보</span><h1>가상 제목</h1></header><div data-article-body>가상 본문</div></article>'
    record=extract_article({'url':URL,'html':raw,'category':'속보'})
    assert record.get('category')=='속보'


def test_control_successful_send_is_deduplicated(monkeypatch,tmp_path):
    sends=[]
    setup_sender(monkeypatch,tmp_path,lambda text:(sends.append(text),True)[1])
    N.main()
    N.main()
    assert len(sends)==1


def test_control_standard_breaking_is_filtered_by_existing_importance_policy():
    score,_=N._rule_score({'title':'검증용 기업 일반 발표','tags':['속보']})
    assert score<N.IMPORTANCE_THRESHOLD
