import hashlib
import json
import time
from datetime import datetime, timezone

import pytest


def batch(delivery='test-delivery'):
    return {'schema': 'stock-report.source-batch.v1', 'delivery_id': delivery,
            'collected_at': datetime.now(timezone.utc).isoformat(),
            'records': [{'url': 'https://saveticker.com/news/1234', 'title': 'Source article',
                         'body': 'Original source body. ' * 20 + 'Important last paragraph.',
                         'body_coverage': 'full', 'extraction_scope': 'rendered_article',
                         'published_at': '2026-10-06T01:00:00+00:00'}]}


@pytest.fixture
def receiver(tmp_path, monkeypatch):
    from reports import hermes_source_webhook as wh
    monkeypatch.setenv('STOCK_REPORT_REPORTS_DIR', str(tmp_path / 'reports'))
    app = wh.create_app(secret='test-only-secret', cache_dir=tmp_path / 'cache', state_dir=tmp_path / 'state')
    app.testing = True
    return wh, app.test_client(), tmp_path


def send(wh, client, payload, timestamp=None):
    raw = json.dumps(payload, ensure_ascii=False).encode()
    timestamp = str(timestamp or int(time.time()))
    return client.post('/webhooks/hermes-sources', data=raw,
                       headers={**wh.signed_headers(raw, 'test-only-secret', timestamp, payload['delivery_id']),
                                'Content-Type': 'application/json'})


def test_signed_body_is_archived_without_truncation_and_retry_is_idempotent(receiver):
    from reports import source_collector, raw_archive
    wh, client, root = receiver
    payload = batch()
    response = send(wh, client, payload)
    assert response.status_code == 200
    assert response.json['wiki_status'] == 'pending'
    first = source_collector.load_recent_events(cache_dir=root / 'cache')
    assert len(first) == 1 and first[0]['body_raw'] == payload['records'][0]['body']
    assert first[0]['transport'] == 'hermes_webhook'
    assert first[0]['published_at'] == payload['records'][0]['published_at']
    raw_path = first[0]['raw_path']
    original = raw_archive.read_raw_artifact(raw_path)
    assert hashlib.sha256(original).hexdigest() == first[0]['raw_sha256']
    assert raw_archive.read_raw_text(first[0]['text_path']) == payload['records'][0]['body']
    assert send(wh, client, payload).json['duplicate'] is True
    assert len(source_collector.load_recent_events(cache_dir=root / 'cache')) == 1
    assert raw_archive.read_raw_artifact(raw_path) == original


def test_unauthorized_and_old_deliveries_cannot_write(receiver):
    wh, client, root = receiver
    assert client.post('/webhooks/hermes-sources', json=batch()).status_code == 401
    assert send(wh, client, batch(), int(time.time()) - 1000).status_code == 401
    assert not list((root / 'reports').glob('raw/**/*'))


@pytest.mark.parametrize('change', [
    {'body': ''}, {'body_coverage': 'preview_only'},
    {'url': 'http://127.0.0.1/private'}, {'url': 'https://saveticker.com/login'},
    {'body': 'Just a moment... Cloudflare challenge'},
    {'body': '소셜 로그인 후 이용 가능합니다'}, {'cookies': 'must-not-be-sent'},
])
def test_invalid_or_authentication_content_rejected_before_writes(receiver, change):
    wh, client, root = receiver
    payload = batch()
    payload['records'][0].update(change)
    assert send(wh, client, payload).status_code == 400
    assert not list((root / 'reports').glob('raw/**/*'))


def test_reused_delivery_id_with_different_content_is_conflict(receiver):
    wh, client, _ = receiver
    payload = batch()
    assert send(wh, client, payload).status_code == 200
    payload['records'][0]['body'] += ' Different content.'
    assert send(wh, client, payload).status_code == 409


def test_entire_batch_validated_before_first_write(receiver):
    wh, client, root = receiver
    payload = batch()
    payload['records'].append({**payload['records'][0], 'url': 'https://other.example/private'})
    assert send(wh, client, payload).status_code == 400
    assert not list((root / 'reports').glob('raw/**/*'))




def test_crash_after_event_append_can_resume_same_delivery(receiver, monkeypatch):
    from reports import source_collector
    wh, client, root = receiver
    append = source_collector.append_events
    first = True

    def interrupted(*args, **kwargs):
        nonlocal first
        count = append(*args, **kwargs)
        if first:
            first = False
            raise OSError('simulated process failure after append')
        return count

    monkeypatch.setattr(source_collector, 'append_events', interrupted)
    payload = batch('resume-after-crash')
    assert send(wh, client, payload).status_code == 500
    assert send(wh, client, payload).status_code == 200
    assert len(source_collector.load_recent_events(cache_dir=root / 'cache')) == 1


def test_sender_signs_only_source_batch_and_rejects_redirect(tmp_path, monkeypatch):
    from scripts.hermes_source_send import send_batch
    from reports import hermes_source_webhook as wh
    path = tmp_path / 'body.json'
    path.write_text(json.dumps(batch()))
    monkeypatch.setenv('HERMES_SOURCE_WEBHOOK_SECRET', 'test-only-secret')
    seen = {}

    def post(url, **kwargs):
        seen.update(kwargs)
        expected = wh.signed_headers(kwargs['data'], 'test-only-secret',
                                     kwargs['headers']['X-Hermes-Timestamp'], 'test-delivery')
        assert kwargs['headers']['X-Hermes-Signature-256'] == expected['X-Hermes-Signature-256']
        return type('Response', (), {'status_code': 302, 'json': lambda self: {}, 'raise_for_status': lambda self: None})()

    with pytest.raises(ValueError, match='redirect'):
        send_batch(path, endpoint='http://127.0.0.1:8777/webhooks/hermes-sources', post=post)
    assert seen['allow_redirects'] is False


def test_credential_content_in_raw_tail_is_rejected(receiver):
    wh, client, root = receiver
    payload = batch('raw-tail')
    payload['records'][0]['raw_content'] = 'Article text. ' * 600 + 'access_token="not-source-data"'
    assert send(wh, client, payload).status_code == 400
    assert not list((root / 'reports').glob('raw/**/*'))


def test_oversize_request_rejected(receiver):
    wh, client, _ = receiver
    payload = batch('too-large')
    payload['records'][0]['body'] = 'x' * wh.MAX_REQUEST_BYTES
    assert send(wh, client, payload).status_code == 413


def test_receiver_not_configured_fails_closed(tmp_path, monkeypatch):
    from reports import hermes_source_webhook as wh
    monkeypatch.delenv('HERMES_SOURCE_WEBHOOK_SECRET', raising=False)
    client = wh.create_app(cache_dir=tmp_path / 'cache', state_dir=tmp_path / 'state').test_client()
    assert client.post('/webhooks/hermes-sources', json=batch()).status_code == 503


def test_tampered_signature_and_duplicate_json_keys_rejected(receiver):
    wh, client, _ = receiver
    payload = batch('duplicate-key')
    raw = json.dumps(payload).encode()
    headers = {**wh.signed_headers(raw, 'test-only-secret', str(int(time.time())), payload['delivery_id']),
               'Content-Type': 'application/json'}
    assert client.post('/webhooks/hermes-sources', data=raw + b' ', headers=headers).status_code == 401
    raw = raw[:-1] + b', "schema": "stock-report.source-batch.v1"}'
    headers.update(wh.signed_headers(raw, 'test-only-secret', str(int(time.time())), payload['delivery_id']))
    assert client.post('/webhooks/hermes-sources', data=raw, headers=headers).status_code == 400


def test_runtime_loads_dedicated_configuration(tmp_path, monkeypatch):
    from reports import hermes_source_webhook as wh
    env = tmp_path / 'private.env'
    env.write_text('HERMES_SOURCE_WEBHOOK_SECRET=test-runtime-secret\n')
    monkeypatch.delenv('HERMES_SOURCE_WEBHOOK_SECRET', raising=False)
    assert wh.runtime_app(env_file=env).test_client().get('/health').json['configured'] is True


def test_source_scope_must_match_provider(receiver):
    wh, client, _ = receiver
    payload = batch('wrong-scope')
    payload['records'][0]['extraction_scope'] = 'public_message'
    assert send(wh, client, payload).status_code == 400


def test_existing_preview_is_upgraded_without_erasing_source_history(receiver):
    from reports import source_collector
    wh, client, root = receiver
    payload = batch('preview-upgrade')
    record = payload['records'][0]
    source_collector.append_events([{'source': 'saveticker', 'url': record['url'],
                                    'title': record['title'], 'body_raw': 'Only a preview',
                                    'fulltext_status': 'preview_only'}], cache_dir=root / 'cache')
    assert send(wh, client, payload).status_code == 200
    latest = source_collector.load_recent_events(cache_dir=root / 'cache')
    assert len(latest) == 1 and latest[0]['body_raw'] == record['body']
    files = list((root / 'cache').glob('events-*.jsonl'))
    rows = [json.loads(line) for path in files for line in path.read_text().splitlines()]
    assert len(rows) == 2 and rows[0]['body_raw'] == 'Only a preview'
    payload['delivery_id'] = 'same-body-different-delivery'
    assert send(wh, client, payload).json['persisted'] == 0


def test_sender_success_and_http_failure_are_not_confused(tmp_path, monkeypatch):
    import requests
    from scripts.hermes_source_send import send_batch
    path = tmp_path / 'body.json'
    path.write_text(json.dumps(batch()))
    monkeypatch.setenv('HERMES_SOURCE_WEBHOOK_SECRET', 'test-only-secret')
    response = requests.Response()
    response.status_code = 503
    with pytest.raises(requests.HTTPError):
        send_batch(path, post=lambda *args, **kwargs: response)
    response.status_code = 200
    response._content = b'{"ok": true, "wiki_status": "pending"}'
    assert send_batch(path, post=lambda *args, **kwargs: response)['wiki_status'] == 'pending'


def test_hermes_health_does_not_hide_public_source_block(receiver):
    from reports import source_collector
    wh, client, root = receiver
    source_collector.update_source_health([], cache_dir=root / 'cache', attempted_sources=['saveticker'],
        run_stats={'saveticker': {'availability': 'blocked', 'error': 'HTTP 403', 'fetch_success': False}})
    before = source_collector.load_source_health(cache_dir=root / 'cache')['saveticker']
    assert send(wh, client, batch()).status_code == 200
    health = source_collector.load_source_health(cache_dir=root / 'cache')
    assert health['saveticker'] == before
    assert health['hermes_webhook']['fetch_success'] is True
    assert health['hermes_webhook']['last_persisted_count'] == 1


@pytest.mark.parametrize('body', ["document.cookie='sessionid=abc123'",
                                'Set-Cookie: sessionid=abc123; Secure',
                                'Cookie: cf_clearance=abc123', 'Log in to continue',
                                '로그인이 필요합니다'])
def test_additional_session_material_and_login_prompts_are_not_sources(receiver, body):
    wh, client, _ = receiver
    payload = batch('login-or-session')
    payload['records'][0]['body'] = body
    assert send(wh, client, payload).status_code == 400


def test_delayed_first_event_append_is_current_and_keeps_original_collection_time(receiver):
    from reports import source_collector
    from datetime import timedelta
    import safe_io
    wh, client, root = receiver
    payload = batch('late-first-append')
    old = datetime.now(timezone.utc) - timedelta(days=4)
    payload['collected_at'] = old.isoformat()
    fingerprint = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                           separators=(',', ':')).encode()).hexdigest()
    path = root / 'state' / (hashlib.sha256(payload['delivery_id'].encode()).hexdigest() + '.json')
    safe_io.atomic_write_json(str(path), {'payload_sha256': fingerprint, 'delivery_id': payload['delivery_id'],
                                       'status': 'pending', 'received_at': old.isoformat(), 'archives': {}})
    assert send(wh, client, payload).status_code == 200
    events = source_collector.load_recent_events(cache_dir=root / 'cache', hours=48)
    assert len(events) == 1 and events[0]['source_collected_at'] == old.isoformat()
