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
from dashboard import chart_document

LAYOUTS = {
    "1": 1,
    "2v": 2,
    "2h": 2,
    "2x2": 4,
    "3+1": 4,
    "2x3": 6,
    "3x3": 9,
    "4x3": 12,
    "4x4": 16,
}
TIMEFRAMES = {"5m", "1h", "2h", "4h", "1d", "1wk", "1mo"}
PERIODS = {"3mo", "6mo", "1y", "5y", "전체"}
CHART_KINDS = chart_document.CHART_TYPES
TEMPLATE_KINDS = {"style", "indicators", "series"}
DRAWING_SYNC = {"off", "layout_symbol", "global_symbol"}
LINK_GROUPS = {"", "red", "orange", "yellow", "green", "blue", "purple", "gray"}
MUTABLE_PANEL_FIELDS = {
    "ticker",
    "timeframe",
    "period",
    "chart_kind",
    "top_indicators",
    "bottom_indicators",
    "compare",
    "log_scale",
    "link_group",
}
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
_TEMPLATE_SAFE_RE = re.compile(r"[^a-z0-9가-힣._-]+")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm_ticker(value: Any, fallback: str = "MSFT") -> str:
    ticker = ticker_names.normalize_input(str(value or "").strip())
    return ticker or fallback


def _panel(panel_id: str, ticker: str) -> dict[str, Any]:
    panel = {
        "id": panel_id,
        "ticker": _norm_ticker(ticker),
        "timeframe": "1d",
        "period": "6mo",
        "chart_kind": "candlestick",
        "top_indicators": ["이동평균선"],
        "bottom_indicators": ["거래량", "RSI"],
        "compare": [],
        "log_scale": False,
        "style_template_id": None,
        "indicator_template_id": None,
        "series_template_id": None,
        "link_group": "",
    }
    panel["document"] = chart_document.document_from_panel(panel)
    return panel


def _merge_panel_studies(
    current: Any,
    legacy: list[Any],
    fields: set[str],
) -> list[Any]:
    panes = {
        pane
        for pane, field in (("top", "top_indicators"), ("bottom", "bottom_indicators"))
        if field in fields
    }
    if not panes:
        return copy.deepcopy(current) if isinstance(current, list) else []
    wanted = [
        study for study in legacy
        if isinstance(study, dict) and study.get("pane") in panes
    ]
    existing = current if isinstance(current, list) else []
    wanted_keys = {(study.get("pane"), study.get("name")) for study in wanted}
    remaining = []
    for study in existing:
        if not isinstance(study, dict) or study.get("pane") not in panes:
            remaining.append(copy.deepcopy(study))
            continue
        key = (study.get("pane"), study.get("name"))
        if key in wanted_keys:
            remaining.append(copy.deepcopy(study))
            wanted_keys.remove(key)
    for study in wanted:
        if (study.get("pane"), study.get("name")) in wanted_keys:
            remaining.append(copy.deepcopy(study))
    return remaining


def _merge_panel_series(
    current: Any,
    legacy: list[Any],
    fields: set[str],
) -> list[Any]:
    existing = current if isinstance(current, list) else []
    if not ({"ticker", "compare"} & fields):
        return copy.deepcopy(existing)

    primary = next(
        (copy.deepcopy(series) for series in existing if isinstance(series, dict) and series.get("id") == "primary"),
        copy.deepcopy(legacy[0]),
    )
    if "ticker" in fields:
        primary["symbol"] = legacy[0]["symbol"]
    if "compare" not in fields:
        return [primary] + [
            copy.deepcopy(series)
            for series in existing
            if not (isinstance(series, dict) and series.get("id") == "primary")
        ]

    current_comparisons = {
        series.get("symbol"): series
        for series in existing
        if isinstance(series, dict) and series.get("kind") in {"benchmark", "peer"}
    }
    comparisons = [
        copy.deepcopy(current_comparisons.get(series["symbol"], series))
        for series in legacy[1:]
    ]
    document_owned = [
        copy.deepcopy(series)
        for series in existing
        if isinstance(series, dict)
        and series.get("id") != "primary"
        and series.get("kind") not in {"benchmark", "peer"}
    ]
    return [primary] + comparisons + document_owned


def _sync_panel_document(
    panel: dict[str, Any],
    workspace_id: str,
    *,
    fields: set[str],
) -> None:
    """Sync only legacy-owned fields without replacing document-owned content."""
    legacy = chart_document.document_from_panel(panel, workspace_id=workspace_id)
    current = panel.get("document")
    if not isinstance(current, dict):
        panel["document"] = legacy
        return
    document = chart_document.normalize_chart_document(current, ticker=panel.get("ticker") or "MSFT")
    if "ticker" in fields:
        document["symbol"] = legacy["symbol"]
    if "timeframe" in fields:
        document["timeframe"] = legacy["timeframe"]
    if "period" in fields:
        document["period"] = legacy["period"]
    if "chart_kind" in fields:
        document["chart"]["type"] = legacy["chart"]["type"]
    if "log_scale" in fields:
        document["scale"]["type"] = legacy["scale"]["type"]
    document["series"] = _merge_panel_series(document.get("series"), legacy["series"], fields)
    document["studies"] = _merge_panel_studies(document.get("studies"), legacy["studies"], fields)
    panel["document"] = chart_document.normalize_chart_document(document, ticker=legacy["symbol"])


def allowed_indicator_names() -> set[str]:
    return set(TOP_INDICATORS) | set(BOTTOM_INDICATORS)


def default_workspace(ticker: str = "MSFT") -> dict[str, Any]:
    tk = _norm_ticker(ticker)
    return {
        "id": "default",
        "name": "Default Workspace",
        "layout": "1",
        "active_panel": "p1",
        "maximized_panel": None,
        "sync": {
            "symbol": False,
            "interval": True,
            "range": True,
            "crosshair": True,
            "drawings": "layout_symbol",
        },
        "panels": [_panel("p1", tk)],
        "parked_panels": [],
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
            if k not in {"sync", "panels", "parked_panels", "metadata"}
        }
    )
    out["layout"] = str(out.get("layout") or "1")
    count = LAYOUTS.get(out["layout"], 1)
    panels = [p for p in workspace.get("panels", []) if isinstance(p, dict)]
    parked = [p for p in workspace.get("parked_panels", []) if isinstance(p, dict)]
    candidates: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for idx, panel in enumerate([*panels, *parked]):
        panel_id = str(panel.get("id") or f"p{idx + 1}")
        if panel_id in seen_ids:
            continue
        seen_ids.add(panel_id)
        candidates.append(copy.deepcopy(panel))
        if len(candidates) >= max(LAYOUTS.values()):
            break

    by_id = {str(panel.get("id") or ""): panel for panel in candidates}
    used_ids: set[str] = set()

    def source_for_slot(idx: int) -> dict[str, Any]:
        expected = f"p{idx + 1}"
        exact = by_id.get(expected)
        if exact is not None and expected not in used_ids:
            used_ids.add(expected)
            return copy.deepcopy(exact)
        for candidate in candidates:
            candidate_id = str(candidate.get("id") or "")
            if candidate_id not in used_ids:
                used_ids.add(candidate_id)
                return copy.deepcopy(candidate)
        return {}

    def normalize_panel(src: dict[str, Any], idx: int) -> dict[str, Any]:
        p = _panel(f"p{idx + 1}", src.get("ticker") or ticker)
        p.update(src)
        p["id"] = str(p.get("id") or f"p{idx + 1}")
        p["ticker"] = _norm_ticker(p.get("ticker"), ticker)
        p["timeframe"] = str(p.get("timeframe") or "1d")
        p["period"] = str(p.get("period") or "6mo")
        p["chart_kind"] = str(p.get("chart_kind") or "candlestick")
        p["top_indicators"] = [
            x for x in (p.get("top_indicators") or []) if x in TOP_INDICATORS
        ]
        p["bottom_indicators"] = [
            x for x in (p.get("bottom_indicators") or []) if x in BOTTOM_INDICATORS
        ]
        p["compare"] = [_norm_ticker(x) for x in (p.get("compare") or [])][:3]
        p["log_scale"] = bool(p.get("log_scale"))
        p["link_group"] = str(p.get("link_group") or "")
        if p["link_group"] not in LINK_GROUPS:
            p["link_group"] = ""
        raw_document = src.get("document")
        p["document"] = chart_document.normalize_chart_document(
            raw_document if isinstance(raw_document, dict) else chart_document.document_from_panel(
                p, workspace_id=str(out.get("id") or "")
            ),
            ticker=p["ticker"],
        )
        p.update(chart_document.panel_from_document(p["document"], p))
        return p

    merged = []
    for idx in range(count):
        merged.append(normalize_panel(source_for_slot(idx), idx))

    out["panels"] = merged
    out["parked_panels"] = [
        normalize_panel(candidate, idx + count)
        for idx, candidate in enumerate(candidates)
        if str(candidate.get("id") or "") not in used_ids
    ]
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
    out["maximized_panel"] = (
        out.get("maximized_panel")
        if any(p["id"] == out.get("maximized_panel") for p in merged)
        else None
    )
    if out["maximized_panel"]:
        out["active_panel"] = out["maximized_panel"]
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
        if chart_kind == "candle":
            chart_kind = "candlestick"
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
        link_group = str(raw_panel.get("link_group") or panel.get("link_group") or "")
        if link_group not in LINK_GROUPS:
            errors.append(f"panel[{idx}] unsupported link_group: {link_group}")
        raw_document = raw_panel.get("document")
        if raw_document is not None and not isinstance(raw_document, dict):
            errors.append(f"panel[{idx}].document: document must be an object")
        else:
            document_errors, document_warnings = chart_document.validate_chart_document(panel["document"])
            errors.extend(f"panel[{idx}].document: {error}" for error in document_errors)
            warnings.extend(f"panel[{idx}].document: {warning}" for warning in document_warnings)
    return errors, warnings


def mutate_panel(
    workspace: dict[str, Any],
    panel_id: str,
    changes: dict[str, Any],
) -> dict[str, Any]:
    """Apply a typed panel mutation and propagate enabled synchronized fields."""
    if not isinstance(changes, dict):
        raise ValueError("panel changes must be an object")
    unknown = set(changes) - MUTABLE_PANEL_FIELDS
    if unknown:
        raise ValueError(f"unsupported panel field: {sorted(unknown)[0]}")

    ws = normalize_workspace(workspace)
    source = next((panel for panel in ws["panels"] if panel["id"] == panel_id), None)
    if source is None:
        raise ValueError(f"unknown panel: {panel_id}")

    normalized: dict[str, Any] = {}
    for field, value in changes.items():
        if field == "ticker":
            normalized[field] = _norm_ticker(value, source["ticker"])
        elif field == "timeframe":
            value = str(value or "")
            if value not in TIMEFRAMES:
                raise ValueError(f"unsupported timeframe: {value}")
            normalized[field] = value
        elif field == "period":
            value = str(value or "")
            if value not in PERIODS:
                raise ValueError(f"unsupported period: {value}")
            normalized[field] = value
        elif field == "chart_kind":
            value = "candlestick" if str(value) == "candle" else str(value or "")
            if value not in CHART_KINDS:
                raise ValueError(f"unsupported chart_kind: {value}")
            normalized[field] = value
        elif field == "top_indicators":
            values = list(value or [])
            unknown_indicators = set(values) - TOP_INDICATORS
            if unknown_indicators:
                raise ValueError(f"unknown indicator: {sorted(unknown_indicators)[0]}")
            normalized[field] = values
        elif field == "bottom_indicators":
            values = list(value or [])
            unknown_indicators = set(values) - BOTTOM_INDICATORS
            if unknown_indicators:
                raise ValueError(f"unknown indicator: {sorted(unknown_indicators)[0]}")
            normalized[field] = values
        elif field == "compare":
            normalized[field] = [_norm_ticker(item) for item in list(value or [])][:3]
        elif field == "log_scale":
            normalized[field] = bool(value)
        elif field == "link_group":
            value = str(value or "")
            if value not in LINK_GROUPS:
                raise ValueError(f"unsupported link group: {value}")
            normalized[field] = value

    source_group = normalized.get("link_group", source.get("link_group") or "")
    sync_field = {"ticker": "symbol", "timeframe": "interval", "period": "range"}
    trace: list[dict[str, Any]] = []

    for field, value in normalized.items():
        sync_key = sync_field.get(field)
        if sync_key and ws["sync"].get(sync_key):
            targets = [
                panel for panel in ws["panels"]
                if not source_group or panel.get("link_group") == source_group
            ]
        else:
            targets = [source]
        if source not in targets:
            targets.insert(0, source)

        for panel in targets:
            before = copy.deepcopy(panel.get(field))
            if before == value:
                continue
            panel[field] = copy.deepcopy(value)
            if field != "link_group":
                _sync_panel_document(panel, str(ws.get("id") or ""), fields={field})
            trace.append({
                "panel_id": panel["id"],
                "field": field,
                "before": before,
                "after": copy.deepcopy(value),
                "source_panel_id": panel_id,
            })

    after = normalize_workspace(ws)
    return {"workspace": after, "trace": trace}


def workspace_id(workspace: dict) -> str:
    ws = normalize_workspace(workspace)
    name = str(ws.get("name") or "default").strip().lower()
    raw = re.sub(r"[^a-z0-9가-힣._-]+", "-", name).strip("-") or "workspace"
    digest = hashlib.sha1(
        json.dumps(ws, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:8]
    return f"{raw[:48]}-{digest}"


def chart_template_id(kind: str, name: str) -> str:
    kind = str(kind or "").strip().lower()
    name = str(name or "").strip().lower()
    raw = _TEMPLATE_SAFE_RE.sub("-", name).strip("-") or "template"
    prefix = kind if kind in TEMPLATE_KINDS else "template"
    return f"{prefix}-{raw[:40]}"


def template_kind_label(kind: str) -> str:
    return {
        "style": "스타일",
        "indicators": "인디케이터",
        "series": "시리즈",
    }.get(str(kind or "").strip().lower(), str(kind or "템플릿"))


def _panel_index(workspace: dict[str, Any], panel_id: str | None = None) -> int:
    panels = list((workspace or {}).get("panels") or [])
    if panel_id:
        for idx, panel in enumerate(panels):
            if str(panel.get("id") or "") == str(panel_id):
                return idx
    active = str((workspace or {}).get("active_panel") or "").strip()
    for idx, panel in enumerate(panels):
        if str(panel.get("id") or "") == active:
            return idx
    return 0


def chart_template_payload(
    workspace: dict | None,
    *,
    kind: str,
    name: str,
    panel_id: str | None = None,
) -> dict[str, Any]:
    ws = normalize_workspace(workspace)
    panels = ws.get("panels") or []
    if not panels:
        raise ValueError("workspace has no panels")
    idx = _panel_index(ws, panel_id)
    panel = dict(panels[idx])
    document = chart_document.normalize_chart_document(
        panel.get("document"), ticker=panel.get("ticker") or "MSFT"
    )
    kind = str(kind or "").strip().lower()
    if kind not in TEMPLATE_KINDS:
        raise ValueError(f"unsupported chart template kind: {kind}")
    if kind == "style":
        payload = {
            "chart_kind": panel.get("chart_kind"),
            "timeframe": panel.get("timeframe"),
            "period": panel.get("period"),
            "log_scale": bool(panel.get("log_scale")),
            "document_style": {
                "chart": copy.deepcopy(document.get("chart") or {}),
                "session": copy.deepcopy(document.get("session") or {}),
                "scale": copy.deepcopy(document.get("scale") or {}),
                "renderer": copy.deepcopy(document.get("renderer") or {}),
            },
        }
    elif kind == "indicators":
        payload = {
            "top_indicators": list(panel.get("top_indicators") or []),
            "bottom_indicators": list(panel.get("bottom_indicators") or []),
            "studies": copy.deepcopy(document.get("studies") or []),
        }
    else:
        payload = {
            "ticker": panel.get("ticker"),
            "compare": list(panel.get("compare") or []),
            "series": copy.deepcopy(document.get("series") or []),
        }
    return {
        "id": chart_template_id(kind, name),
        "kind": kind,
        "name": str(name or "").strip() or template_kind_label(kind),
        "payload": payload,
        "source": {
            "workspace_id": ws.get("id"),
            "panel_id": panel.get("id"),
            "ticker": panel.get("ticker"),
            "timeframe": panel.get("timeframe"),
            "period": panel.get("period"),
            "chart_kind": panel.get("chart_kind"),
        },
    }


def apply_chart_template(
    workspace: dict,
    template: dict,
    *,
    panel_id: str | None = None,
    apply_to_all: bool = False,
) -> dict[str, Any]:
    out = normalize_workspace(workspace)
    record = dict(template or {})
    kind = str(record.get("kind") or "").strip().lower()
    if kind not in TEMPLATE_KINDS:
        raise ValueError(f"unsupported chart template kind: {kind}")
    payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
    target_ids: list[int]
    if kind == "series":
        target_ids = [_panel_index(out, panel_id)]
    elif apply_to_all:
        target_ids = list(range(len(out["panels"])))
    else:
        target_ids = [_panel_index(out, panel_id)]

    for idx in target_ids:
        panel = out["panels"][idx]
        if kind == "style":
            sync_fields = {"chart_kind", "timeframe", "period", "log_scale"}
            chart_kind = payload.get("chart_kind")
            if chart_kind == "candle":
                chart_kind = "candlestick"
            if chart_kind in CHART_KINDS:
                panel["chart_kind"] = chart_kind
            if payload.get("timeframe") in TIMEFRAMES:
                panel["timeframe"] = payload["timeframe"]
            if payload.get("period") in PERIODS:
                panel["period"] = payload["period"]
            panel["log_scale"] = bool(payload.get("log_scale"))
            panel["style_template_id"] = str(record.get("id") or panel.get("style_template_id") or "")
        elif kind == "indicators":
            sync_fields = {"top_indicators", "bottom_indicators"}
            panel["top_indicators"] = [x for x in (payload.get("top_indicators") or []) if x in TOP_INDICATORS]
            panel["bottom_indicators"] = [x for x in (payload.get("bottom_indicators") or []) if x in BOTTOM_INDICATORS]
            panel["indicator_template_id"] = str(record.get("id") or panel.get("indicator_template_id") or "")
        else:
            sync_fields = {"ticker", "compare"}
            ticker = payload.get("ticker")
            if ticker:
                panel["ticker"] = _norm_ticker(ticker, panel.get("ticker") or "MSFT")
            panel["compare"] = [_norm_ticker(x) for x in (payload.get("compare") or [])][:3]
            panel["series_template_id"] = str(record.get("id") or panel.get("series_template_id") or "")
        _sync_panel_document(panel, str(out.get("id") or ""), fields=sync_fields)
        document = chart_document.normalize_chart_document(
            panel.get("document"), ticker=panel.get("ticker") or "MSFT"
        )
        if kind == "style" and isinstance(payload.get("document_style"), dict):
            style = payload["document_style"]
            for key in ("chart", "session", "scale", "renderer"):
                if isinstance(style.get(key), dict):
                    document[key] = copy.deepcopy(style[key])
        elif kind == "indicators" and isinstance(payload.get("studies"), list):
            document["studies"] = copy.deepcopy(payload["studies"])
        elif kind == "series" and isinstance(payload.get("series"), list):
            document["series"] = copy.deepcopy(payload["series"])
        panel["document"] = chart_document.normalize_chart_document(
            document, ticker=panel.get("ticker") or "MSFT"
        )
        panel.update(chart_document.panel_from_document(panel["document"], panel))
    return normalize_workspace(out)


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
    if field == "chart_kind" and str(value) not in CHART_KINDS | {"candle"}:
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
        if field == "chart_kind" and value == "candle":
            value = "candlestick"
        out["panels"][idx][field] = value
        _sync_panel_document(
            out["panels"][idx],
            str(out.get("id") or ""),
            fields={field},
        )
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


def _apply_document_patch_to_panel(
    workspace: dict,
    patch: dict[str, Any],
    *,
    panel_index: int = 0,
) -> dict[str, Any]:
    out = normalize_workspace(workspace)
    if panel_index >= len(out["panels"]):
        raise ValueError(f"panel index out of range: {panel_index}")
    panel = out["panels"][panel_index]
    panel["document"] = chart_document.apply_chart_document_patch(panel["document"], patch)
    panel.update(chart_document.panel_from_document(panel["document"], panel))
    return normalize_workspace(out)


def propose_workspace_patch(prompt: str, workspace: dict) -> dict[str, Any]:
    """Convert a natural-language chart request into a safe workspace patch.

    This is a deterministic first pass. It gives the UI the same preview/apply
    shape that a future LLM-backed chart agent can return.
    """
    text = str(prompt or "").lower()
    before = normalize_workspace(workspace)
    patch: dict[str, Any] = {}
    panel_idx = _panel_index(before)
    panel = before["panels"][panel_idx]
    top = list(panel.get("top_indicators") or [])
    bottom = list(panel.get("bottom_indicators") or [])
    warnings: list[str] = []
    studies_changed = False
    remove_comparisons = False

    if any(token in text for token in ("5분", "5m", "분봉", "intraday", "장중")):
        patch["timeframe"] = "5m"
        if "VWAP(세션)" not in top:
            top.append("VWAP(세션)")
            studies_changed = True
        if "거래량" not in bottom:
            bottom.append("거래량")
            studies_changed = True
        warnings.append("5분봉 데이터는 provider 보존 기간과 장중 수집 상태에 따라 제한될 수 있습니다.")
    if any(token in text for token in ("1시간", "1h")):
        patch["timeframe"] = "1h"
    if any(token in text for token in ("일봉", "1d")):
        patch["timeframe"] = "1d"
    if any(token in text for token in ("추세", "trend", "모멘텀")):
        for name in ("이동평균선", "자동 추세선·채널", "지수이평(EMA)"):
            if name not in top:
                top.append(name)
                studies_changed = True
    if any(token in text for token in ("변동성", "volatility", "밴드", "압축", "스퀴즈")):
        for name in ("볼린저 밴드", "켈트너 채널"):
            if name not in top:
                top.append(name)
                studies_changed = True
    if any(token in text for token in ("매물대", "volume profile", "볼륨프로필")) and "매물대" not in top:
        top.append("매물대")
        studies_changed = True
    if "macd" in text and "MACD" not in bottom:
        bottom.append("MACD")
        studies_changed = True
    if "rsi" in text and "RSI" not in bottom:
        bottom.append("RSI")
        studies_changed = True
    if any(token in text for token in ("비교 제거", "비교 빼", "비교 없")):
        remove_comparisons = True

    if studies_changed:
        legacy = chart_document.document_from_panel({
            **panel,
            "top_indicators": top[:8],
            "bottom_indicators": bottom[:6],
        })
        patch["studies"] = _merge_panel_studies(
            panel["document"].get("studies"),
            legacy["studies"],
            {"top_indicators", "bottom_indicators"},
        )
    if remove_comparisons:
        legacy = chart_document.document_from_panel({**panel, "compare": []})
        patch["series"] = _merge_panel_series(
            panel["document"].get("series"),
            legacy["series"],
            {"compare"},
        )

    after = _apply_document_patch_to_panel(before, patch, panel_index=panel_idx) if patch else before
    return {
        "ok": True,
        "summary": "차트 요청을 워크스페이스 패치로 변환했습니다.",
        "patch": patch,
        "before": before,
        "after": after,
        "diff": diff_workspaces(before, after),
        "warnings": warnings,
    }
