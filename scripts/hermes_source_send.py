"""Send only an explicit source batch, never a general Hermes tool transcript."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import requests

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from reports.hermes_source_webhook import MAX_REQUEST_BYTES, signed_headers, validate_batch


def send_batch(path: Path | str, *, endpoint: str | None = None, post=None) -> dict:
    endpoint = endpoint or os.getenv('HERMES_SOURCE_WEBHOOK_URL',
                                     'http://127.0.0.1:8777/webhooks/hermes-sources')
    parsed = urlparse(endpoint)
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('invalid webhook endpoint')
    if parsed.scheme != 'https' and not (parsed.scheme == 'http' and parsed.hostname in {'localhost', '127.0.0.1', '::1'}):
        raise ValueError('remote webhook requires HTTPS')
    secret = os.getenv('HERMES_SOURCE_WEBHOOK_SECRET', '')
    if not secret:
        raise ValueError('webhook secret is not configured')
    with Path(path).open('rb') as stream:
        raw = stream.read(MAX_REQUEST_BYTES + 1)
    if len(raw) > MAX_REQUEST_BYTES:
        raise ValueError('source batch exceeds request limit')
    payload = json.loads(raw)
    validate_batch(payload)
    headers = signed_headers(raw, secret, str(int(time.time())), payload['delivery_id'])
    response = (post or requests.post)(endpoint, data=raw,
        headers={**headers, 'Content-Type': 'application/json'}, timeout=20, allow_redirects=False)
    if 300 <= response.status_code < 400:
        raise ValueError('webhook redirect rejected')
    response.raise_for_status()
    receipt = response.json()
    if not isinstance(receipt, dict) or receipt.get('ok') is not True:
        raise ValueError('webhook did not confirm source ingestion')
    return receipt


def main():
    from dotenv import load_dotenv
    load_dotenv(ROOT / '.env.hermes-source')
    parser = argparse.ArgumentParser()
    parser.add_argument('source_batch', type=Path)
    args = parser.parse_args()
    try:
        receipt = send_batch(args.source_batch)
    except Exception as exc:
        print('Source delivery failed: ' + type(exc).__name__, file=sys.stderr)
        return 1
    print(json.dumps(receipt, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
