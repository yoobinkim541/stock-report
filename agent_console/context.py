from __future__ import annotations

import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from . import storage


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


def context_pack(surface: str = "market", *, hours: int = 72) -> dict:
    surface = str(surface or "market").strip().lower()
    events = recent_source_events(hours=hours)
    memory = storage.list_memory_events(limit=50)
    symbols = Counter()
    sources = Counter()
    for row in events:
        sources[str(row.get("source") or "unknown")] += 1
        for value in row.get("tickers") or row.get("tags") or []:
            text = str(value).lstrip("$").upper()
            if 1 <= len(text) <= 12:
                symbols[text] += 1
    return {
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
        "paper": paper_state(),
        "models": model_state(),
        "memory": memory,
        "focus": focus_for_surface(surface),
    }


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
    for row in ml_activity(limit=40):
        ticker = row.get("ticker") or row.get("symbol") or row.get("asset") or ""
        title = row.get("title") or row.get("decision") or row.get("action") or row.get("_file") or "ML activity"
        events.append(
            {
                "observed_at": row.get("ts") or row.get("created_at") or row.get("decided_at") or row.get("_mtime"),
                "source": f"ml:{row.get('_file') or 'unknown'}",
                "kind": "model_activity",
                "title": str(title)[:300],
                "body": json.dumps(row, ensure_ascii=False)[:2000],
                "symbols": [ticker] if ticker else [],
                "impact": "learn",
                "confidence": 0.6,
                "metadata": {"file": row.get("_file")},
            }
        )
    changed = storage.upsert_memory_events(events)
    return {"ok": True, "considered": len(events), "changed": changed, "total_recent": len(storage.list_memory_events())}
