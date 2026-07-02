#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
price_alerts.py — 가격 알림 관리 모듈
"""

import os
import sys
import threading
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

# 교차 스레드 lost update 방지 — 주기 check_alerts(백그라운드 스레드)와 add/remove(명령·entry 스레드)가
# 같은 컬렉션을 load→수정→save 할 때 겹치면 방금 등록/삭제한 알림이 소실된다(감사 확정).
_ALERTS_LOCK = threading.RLock()


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
    with _ALERTS_LOCK:
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
    with _ALERTS_LOCK:
        alerts = load_alerts()
        new_alerts = [a for a in alerts if a["id"] != alert_id]
        if len(new_alerts) < len(alerts):
            save_alerts(new_alerts)
            return True
    return False


_ALERT_STALE_S = int(os.getenv("REALTIME_ALERT_STALE_S", "30"))


def _spot_price(ticker: str):
    """현재가 — 실시간 캐시(활성·신선) 우선, 실패 시 yfinance. 예외 무발(폴백 보장)."""
    try:
        from providers import realtime_quotes
        if realtime_quotes.enabled():
            rt = realtime_quotes.get_price(ticker.split(".")[0], max_age_s=_ALERT_STALE_S)
            if rt:
                return rt
    except Exception:
        pass
    try:
        hist = yf.Ticker(ticker).history(period="1d")
        if not hist.empty:
            return float(hist["Close"].iloc[-1])
    except Exception:
        pass
    return None


def check_alerts() -> list:
    """
    미발동 알림 대상으로 가격 조회(실시간 캐시→yfinance 폴백) 후 조건 충족 시 트리거.
    Returns: 이번 호출에서 트리거된 알림 목록.
    """
    # 만료 퍼지 — 락 안(load→수정→save). auto_trade_level 은 30일 후 만료(레벨 노화 방지 +
    # 비워줘야 동일 종목의 새 enter 신호가 다시 등록됨).
    with _ALERTS_LOCK:
        alerts = load_alerts()
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

    # 필요한 종목 일괄 조회 (실시간 캐시 우선·yfinance 폴백) — 느리므로 락 밖에서 수행
    tickers = list({a["ticker"] for a in active})
    prices: dict[str, float] = {}
    for ticker in tickers:
        p = _spot_price(ticker)
        if p is not None:
            prices[ticker] = p

    # 트리거 판정·저장 — 락 안에서 최신 상태 재로드 후 반영(조회 중 들어온 add/remove 보존)
    triggered = []
    with _ALERTS_LOCK:
        alerts = load_alerts()
        changed = False
        for alert in alerts:
            if alert.get("triggered", False):
                continue
            current = prices.get(alert["ticker"])
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
                changed = True
        if changed:
            save_alerts(alerts)

    return triggered
