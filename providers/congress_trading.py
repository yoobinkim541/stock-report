#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""providers/congress_trading.py — 미 하원의원 주식거래 공시(STOCK Act PTR) 조회.

House Clerk(disclosures-clerk.house.gov)의 PTR PDF 를 매일 파싱해 무료 공개하는
커뮤니티 저장소(TattooedHead/house-stock-watcher-data, GitHub — 2026-08-15 기준 당일
갱신 확인)에서 읽는다. 금액은 구간(bracket)으로만 공시되며 정확한 액수가 아니다 —
source_url 로 원본 PDF 검증 가능.

상원은 제외(감사 후속 결정, 2026-08-15): 잘 알려진 무료 데이터셋
(timothycarambat/senate-stock-watcher-data)이 2021-03 이후 갱신이 끊겼고, 현재
활발히 유지되는 대안은 유료(Apify) 뿐이라 하원만 먼저 연결.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_CACHE_DIR = Path(os.path.expanduser("~/reports/ml-cache/congress_trading"))
_CACHE_TTL_H = 12   # 소스가 매일 갱신되는 정도라 반나절 캐시로 충분
_SOURCE_URL = ("https://raw.githubusercontent.com/TattooedHead/"
              "house-stock-watcher-data/main/data/all_transactions.json")


def _get(url: str) -> bytes:
    from lib.http_utils import http_get
    return http_get(url, timeout=30)


def _load_all() -> list[dict]:
    from lib.file_cache import is_fresh
    path = _CACHE_DIR / "house_all_transactions.json"
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        if not is_fresh(path, _CACHE_TTL_H):
            path.write_bytes(_get(_SOURCE_URL))
        rows = json.loads(path.read_text(encoding="utf-8"))
        return rows if isinstance(rows, list) else []
    except Exception as e:
        logger.warning("하원 거래공시 캐시 조회 실패: %s", e)
        return []


def list_members() -> list[str]:
    """공시 데이터에 등장하는 하원의원 이름(중복 제거, 알파벳순)."""
    rows = _load_all()
    return sorted({r.get("representative") for r in rows if r.get("representative")})


def member_transactions(name: str | None, *, limit: int = 20) -> list[dict] | None:
    """이름(부분일치·대소문자 무시) 최근 거래 최대 limit 건 — 최신순. 매치 없으면 None.

    반환 각 항목: {representative, date, disclosure_date, ticker, asset, asset_type,
    type(Purchase/Sale/Exchange), amount(구간 문자열), amount_mid(구간 중간값 추정),
    owner, source_url(원본 PDF)}."""
    if not name or not name.strip():
        return None
    needle = name.strip().lower()
    rows = _load_all()
    matched = [r for r in rows if needle in (r.get("representative") or "").lower()]
    if not matched:
        return None

    from datetime import datetime

    def _sort_key(r):
        try:
            return datetime.strptime(r.get("transaction_date") or "", "%m/%d/%Y")
        except ValueError:
            return datetime.min

    matched.sort(key=_sort_key, reverse=True)
    return [{
        "representative": r.get("representative"),
        "date": r.get("transaction_date"),
        "disclosure_date": r.get("disclosure_date"),
        "ticker": r.get("ticker"),
        "asset": r.get("asset_description"),
        "asset_type": r.get("asset_type"),
        "type": r.get("type"),
        "amount": r.get("amount"),
        "amount_mid": r.get("amount_mid"),
        "owner": r.get("owner"),
        "source_url": r.get("source_url"),
    } for r in matched[:limit]]


def top_traded(days: int = 90, limit: int = 10) -> dict:
    """최근 days 일 하원 공시 거래를 티커별로 묶어 매수/매도 상위 — 거래한 의원 수
    내림차순(같으면 추정금액 합 내림차순). Exchange·티커 미상('--' 등)은 제외.
    유빈님 요청(감사 후속, 2026-08-15)."""
    from datetime import datetime, timedelta

    cutoff = datetime.now() - timedelta(days=days)
    rows = _load_all()

    def _parse(d):
        try:
            return datetime.strptime(d or "", "%m/%d/%Y")
        except ValueError:
            return None

    buckets = {"Purchase": {}, "Sale": {}}
    for r in rows:
        ticker = (r.get("ticker") or "").strip()
        if not ticker or ticker == "--":
            continue
        tx_type = r.get("type")
        if tx_type not in buckets:
            continue
        dt = _parse(r.get("transaction_date"))
        if dt is None or dt < cutoff:
            continue
        entry = buckets[tx_type].setdefault(
            ticker, {"ticker": ticker, "members": set(), "total_amount_mid": 0.0})
        entry["members"].add(r.get("representative") or "?")
        entry["total_amount_mid"] += float(r.get("amount_mid") or 0)

    def _finalize(bucket: dict) -> list[dict]:
        rows_out = [{
            "ticker": e["ticker"],
            "member_count": len(e["members"]),
            "members": sorted(e["members"]),
            "total_amount_mid": e["total_amount_mid"],
        } for e in bucket.values()]
        rows_out.sort(key=lambda r: (r["member_count"], r["total_amount_mid"]), reverse=True)
        return rows_out[:limit]

    return {"bought": _finalize(buckets["Purchase"]), "sold": _finalize(buckets["Sale"])}
