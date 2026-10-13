import importlib
import queue

import notify


def test_notification_supports_recovery_buttons(monkeypatch):
    calls=[]
    class Response:
        def raise_for_status(self): pass
    monkeypatch.setattr(notify.requests,'post',lambda url,**kw:(calls.append(kw['json']),Response())[1])
    markup={'inline_keyboard':[[{'text':'확인','callback_data':'savecheck:'+'a'*24}]]}
    assert notify.send_telegram('재인증',token='test',chat_id='owner',reply_markup=markup)
    assert calls[0]['reply_markup']==markup


def callback(chat='owner',user='owner',nonce='a'*24):
    return {'callback_query':{'id':'query','data':'savecheck:'+nonce,
        'from':{'id':user},'message':{'chat':{'id':chat}}}}


def test_owner_check_button_queues_authorized_confirmation(monkeypatch):
    bot=importlib.import_module('telegram_bot')
    monkeypatch.setattr(bot,'OWNER_CHAT_ID','owner')
    monkeypatch.delenv('STOCK_BOT_OWNER_USER_ID',raising=False)
    q=queue.Queue(); calls=[]
    monkeypatch.setattr(bot,'_DISPATCH_Q',q)
    monkeypatch.setattr(bot,'_api',lambda method,**kw:(calls.append(method),{'ok':True})[1])
    assert bot._handle_save_recovery_callback(callback()) is True
    assert q.get_nowait()==('/save check '+'a'*24,'owner','owner')
    assert calls==['answerCallbackQuery']


def test_non_owner_user_cannot_use_button_even_in_owner_chat(monkeypatch):
    bot=importlib.import_module('telegram_bot')
    monkeypatch.setattr(bot,'OWNER_CHAT_ID','owner')
    monkeypatch.delenv('STOCK_BOT_OWNER_USER_ID',raising=False)
    q=queue.Queue()
    monkeypatch.setattr(bot,'_DISPATCH_Q',q)
    monkeypatch.setattr(bot,'_api',lambda *a,**kw:{'ok':True})
    assert bot._handle_save_recovery_callback(callback(user='attacker')) is True
    assert q.empty()


def test_guest_cannot_run_recovery_command(monkeypatch):
    bot=importlib.import_module('telegram_bot')
    calls=[]
    monkeypatch.setattr(bot,'send',lambda *args:calls.append(args))
    bot.dispatch('/save check','guest',role='guest')
    assert calls and '소유자' in calls[0][1]


def test_poll_subscribes_to_callbacks(monkeypatch):
    bot=importlib.import_module('telegram_bot')
    calls=[]
    monkeypatch.setattr(bot,'_api',lambda method,**kw:(calls.append(kw),{'ok':True,'result':[]})[1])
    bot.get_updates()
    assert 'callback_query' in calls[0]['allowed_updates']


def test_command_check_requests_only_current_incident(tmp_path,monkeypatch):
    from reports import saveticker_recovery as recovery
    from bot import saveticker_commands as commands
    import safe_io
    path=tmp_path/'state.json'
    safe_io.atomic_write_json(str(path),{'phase':'awaiting_user','incident_id':'a'*24})
    monkeypatch.setenv('SAVETICKER_RECOVERY_STATE_PATH',str(path))
    sent=[]
    commands.handle('owner',['check','old'],send_fn=lambda *a:sent.append(a))
    assert not recovery.read_state(path).get('check_requested')
    commands.handle('owner',['check','a'*24],send_fn=lambda *a:sent.append(a))
    assert recovery.read_state(path)['check_requested'] is True


def test_owner_reopen_button_requests_screen_without_login(monkeypatch):
    bot=importlib.import_module('telegram_bot')
    monkeypatch.setattr(bot,'OWNER_CHAT_ID','owner')
    monkeypatch.delenv('STOCK_BOT_OWNER_USER_ID',raising=False)
    q=queue.Queue()
    monkeypatch.setattr(bot,'_DISPATCH_Q',q)
    monkeypatch.setattr(bot,'_api',lambda *a,**kw:{'ok':True})
    update=callback()
    update['callback_query']['data']='saveopen:'+'a'*24
    assert bot._handle_save_recovery_callback(update) is True
    assert q.get_nowait()==('/save open '+'a'*24,'owner','owner')
