"""Run SaveTicker collection with the project's dependencies and existing Hermes browser."""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import safe_io
from reports.raw_archive import reports_root
from reports.saveticker_hermes_collector import CollectionError, HermesBrowser, collect
from scripts.hermes_source_send import send_batch


def main(argv=None, *, browser_factory=None, sender=None):
    from dotenv import load_dotenv
    load_dotenv(ROOT / '.env.hermes-source')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--outbox', type=Path, default=reports_root()/'hermes-source-outbox'/'saveticker')
    parser.add_argument('--limit', type=int, choices=range(1,21), default=10)
    parser.add_argument('--url', action='append', help='Collect explicit article URLs without opening other articles')
    parser.add_argument('--check-connection', action='store_true', help='Check configuration without navigation, delivery or writes')
    args = parser.parse_args(argv)
    if browser_factory is None:
        hermes_root = Path(os.getenv('HERMES_SOURCE_AGENT_ROOT', '~/.hermes/hermes-agent')).expanduser()
        if str(hermes_root) not in sys.path:
            sys.path.insert(0, str(hermes_root))
        browser_factory = HermesBrowser
    try:
        browser=browser_factory()
        if args.check_connection:
            print(json.dumps({'status':'browser_configured','authenticated':False,'delivered':0}))
            return 0
        result = collect(browser, outbox=args.outbox, sender=sender or send_batch, limit=args.limit,
                         article_urls=args.url)
    except Exception as exc:
        status = 'browser_connection_required' if isinstance(exc, CollectionError) and str(exc)=='browser_connection_required' else 'browser_unavailable'
        result = {'status':status, 'delivered':0, 'unchanged':0, 'wiki_status':'pending'}
        if args.check_connection:
            print(json.dumps(result))
            return 2
        path = args.outbox/'collector-state.json'
        with safe_io.file_write_lock(str(path)):
            state = json.loads(path.read_text()) if path.exists() else {}
            state.setdefault('seen', {})
            state.update(status=status,last_attempt=datetime.now(timezone.utc).isoformat())
            safe_io.atomic_write_json(str(path),state)
    print(json.dumps(result,ensure_ascii=False))
    return 0 if result['status'] in {'delivered','no_change'} else 2


if __name__=='__main__':
    raise SystemExit(main())
