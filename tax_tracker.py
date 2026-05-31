#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tax_tracker.py — 실현손익 기록 / 조회 / 세금 추산
데이터: ~/.local/share/stock-report/tax_records.json
"""
import json
import os
from datetime import datetime

DATA_DIR = os.path.expanduser("~/.local/share/stock-report")
TAX_FILE = os.path.join(DATA_DIR, "tax_records.json")

EXEMPTION_KRW = 2_500_000  # 연간 기본공제 250만원
TAX_RATE = 0.22            # 22% (소득세 20% + 지방세 2.2%)


def _ensure_dir():
    os.makedirs(DATA_DIR, exist_ok=True)


def _load() -> list[dict]:
    try:
        with open(TAX_FILE, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return []
    except Exception:
        return []


def _save(records: list[dict]):
    _ensure_dir()
    with open(TAX_FILE, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)


def add_sell(ticker: str, qty: float, buy_price_usd: float,
             sell_price_usd: float, fx: float) -> dict:
    """매도 기록 추가. fx: USD/KRW 환율 (매도 시점 적용)."""
    ticker = ticker.upper()
    gain_usd = (sell_price_usd - buy_price_usd) * qty
    gain_krw = gain_usd * fx
    record = {
        "date":           datetime.now().strftime("%Y-%m-%d"),
        "ticker":         ticker,
        "qty":            qty,
        "buy_price_usd":  round(buy_price_usd, 4),
        "sell_price_usd": round(sell_price_usd, 4),
        "gain_usd":       round(gain_usd, 4),
        "gain_krw":       round(gain_krw, 0),
        "fx":             round(fx, 2),
    }
    records = _load()
    records.append(record)
    _save(records)
    return record


def get_yearly_summary(year: int | None = None) -> dict:
    """연도별 실현손익 합산."""
    if year is None:
        year = datetime.now().year
    records = _load()
    year_records = [r for r in records if r.get("date", "").startswith(str(year))]
    total_gain_usd = sum(r.get("gain_usd", 0) for r in year_records)
    total_gain_krw = sum(r.get("gain_krw", 0) for r in year_records)
    taxable_krw = max(0.0, total_gain_krw - EXEMPTION_KRW)
    tax_krw = taxable_krw * TAX_RATE
    return {
        "year":           year,
        "records":        year_records,
        "total_gain_usd": round(total_gain_usd, 4),
        "total_gain_krw": round(total_gain_krw, 0),
        "taxable_krw":    round(taxable_krw, 0),
        "tax_krw":        round(tax_krw, 0),
        "count":          len(year_records),
    }


def get_all_records() -> list[dict]:
    """전체 매도 기록 반환."""
    return _load()


def delete_record(index: int) -> dict | None:
    """1-based index로 매도 기록 삭제. 삭제된 레코드 반환, 없으면 None."""
    records = _load()
    if index < 1 or index > len(records):
        return None
    removed = records.pop(index - 1)
    _save(records)
    return removed
