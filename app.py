from __future__ import annotations

import logging

from flask import Flask, Response, jsonify

app = Flask(__name__)
logger = logging.getLogger(__name__)


@app.get("/")
def index():
    return jsonify({"ok": True, "service": "stock-report-sync"})


@app.get("/health")
def health():
    return jsonify({"ok": True})


@app.get("/favicon.ico")
@app.get("/favicon.png")
def favicon():
    return Response(status=204)


@app.post("/sync")
def sync():
    try:
        from portfolio_sync_server import sync as sync_impl
        return sync_impl()
    except Exception as exc:
        logger.exception("sync route failed")
        return jsonify({"error": "internal error", "detail": str(exc)}), 500
