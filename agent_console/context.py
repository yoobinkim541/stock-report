from __future__ import annotations

import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from . import shared_memory, storage


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def reports_dir() -> Path:
    return Path(os.getenv("AGENT_CONSOLE_REPORTS_DIR", str(Path.home() / "reports")))


def source_cache_dir() -> Path:
    return Path(os.getenv("AGENT_CONSOLE_SOURCE_CACHE_DIR", str(reports_dir() / "source-cache")))


def ml_data_dir() -> Path:
    return Path(os.getenv("AGENT_CONSOLE_ML_DATA_DIR", str(reports_dir() / "ml-data")))


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _jsonl_tail(path: Path, limit: int = 100) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return []
    for line in lines[-limit:]:
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return rows


def recent_source_events(hours: int = 72, limit: int = 60) -> list[dict]:
    try:
        from reports.source_collector import load_recent_events

        events = load_recent_events(cache_dir=source_cache_dir(), hours=hours)
    except Exception:
        events = []
        for path in sorted(source_cache_dir().glob("events-*.jsonl"))[-5:]:
            events.extend(_jsonl_tail(path, limit=200))
    events = sorted(events, key=lambda row: row.get("collected_at") or row.get("published_at") or "", reverse=True)
    return events[:limit]


def latest_reports(limit: int = 10) -> list[dict]:
    root = reports_dir()
    patterns = [
        "investment-summary-*.json",
        "investment-report-*.json",
        "investment-summary-*.txt",
        "investment-report-*.txt",
        "market-report-*.json",
        "market-report-*.txt",
    ]
    files: list[Path] = []
    for pattern in patterns:
        files.extend(root.glob(pattern))
    files = sorted(set(files), key=lambda path: path.stat().st_mtime if path.exists() else 0, reverse=True)
    out = []
    for path in files[:limit]:
        item = {
            "path": str(path),
            "name": path.name,
            "mtime": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(timespec="seconds"),
            "kind": "json" if path.suffix == ".json" else "text",
        }
        if path.suffix == ".json":
            data = _read_json(path) or {}
            item["title"] = data.get("title") or data.get("headline") or path.stem
            item["summary"] = data.get("summary") or data.get("commentary") or data.get("phase") or ""
            item["keys"] = sorted(list(data.keys()))[:24] if isinstance(data, dict) else []
        else:
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                text = ""
            item["title"] = text.splitlines()[0][:160] if text.strip() else path.stem
            item["summary"] = text[:800]
        out.append(item)
    return out


def ml_activity(limit: int = 80) -> list[dict]:
    paths = []
    for pattern in ("*decisions*.jsonl", "*outcomes*.jsonl", "*learning*.jsonl", "news_llm_labels.jsonl"):
        paths.extend(ml_data_dir().glob(pattern))
    rows = []
    for path in sorted(set(paths), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)[:24]:
        for row in _jsonl_tail(path, limit=40):
            row["_file"] = path.name
            row["_mtime"] = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")
            rows.append(row)
    rows.sort(key=lambda r: str(r.get("ts") or r.get("created_at") or r.get("decided_at") or r.get("_mtime")), reverse=True)
    return rows[:limit]


def paper_state() -> dict:
    live = os.getenv("AGENT_CONSOLE_LIVE_PAPER", "0").lower() in {"1", "true", "yes", "on"}
    if not live:
        return _offline_paper_state()

    out = {"kr": None, "us": None, "combined": None, "errors": []}
    try:
        from dashboard import views

        out["kr"] = views.paper_summary("kr_mock")
        out["us"] = views.paper_summary("us_mock")
        combined = getattr(views, "combined_paper_summary", None)
        if combined:
            out["combined"] = combined()
        else:
            out["combined"] = _fallback_combined_paper(out["kr"], out["us"])
    except Exception as exc:
        out["errors"].append(str(exc))
    return out


def _offline_paper_state() -> dict:
    out = {"kr": None, "us": None, "combined": None, "errors": []}
    try:
        out["kr"] = _offline_paper_summary("kr_mock")
    except Exception as exc:
        out["errors"].append(f"kr_mock: {exc}")
    try:
        out["us"] = _offline_paper_summary("us_mock")
    except Exception as exc:
        out["errors"].append(f"us_mock: {exc}")
    out["combined"] = _fallback_combined_paper(out["kr"], out["us"])
    return out


def _offline_paper_summary(surface: str) -> dict:
    spec = {
        "kr_mock": ("kr_mock_history", "₩", "KOSPI"),
        "us_mock": ("us_mock_history", "$", "QQQ"),
    }.get(surface, ("kr_mock_history", "₩", "KOSPI"))
    hist_name, currency, bench_name = spec
    out: dict = {
        "surface": surface,
        "currency": currency,
        "bench_name": bench_name,
        "balance_ok": False,
        "nav": None,
        "cash": None,
        "positions": [],
        "nav_series": [],
        "inception_date": None,
        "cum_ret": None,
        "day_ret": None,
        "strat_mdd": None,
        "bench_ret": None,
        "bench_mdd": None,
        "cost": None,
        "scorecard": {},
        "decisions": [],
    }
    try:
        import store

        hist = store.all(hist_name)
    except Exception:
        hist = []
    snaps = [r for r in hist if r.get("kind") == "snapshot" and r.get("nav") is not None]
    if snaps:
        out["nav_series"] = [{"date": str(r.get("date", ""))[:10], "nav": float(r["nav"])} for r in snaps]
        out["nav"] = float(snaps[-1]["nav"])
        out["cash"] = snaps[-1].get("cash")
        out["inception_date"] = str(snaps[0].get("date", ""))[:10]
        first_nav = float(snaps[0]["nav"])
        if first_nav:
            out["cum_ret"] = (float(snaps[-1]["nav"]) / first_nav - 1.0) * 100.0
        if len(snaps) >= 2:
            prev_nav = float(snaps[-2]["nav"])
            out["day_ret"] = (float(snaps[-1]["nav"]) / prev_nav - 1.0) * 100.0 if prev_nav else None
        mdd = _max_drawdown([float(row["nav"]) for row in snaps])
        out["strat_mdd"] = mdd * 100.0 if mdd is not None else None

    decisions = [
        r for r in hist
        if r.get("kind") in {"decision", "trade_decision", "rebalance", "order"}
        or r.get("side") is not None
        or r.get("ticker") is not None
        or r.get("code") is not None
    ]
    decisions.sort(key=lambda r: str(r.get("date") or r.get("ts") or r.get("created_at") or ""), reverse=True)
    out["decisions"] = decisions[:20]
    return out


def _max_drawdown(values: list[float]) -> float | None:
    peak = None
    worst = 0.0
    for raw in values:
        value = float(raw)
        if value <= 0:
            continue
        if peak is None or value > peak:
            peak = value
        if peak:
            worst = min(worst, value / peak - 1.0)
    return abs(worst) if peak else None


def portfolio_state() -> dict:
    out = {"holdings": [], "summary": {}, "risk": {}, "targets": {}, "errors": []}
    try:
        from dashboard import data, views

        holdings = data.load_holdings()
        out["holdings"] = sorted(holdings or [], key=lambda row: float(row.get("weight") or 0), reverse=True)
        out["summary"] = data.portfolio_summary()
        try:
            out["targets"] = views.target_weights_map()
        except Exception as exc:
            out["errors"].append(f"targets: {exc}")
    except Exception as exc:
        out["errors"].append(str(exc))
    return out


def _fallback_combined_paper(kr: dict | None, us: dict | None) -> dict:
    rows = [row for row in (kr, us) if isinstance(row, dict)]
    return {
        "surfaces": [row.get("surface") for row in rows],
        "nav": [
            {
                "surface": row.get("surface"),
                "currency": row.get("currency"),
                "nav": row.get("nav"),
                "cum_ret": row.get("cum_ret"),
                "strat_mdd": row.get("strat_mdd"),
            }
            for row in rows
        ],
    }


def model_state() -> dict:
    cache = reports_dir() / "ml-cache"
    names = [
        "ranker_model.pkl",
        "kr_ranker_model.pkl",
        "us_policy_backtest.json",
        "kr_policy_backtest.json",
        "structural_leverage_shadow.json",
        "advice_blend_shadow.json",
    ]
    items = []
    for name in names:
        path = cache / name
        if not path.exists():
            continue
        item = {
            "name": name,
            "path": str(path),
            "mtime": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(timespec="seconds"),
            "size": path.stat().st_size,
        }
        if path.suffix == ".json":
            data = _read_json(path)
            if isinstance(data, dict):
                item["summary"] = {
                    key: data.get(key)
                    for key in ("verdict", "status", "mean_ic", "icir", "adopted", "reason")
                    if key in data
                }
        items.append(item)
    return {"items": items}


def world_memory_rows(limit: int = 50, query: str = "") -> list[dict]:
    """월드 메모리 단일 진실원(lib.world_memory) 타임라인 → 콘솔 memory 행 매핑.

    /ask·대시보드 🧭·크론 자동 적재와 같은 축적을 콘솔이 읽는다.
    lib 불가·비어 있으면 콘솔 로컬 storage(구 데이터) 폴백 — 마이그레이션 전 기록 보존.
    """
    try:
        from lib import world_memory

        rows = world_memory.timeline(query, limit=limit)
        mapped = [{
            "id": r.get("event_id"),
            "observed_at": r.get("issue_date"),
            "source": r.get("source") or "world",
            "kind": r.get("category"),
            "title": r.get("title"),
            "body": r.get("body") or "",
            "symbols": r.get("tickers") or [],
            "impact": r.get("importance"),
            "confidence": None,
            "metadata": {},
        } for r in rows]
        if mapped:
            return mapped
    except Exception:
        pass
    return storage.list_memory_events(limit=limit)


def log_world_issue(title: str, *, category: str = "메모", importance: str = "medium",
                    tickers: list[str] | None = None, body: str = "",
                    source: str = "console", observed_at: str = "") -> bool:
    """단일 월드 메모리에 이슈 기록 (dedupe 멱등). lib 불가 시 콘솔 storage 폴백."""
    title = str(title or "").strip()
    if not title:
        return False
    try:
        from lib import world_memory

        issue_date = str(observed_at or "")[:10] or None
        eid = world_memory.log_issue(title, category=category, importance=importance,
                                     issue_date=issue_date, tickers=tickers or [],
                                     body=body, source=source)
        return eid is not None
    except Exception:
        changed = storage.upsert_memory_events([{
            "observed_at": observed_at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source": source, "kind": category, "title": title, "body": body,
            "symbols": tickers or [], "impact": importance,
        }])
        return changed > 0


def context_pack(surface: str = "market", *, hours: int = 72) -> dict:
    surface = str(surface or "market").strip().lower()
    events = recent_source_events(hours=hours)
    memory = world_memory_rows(limit=50)
    symbols = Counter()
    sources = Counter()
    for row in events:
        sources[str(row.get("source") or "unknown")] += 1
        for value in row.get("tickers") or row.get("tags") or []:
            text = str(value).lstrip("$").upper()
            if 1 <= len(text) <= 12:
                symbols[text] += 1
    pack = {
        "ok": True,
        "surface": surface,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "project": "stock-report",
        "sources": {
            "events": events[:40],
            "source_counts": sources.most_common(12),
            "symbol_counts": symbols.most_common(16),
        },
        "reports": latest_reports(),
        "ml_activity": ml_activity(),
        "portfolio": portfolio_state(),
        "paper": paper_state(),
        "models": model_state(),
        "memory": memory,
        "focus": focus_for_surface(surface),
    }
    try:
        shared_memory.sync_external_layer_from_pack(pack)
        pack["shared_memory"] = shared_memory.status(limit=8)
    except Exception as exc:
        pack["shared_memory"] = {"ok": False, "error": str(exc), "records": []}
    return pack


def focus_for_surface(surface: str) -> list[str]:
    mapping = {
        "market": ["거시/뉴스 이벤트 흐름", "금리·유가·달러·VIX 변화", "이전 리포트와 달라진 점"],
        "portfolio": ["보유 비중과 손실한도", "모의투자 성과와 비용", "전략별 리밸런싱 가설"],
        "ticker": ["종목별 뉴스/차트/랭커 근거", "추천 성숙 outcome", "실패/성공 요인"],
        "paper": ["KR/US 모의투자 NAV", "MDD·회전율·거래비용", "정책 게이트와 shadow 학습"],
        "lab": ["가설·비중·규칙", "손실한도와 레버리지", "백테스트/모의 원장 연결"],
    }
    return mapping.get(surface, mapping["market"])


def ingest_recent_memory(hours: int = 72) -> dict:
    """최근 뉴스·리포트를 **단일 월드 메모리(lib.world_memory)** 에 적재 (dedupe 멱등).

    ML 원장(decisions/outcomes)은 append-only 원장이 이미 진실원 — 월드 메모리에
    중복 적재하지 않는다(콘솔 팩이 ml_activity 로 직접 읽음).
    """
    events = []
    for row in recent_source_events(hours=hours, limit=120):
        title = str(row.get("title") or row.get("summary") or "").strip()
        if not title:
            continue
        events.append(
            {
                "observed_at": row.get("published_at") or row.get("collected_at") or datetime.now(timezone.utc).isoformat(),
                "source": f"source:{row.get('source') or 'unknown'}",
                "kind": "market_event",
                "title": title,
                "body": str(row.get("body") or row.get("text") or row.get("summary") or "")[:2000],
                "symbols": row.get("tickers") or row.get("tags") or [],
                "impact": "watch",
                "confidence": 0.55,
                "metadata": {"url": row.get("url"), "raw_id": row.get("id")},
            }
        )
    for report in latest_reports(limit=8):
        events.append(
            {
                "observed_at": report.get("mtime"),
                "source": "stock-report:report",
                "kind": "report",
                "title": report.get("title") or report.get("name"),
                "body": report.get("summary") or "",
                "symbols": [],
                "impact": "context",
                "confidence": 0.7,
                "metadata": {"path": report.get("path"), "name": report.get("name")},
            }
        )
    changed = _log_ingest_events_to_world(events)
    return {"ok": True, "considered": len(events), "changed": changed,
            "total_recent": len(world_memory_rows(limit=200))}


def _log_ingest_events_to_world(events: list[dict]) -> int:
    """수집 이벤트 목록을 단일 월드 메모리에 기록 (dedupe 멱등 — 반복 실행 안전)."""
    category_map = {"market_event": "수집", "report": "리포트", "community_signal": "커뮤니티"}
    importance_map = {"report": "medium"}
    changed = 0
    for ev in events:
        kind = str(ev.get("kind") or "")
        if kind == "model_activity":
            # ML 원장(decisions/outcomes)은 append-only 원장이 이미 진실원 — 월드 메모리
            # 중복 적재로 /ask 타임라인을 오염시키지 않는다 (콘솔 팩이 원장을 직접 읽음).
            continue
        if log_world_issue(
            str(ev.get("title") or ""),
            category=category_map.get(kind, kind or "수집"),
            importance=importance_map.get(kind, "low"),
            tickers=[str(t) for t in (ev.get("symbols") or [])][:8],
            body=str(ev.get("body") or "")[:1200],
            source=str(ev.get("source") or "console"),
            observed_at=str(ev.get("observed_at") or ""),
        ):
            changed += 1
    return changed


def ingest_arca_proxy(max_pages: int = 2, proxy: str | None = None) -> dict:
    """Fetch Arca through the local SOCKS tunnel and store successful rows in cache + World Memory."""
    proxy = proxy or os.getenv("STOCK_COLLECTOR_ARCA_PROXY") or "socks5://127.0.0.1:1080"
    try:
        from reports import source_collector

        status = source_collector.arca_proxy_status(proxy)
        if not status.get("reachable"):
            return {"ok": False, "proxy": status, "fetched": 0, "written": 0, "changed": 0,
                    "error": status.get("error") or "proxy unavailable"}
        events = source_collector.fetch_arca_events(max_pages=max_pages, proxy=proxy, prefer_proxy=True)
        written = source_collector.append_events(events, cache_dir=source_cache_dir())
    except Exception as exc:
        return {"ok": False, "proxy": {"proxy": proxy}, "fetched": 0, "written": 0,
                "changed": 0, "error": str(exc)}

    memory_events = []
    for row in events:
        memory_events.append(
            {
                "observed_at": row.get("published_at") or datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "source": "arca:proxy",
                "kind": "community_signal",
                "title": row.get("title") or "",
                "body": row.get("body") or row.get("title") or "",
                "symbols": row.get("tickers") or row.get("tags") or [],
            }
        )
    changed = _log_ingest_events_to_world(memory_events)
    return {
        "ok": bool(events),
        "proxy": status,
        "fetched": len(events),
        "written": written,
        "changed": changed,
        "events": events[:12],
        "error": "" if events else "no arca rows parsed",
    }
