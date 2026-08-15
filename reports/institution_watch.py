#!/usr/bin/env python3
"""Reusable institution registry and normalized snapshot model for watchlist UI."""
from __future__ import annotations

import argparse
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
_DEFAULT_13F_META = {
    "berkshire": {
        "category": "holding_company",
        "primary_sources": ["13f"],
        "metric_capabilities": ["holdings", "concentration", "source_refs"],
        "refresh_policy": "quarterly",
        "confidence": 0.95,
    },
    "bridgewater": {
        "category": "hedge_fund",
        "primary_sources": ["13f"],
        "metric_capabilities": ["holdings", "concentration", "source_refs"],
        "refresh_policy": "quarterly",
        "confidence": 0.9,
    },
    "scion": {
        "category": "hedge_fund",
        "primary_sources": ["13f"],
        "metric_capabilities": ["holdings", "concentration", "source_refs"],
        "refresh_policy": "quarterly",
        "confidence": 0.9,
    },
    "citadel": {
        "category": "hedge_fund",
        "primary_sources": ["13f"],
        "metric_capabilities": ["holdings", "concentration", "source_refs"],
        "refresh_policy": "quarterly",
        "confidence": 0.9,
    },
    "duquesne": {
        "category": "family_office",
        "primary_sources": ["13f"],
        "metric_capabilities": ["holdings", "concentration", "source_refs"],
        "refresh_policy": "quarterly",
        "confidence": 0.9,
    },
    "pershing_square": {
        "category": "hedge_fund",
        "primary_sources": ["13f"],
        "metric_capabilities": ["holdings", "concentration", "source_refs"],
        "refresh_policy": "quarterly",
        "confidence": 0.9,
    },
    "point72": {
        "category": "hedge_fund",
        "primary_sources": ["13f"],
        "metric_capabilities": ["holdings", "concentration", "source_refs"],
        "refresh_policy": "quarterly",
        "confidence": 0.9,
    },
    "third_point": {
        "category": "hedge_fund",
        "primary_sources": ["13f"],
        "metric_capabilities": ["holdings", "concentration", "source_refs"],
        "refresh_policy": "quarterly",
        "confidence": 0.9,
    },
    "tudor": {
        "category": "hedge_fund",
        "primary_sources": ["13f"],
        "metric_capabilities": ["holdings", "concentration", "source_refs"],
        "refresh_policy": "quarterly",
        "confidence": 0.9,
    },
    "nps": {
        "category": "pension",
        "primary_sources": ["13f"],
        "metric_capabilities": ["holdings", "concentration", "source_refs"],
        "refresh_policy": "quarterly",
        "confidence": 0.85,
    },
}
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
        defaults = _DEFAULT_13F_META.get(key, {})
        registry[key] = {
            "key": key,
            "display_name": meta["name"],
            "source_kind": "13f",
            "category": defaults.get("category") or "hedge_fund",
            "freshness": "fresh",
            "primary_sources": list(defaults.get("primary_sources") or ["13f"]),
            "metric_capabilities": list(defaults.get("metric_capabilities")
                                         or ["holdings", "concentration", "source_refs"]),
            "refresh_policy": defaults.get("refresh_policy") or "quarterly",
            "confidence": defaults.get("confidence") or 0.9,
        }
    for row in _load_seed_rows():
        registry[row["key"]] = {
            "key": row["key"],
            "display_name": row.get("display_name") or row["key"],
            "source_kind": row.get("source_kind") or "seed",
            "category": row.get("category") or "seed",
            "freshness": row.get("freshness") or "proxy",
            "primary_sources": list(row.get("primary_sources") or []),
            "metric_capabilities": list(row.get("metric_capabilities") or []),
            "refresh_policy": row.get("refresh_policy") or "manual",
            "confidence": row.get("confidence") if row.get("confidence") is not None else 0.35,
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
        "category": row.get("category") or meta.get("category") or "seed",
        "freshness": freshness,
        "holdings_count": row.get("holdings_count") or 0,
        "top_holdings": row.get("top_holdings") or [],
        "portfolio_concentration": row.get("portfolio_concentration"),
        "cash_ratio": row.get("cash_ratio"),
        "options_exposure": row.get("options_exposure"),
        "reported_return": row.get("reported_return"),
        "return_proxy": row.get("return_proxy"),
        "primary_sources": list(row.get("primary_sources") or meta.get("primary_sources") or []),
        "metric_capabilities": list(row.get("metric_capabilities") or meta.get("metric_capabilities") or []),
        "refresh_policy": row.get("refresh_policy") or meta.get("refresh_policy") or "manual",
        "confidence": row.get("confidence") if row.get("confidence") is not None else meta.get("confidence", 0.35),
        "availability_flags": {},
        "notes": list(row.get("notes") or []),
    }
    snapshot["availability_flags"] = {
        metric: _availability(snapshot.get(metric), freshness=freshness)
        for metric in _UNAVAILABLE_METRICS
    }
    return snapshot


_RETURN_PROXY_MIN_COVERAGE = 0.20   # 연속보유 비중이 이보다 얇으면 대부분 물갈이라 신뢰 불가


def _compute_return_proxy(current: list[dict] | None, prior: list[dict] | None) -> float | None:
    """연속 보유 종목만의 가중평균 가격변동(분수, 0.0801=+8.01%) — 13F 자체 value_usd/shares 로
    주당가를 역산해 산출(외부 가격 API 불필요). 신규 편입·전량 청산은 제외(매매를 수익으로
    오인하는 함정 방지 — 포트폴리오 TWR 미기록 현금흐름 버그와 같은 계열). 유빈님 아이디어(감사 후속).

    분수 단위로 반환 — portfolio_concentration 등 다른 _fmt_pct 렌더 필드와 동일 관례
    (dashboard/pages/watchlist.py::_fmt_pct 가 ×100 해서 표시. 퍼센트로 반환하면 100배 부풀어 표시됨).

    가중치는 직전 분기 포트폴리오 내 비중, 연속보유 부분집합으로 재정규화한다(포트 전체가
    아니라 "그때 그 종목들만" 얼마나 움직였는지). 연속보유 비중이 _RETURN_PROXY_MIN_COVERAGE
    미만이면(대부분 리밸런싱) 표본이 너무 얇아 None.
    """
    prior_by_cusip = {p["cusip"]: p for p in (prior or []) if p.get("cusip")}
    prior_total = sum(float(p.get("value_usd") or 0) for p in (prior or []))
    if not prior_by_cusip or prior_total <= 0:
        return None
    weighted_sum = 0.0
    weight_total = 0.0
    for c in (current or []):
        p = prior_by_cusip.get(c.get("cusip"))
        if not p:
            continue
        p_shares, c_shares = float(p.get("shares") or 0), float(c.get("shares") or 0)
        p_value, c_value = float(p.get("value_usd") or 0), float(c.get("value_usd") or 0)
        if p_shares <= 0 or c_shares <= 0 or p_value <= 0:
            continue
        prior_per_share = p_value / p_shares
        if prior_per_share <= 0:
            continue
        current_per_share = c_value / c_shares
        w = p_value / prior_total
        weighted_sum += (current_per_share / prior_per_share - 1) * w
        weight_total += w
    if weight_total < _RETURN_PROXY_MIN_COVERAGE:
        return None
    return round(weighted_sum / weight_total, 4)


_SCREEN_DELTA_THRESHOLD = 0.005   # 0.5%p (분수 단위, weight_pct/100 스케일)


def screen_position_changes(institution_keys: list[str]) -> dict:
    """13F 기관들의 직전분기 대비 종목별 비중 변화를 교차 집계 — 신규편입/증가/감소.

    각 기관 current/prior 보유를 CUSIP 매칭해 delta_pct(분수)를 구하고, 같은 티커를
    여러 기관이 어떻게 움직였는지 모아 기관수 내림차순→|평균변화폭| 내림차순 정렬,
    각 버킷 상위 10개. return_proxy 와 같은 raw value_usd/shares 기반(외부가격 불요).
    유빈님 요청(감사 후속, 2026-08-15)."""
    buckets: dict[str, dict[str, dict]] = {"new_buys": {}, "increased": {}, "decreased": {}}

    for key in institution_keys:
        try:
            current = thirteenf.latest_holdings(key)
            prior = thirteenf.latest_holdings(key, skip=1)
        except Exception:
            continue
        if not current or not prior:
            continue
        cur_rows = current.get("holdings") or []
        prior_by_cusip = {p["cusip"]: p for p in (prior.get("holdings") or []) if p.get("cusip")}

        for row in cur_rows:
            cusip = row.get("cusip")
            if not cusip:
                continue
            ticker = row.get("ticker")
            name = row.get("issuer") or ticker or cusip
            now_w = float(row.get("weight_pct") or 0.0) / 100.0
            prior_row = prior_by_cusip.get(cusip)
            prior_w = float(prior_row.get("weight_pct") or 0.0) / 100.0 if prior_row else 0.0
            delta = now_w - prior_w

            if prior_row is None:
                bucket_name = "new_buys"
            elif delta >= _SCREEN_DELTA_THRESHOLD:
                bucket_name = "increased"
            elif delta <= -_SCREEN_DELTA_THRESHOLD:
                bucket_name = "decreased"
            else:
                continue

            entry = buckets[bucket_name].setdefault(
                cusip, {"ticker": ticker, "name": name, "institutions": [], "deltas": []})
            entry["institutions"].append(key)
            entry["deltas"].append(delta)

    out: dict = {}
    for bucket_name, entries in buckets.items():
        rows = []
        for entry in entries.values():
            deltas = entry.pop("deltas")
            rows.append({
                **entry,
                "count": len(entry["institutions"]),
                "avg_delta_pct": round(sum(deltas) / len(deltas), 4),
            })
        rows.sort(key=lambda r: (r["count"], abs(r["avg_delta_pct"])), reverse=True)
        out[bucket_name] = rows[:10]
    return out


def _try_llm_prompt_for_screen(prompt: str) -> str | None:
    """explain_screen 전용 LLM 호출 seam (테스트 monkeypatch 지점)."""
    try:
        from agent_console.agent import _try_llm_prompt
        return _try_llm_prompt(prompt, max_timeout=20)
    except Exception:
        return None


def _screen_fallback_summary(screen: dict, congress: dict) -> str:
    parts = []
    for r in (screen.get("new_buys") or [])[:3]:
        parts.append(f"{r.get('count')}개 기관이 {r.get('name') or r.get('ticker')} 신규편입")
    for r in (screen.get("increased") or [])[:3]:
        parts.append(f"{r.get('count')}개 기관이 {r.get('name') or r.get('ticker')} 비중 확대")
    for r in (congress.get("bought") or [])[:3]:
        parts.append(f"하원의원 {r.get('member_count')}명이 {r.get('ticker')} 매수 공시")
    if not parts:
        return "표시할 만한 공통 움직임이 아직 없습니다."
    return " · ".join(parts)


def explain_screen(screen: dict, congress: dict) -> dict:
    """교차기관 스크리닝 + 정치인 매매를 LLM 이 해설(왜 이런 흐름일 수 있는지).

    LLM 실패/미가용 시 추측 없이 사실 나열로 대체(build_common_moves_analysis 와
    같은 원칙) — 버튼 클릭 시에만 호출되도록 UI 레이어에서 게이팅한다.
    유빈님 요청(감사 후속, 2026-08-15)."""
    fallback = {"summary": _screen_fallback_summary(screen, congress),
               "confidence": 0.3, "mode": "heuristic"}
    has_data = any(screen.get(k) for k in ("new_buys", "increased", "decreased")) or \
        any(congress.get(k) for k in ("bought", "sold"))
    if not has_data:
        return fallback

    prompt = "\n".join([
        "너는 기관투자자·정치인 매매 스크리닝 결과 해설자다.",
        "아래 JSON(여러 13F 기관의 신규편입/비중증가/비중감소 종목, 하원의원 90일 매수·매도",
        "상위 종목)만 보고, 왜 이런 흐름이 나타날 수 있는지 간결하게(2~4문장) 추정해라.",
        "확정적으로 단언하지 말고 '~일 가능성' 식으로 표현해라. 데이터에 없는 사실은 지어내지 마라.",
        "출력은 JSON object만 허용. 키는 summary(string), confidence(number 0.0~1.0).",
        "",
        json.dumps({"screen": screen, "congress": congress}, ensure_ascii=False, default=str),
    ])
    text = _try_llm_prompt_for_screen(prompt)
    if not text:
        return fallback
    try:
        parsed = json.loads(text)
    except Exception:
        match = re.search(r"\{.*\}", text, re.S)
        parsed = json.loads(match.group(0)) if match else None
    if not isinstance(parsed, dict) or not parsed.get("summary"):
        return fallback
    try:
        confidence = min(max(float(parsed.get("confidence")), 0.0), 1.0)
    except Exception:
        confidence = 0.5
    return {"summary": str(parsed["summary"]).strip(), "confidence": round(confidence, 2),
           "mode": "llm"}


def _normalize_13f_snapshot(meta: dict, raw: dict, *, prior: dict | None = None) -> dict:
    holdings = raw.get("holdings") or []
    concentration = None
    if holdings:
        top_weights = [float(h.get("weight_pct") or 0.0) for h in holdings[:5]]
        concentration = round(sum(top_weights) / 100.0, 4)
    return_proxy = _compute_return_proxy(holdings, (prior or {}).get("holdings"))
    notes = [
        f"Latest 13F filing date: {raw.get('filing_date')}" if raw.get("filing_date") else "Latest 13F snapshot.",
        "13F data is delayed and does not disclose cash or complete derivatives exposure.",
    ]
    if return_proxy is not None:
        notes.append(
            "Return proxy = weighted price return of positions held in both the latest and prior "
            "13F snapshot (price inferred from each filing's own value/shares, no external price feed). "
            "Excludes new buys, full exits, cash, shorts, and intra-quarter trading — an approximation, "
            "not an audited return.")
    snapshot = {
        "institution_key": meta["key"],
        "display_name": raw.get("filer_name") or meta["display_name"],
        "source_kind": "13f",
        "category": meta.get("category") or "hedge_fund",
        "freshness": meta.get("freshness") or "fresh",
        "holdings_count": len(holdings),
        "top_holdings": _normalize_top_holdings(holdings),
        "portfolio_concentration": concentration,
        "cash_ratio": None,
        "options_exposure": None,
        "reported_return": None,
        "return_proxy": return_proxy,
        "primary_sources": list(meta.get("primary_sources") or ["13f"]),
        "metric_capabilities": list(meta.get("metric_capabilities")
                                     or ["holdings", "concentration", "source_refs"]),
        "refresh_policy": meta.get("refresh_policy") or "quarterly",
        "confidence": meta.get("confidence") or 0.9,
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
        "return_proxy": "proxy" if return_proxy is not None else "unavailable",
    }
    return snapshot


def list_institutions() -> list[dict]:
    rows = list(INSTITUTION_REGISTRY.values())
    rows.sort(key=lambda row: (row["source_kind"], row.get("category", ""), row["display_name"].lower()))
    return [dict(row) for row in rows]


def source_backed_institution_keys() -> list[str]:
    return [row["key"] for row in list_institutions() if row.get("source_kind") == "13f"]


def latest_snapshot(institution_key: str) -> dict | None:
    meta = INSTITUTION_REGISTRY.get(institution_key)
    if not meta:
        return None
    if meta["source_kind"] == "13f":
        raw = thirteenf.latest_holdings(institution_key)
        if not raw:
            return None
        prior = thirteenf.latest_holdings(institution_key, skip=1)
        return _normalize_13f_snapshot(meta, raw, prior=prior)
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
            "category": snapshot.get("category") or "",
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
            "primary_sources": list(snapshot.get("primary_sources") or []),
            "metric_capabilities": list(snapshot.get("metric_capabilities") or []),
            "refresh_policy": snapshot.get("refresh_policy") or "",
            "confidence": snapshot.get("confidence"),
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
    primary_sources = ", ".join(snapshot.get("primary_sources") or []) or "—"
    metric_caps = ", ".join(snapshot.get("metric_capabilities") or []) or "—"
    body = "\n".join([
        f"Source: {snapshot.get('source_kind')}",
        f"Category: {snapshot.get('category') or 'seed'}",
        f"Freshness: {snapshot.get('freshness')}",
        f"Primary sources: {primary_sources}",
        f"Metric capabilities: {metric_caps}",
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
            f"category:{snapshot.get('category') or 'seed'}",
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
    source_backed = bool(snapshots) and all(bool(_snapshot_source_refs(snapshot)) for snapshot in snapshots)
    source_refs: list[str] = []
    if source_backed:
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
        f"Categories: {', '.join(sorted({snapshot.get('category') or 'seed' for snapshot in snapshots}))}",
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
        "kind": "source_digest" if source_backed else "note",
        "status": "reviewed" if source_backed else "draft",
        "tags": [
            "wiki",
            "market",
            "institution_watch",
            "common_moves",
            "llm_synthesis",
            *(["source_digest"] if source_backed else []),
        ],
        "summary": analysis.get("summary") or f"{len(shared_moves)} shared moves across {len(snapshots)} institutions",
        "body": body,
        "source_refs": source_refs,
        "confidence": min(float(analysis.get("confidence", 0.5)), 0.6 if source_backed else 0.55),
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


def _legacy_history_path(history_path: Path | None = None) -> Path:
    return history_path or (Path.home() / "reports" / "ml-data" / "notable_investors_13f.jsonl")


def _legacy_load_history(history_path: Path | None, filer_key: str) -> list[dict]:
    path = _legacy_history_path(history_path)
    if not path.exists():
        return []
    rows = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                if rec.get("filer") == filer_key:
                    rows.append(rec)
    except Exception as e:
        logger.warning("이력 로드 실패(무시): %s", e)
    return rows


def _legacy_append_history(history_path: Path | None, rec: dict) -> None:
    path = _legacy_history_path(history_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning("이력 기록 실패(무시): %s", e)


def _legacy_diff_holdings(prev: list[dict] | None, cur: list[dict]) -> dict:
    """이전(없으면 빈) vs 현재 보유 — cusip 기준 신규 편입/청산."""
    prev_by_cusip = {h["cusip"]: h for h in (prev or [])}
    cur_by_cusip = {h["cusip"]: h for h in cur}
    new_positions = [h for c, h in cur_by_cusip.items() if c not in prev_by_cusip]
    exited_positions = [h for c, h in prev_by_cusip.items() if c not in cur_by_cusip]
    new_positions.sort(key=lambda h: -h.get("weight_pct", 0))
    exited_positions.sort(key=lambda h: -h.get("weight_pct", 0))
    return {"new": new_positions, "exited": exited_positions}


def _legacy_fmt_holding(h: dict) -> str:
    tk = f" ({h['ticker']})" if h.get("ticker") else ""
    return f"{h['issuer']}{tk} — {h.get('weight_pct', 0):.1f}% · ${h.get('value_usd', 0) / 1e9:.2f}B"


def build_legacy_investor_page(snapshot: dict, diff: dict) -> dict:
    filer_key = snapshot["filer"]
    holdings = snapshot["holdings"]
    top = holdings[:10]
    lines = [
        f"필링일: {snapshot['filing_date']} · 총 {len(holdings)}종목 · "
        f"총액 ${snapshot['total_value_usd'] / 1e9:.1f}B",
        "",
        "상위 10 종목:",
        *[f"- {_legacy_fmt_holding(h)}" for h in top],
    ]
    if diff["new"]:
        lines += ["", "🆕 신규 편입:", *[f"- {_legacy_fmt_holding(h)}" for h in diff["new"]]]
    if diff["exited"]:
        lines += ["", "📤 청산(전량 매도):", *[f"- {_legacy_fmt_holding(h)}" for h in diff["exited"]]]
    lines += [
        "",
        f"출처: SEC EDGAR 13F-HR (accession {snapshot['accession']})",
        "정보·표시용 — 13F 는 분기말 기준 45일 지연 공시라 현재 포지션과 다를 수 있음",
    ]
    top_tickers = [h["ticker"] for h in top if h.get("ticker")]
    tags = ["wiki", "market", "source_digest", "notable_investor", "13f", filer_key,
            *(f"ticker:{t}" for t in top_tickers[:8])]
    return {
        "id": f"notable-investor-{filer_key}",
        "title": f"기관투자자 위키: {snapshot['filer_name']}",
        "surface": "market",
        "kind": "source_digest",
        "status": "reviewed",
        "tags": tags,
        "summary": (f"{snapshot['filing_date']} 13F 기준 {len(holdings)}종목·"
                    f"${snapshot['total_value_usd'] / 1e9:.1f}B · "
                    f"신규편입 {len(diff['new'])}·청산 {len(diff['exited'])}"),
        "body": "\n".join(lines),
        "source_refs": [
            f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={snapshot['cik']}"
            f"&type=13F-HR&dateb=&owner=include&count=10"
        ],
        "staleness_policy": "refresh_after_90d",
        "confidence": 0.9,
    }


def run_legacy_investor(filer_key: str, *, dry_run: bool = False, history_path: Path | None = None) -> dict:
    snapshot = thirteenf.latest_holdings(filer_key)
    if not snapshot:
        return {"ok": False, "filer": filer_key, "reason": "fetch_failed"}

    history = _legacy_load_history(history_path, filer_key)
    if any(h.get("accession") == snapshot["accession"] for h in history):
        return {"ok": True, "filer": filer_key, "status": "unchanged",
                "accession": snapshot["accession"]}

    is_first_snapshot = not history
    prev = None if is_first_snapshot else history[-1]["holdings"]
    diff = {"new": [], "exited": []} if is_first_snapshot else _legacy_diff_holdings(prev, snapshot["holdings"])
    page = build_legacy_investor_page(snapshot, diff)

    if not dry_run:
        from agent_console import wiki
        wiki.upsert_page(page)
        _legacy_append_history(history_path, {
            "date": datetime.now(KST).strftime("%Y-%m-%d %H:%M"),
            "filer": filer_key, "accession": snapshot["accession"],
            "filing_date": snapshot["filing_date"], "holdings": snapshot["holdings"],
        })
        from lib import watchlist
        for h in diff["new"]:
            if not h.get("ticker"):
                continue
            try:
                watchlist.add_ticker(
                    h["ticker"],
                    reason=f"{snapshot['filer_name']} 신규 편입 ({snapshot['filing_date']})",
                    source=f"notable_investor:{filer_key}",
                )
            except Exception as e:
                logger.warning("관심종목 추가 실패(무시) %s: %s", h["ticker"], e)

    return {"ok": True, "filer": filer_key, "status": "updated",
            "accession": snapshot["accession"], "new": diff["new"], "exited": diff["exited"],
            "filer_name": snapshot["filer_name"], "filing_date": snapshot["filing_date"]}


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


def run(institution_keys=None, *, dry_run: bool = False, analysis_keys=None) -> dict:
    """Persist per-institution snapshot digests and a cross-institution pattern digest."""
    keys, single = _normalize_run_keys(institution_keys)
    analysis_keys_norm = None
    if analysis_keys is not None:
        analysis_keys_norm, _ = _normalize_run_keys(analysis_keys)
    snapshots: list[dict] = []
    snapshots_by_key: dict[str, dict] = {}
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
        snapshots_by_key[key] = snapshot

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
        pattern_keys = analysis_keys_norm or [snapshot["institution_key"] for snapshot in snapshots]
        pattern_snapshots = [snapshots_by_key[key] for key in pattern_keys if key in snapshots_by_key]
        if not pattern_snapshots:
            pattern_snapshots = list(snapshots)
            pattern_keys = [snapshot["institution_key"] for snapshot in pattern_snapshots]
        comparison = compare_institutions(pattern_keys, snapshots=snapshots_by_key)
        analysis = build_common_moves_analysis(pattern_snapshots, comparison)
        pattern_page = build_common_moves_digest(pattern_snapshots, comparison, analysis)
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


def _default_run_keys() -> list[str]:
    return [row["key"] for row in list_institutions()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--keys",
        nargs="*",
        default=None,
        help="Override the institution keys to persist (defaults to all registered institutions).",
    )
    parser.add_argument(
        "--analysis-keys",
        nargs="*",
        default=None,
        help="Override the institution keys used for the common-pattern wiki digest.",
    )
    args = parser.parse_args(argv)

    keys = args.keys if args.keys else _default_run_keys()
    analysis_keys = args.analysis_keys if args.analysis_keys else source_backed_institution_keys()
    result = run(keys, dry_run=args.dry_run, analysis_keys=analysis_keys or None)
    logger.info(
        "기관 watch 완료: 선택 %d개 · 갱신 %d · 변동 없음 %d · 실패 %d · 패턴 %d",
        len(result.get("selected_keys") or []),
        len(result.get("updated") or []),
        len(result.get("unchanged") or []),
        len(result.get("failed") or []),
        len([p for p in result.get("pages") or [] if p.get("id") == "institution-watch-common-moves"]),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
