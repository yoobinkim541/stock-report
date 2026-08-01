"""Pure chart workspace model for saved multi-chart layouts.

This module intentionally has no Streamlit dependency. It validates the state
that the full chart workspace UI, storage layer, and AI patch preview share.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any

import ticker_names

LAYOUTS = {"1": 1, "2v": 2, "2h": 2, "2x2": 4, "3+1": 4, "2x3": 6}
TIMEFRAMES = {"5m", "1h", "2h", "4h", "1d", "1wk", "1mo"}
PERIODS = {"3mo", "6mo", "1y", "5y", "전체"}
CHART_KINDS = {"line", "candle", "heikin_ashi"}
DRAWING_SYNC = {"off", "layout_symbol", "global_symbol"}
TOP_INDICATORS = {
    "이동평균선",
    "자동 추세선·채널",
    "지수이평(EMA)",
    "볼린저 밴드",
    "일목균형표",
    "슈퍼트렌드",
    "엔벨로프",
    "파라볼릭 SAR",
    "프라이스 채널",
    "매물대",
    "프랙탈",
    "VWAP(세션)",
    "앵커드 VWAP",
    "켈트너 채널",
    "KAMA",
    "샹들리에 엑시트",
}
BOTTOM_INDICATORS = {
    "거래량",
    "RSI",
    "RSI 다이버전스",
    "MACD",
    "스토캐스틱",
    "Aroon",
    "%b",
    "PVT",
    "분기 EPS",
    "펀더멘털",
}

_PATCH_PATH_RE = re.compile(r"^panels\[(\d+)\]\.([A-Za-z_][A-Za-z0-9_]*)$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm_ticker(value: Any, fallback: str = "MSFT") -> str:
    ticker = ticker_names.normalize_input(str(value or "").strip())
    return ticker or fallback


def _panel(panel_id: str, ticker: str) -> dict[str, Any]:
    return {
        "id": panel_id,
        "ticker": _norm_ticker(ticker),
        "timeframe": "1d",
        "period": "6mo",
        "chart_kind": "candle",
        "top_indicators": ["이동평균선"],
        "bottom_indicators": ["거래량", "RSI"],
        "compare": [],
        "log_scale": False,
        "style_template_id": None,
        "indicator_template_id": None,
        "series_template_id": None,
    }


def allowed_indicator_names() -> set[str]:
    return set(TOP_INDICATORS) | set(BOTTOM_INDICATORS)


def default_workspace(ticker: str = "MSFT") -> dict[str, Any]:
    tk = _norm_ticker(ticker)
    return {
        "id": "default",
        "name": "Default Workspace",
        "layout": "1",
        "active_panel": "p1",
        "sync": {
            "symbol": False,
            "interval": True,
            "range": True,
            "crosshair": True,
            "drawings": "layout_symbol",
        },
        "panels": [_panel("p1", tk)],
        "metadata": {"created_at": _now(), "updated_at": _now()},
    }


def normalize_workspace(workspace: dict | None, *, ticker: str = "MSFT") -> dict[str, Any]:
    base = default_workspace(ticker)
    if not isinstance(workspace, dict):
        return base

    out = copy.deepcopy(base)
    out.update(
        {
            k: copy.deepcopy(v)
            for k, v in workspace.items()
            if k not in {"sync", "panels", "metadata"}
        }
    )
    out["layout"] = str(out.get("layout") or "1")
    count = LAYOUTS.get(out["layout"], 1)
    panels = [p for p in workspace.get("panels", []) if isinstance(p, dict)]

    merged = []
    for idx in range(count):
        src = copy.deepcopy(panels[idx]) if idx < len(panels) else {}
        p = _panel(f"p{idx + 1}", src.get("ticker") or ticker)
        p.update(src)
        p["id"] = str(p.get("id") or f"p{idx + 1}")
        p["ticker"] = _norm_ticker(p.get("ticker"), ticker)
        p["timeframe"] = str(p.get("timeframe") or "1d")
        p["period"] = str(p.get("period") or "6mo")
        p["chart_kind"] = str(p.get("chart_kind") or "candle")
        p["top_indicators"] = [
            x for x in (p.get("top_indicators") or []) if x in TOP_INDICATORS
        ]
        p["bottom_indicators"] = [
            x for x in (p.get("bottom_indicators") or []) if x in BOTTOM_INDICATORS
        ]
        p["compare"] = [_norm_ticker(x) for x in (p.get("compare") or [])][:3]
        p["log_scale"] = bool(p.get("log_scale"))
        merged.append(p)

    out["panels"] = merged
    sync = dict(base["sync"])
    incoming_sync = workspace.get("sync") if isinstance(workspace.get("sync"), dict) else {}
    sync.update(incoming_sync)
    sync["symbol"] = bool(sync.get("symbol"))
    sync["interval"] = bool(sync.get("interval"))
    sync["range"] = bool(sync.get("range"))
    sync["crosshair"] = bool(sync.get("crosshair"))
    sync["drawings"] = (
        sync.get("drawings") if sync.get("drawings") in DRAWING_SYNC else "layout_symbol"
    )
    out["sync"] = sync
    out["active_panel"] = (
        out.get("active_panel")
        if any(p["id"] == out.get("active_panel") for p in merged)
        else merged[0]["id"]
    )
    meta = dict(workspace.get("metadata") or {})
    meta.setdefault("created_at", base["metadata"]["created_at"])
    meta["updated_at"] = _now()
    out["metadata"] = meta
    return out


def validate_workspace(workspace: dict) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    raw = workspace if isinstance(workspace, dict) else {}
    ws = normalize_workspace(raw)

    if str(raw.get("layout") or ws["layout"]) not in LAYOUTS:
        errors.append(f"unsupported layout: {raw.get('layout')}")
    if len(ws["panels"]) != LAYOUTS.get(ws["layout"], 1):
        errors.append("panel count does not match layout")

    raw_panels = raw.get("panels") if isinstance(raw.get("panels"), list) else []
    for idx, panel in enumerate(ws["panels"]):
        raw_panel = raw_panels[idx] if idx < len(raw_panels) and isinstance(raw_panels[idx], dict) else {}
        timeframe = str(raw_panel.get("timeframe") or panel["timeframe"])
        period = str(raw_panel.get("period") or panel["period"])
        chart_kind = str(raw_panel.get("chart_kind") or panel["chart_kind"])
        if timeframe not in TIMEFRAMES:
            errors.append(f"panel[{idx}] unsupported timeframe: {timeframe}")
        if period not in PERIODS:
            errors.append(f"panel[{idx}] unsupported period: {period}")
        if chart_kind not in CHART_KINDS:
            errors.append(f"panel[{idx}] unsupported chart_kind: {chart_kind}")
        for name in raw_panel.get("top_indicators") or []:
            if name not in TOP_INDICATORS:
                errors.append(f"unknown indicator: {name}")
        for name in raw_panel.get("bottom_indicators") or []:
            if name not in BOTTOM_INDICATORS:
                errors.append(f"unknown indicator: {name}")
        if len(raw_panel.get("compare") or []) > 3:
            warnings.append(f"panel[{idx}] compare symbols capped at 3")
    return errors, warnings


def workspace_id(workspace: dict) -> str:
    ws = normalize_workspace(workspace)
    name = str(ws.get("name") or "default").strip().lower()
    raw = re.sub(r"[^a-z0-9가-힣._-]+", "-", name).strip("-") or "workspace"
    digest = hashlib.sha1(
        json.dumps(ws, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:8]
    return f"{raw[:48]}-{digest}"


def _validate_patch_value(field: str, value: Any) -> None:
    if field == "top_indicators":
        bad = [x for x in (value or []) if x not in TOP_INDICATORS]
        if bad:
            raise ValueError(f"unknown indicator: {bad[0]}")
    if field == "bottom_indicators":
        bad = [x for x in (value or []) if x not in BOTTOM_INDICATORS]
        if bad:
            raise ValueError(f"unknown indicator: {bad[0]}")
    if field == "timeframe" and str(value) not in TIMEFRAMES:
        raise ValueError(f"unsupported timeframe: {value}")
    if field == "period" and str(value) not in PERIODS:
        raise ValueError(f"unsupported period: {value}")
    if field == "chart_kind" and str(value) not in CHART_KINDS:
        raise ValueError(f"unsupported chart_kind: {value}")


def apply_workspace_patch(workspace: dict, patch: dict) -> dict[str, Any]:
    out = normalize_workspace(workspace)
    for path, value in (patch or {}).items():
        path = str(path)
        if path in {"layout", "name", "active_panel"}:
            if path == "layout" and str(value) not in LAYOUTS:
                raise ValueError(f"unsupported layout: {value}")
            out[path] = value
            out = normalize_workspace(out)
            continue
        if path.startswith("sync."):
            key = path.split(".", 1)[1]
            out["sync"][key] = value
            out = normalize_workspace(out)
            continue
        match = _PATCH_PATH_RE.match(path)
        if not match:
            raise ValueError(f"unsupported patch path: {path}")
        idx, field = int(match.group(1)), match.group(2)
        if idx >= len(out["panels"]):
            raise ValueError(f"panel index out of range: {idx}")
        _validate_patch_value(field, value)
        out["panels"][idx][field] = value
        out = normalize_workspace(out)

    errors, _warnings = validate_workspace(out)
    if errors:
        raise ValueError("; ".join(errors))
    return out


def diff_workspaces(before: dict, after: dict) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    left = normalize_workspace(before)
    right = normalize_workspace(after)

    def walk(path: str, a: Any, b: Any) -> None:
        if isinstance(a, dict) and isinstance(b, dict):
            for key in sorted(set(a) | set(b)):
                walk(f"{path}.{key}" if path else key, a.get(key), b.get(key))
            return
        if isinstance(a, list) and isinstance(b, list):
            for idx in range(max(len(a), len(b))):
                av = a[idx] if idx < len(a) else None
                bv = b[idx] if idx < len(b) else None
                walk(f"{path}[{idx}]", av, bv)
            return
        if a != b:
            rows.append({"path": path, "before": a, "after": b})

    walk("", left, right)
    return rows


def propose_workspace_patch(prompt: str, workspace: dict) -> dict[str, Any]:
    """Convert a natural-language chart request into a safe workspace patch.

    This is a deterministic first pass. It gives the UI the same preview/apply
    shape that a future LLM-backed chart agent can return.
    """
    text = str(prompt or "").lower()
    before = normalize_workspace(workspace)
    patch: dict[str, Any] = {}
    panel = before["panels"][0]
    top = list(panel.get("top_indicators") or [])
    bottom = list(panel.get("bottom_indicators") or [])
    warnings: list[str] = []

    if any(token in text for token in ("5분", "5m", "분봉", "intraday", "장중")):
        patch["panels[0].timeframe"] = "5m"
        if "VWAP(세션)" not in top:
            top.append("VWAP(세션)")
        if "거래량" not in bottom:
            bottom.append("거래량")
        warnings.append("5분봉 데이터는 provider 보존 기간과 장중 수집 상태에 따라 제한될 수 있습니다.")
    if any(token in text for token in ("1시간", "1h")):
        patch["panels[0].timeframe"] = "1h"
    if any(token in text for token in ("일봉", "1d")):
        patch["panels[0].timeframe"] = "1d"
    if any(token in text for token in ("추세", "trend", "모멘텀")):
        for name in ("이동평균선", "자동 추세선·채널", "지수이평(EMA)"):
            if name not in top:
                top.append(name)
    if any(token in text for token in ("변동성", "volatility", "밴드", "압축", "스퀴즈")):
        for name in ("볼린저 밴드", "켈트너 채널"):
            if name not in top:
                top.append(name)
    if any(token in text for token in ("매물대", "volume profile", "볼륨프로필")) and "매물대" not in top:
        top.append("매물대")
    if "macd" in text and "MACD" not in bottom:
        bottom.append("MACD")
    if "rsi" in text and "RSI" not in bottom:
        bottom.append("RSI")
    if any(token in text for token in ("비교 제거", "비교 빼", "비교 없")):
        patch["panels[0].compare"] = []

    if top:
        patch["panels[0].top_indicators"] = top[:8]
    if bottom:
        patch["panels[0].bottom_indicators"] = bottom[:6]

    after = apply_workspace_patch(before, patch) if patch else before
    return {
        "ok": True,
        "summary": "차트 요청을 워크스페이스 패치로 변환했습니다.",
        "patch": patch,
        "before": before,
        "after": after,
        "diff": diff_workspaces(before, after),
        "warnings": warnings,
    }
