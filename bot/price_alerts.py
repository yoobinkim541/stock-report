#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
price_alerts.py — 가격 알림 관리 모듈
"""

import os
import sys
import uuid
from datetime import datetime

import yfinance as yf

# bot/ → 프로젝트 루트 (store import + 레거시 price_alerts.json 미러 위치)
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
import store

ALERTS_FILE = os.path.join(_ROOT, "price_alerts.json")  # 레거시 미러 (advisor 편집 대상)
_COLLECTION = "price_alerts"


def load_alerts() -> list:
    # store 권위 (레거시 JSON 자동 마이그레이션)
    return store.load_collection(_COLLECTION, ALERTS_FILE)


def save_alerts(alerts: list):
    # store 권위 + 레거시 파일 미러 (advisor 워크플로 유지)
    store.save_collection(_COLLECTION, alerts, ALERTS_FILE)


def add_alert(ticker: str, price: float, alert_type: str, note: str = "",
              meta: dict | None = None) -> str:
    """
    알림 추가.
    alert_type: "buy" (현재가 <= 목표가 시 발동) | "sell" (현재가 >= 목표가 시 발동)
    meta:       부가 정보 (자동 등록 알림의 진입가·목표·손절·점수 등)
    Returns: alert id
    """
    alerts = load_alerts()
    alert_id = str(uuid.uuid4())[:8]
    entry: dict = {
        "id": alert_id,
        "ticker": ticker.upper(),
        "price": float(price),
        "type": alert_type.lower(),
        "triggered": False,
        "created_at": datetime.now().isoformat(),
    }
    if note:
        entry["note"] = note
    if meta:
        entry["meta"] = meta
    alerts.append(entry)
    save_alerts(alerts)
    return alert_id


def remove_alert(alert_id: str) -> bool:
    """알림 삭제. 성공 시 True."""
    alerts = load_alerts()
    new_alerts = [a for a in alerts if a["id"] != alert_id]
    if len(new_alerts) < len(alerts):
        save_alerts(new_alerts)
        return True
    return False


def check_alerts() -> list:
    """
    미발동 알림 대상으로 yfinance 실시간 가격 조회 후 조건 충족 시 트리거.
    Returns: 이번 호출에서 트리거된 알림 목록.
    """
    alerts = load_alerts()

    # 자동 등록 알림(auto_trade_level)은 30일 후 만료 — 20일 분포 기반 레벨 노화 방지
    # + 만료로 비워줘야 동일 종목의 새 enter 신호가 다시 등록될 수 있음
    now = datetime.now()
    expired_ids = {
        a["id"] for a in alerts
        if not a.get("triggered", False)
        and (a.get("meta") or {}).get("kind") == "auto_trade_level"
        and a.get("created_at")
        and (now - datetime.fromisoformat(a["created_at"])).days >= 30
    }
    if expired_ids:
        alerts = [a for a in alerts if a["id"] not in expired_ids]
        save_alerts(alerts)

    active = [a for a in alerts if not a.get("triggered", False)]
    if not active:
        return []

    # 필요한 종목 일괄 조회
    tickers = list({a["ticker"] for a in active})
    prices: dict[str, float] = {}
    for ticker in tickers:
        try:
            hist = yf.Ticker(ticker).history(period="1d")
            if not hist.empty:
                prices[ticker] = float(hist["Close"].iloc[-1])
        except Exception:
            pass

    triggered = []
    for alert in alerts:
        if alert.get("triggered", False):
            continue
        ticker = alert["ticker"]
        current = prices.get(ticker)
        if current is None:
            continue

        target = alert["price"]
        atype = alert.get("type", "buy")

        hit = (atype == "buy" and current <= target) or (atype == "sell" and current >= target)
        if hit:
            alert["triggered"] = True
            alert["triggered_at"] = datetime.now().isoformat()
            alert["triggered_price"] = round(current, 2)
            triggered.append(alert)

    if triggered:
        save_alerts(alerts)

    return triggered
