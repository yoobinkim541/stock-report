import importlib
import json
from pathlib import Path

import pytest


def module():
    return importlib.import_module('reports.saveticker_recovery')


def run(tmp_path, result=None, now=1000, sent=None, login=None, remote=None):
    sent=sent if sent is not None else []
    return module().tick(state_path=tmp_path/'state.json', now=now,
        collect_fn=lambda:result or {'status':'reauth_required'},
        login_fn=login or (lambda:{'status':'authenticated'}),
        remote_fn=lambda **kw:(remote or (lambda:'http://100.93.71.84:6081/vnc.html?autoconnect=true&resize=scale'))(),
        send_fn=lambda text,**kw:(sent.append((text,kw)),True)[1])


def test_expired_login_sends_private_link_and_incident_check_button_once(tmp_path):
    sent=[]
    assert run(tmp_path,sent=sent)['phase']=='awaiting_user'
    state=json.loads((tmp_path/'state.json').read_text())
    assert len(sent)==1 and '100.93.71.84' in sent[0][0]
    rows=sent[0][1]['reply_markup']['inline_keyboard']
    assert rows[-1][0]['callback_data']=='savecheck:'+state['incident_id']
    run(tmp_path,sent=sent,now=1060)
    assert len(sent)==1


def test_confirmation_does_not_claim_recovery_until_login_and_ingest_pass(tmp_path):
    run(tmp_path)
    assert module().request_check(tmp_path/'state.json')=='queued'
    sent=[]
    calls=[]
    result=run(tmp_path,result={'status':'delivered','delivered':1},now=1100,sent=sent,
        login=lambda:(calls.append('login'),{'status':'authenticated'})[1])
    assert calls==['login'] and result['phase']=='healthy'
    assert len(sent)==1 and '복구 완료' in sent[0][0]
    state=json.loads((tmp_path/'state.json').read_text())
    assert state['last_success']==1100 and not state.get('incident_id')


@pytest.mark.parametrize('result',[
    {'status':'parse_failed'},
    {'status':'no_change','unchanged':0},
    {'status':'partial','delivered':1,'errors':[{'reason':'reauth_required'}]},
])
def test_failed_or_empty_collection_cannot_mark_recovered(tmp_path,result):
    run(tmp_path)
    module().request_check(tmp_path/'state.json')
    sent=[]
    outcome=run(tmp_path,result=result,now=1100,sent=sent)
    assert outcome['phase']!='healthy'
    assert not json.loads((tmp_path/'state.json').read_text()).get('last_success')
    assert not any(text.startswith('SaveTicker 복구 완료:') for text,_ in sent)


def test_waiting_for_user_never_retries_login_or_bypasses_challenge(tmp_path):
    run(tmp_path,result={'status':'blocked'})
    outcome=module().tick(state_path=tmp_path/'state.json',now=1060,
        collect_fn=lambda:pytest.fail('no collection while blocked'),
        login_fn=lambda:pytest.fail('no automated challenge'),
        remote_fn=lambda **kw:'http://100.93.71.84:6081/vnc.html',send_fn=lambda *a,**kw:True)
    assert outcome['phase']=='awaiting_user'


def test_stale_check_button_cannot_confirm_new_incident(tmp_path):
    run(tmp_path)
    assert module().request_check(tmp_path/'state.json',incident_id='old')=='stale'
    assert not json.loads((tmp_path/'state.json').read_text()).get('check_requested')


def test_notification_failure_retries_without_repeating_browser_fetch(tmp_path):
    sends=[]
    def send(text,**kw):
        sends.append(text)
        return len(sends)>1
    args=dict(state_path=tmp_path/'state.json',collect_fn=lambda:{'status':'blocked'},
        login_fn=lambda:pytest.fail('no login'),remote_fn=lambda **kw:'http://100.93.71.84:6081/vnc.html',send_fn=send)
    module().tick(**args,now=1000)
    args['collect_fn']=lambda:pytest.fail('no repeat fetch')
    module().tick(**args,now=1060)
    assert len(sends)==2


def test_failed_remote_setup_is_retried_without_repeating_broken_link_alerts(tmp_path):
    sent=[]
    run(tmp_path,sent=sent,remote=lambda:None)
    assert len(sent)==1
    run(tmp_path,now=1060,sent=sent,remote=lambda:None)
    assert len(sent)==1
    run(tmp_path,now=1120,sent=sent)
    assert len(sent)==2 and '100.93.71.84' in sent[-1][0]


def test_login_failure_does_not_loop_credential_attempts(tmp_path):
    run(tmp_path)
    module().request_check(tmp_path/'state.json')
    calls=[]
    outcome=run(tmp_path,now=1100,login=lambda:(calls.append(1),{'status':'reauth_required'})[1])
    assert outcome['phase']=='awaiting_user'
    run(tmp_path,now=1160,login=lambda:pytest.fail('must await new user action'))
    assert calls==[1]


def test_healthy_ticks_are_rate_limited(tmp_path):
    assert run(tmp_path,result={'status':'no_change','unchanged':1})['phase']=='healthy'
    assert module().tick(state_path=tmp_path/'state.json',now=1060,
        collect_fn=lambda:pytest.fail('not due'),login_fn=lambda:pytest.fail('not needed'),
        remote_fn=lambda **kw:pytest.fail('not needed'),send_fn=lambda *a,**kw:True)['phase']=='healthy'


@pytest.mark.parametrize('url',['http://0.0.0.0:6081/vnc.html','http://example.com/vnc.html',
    'http://user:pass@100.93.71.84:6081/vnc.html'])
def test_public_or_credential_bearing_remote_links_are_not_sent(tmp_path,url):
    sent=[]
    run(tmp_path,sent=sent,remote=lambda:url)
    assert not any(url in text for text,_ in sent)


def test_confirmation_is_consumed_durably_even_if_worker_dies_after_login(tmp_path):
    run(tmp_path)
    module().request_check(tmp_path/'state.json')
    calls=[]
    def interrupted():
        calls.append(1)
        raise SystemExit()
    with pytest.raises(SystemExit):
        run(tmp_path,now=1100,login=interrupted)
    run(tmp_path,now=1160,login=lambda:pytest.fail('needs fresh owner confirmation'))
    assert calls==[1]
    assert not module().read_state(tmp_path/'state.json').get('check_requested')


def test_incident_access_is_not_renewed_without_owner_action(tmp_path):
    calls=[]
    sent=[]
    run(tmp_path,sent=sent,remote=lambda:(calls.append(1000),'http://100.93.71.84:6081/vnc.html')[1])
    run(tmp_path,now=8300,sent=sent,remote=lambda:pytest.fail('access deadline passed'))
    run(tmp_path,now=8360,sent=sent,remote=lambda:pytest.fail('access deadline passed'))
    assert calls==[1000]
    assert len(sent)==2
    assert not module().read_state(tmp_path/'state.json').get('remote_url')


def test_remote_reopen_requires_new_security_confirmation(tmp_path):
    run(tmp_path)
    module().request_check(tmp_path/'state.json')
    assert module().request_remote(tmp_path/'state.json')=='queued'
    outcome=run(tmp_path,now=8300,login=lambda:pytest.fail('opening screen is not login consent'))
    assert outcome['phase']=='awaiting_user'
    assert module().read_state(tmp_path/'state.json')['access_expires_at']==15500


def test_failed_failure_notification_is_retried_without_login(tmp_path):
    run(tmp_path)
    module().request_check(tmp_path/'state.json')
    sent=[]
    def send(text,**kw):
        sent.append(text)
        return len(sent)>1
    args=dict(state_path=tmp_path/'state.json',collect_fn=lambda:{'status':'parse_failed'},
        login_fn=lambda:{'status':'authenticated'},remote_fn=lambda **kw:None,send_fn=send)
    module().tick(**args,now=1100)
    args['login_fn']=lambda:pytest.fail('no additional login')
    module().tick(**args,now=1160)
    assert len(sent)==2 and '검증' in sent[1]


def test_initial_remote_deadline_survives_worker_exit_during_provisioning(tmp_path):
    def interrupted(): raise SystemExit()
    with pytest.raises(SystemExit):
        run(tmp_path,remote=interrupted)
    state=module().read_state(tmp_path/'state.json')
    assert state['phase']=='awaiting_user' and state['access_expires_at']==8200
    run(tmp_path,now=8300,remote=lambda:pytest.fail('no renewed access'))


def test_pending_failure_notice_survives_worker_exit_during_send(tmp_path):
    run(tmp_path)
    module().request_check(tmp_path/'state.json')
    def interrupted(*a,**kw): raise SystemExit()
    with pytest.raises(SystemExit):
        module().tick(state_path=tmp_path/'state.json',now=1100,
            collect_fn=lambda:{'status':'parse_failed'},login_fn=lambda:{'status':'authenticated'},
            remote_fn=lambda **kw:None,send_fn=interrupted)
    assert module().read_state(tmp_path/'state.json')['failure_notice_pending'] is True
    sent=[]
    run(tmp_path,now=1160,sent=sent)
    assert len(sent)==1 and '검증' in sent[0][0]


def test_recovered_state_survives_worker_exit_during_success_notice(tmp_path):
    run(tmp_path)
    module().request_check(tmp_path/'state.json')
    def interrupted(*a,**kw): raise SystemExit()
    with pytest.raises(SystemExit):
        module().tick(state_path=tmp_path/'state.json',now=1100,
            collect_fn=lambda:{'status':'delivered','delivered':1},login_fn=lambda:{'status':'authenticated'},
            remote_fn=lambda **kw:None,send_fn=interrupted)
    assert module().read_state(tmp_path/'state.json')['phase']=='healthy'
    sent=[]
    run(tmp_path,now=1160,sent=sent)
    assert len(sent)==1 and sent[0][0].startswith('SaveTicker 복구 완료:')
