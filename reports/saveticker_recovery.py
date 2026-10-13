"""Human-confirmed SaveTicker reauthentication and verified collection recovery."""
from __future__ import annotations

import ipaddress
import json
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import safe_io

DEFAULT_STATE = Path.home() / 'reports/saveticker-recovery/state.json'
AUTH_FAILURES = {'blocked', 'reauth_required', 'authentication_failed', 'access_required', 'auth_config_required'}


def read_state(path=DEFAULT_STATE):
    path = Path(path)
    return json.loads(path.read_text()) if path.exists() else {'phase':'unknown'}


def request_check(path=DEFAULT_STATE, *, incident_id=None):
    path = Path(path)
    with safe_io.file_write_lock(str(path)):
        state = read_state(path)
        if state.get('phase') != 'awaiting_user':
            return 'not_waiting'
        if incident_id is not None and incident_id != state.get('incident_id'):
            return 'stale'
        state['check_requested'] = True
        safe_io.atomic_write_json(str(path), state)
        return 'queued'


def request_remote(path=DEFAULT_STATE, *, incident_id=None):
    path = Path(path)
    with safe_io.file_write_lock(str(path)):
        state = read_state(path)
        if state.get('phase') != 'awaiting_user':
            return 'not_waiting'
        if incident_id is not None and incident_id != state.get('incident_id'):
            return 'stale'
        state['remote_requested'] = True
        state['check_requested'] = False
        safe_io.atomic_write_json(str(path),state)
        return 'queued'


def _private_url(value):
    if not isinstance(value,str):
        return None
    parsed = urlsplit(value)
    try:
        host = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        return None
    if (parsed.scheme not in {'http','https'} or parsed.username or parsed.password
            or parsed.fragment or host not in ipaddress.ip_network('100.64.0.0/10')
            or parsed.path != '/vnc.html'):
        return None
    if parsed.query not in {'','autoconnect=true&resize=scale'}:
        return None
    return value


def _send(send_fn,text,markup=None):
    try:
        return send_fn(text,reply_markup=markup) is True
    except Exception:
        return False


def _auth_notice(state,remote_fn,send_fn,now,persist):
    state.setdefault('access_expires_at',now+7200)
    persist()
    remaining = int(state['access_expires_at']-now)
    try:
        url = _private_url(remote_fn(max_seconds=remaining)) if remaining>=60 else None
    except Exception:
        url = None
    message = ('SaveTicker 재인증이 필요합니다.\n'
               '노트북에서 Tailscale을 켜고 서버 브라우저의 보안 확인을 직접 완료해주세요.\n'
               '완료 후 아래 확인 버튼을 누르면 저장된 로그인 정보로 로그인과 원문 수집을 검증합니다.\n')
    rows = []
    if url:
        message += '\n원격 화면: ' + url
        rows.append([{'text':'서버 브라우저 열기','url':url}])
        state['remote_url'] = url
    else:
        message += '\n원격 화면이 만료되었거나 준비되지 않았습니다. 아래 화면 다시 열기 버튼을 사용해주세요.'
        state.pop('remote_url',None)
        rows.append([{'text':'원격 화면 다시 열기','callback_data':'saveopen:'+state['incident_id']}])
    rows.append([{'text':'보안 확인 완료 · 복구 확인','callback_data':'savecheck:'+state['incident_id']}])
    if not url and state.get('auth_notice_sent_at') and remaining>=60:
        return
    persist()
    if _send(send_fn,message,{'inline_keyboard':rows}):
        state['auth_notice_sent_at'] = now


def tick(*,state_path=DEFAULT_STATE,collect_fn,login_fn,remote_fn,send_fn,
         now=None,interval=900,cooldown=3600):
    now = time.time() if now is None else now
    state_path = Path(state_path)
    with safe_io.file_write_lock(str(state_path)):
        state = read_state(state_path)
        persist = lambda:safe_io.atomic_write_json(str(state_path),state)
        previous = state.get('phase')
        if state.get('recovery_notice_pending'):
            if _send(send_fn,'SaveTicker 복구 완료: 로그인과 원문 수집·저장 검증을 통과했습니다.\n외부 LLM 전송은 활성화하지 않았습니다.'):
                state.pop('recovery_notice_pending',None)
        if state.get('failure_notice_pending'):
            if _send(send_fn,'SaveTicker 재인증 또는 원문 수집 검증이 실패했습니다. 아직 정상화되지 않았습니다.\n/save check 로 다시 확인할 수 있습니다.'):
                state.pop('failure_notice_pending',None)
        if previous=='awaiting_user' and state.pop('remote_requested',False):
            state['access_expires_at']=now+7200
            state.pop('auth_notice_sent_at',None)
            safe_io.atomic_write_json(str(state_path),state)
            _auth_notice(state,remote_fn,send_fn,now,persist)
        confirmed = previous == 'awaiting_user' and state.pop('check_requested',False)
        if previous == 'awaiting_user' and not confirmed:
            if (not state.get('auth_notice_sent_at') or now-state['auth_notice_sent_at'] >= cooldown
                    or (not state.get('remote_url') and now<state.get('access_expires_at',0))):
                _auth_notice(state,remote_fn,send_fn,now,persist)
            safe_io.atomic_write_json(str(state_path),state)
            return {'phase':state['phase']}
        if not confirmed and now-state.get('last_attempt',-interval) < interval:
            safe_io.atomic_write_json(str(state_path),state)
            return {'phase':state.get('phase','unknown')}
        state['last_attempt'] = now
        # A killed/timed-out worker must never replay an already consumed confirmation.
        safe_io.atomic_write_json(str(state_path),state)
        try:
            auth = login_fn() if confirmed else {'status':'authenticated'}
            result = collect_fn() if auth.get('status') == 'authenticated' else auth
            if not isinstance(result,dict):
                result = {'status':'collection_failed'}
        except Exception:
            result = {'status':'collection_failed'}
        reasons = {result.get('status')} | {e.get('reason') for e in result.get('errors',[]) if isinstance(e,dict)}
        if reasons & AUTH_FAILURES:
            state['phase'] = 'awaiting_user'
            if not state.get('incident_id'):
                state['incident_id'] = uuid.uuid4().hex[:24]
                state.pop('auth_notice_sent_at',None)
            state['reason'] = next(iter(sorted(reasons & AUTH_FAILURES)))
            if confirmed or not state.get('auth_notice_sent_at'):
                _auth_notice(state,remote_fn,send_fn,now,persist)
        elif result.get('status') in {'delivered','no_change'} and (
                result.get('delivered',0)+result.get('unchanged',0)>0):
            state.update(phase='healthy',last_success=now)
            had_incident = bool(state.pop('incident_id',None))
            for key in ('reason','remote_url','auth_notice_sent_at'):
                state.pop(key,None)
            state.pop('access_expires_at',None)
            state.pop('failure_notice_pending',None)
            if had_incident:
                state['recovery_notice_pending'] = True
                persist()
                if _send(send_fn,'SaveTicker 복구 완료: 로그인과 원문 수집·저장 검증을 통과했습니다.\n외부 LLM 전송은 활성화하지 않았습니다.'):
                    state.pop('recovery_notice_pending',None)
        else:
            state['phase'] = 'awaiting_user' if state.get('incident_id') else 'error'
            state['reason'] = 'collection_failed'
            if confirmed:
                state['failure_notice_pending']=True
                persist()
                if _send(send_fn,'SaveTicker 재인증 또는 원문 수집 검증이 실패했습니다. 아직 정상화되지 않았습니다.\n/save check 로 다시 확인할 수 있습니다.'):
                    state.pop('failure_notice_pending',None)
        safe_io.atomic_write_json(str(state_path),state)
        return {'phase':state['phase'],'collection_status':result.get('status')}
