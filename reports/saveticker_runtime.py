"""Collect immutable authorized private archives for the personal wiki."""
from __future__ import annotations

import os
import threading
from pathlib import Path

from werkzeug.serving import make_server

from reports.hermes_source_webhook import create_app
from reports.saveticker_hermes_collector import collect
from reports.saveticker_browser import CDPBrowser
from scripts.hermes_source_send import send_batch

DEFAULT_ROOT = Path.home()/'reports/private-sources'


def collect_private(*,root=DEFAULT_ROOT,limit=3,article_urls=None,browser=None):
    root = Path(root).resolve()
    production = Path.home()/'reports'
    if (root==production or root.is_relative_to(production/'raw')
            or root.is_relative_to(production/'source-cache')):
        raise ValueError('private sources cannot use production storage')
    secret = os.getenv('HERMES_SOURCE_WEBHOOK_SECRET')
    if not secret:
        raise ValueError('private receiver secret is missing')
    root.mkdir(parents=True,exist_ok=True,mode=0o700)
    previous = os.environ.get('STOCK_REPORT_REPORTS_DIR')
    os.environ['STOCK_REPORT_REPORTS_DIR'] = str(root)
    server = None
    thread = None
    try:
        app = create_app(secret=secret,cache_dir=root/'cache',state_dir=root/'receipts')
        server = make_server('127.0.0.1',0,app)
        thread = threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        endpoint=f'http://127.0.0.1:{server.server_port}/webhooks/hermes-sources'
        return collect(browser or CDPBrowser(),outbox=root/'outbox',limit=limit,
            article_urls=article_urls,sender=lambda path:send_batch(path,endpoint=endpoint))
    finally:
        if server:
            server.shutdown()
            if thread:
                thread.join(timeout=5)
            server.server_close()
        if previous is None:
            os.environ.pop('STOCK_REPORT_REPORTS_DIR',None)
        else:
            os.environ['STOCK_REPORT_REPORTS_DIR'] = previous
