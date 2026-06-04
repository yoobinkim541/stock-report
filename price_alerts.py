#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
price_alerts.py — 가격 알림 관리 모듈
"""

import json
import os
import uuid
from datetime import datetime

import yfinance as yf

ALERTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "price_alerts.json")


def load_alerts() -> list:
    if not os.path.exists(ALERTS_FILE):
        return []
    try:
        with open(ALERTS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def save_alerts(alerts: list):
    with open(ALERTS_FILE, "w", encoding="utf-8") as f:
        json.dump(alerts, f, ensure_ascii=False, indent=2)


def add_alert(ticker: str, price: float, alert_type: str, note: str = "") -> str:
    """
    알림 추가.
    alert_type: "buy" (현재가 <= 목표가 시 발동) | "sell" (현재가 >= 목표가 시 발동)
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
