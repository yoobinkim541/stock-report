#!/usr/bin/env python3
"""Reusable institution registry and normalized snapshot model for watchlist UI."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import thirteenf

_SEED_PATH = Path(__file__).resolve().parent.parent / "data" / "institution_watch_seed.json"
_UNAVAILABLE_METRICS = (
    "portfolio_concentration",
    "cash_ratio",
    "options_exposure",
    "reported_return",
    "return_proxy",
)


def _load_seed_rows() -> list[dict]:
    try:
        rows = json.loads(_SEED_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []
    return [row for row in rows if isinstance(row, dict) and row.get("key")]


def _build_registry() -> dict[str, dict]:
    registry: dict[str, dict] = {}
    for key, meta in thirteenf.FILERS.items():
        registry[key] = {
            "key": key,
            "display_name": meta["name"],
            "source_kind": "13f",
            "freshness": "fresh",
        }
    for row in _load_seed_rows():
        registry[row["key"]] = {
            "key": row["key"],
            "display_name": row.get("display_name") or row["key"],
            "source_kind": row.get("source_kind") or "seed",
            "freshness": row.get("freshness") or "proxy",
        }
    return registry


INSTITUTION_REGISTRY = _build_registry()


def _availability(value, *, freshness: str = "fresh") -> str:
    if value is None:
        return "unavailable"
    if freshness == "proxy":
        return "proxy"
    return "available"


def _normalize_top_holdings(rows: list[dict]) -> list[dict]:
    top = []
    for row in rows[:10]:
        top.append({
            "issuer": row.get("issuer"),
            "ticker": row.get("ticker"),
            "cusip": row.get("cusip"),
            "weight_pct": row.get("weight_pct"),
            "value_usd": row.get("value_usd"),
            "shares": row.get("shares"),
        })
    return top


def _normalize_seed_snapshot(meta: dict, row: dict) -> dict:
    freshness = row.get("freshness") or meta.get("freshness") or "proxy"
    snapshot = {
        "institution_key": meta["key"],
        "display_name": row.get("display_name") or meta["display_name"],
        "source_kind": "seed",
        "freshness": freshness,
        "holdings_count": row.get("holdings_count") or 0,
        "top_holdings": row.get("top_holdings") or [],
        "portfolio_concentration": row.get("portfolio_concentration"),
        "cash_ratio": row.get("cash_ratio"),
        "options_exposure": row.get("options_exposure"),
        "reported_return": row.get("reported_return"),
        "return_proxy": row.get("return_proxy"),
        "availability_flags": {},
        "notes": list(row.get("notes") or []),
    }
    snapshot["availability_flags"] = {
        metric: _availability(snapshot.get(metric), freshness=freshness)
        for metric in _UNAVAILABLE_METRICS
    }
    return snapshot


def _normalize_13f_snapshot(meta: dict, raw: dict) -> dict:
    holdings = raw.get("holdings") or []
    concentration = None
    if holdings:
        top_weights = [float(h.get("weight_pct") or 0.0) for h in holdings[:5]]
        concentration = round(sum(top_weights) / 100.0, 4)
    notes = [
        f"Latest 13F filing date: {raw.get('filing_date')}" if raw.get("filing_date") else "Latest 13F snapshot.",
        "13F data is delayed and does not disclose cash or complete derivatives exposure.",
    ]
    snapshot = {
        "institution_key": meta["key"],
        "display_name": raw.get("filer_name") or meta["display_name"],
        "source_kind": "13f",
        "freshness": meta.get("freshness") or "fresh",
        "holdings_count": len(holdings),
        "top_holdings": _normalize_top_holdings(holdings),
        "portfolio_concentration": concentration,
        "cash_ratio": None,
        "options_exposure": None,
        "reported_return": None,
        "return_proxy": None,
        "availability_flags": {},
        "notes": notes,
        "filing_date": raw.get("filing_date"),
        "accession": raw.get("accession"),
        "cik": raw.get("cik"),
        "total_value_usd": raw.get("total_value_usd"),
    }
    snapshot["availability_flags"] = {
        "portfolio_concentration": _availability(snapshot.get("portfolio_concentration")),
        "cash_ratio": "unavailable",
        "options_exposure": "unavailable",
        "reported_return": "unavailable",
        "return_proxy": "unavailable",
    }
    return snapshot


def list_institutions() -> list[dict]:
    rows = list(INSTITUTION_REGISTRY.values())
    rows.sort(key=lambda row: (row["source_kind"], row["display_name"].lower()))
    return [dict(row) for row in rows]


def latest_snapshot(institution_key: str) -> dict | None:
    meta = INSTITUTION_REGISTRY.get(institution_key)
    if not meta:
        return None
    if meta["source_kind"] == "13f":
        raw = thirteenf.latest_holdings(institution_key)
        if not raw:
            return None
        return _normalize_13f_snapshot(meta, raw)
    for row in _load_seed_rows():
        if row.get("key") == institution_key:
            return _normalize_seed_snapshot(meta, row)
    return None


def compare_institutions(keys: list[str], *, snapshots: dict[str, dict] | None = None) -> dict:
    snapshots = snapshots or {}
    rows = []
    for key in keys:
        snapshot = snapshots.get(key)
        if snapshot is None:
            snapshot = latest_snapshot(key)
        if snapshot is None:
            continue
        flags = dict(snapshot.get("availability_flags") or {})
        rows.append({
            "institution_key": snapshot["institution_key"],
            "display_name": snapshot["display_name"],
            "source_kind": snapshot["source_kind"],
            "freshness": snapshot["freshness"],
            "holdings_count": snapshot["holdings_count"],
            "portfolio_concentration": snapshot.get("portfolio_concentration"),
            "portfolio_concentration_flag": flags.get("portfolio_concentration", "unavailable"),
            "cash_ratio": snapshot.get("cash_ratio"),
            "cash_ratio_flag": flags.get("cash_ratio", "unavailable"),
            "options_exposure": snapshot.get("options_exposure"),
            "options_exposure_flag": flags.get("options_exposure", "unavailable"),
            "reported_return": snapshot.get("reported_return"),
            "reported_return_flag": flags.get("reported_return", "unavailable"),
            "return_proxy": snapshot.get("return_proxy"),
            "return_proxy_flag": flags.get("return_proxy", "unavailable"),
        })
    return {
        "selected_keys": [row["institution_key"] for row in rows],
        "rows": rows,
    }


def _fmt_pct(value) -> str:
    if value is None:
        return "N/A"
    return f"{float(value) * 100:.1f}%"


def build_snapshot_digest(snapshot: dict, diff: dict) -> dict:
    top_lines = []
    for holding in snapshot.get("top_holdings") or []:
        label = holding.get("ticker") or holding.get("issuer") or "Unknown"
        weight = holding.get("weight_pct")
        if weight is None:
            top_lines.append(f"- {label}")
        else:
            top_lines.append(f"- {label}: {float(weight):.2f}%")
    if not top_lines:
        top_lines = ["- No top holdings available"]
    new_lines = [
        f"- {(row.get('ticker') or row.get('issuer') or 'Unknown')}"
        for row in (diff.get("new") or [])
    ] or ["- None"]
    exited_lines = [
        f"- {(row.get('ticker') or row.get('issuer') or 'Unknown')}"
        for row in (diff.get("exited") or [])
    ] or ["- None"]
    flags = snapshot.get("availability_flags") or {}
    note_lines = [f"- {note}" for note in (snapshot.get("notes") or [])] or ["- None"]
    body = "\n".join([
        f"Source: {snapshot.get('source_kind')}",
        f"Freshness: {snapshot.get('freshness')}",
        f"Holdings count: {snapshot.get('holdings_count', 0)}",
        f"Portfolio concentration: {_fmt_pct(snapshot.get('portfolio_concentration'))}",
        f"Cash ratio: {snapshot.get('cash_ratio')} ({flags.get('cash_ratio', 'unavailable')})",
        f"Options exposure: {snapshot.get('options_exposure')} ({flags.get('options_exposure', 'unavailable')})",
        "",
        "Top holdings:",
        *top_lines,
        "",
        "New positions:",
        *new_lines,
        "",
        "Exited positions:",
        *exited_lines,
        "",
        "Notes:",
        *note_lines,
    ])
    return {
        "id": f"institution-watch-{snapshot['institution_key']}",
        "title": f"기관투자자 스냅샷: {snapshot['display_name']}",
        "surface": "market",
        "kind": "source_digest",
        "status": "reviewed",
        "tags": [
            "wiki",
            "market",
            "source_digest",
            "institution_watch",
            snapshot["institution_key"],
            f"source:{snapshot.get('source_kind')}",
        ],
        "summary": (
            f"{snapshot['display_name']} · {snapshot.get('source_kind')} · "
            f"{snapshot.get('holdings_count', 0)} holdings · "
            f"cash {flags.get('cash_ratio', 'unavailable')} · "
            f"options {flags.get('options_exposure', 'unavailable')}"
        ),
        "body": body,
        "source_refs": [],
        "confidence": 0.8 if snapshot.get("source_kind") == "13f" else 0.6,
    }


def build_common_moves_digest(snapshots: list[dict], comparison: dict, analysis: dict) -> dict:
    names = ", ".join(snapshot.get("display_name", snapshot.get("institution_key", "")) for snapshot in snapshots)
    shared_moves = list(analysis.get("shared_moves") or []) or ["No shared moves supplied"]
    divergences = list(analysis.get("divergences") or []) or ["No divergences supplied"]
    body = "\n".join([
        f"Institutions: {names}",
        f"Compared rows: {len(comparison.get('rows') or [])}",
        "",
        "Shared moves:",
        *[f"- {item}" for item in shared_moves],
        "",
        "Divergences:",
        *[f"- {item}" for item in divergences],
    ])
    return {
        "id": "institution-watch-common-moves",
        "title": "기관투자자 공통 패턴",
        "surface": "market",
        "kind": "source_digest",
        "status": "reviewed",
        "tags": ["wiki", "market", "source_digest", "institution_watch", "common_moves"],
        "summary": analysis.get("summary") or f"{len(shared_moves)} shared moves across {len(snapshots)} institutions",
        "body": body,
        "source_refs": [],
        "confidence": analysis.get("confidence", 0.5),
    }
