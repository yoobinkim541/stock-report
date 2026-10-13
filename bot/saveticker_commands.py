"""Owner-only recovery commands; actual login happens in the monitored worker."""
import os
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from reports import saveticker_recovery as recovery


def handle(chat_id,args,*,send_fn):
    path=Path(os.getenv('SAVETICKER_RECOVERY_STATE_PATH',str(recovery.DEFAULT_STATE)))
    sub=args[0].lower() if args else 'status'
    if sub in {'check','open'}:
        action=recovery.request_check if sub=='check' else recovery.request_remote
        result=action(path,incident_id=args[1] if len(args)>1 else None)
        messages={
            'queued':'SaveTicker 복구 확인을 요청했습니다. 로그인과 원문 저장 검증 후 결과를 보내드립니다.',
            'stale':'이전 장애의 확인 버튼입니다. 최신 알림을 사용해주세요.',
            'not_waiting':'현재 재인증 대기 중인 장애가 없습니다. /save status 로 확인해주세요.',
        }
        if sub=='open':
            messages['queued']='원격 화면 다시 열기를 요청했습니다. 새 화면에서 보안 확인 후 복구 확인 버튼을 눌러주세요.'
        send_fn(chat_id,messages[result])
    else:
        state=recovery.read_state(path)
        labels={'healthy':'정상','awaiting_user':'사용자 보안 확인 대기','error':'수집 검증 실패','unknown':'아직 검증하지 않음'}
        message='SaveTicker 상태: '+labels.get(state.get('phase'),'확인 중')
        if state.get('last_success'):
            stamp=datetime.fromtimestamp(state['last_success'],ZoneInfo('Asia/Seoul')).strftime('%m-%d %H:%M KST')
            message+='\n마지막 원문 검증: '+stamp
            if time.time()-state['last_success']>3600:
                message+='\n검증 정보가 오래되었습니다. 감시 작업 상태를 확인해주세요.'
        if state.get('remote_url') and recovery._private_url(state['remote_url']):
            message+='\n원격 화면: '+state['remote_url']
        if state.get('phase')=='awaiting_user':
            message+='\n보안 확인을 마친 뒤 /save check 를 보내주세요.'
        send_fn(chat_id,message)
