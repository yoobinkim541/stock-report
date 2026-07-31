#!/usr/bin/env python3
"""Reusable institution registry and normalized snapshot model for watchlist UI."""
from __future__ import annotations

import json
import logging
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import thirteenf

logger = logging.getLogger(__name__)
KST = timezone(timedelta(hours=9))
HISTORY_PATH = Path.home() / "reports" / "ml-data" / "notable_investors_13f.jsonl"
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


def _thirteenf_source_refs(snapshot: dict) -> list[str]:
    cik = snapshot.get("cik")
    accession = snapshot.get("accession")
    if not cik or not accession:
        return []
    accession_nodash = str(accession).replace("-", "")
    return [
        (
            f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}"
            f"&type=13F-HR&dateb=&owner=include&count=10"
        ),
        f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession_nodash}/",
    ]


def _snapshot_source_refs(snapshot: dict) -> list[str]:
    if snapshot.get("source_kind") == "13f":
        return _thirteenf_source_refs(snapshot)
    return []


def _holding_identity(holding: dict) -> str:
    for key in ("cusip", "ticker", "issuer"):
        value = str(holding.get(key) or "").strip().upper()
        if value:
            return f"{key}:{value}"
    return json.dumps(holding, sort_keys=True, ensure_ascii=False, default=str)


def diff_holdings(prev: list[dict] | None, cur: list[dict]) -> dict:
    """Compare prior vs current holdings by stable security identity."""
    prev_by_id = {_holding_identity(h): h for h in (prev or [])}
    cur_by_id = {_holding_identity(h): h for h in cur}
    new_positions = [h for identity, h in cur_by_id.items() if identity not in prev_by_id]
    exited_positions = [h for identity, h in prev_by_id.items() if identity not in cur_by_id]
    new_positions.sort(key=lambda h: -float(h.get("weight_pct") or 0.0))
    exited_positions.sort(key=lambda h: -float(h.get("weight_pct") or 0.0))
    return {"new": new_positions, "exited": exited_positions}


def _load_history(institution_key: str) -> list[dict]:
    if not HISTORY_PATH.exists():
        return []
    rows = []
    try:
        with open(HISTORY_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                if rec.get("institution_key") == institution_key or rec.get("filer") == institution_key:
                    rows.append(rec)
    except Exception as e:
        logger.warning("기관 이력 로드 실패(무시): %s", e)
    return rows


def _append_history(rec: dict) -> None:
    try:
        HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(HISTORY_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    except Exception as e:
        logger.warning("기관 이력 기록 실패(무시): %s", e)


def _snapshot_holdings(snapshot: dict) -> list[dict]:
    return list(snapshot.get("top_holdings") or snapshot.get("holdings") or [])


def _has_same_snapshot(history: list[dict], snapshot: dict) -> bool:
    accession = snapshot.get("accession")
    if accession:
        return any(row.get("accession") == accession for row in history)
    current = _snapshot_holdings(snapshot)
    if not history:
        return False
    previous = history[-1].get("top_holdings") or history[-1].get("holdings") or []
    return {_holding_identity(h) for h in previous} == {_holding_identity(h) for h in current}


def _history_record(snapshot: dict) -> dict:
    institution_key = snapshot["institution_key"]
    holdings = _snapshot_holdings(snapshot)
    return {
        "date": datetime.now(KST).strftime("%Y-%m-%d %H:%M"),
        "institution_key": institution_key,
        "filer": institution_key,
        "accession": snapshot.get("accession"),
        "filing_date": snapshot.get("filing_date"),
        "top_holdings": holdings,
        "holdings": holdings,
    }


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
    source_refs = _snapshot_source_refs(snapshot)
    is_source_backed = bool(source_refs)
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
        "kind": "source_digest" if is_source_backed else "note",
        "status": "reviewed" if is_source_backed else "draft",
        "tags": [
            "wiki",
            "market",
            "institution_watch",
            snapshot["institution_key"],
            f"source:{snapshot.get('source_kind')}",
            *([] if not is_source_backed else ["source_digest"]),
        ],
        "summary": (
            f"{snapshot['display_name']} · {snapshot.get('source_kind')} · "
            f"{snapshot.get('holdings_count', 0)} holdings · "
            f"cash {flags.get('cash_ratio', 'unavailable')} · "
            f"options {flags.get('options_exposure', 'unavailable')}"
        ),
        "body": body,
        "source_refs": source_refs,
        "confidence": 0.8 if is_source_backed else 0.55,
    }


def build_common_moves_digest(snapshots: list[dict], comparison: dict, analysis: dict) -> dict:
    names = ", ".join(snapshot.get("display_name", snapshot.get("institution_key", "")) for snapshot in snapshots)
    shared_moves = list(analysis.get("shared_moves") or []) or ["No shared moves supplied"]
    divergences = list(analysis.get("divergences") or []) or ["No divergences supplied"]
    source_refs: list[str] = []
    seen_refs: set[str] = set()
    for snapshot in snapshots:
        page_ref = f"wiki:institution-watch-{snapshot['institution_key']}"
        if page_ref not in seen_refs:
            seen_refs.add(page_ref)
            source_refs.append(page_ref)
        for ref in _snapshot_source_refs(snapshot):
            if ref in seen_refs:
                continue
            seen_refs.add(ref)
            source_refs.append(ref)
    body = "\n".join([
        f"Institutions: {names}",
        f"Compared rows: {len(comparison.get('rows') or [])}",
        f"Provenance refs: {len(source_refs)}",
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
        "tags": ["wiki", "market", "source_digest", "institution_watch", "common_moves", "llm_synthesis"],
        "summary": analysis.get("summary") or f"{len(shared_moves)} shared moves across {len(snapshots)} institutions",
        "body": body,
        "source_refs": source_refs,
        "confidence": min(float(analysis.get("confidence", 0.5)), 0.6),
    }


def _common_moves_fallback(snapshots: list[dict], comparison: dict) -> dict:
    if not snapshots:
        return {
            "summary": "표시할 기관 데이터가 아직 없습니다.",
            "shared_moves": [],
            "divergences": [],
            "confidence": 0.0,
            "mode": "heuristic",
        }
    shared_moves: list[str] = []
    divergences: list[str] = []
    rows = list(comparison.get("rows") or [])
    repeated: dict[str, int] = {}
    for snapshot in snapshots:
        for holding in snapshot.get("top_holdings") or []:
            ticker = str(holding.get("ticker") or "").strip().upper()
            if not ticker:
                continue
            repeated[ticker] = repeated.get(ticker, 0) + 1
    overlap = [ticker for ticker, count in repeated.items() if count >= 2]
    if overlap:
        shared_moves.append(f"여러 기관 상위 보유에서 {', '.join(overlap[:3])} 가 반복됩니다.")
    fresh_rows = [row for row in rows if row.get("freshness") == "fresh"]
    if fresh_rows:
        shared_moves.append(f"최신 공시 기반으로 {len(fresh_rows)}개 기관 스냅샷을 비교 중입니다.")
    unavailable_cash = [
        row for row in rows
        if row.get("cash_ratio_flag") == "unavailable"
    ]
    if unavailable_cash:
        shared_moves.append("현금 비중은 기관별 공시 범위가 달라 직접 비교 가능한 표본이 제한적입니다.")
    option_flags = {row.get("options_exposure_flag") for row in rows}
    if len(option_flags - {None}) > 1:
        divergences.append("옵션 노출 공개 수준이 기관마다 달라 해석 폭이 다릅니다.")
    counts = [int(row.get("holdings_count") or 0) for row in rows]
    if counts and (max(counts) - min(counts) >= 25):
        divergences.append("집중형 포트폴리오와 분산형 포트폴리오가 함께 보여 운용 스타일 차이가 큽니다.")
    if not shared_moves:
        shared_moves.append("아직 공통 패턴을 단정할 만큼 충분한 겹침이 보이지 않습니다.")
    if not divergences:
        divergences.append("눈에 띄는 차이는 다음 공시 업데이트에서 더 선명해질 가능성이 큽니다.")
    confidence = 0.35
    if overlap:
        confidence += 0.2
    if len(rows) >= 3:
        confidence += 0.15
    if fresh_rows:
        confidence += 0.1
    confidence = min(confidence, 0.8)
    return {
        "summary": f"{len(rows)}개 기관 비교 기준으로 공통 패턴과 차이를 함께 요약했습니다.",
        "shared_moves": shared_moves,
        "divergences": divergences,
        "confidence": round(confidence, 2),
        "mode": "heuristic",
    }


def build_common_moves_analysis(snapshots: list[dict], comparison: dict) -> dict:
    fallback = _common_moves_fallback(snapshots, comparison)
    if not snapshots:
        return fallback

    try:
        from agent_console.agent import _try_llm_prompt
    except Exception:
        return fallback

    payload = {
        "snapshots": snapshots,
        "comparison_rows": comparison.get("rows") or [],
    }
    prompt = "\n".join([
        "너는 기관투자자 비교 페이지의 요약기다.",
        "아래 JSON 데이터만 보고, source-backed snapshot 들의 공통 패턴과 차이를 간결하게 요약해라.",
        "반드시 과장 없이 쓰고, 없거나 공개되지 않은 값은 추정하지 마라.",
        "출력은 JSON object만 허용한다. 키는 summary(string), shared_moves(array of strings),",
        "divergences(array of strings), confidence(number 0.0~1.0) 이어야 한다.",
        "shared_moves 와 divergences 는 각각 1~3개 정도가 적당하다.",
        "",
        json.dumps(payload, ensure_ascii=False, default=str),
    ])
    try:
        text = _try_llm_prompt(prompt, max_timeout=20)
    except Exception:
        text = None
    if not text:
        return fallback

    parsed = None
    try:
        parsed = json.loads(text)
    except Exception:
        match = re.search(r"\{.*\}", text, re.S)
        if match:
            try:
                parsed = json.loads(match.group(0))
            except Exception:
                parsed = None
    if not isinstance(parsed, dict):
        return fallback

    summary = str(parsed.get("summary") or fallback["summary"]).strip()
    shared_moves = [str(item).strip() for item in (parsed.get("shared_moves") or []) if str(item).strip()]
    divergences = [str(item).strip() for item in (parsed.get("divergences") or []) if str(item).strip()]
    try:
        confidence = float(parsed.get("confidence"))
    except Exception:
        confidence = float(fallback.get("confidence") or 0.0)
    confidence = min(max(confidence, 0.0), 1.0)
    return {
        "summary": summary,
        "shared_moves": shared_moves or fallback["shared_moves"],
        "divergences": divergences or fallback["divergences"],
        "confidence": round(confidence, 2),
        "mode": "llm",
    }


def _normalize_run_keys(institution_keys) -> tuple[list[str], bool]:
    if institution_keys is None:
        return [row["key"] for row in list_institutions()], False
    if isinstance(institution_keys, str):
        return [institution_keys], True
    return list(institution_keys), False


def _add_new_positions_to_watchlist(snapshot: dict, diff: dict) -> None:
    from lib import watchlist

    for holding in diff.get("new") or []:
        ticker = holding.get("ticker")
        if not ticker:
            continue
        try:
            watchlist.add_ticker(
                ticker,
                reason=f"{snapshot['display_name']} 신규 편입 ({snapshot.get('filing_date') or 'latest snapshot'})",
                source=f"notable_investor:{snapshot['institution_key']}",
            )
        except Exception as e:
            logger.warning("관심종목 추가 실패(무시) %s: %s", ticker, e)


def run(institution_keys=None, *, dry_run: bool = False) -> dict:
    """Persist per-institution snapshot digests and a cross-institution pattern digest."""
    keys, single = _normalize_run_keys(institution_keys)
    snapshots: list[dict] = []
    updated: list[dict] = []
    unchanged: list[dict] = []
    failed: list[dict] = []
    saved_pages: list[dict] = []

    for key in keys:
        snapshot = latest_snapshot(key)
        if not snapshot:
            failed.append({"institution_key": key, "reason": "fetch_failed"})
            continue

        history = _load_history(key)
        if _has_same_snapshot(history, snapshot):
            diff = {"new": [], "exited": []}
            unchanged.append({
                "institution_key": key,
                "status": "unchanged",
                "accession": snapshot.get("accession"),
            })
        else:
            previous = None if not history else (history[-1].get("top_holdings") or history[-1].get("holdings") or [])
            diff = {"new": [], "exited": []} if not history else diff_holdings(previous, _snapshot_holdings(snapshot))
            updated.append({
                "institution_key": key,
                "status": "updated",
                "accession": snapshot.get("accession"),
                "new": diff["new"],
                "exited": diff["exited"],
            })

        snapshot["_diff"] = diff
        snapshots.append(snapshot)

        if unchanged and unchanged[-1]["institution_key"] == key:
            continue

        page = build_snapshot_digest(snapshot, diff)
        if dry_run:
            saved_pages.append(page)
            continue

        from agent_console import wiki
        saved_pages.append(wiki.upsert_page(page))
        if not history or not _has_same_snapshot(history, snapshot):
            _append_history(_history_record(snapshot))
            _add_new_positions_to_watchlist(snapshot, diff)

    analysis = None
    if snapshots and not single:
        comparison = compare_institutions([snapshot["institution_key"] for snapshot in snapshots], snapshots={
            snapshot["institution_key"]: snapshot for snapshot in snapshots
        })
        analysis = build_common_moves_analysis(snapshots, comparison)
        pattern_page = build_common_moves_digest(snapshots, comparison, analysis)
        if dry_run:
            saved_pages.append(pattern_page)
        else:
            from agent_console import wiki
            saved_pages.append(wiki.upsert_page(pattern_page))
            if saved_pages:
                wiki.rebuild_artifacts()

    ok = bool(snapshots) and not (single and failed)
    if single:
        key = keys[0] if keys else None
        if failed:
            return {"ok": False, "filer": key, "institution_key": key, "reason": failed[0]["reason"]}
        row = (updated or unchanged)[0]
        snapshot = snapshots[0]
        return {
            "ok": True,
            "filer": key,
            "institution_key": key,
            "status": row["status"],
            "accession": row.get("accession"),
            "new": row.get("new", []),
            "exited": row.get("exited", []),
            "filer_name": snapshot["display_name"],
            "filing_date": snapshot.get("filing_date"),
            "analysis": analysis or {},
            "pages": saved_pages,
        }
    return {
        "ok": ok,
        "dry_run": dry_run,
        "selected_keys": [snapshot["institution_key"] for snapshot in snapshots],
        "updated": updated,
        "unchanged": unchanged,
        "failed": failed,
        "analysis": analysis or {},
        "pages": saved_pages,
    }
