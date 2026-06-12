#!/usr/bin/env python3
"""
portfolio_sync_server.py — 키움 Windows 노트북 → Ubuntu 잔고 동기화 수신 서버

실행:
    python3 portfolio_sync_server.py

방화벽 (Oracle Cloud):
    Ingress rule: TCP port 8765 허용
    sudo iptables -I INPUT -p tcp --dport 8765 -j ACCEPT
"""

import json
import os
import shutil
import logging
from datetime import datetime
from flask import Flask, request, jsonify
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)

SYNC_TOKEN     = os.getenv("SYNC_TOKEN")
SYNC_PORT      = int(os.getenv("SYNC_PORT", "8765"))
PROJECT_DIR    = os.getenv("STOCK_REPORT_PROJECT_DIR", os.path.dirname(os.path.abspath(__file__)))
PORTFOLIO_PATH = os.path.join(PROJECT_DIR, "portfolio_snapshot.json")


def _shadow_to_store(snap: dict):
    """portfolio_snapshot 을 store 로 best-effort 그림자 동기화 (라이브 동기화 비차단)."""
    try:
        import sys
        if PROJECT_DIR not in sys.path:
            sys.path.insert(0, PROJECT_DIR)
        import store
        store.shadow_doc("portfolio_snapshot", snap)
    except Exception as e:
        logger.warning("store 그림자 동기화 실패: %s", e)

# 텔레그램 알림 (선택)
_TELEGRAM_TOKEN   = os.getenv("STOCK_BOT_TOKEN")
_TELEGRAM_CHAT_ID = os.getenv("STOCK_BOT_CHAT_ID", "5771238245")


def _notify(msg: str):
    if not _TELEGRAM_TOKEN:
        return
    try:
        import requests as _req
        _req.post(
            f"https://api.telegram.org/bot{_TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": _TELEGRAM_CHAT_ID, "text": msg},
            timeout=10,
        )
    except Exception:
        pass


@app.route("/sync", methods=["POST"])
def sync():
    # 인증
    auth = request.headers.get("Authorization", "")
    if not SYNC_TOKEN or auth != f"Bearer {SYNC_TOKEN}":
        logger.warning("sync: 인증 실패 from %s", request.remote_addr)
        return jsonify({"error": "unauthorized"}), 401

    data = request.json
    if not data:
        return jsonify({"error": "empty body"}), 400

    try:
        summary = _update_portfolio(data)
        logger.info("포트폴리오 동기화 완료: %s", summary)
        _notify(f"📥 키움 잔고 자동 동기화 완료\n{summary}")
        return jsonify({"ok": True, "synced_at": data.get("synced_at"), "summary": summary})
    except Exception as e:
        logger.exception("portfolio 업데이트 실패")
        return jsonify({"error": str(e)}), 500


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"ok": True, "time": datetime.now().isoformat()})


def _update_portfolio(data: dict) -> str:
    shutil.copy2(PORTFOLIO_PATH, PORTFOLIO_PATH + ".bak")

    with open(PORTFOLIO_PATH, encoding="utf-8") as f:
        snap = json.load(f)

    general_count = fractional_count = 0

    # ── 일반계좌 ────────────────────────────────────────────────────────
    if data.get("overseas_general"):
        existing = {
            h["ticker"]: h
            for h in snap.get("overseas_general", {}).get("holdings_usd", [])
        }
        for h in data["overseas_general"]:
            ticker = h["ticker"]
            if ticker in existing:
                existing[ticker]["shares"]        = h["shares"]
                existing[ticker]["avg_price_usd"] = h["avg_price_usd"]
                # current_price는 yfinance가 다음 조회 시 갱신
            else:
                existing[ticker] = {
                    "ticker":            ticker,
                    "name":              h.get("name", ticker),
                    "shares":            h["shares"],
                    "avg_price_usd":     h["avg_price_usd"],
                    "current_price_usd": h.get("current_price_usd", 0.0),
                    "cost_usd":          round(h["shares"] * h["avg_price_usd"], 4),
                    "value_usd":         0.0,
                    "pnl_usd":           0.0,
                    "return_pct":        0.0,
                }
        snap.setdefault("overseas_general", {})["holdings_usd"] = list(existing.values())
        general_count = len(data["overseas_general"])

    # ── 소수점 계좌 ─────────────────────────────────────────────────────
    if data.get("overseas_fractional"):
        existing = {
            h["ticker"]: h
            for h in snap.get("overseas_fractional", {}).get("holdings", [])
        }
        for h in data["overseas_fractional"]:
            ticker = h["ticker"]
            if ticker in existing:
                existing[ticker]["shares"]        = h["shares"]
                existing[ticker]["avg_price_usd"] = h["avg_price_usd"]
            else:
                existing[ticker] = {
                    "ticker":            ticker,
                    "name":              h.get("name", ticker),
                    "shares":            h["shares"],
                    "avg_price_usd":     h["avg_price_usd"],
                    "current_price_usd": h.get("current_price_usd", 0.0),
                }
        snap.setdefault("overseas_fractional", {})["holdings"] = list(existing.values())
        fractional_count = len(data["overseas_fractional"])

    snap["last_kiwoom_sync"] = datetime.now().isoformat()

    with open(PORTFOLIO_PATH, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, indent=2)

    _shadow_to_store(snap)
    return f"일반 {general_count}종목 · 소수점 {fractional_count}종목"


if __name__ == "__main__":
    if not SYNC_TOKEN:
        raise RuntimeError("SYNC_TOKEN 환경변수가 설정되지 않았습니다. .env 파일을 확인하세요.")
    logger.info("포트폴리오 동기화 서버 시작 (port %d)", SYNC_PORT)
    app.run(host="0.0.0.0", port=SYNC_PORT)
