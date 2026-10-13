"""Authenticated, narrow source-body ingress from an authorized Hermes collector."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from flask import Flask, jsonify, request

import safe_io
from reports import raw_archive, source_collector

SCHEMA = 'stock-report.source-batch.v1'
MAX_REQUEST_BYTES = 2 * 1024 * 1024
MAX_RECORDS = 20
MAX_BODY_CHARS = 100_000
MAX_RAW_CHARS = 200_000
REPLAY_WINDOW = 300


class DeliveryConflict(ValueError):
    pass


def signed_headers(body: bytes, secret: str, timestamp: str, delivery_id: str) -> dict:
    signed = timestamp.encode() + b'\n' + delivery_id.encode() + b'\n' + body
    signature = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return {'X-Hermes-Timestamp': timestamp, 'X-Hermes-Delivery-ID': delivery_id,
            'X-Hermes-Signature-256': 'sha256=' + signature}


def _date(value: object) -> datetime:
    if not isinstance(value, str) or len(value) > 80:
        raise ValueError('invalid source timestamp')
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('source timestamp requires timezone')
    return parsed


def _source(url: str) -> tuple[str, str]:
    parsed = urlparse(url)
    if parsed.scheme != 'https' or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('unsupported source URL')
    host = parsed.netloc.lower()
    if host in {'saveticker.com', 'www.saveticker.com'} and re.fullmatch(r'/news/[A-Za-z0-9_-]+/?', parsed.path):
        return 'saveticker', 'saveticker_article'
    if host == 'arca.live' and re.fullmatch(r'/b/stock/\d+/?', parsed.path):
        return 'arca', 'arca'
    if host == 't.me' and re.fullmatch(r'/(?:s/)?[A-Za-z0-9_]+/\d+/?', parsed.path):
        channel = parsed.path.strip('/').split('/')[-2]
        return 'telegram:' + channel, 'telegram'
    if host == 'fred.stlouisfed.org' and re.fullmatch(r'/series/[A-Za-z0-9_-]+/?', parsed.path):
        return 'fred', 'fred'
    raise ValueError('unsupported source URL')


def validate_batch(payload: object, *, now: datetime | None = None) -> dict:
    if not isinstance(payload, dict) or set(payload) - {'schema', 'delivery_id', 'collected_at', 'records'}:
        raise ValueError('invalid source batch fields')
    delivery_id = payload.get('delivery_id')
    if payload.get('schema') != SCHEMA or not isinstance(delivery_id, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', delivery_id):
        raise ValueError('invalid source batch identity')
    collected = _date(payload.get('collected_at'))
    now = now or datetime.now(timezone.utc)
    if collected.timestamp() > now.timestamp() + REPLAY_WINDOW:
        raise ValueError('future collection timestamp')
    records = payload.get('records')
    if not isinstance(records, list) or not 1 <= len(records) <= MAX_RECORDS:
        raise ValueError('invalid source record count')
    allowed = {'url', 'title', 'body', 'body_coverage', 'extraction_scope', 'published_at',
               'raw_content', 'raw_format', 'category'}
    scopes = {'rendered_article', 'public_message', 'series_notes', 'authorized_article'}
    for record in records:
        if not isinstance(record, dict) or set(record) - allowed:
            raise ValueError('unsupported source fields; credentials and tool logs are forbidden')
        url, title, body = record.get('url'), record.get('title'), record.get('body')
        if not isinstance(url, str) or len(url) > 1000:
            raise ValueError('invalid source URL')
        source, _ = _source(url)
        if not isinstance(title, str) or not title.strip() or len(title) > 500:
            raise ValueError('invalid source title')
        if not isinstance(body, str) or not body.strip() or len(body) > MAX_BODY_CHARS:
            raise ValueError('missing or oversized body')
        if record.get('body_coverage') != 'full' or record.get('extraction_scope') not in scopes:
            raise ValueError('complete source body and extraction scope required')
        expected_scopes = ({'public_message'} if source.startswith('telegram:') else
                           {'series_notes'} if source == 'fred' else
                           {'rendered_article', 'authorized_article'})
        if record['extraction_scope'] not in expected_scopes:
            raise ValueError('extraction scope does not match source provider')
        if record.get('published_at'):
            _date(record['published_at'])
        raw = record.get('raw_content', body)
        if not isinstance(raw, str) or not raw.strip() or len(raw) > MAX_RAW_CHARS:
            raise ValueError('invalid original source content')
        if record.get('raw_format', 'text') not in {'text', 'html'}:
            raise ValueError('unsupported original format')
        if record.get('category') is not None and (not isinstance(record['category'], str) or len(record['category']) > 80):
            raise ValueError('invalid source category')
        scan = (title + '\n' + body + '\n' + raw).lower()
        if re.search(r'cf-chl-|cf-mitigated|cloudflare challenge|just a moment|로그인\s*후\s*(이용|열람)|로그인[이을]\s*필요|소셜\s*로그인\s*(후|해야)|(?:sign|log)\s*in to (continue|read)', scan):
            raise ValueError('reauth_required: login/challenge content is not an article')
        if re.search(r'<input[^>]+type\s*=\s*["\']?password|(?:authorization|api[_-]?key|access[_-]?token|refresh[_-]?token|set-cookie|cookie|document\.cookie|cf_clearance|sessionid)\s*["\']?\s*[:=]', scan):
            raise ValueError('credential content is forbidden')
    return payload


def ingest_batch(payload: dict, *, cache_dir=None, state_dir=None) -> dict:
    validate_batch(payload)
    state_root = Path(state_dir or raw_archive.reports_root() / 'hermes-source-receipts')
    key = hashlib.sha256(payload['delivery_id'].encode()).hexdigest()
    state_path = state_root / (key + '.json')
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    fingerprint = hashlib.sha256(canonical.encode()).hexdigest()
    with safe_io.file_write_lock(str(state_path)):
        state = json.loads(state_path.read_text()) if state_path.exists() else {}
        if state and state['payload_sha256'] != fingerprint:
            raise DeliveryConflict('delivery ID reused with different source content')
        if state.get('status') == 'accepted':
            return {**state['result'], 'duplicate': True}
        if not state:
            state = {'payload_sha256': fingerprint, 'delivery_id': payload['delivery_id'],
                     'status': 'pending', 'received_at': datetime.now(timezone.utc).isoformat(), 'archives': {}}
            safe_io.atomic_write_json(str(state_path), state)
        received_at = _date(state['received_at'])
        events = []
        for index, record in enumerate(payload['records']):
            source, archive_source = _source(record['url'])
            archive = state['archives'].get(str(index))
            if not archive:
                raw_format = record.get('raw_format', 'text')
                archive = raw_archive.save_raw_artifact(
                    archive_source, raw_format, received_at, record['title'], record['url'],
                    record.get('raw_content', record['body']), '.html' if raw_format == 'html' else '.txt')
                archive.update({'body_coverage': 'full', 'text_complete': True,
                                'extraction_scope': record['extraction_scope'], 'transport': 'hermes_webhook'})
                archive = raw_archive.save_extracted_text(archive, record['body'])
                state['archives'][str(index)] = archive
                safe_io.atomic_write_json(str(state_path), state)
            event = source_collector._classify_event({
                'source': source, 'title': record['title'], 'url': record['url'],
                'published_at': record.get('published_at', ''), 'body': record['body'],
                'body_raw': record['body'], 'body_excerpt': record['body'][:500],
                'category': record.get('category', ''), 'transport': 'hermes_webhook',
                'source_collected_at': payload['collected_at'], 'body_coverage': 'full',
                'fulltext_status': 'full', 'extraction_scope': record['extraction_scope'],
                **{k: archive[k] for k in ('raw_path', 'text_path', 'manifest_path')},
                'raw_sha256': archive['sha256'], 'raw_source': archive_source,
            })
            events.append(event)
        cache = Path(cache_dir or source_collector.DEFAULT_CACHE_DIR)
        # Archive/transport time and event queue time are separate: a delivery
        # delayed before its first append must still reach the recent wiki queue.
        if not state.get('append_at'):
            state['append_at'] = datetime.now(timezone.utc).isoformat()
            safe_io.atomic_write_json(str(state_path), state)
        append_at = _date(state['append_at'])
        persisted = source_collector.append_events(events, cache_dir=cache, now=append_at)
        source_collector.update_source_health(
            [{**event, 'source': 'hermes_webhook'} for event in events], cache_dir=cache,
            attempted_sources=['hermes_webhook'], run_stats={'hermes_webhook': {
                'fetched': len(events), 'persisted': persisted, 'deduped': len(events) - persisted,
                'fetch_success': True, 'persist_success': True, 'transport': 'signed_source_batch',
            }})
        result = {'ok': True, 'duplicate': False, 'received': len(events),
                  'persisted': persisted, 'wiki_status': 'pending', 'delivery_id': payload['delivery_id']}
        state.update({'status': 'accepted', 'result': result})
        safe_io.atomic_write_json(str(state_path), state)
        return result


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key')
        result[key] = value
    return result


def create_app(*, secret=None, cache_dir=None, state_dir=None) -> Flask:
    app = Flask(__name__)
    app.config['MAX_CONTENT_LENGTH'] = MAX_REQUEST_BYTES

    @app.get('/health')
    def health():
        return jsonify(ok=True, configured=bool(secret or os.getenv('HERMES_SOURCE_WEBHOOK_SECRET')),
                       collection_mode=source_collector.source_collection_mode('hermes_webhook'))

    @app.post('/webhooks/hermes-sources')
    def receive():
        shared_secret = secret or os.getenv('HERMES_SOURCE_WEBHOOK_SECRET', '')
        if not shared_secret:
            return jsonify(error='receiver not configured'), 503
        raw = request.get_data()
        timestamp = request.headers.get('X-Hermes-Timestamp', '')
        delivery = request.headers.get('X-Hermes-Delivery-ID', '')
        if not re.fullmatch(r'\d{1,12}', timestamp) or abs(time.time() - int(timestamp)) > REPLAY_WINDOW:
            return jsonify(error='unauthorized'), 401
        if not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', delivery):
            return jsonify(error='unauthorized'), 401
        expected = signed_headers(raw, shared_secret, timestamp, delivery)['X-Hermes-Signature-256']
        provided = request.headers.get('X-Hermes-Signature-256', '')
        if not hmac.compare_digest(expected.encode(), provided.encode()):
            return jsonify(error='unauthorized'), 401
        if request.mimetype != 'application/json':
            return jsonify(error='JSON content type required'), 415
        try:
            payload = json.loads(raw, object_pairs_hook=_unique_object,
                                 parse_constant=lambda _: (_ for _ in ()).throw(ValueError('invalid JSON constant')))
            validate_batch(payload)
            if payload['delivery_id'] != delivery:
                raise ValueError('delivery identity mismatch')
            return jsonify(ingest_batch(payload, cache_dir=cache_dir, state_dir=state_dir))
        except DeliveryConflict as exc:
            return jsonify(error=str(exc)), 409
        except (ValueError, UnicodeError, RecursionError) as exc:
            return jsonify(error=str(exc)[:160]), 400
        except Exception:
            app.logger.exception('source webhook ingestion failed')
            return jsonify(error='source ingestion failed; retry same delivery'), 500

    return app


def runtime_app(*, env_file=None) -> Flask:
    from dotenv import load_dotenv
    load_dotenv(env_file or Path(__file__).resolve().parent.parent / '.env.hermes-source')
    return create_app()


if __name__ == '__main__':
    runtime_app().run(host='127.0.0.1', port=int(os.getenv('HERMES_SOURCE_WEBHOOK_PORT', '8777')))
