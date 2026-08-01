from __future__ import annotations

from typing import Any

import pandas as pd

from dashboard import cached
from ml.strategy_studio import (
    StrategySpec,
    apply_strategy_patch,
    builtin_strategy_presets,
    build_strategy_report,
    diff_strategy_specs,
    run_strategy_backtest,
)

from . import storage


def save_strategy_spec(payload: dict[str, Any] | StrategySpec) -> dict[str, Any]:
    return storage.save_strategy_spec(payload)


def list_strategy_specs(limit: int = 50) -> list[dict[str, Any]]:
    return storage.list_strategy_specs(limit=limit)


def get_strategy_spec(spec_id: str, *, version: int | None = None) -> dict[str, Any] | None:
    return storage.get_strategy_spec(spec_id, version=version)


def list_strategy_versions(spec_id: str, limit: int = 20) -> list[dict[str, Any]]:
    return storage.list_strategy_versions(spec_id, limit=limit)


def save_strategy_version(
    spec_id: str,
    spec: dict[str, Any] | StrategySpec,
    *,
    patch: dict[str, Any] | None = None,
    source: str = "ui",
) -> dict[str, Any]:
    return storage.save_strategy_version(spec_id, spec, patch=patch, source=source)


def revert_strategy_version(spec_id: str, version: int) -> dict[str, Any]:
    return storage.revert_strategy_version(spec_id, version)


def strategy_lab_state(limit: int = 20) -> dict[str, Any]:
    catalog = storage.strategy_spec_catalog(limit=limit)
    return {
        "ok": True,
        "catalog": catalog,
        "presets": builtin_strategy_presets(),
    }


def preview_strategy_spec(
    spec: dict[str, Any] | StrategySpec,
    *,
    benchmark: str | None = None,
    prices: pd.DataFrame | None = None,
    period: str | None = None,
) -> dict[str, Any]:
    spec_obj = StrategySpec.from_dict(spec)
    price_panel = prices if prices is not None else _load_prices(spec_obj, period=period)
    run = run_strategy_backtest(spec_obj, price_panel, benchmark=benchmark or spec_obj.base_symbol or None)
    report = build_strategy_report(run, spec=spec_obj)
    return {
        "ok": bool(run.ok),
        "spec": spec_obj.to_dict(),
        "report": _json_safe(report),
        "metrics": _json_safe(dict(run.metrics or {})),
        "benchmark": _json_safe(dict(run.benchmark or {})),
        "warnings": list(run.warnings or []),
        "errors": list(run.errors or []),
        "trade_count": int(run.metrics.get("trade_count", 0) if isinstance(run.metrics, dict) else 0),
    }


def propose_strategy_patch(
    question: str,
    current_spec: dict[str, Any] | StrategySpec,
    history: list[dict[str, Any]] | None = None,
    pack: dict[str, Any] | None = None,
) -> dict[str, Any]:
    base = StrategySpec.from_dict(current_spec).to_dict()
    patch = _heuristic_patch(question, base)
    patched = apply_strategy_patch(base, patch) if patch else base
    preview = preview_strategy_spec(patched, benchmark=base.get("base_symbol") or None)
    return {
        "ok": bool(preview.get("ok")),
        "current": base,
        "patch": patch,
        "diff": diff_strategy_specs(base, patched),
        "preview": preview,
        "history": list(history or []),
        "surface": (pack or {}).get("surface"),
    }


def _heuristic_patch(question: str, current_spec: dict[str, Any]) -> dict[str, Any]:
    q = str(question or "").lower()
    indicators = list(current_spec.get("indicators") or [])
    rules = dict(current_spec.get("rules") or {})
    sizing = dict(current_spec.get("sizing") or {})

    if any(word in q for word in ("atr", "손절", "stop", "손실")):
        if not any(str(item.get("kind") or "").lower() == "atr" for item in indicators if isinstance(item, dict)):
            indicators.append({"name": "atr14", "kind": "atr", "period": 14, "source": "close", "output": "atr14"})
        rules["exit"] = [
            {"field": "atr14", "op": ">", "value": 0.0, "action": "exit_all", "label": "atr_stop"}
        ]
        sizing["type"] = sizing.get("type") or "fixed_pct"
        sizing.setdefault("position_pct", 1.0)
        return {"indicators": indicators, "rules": rules, "sizing": sizing}

    if any(word in q for word in ("bollinger", "볼린저", "밴드", "mean reversion", "mean-reversion", "평균회귀")):
        indicators = [
            {"name": "bb_mid", "kind": "bollinger", "period": 20, "std_mult": 2.0, "source": "close", "output": "bb_mid"},
            {"name": "rsi_14", "kind": "rsi", "period": 14, "source": "close", "output": "rsi_14"},
        ]
        rules = {
            "entry": [
                {
                    "all": [
                        {"field": "close", "op": "cross_below", "ref": "bb_mid_lower"},
                        {"field": "rsi_14", "op": "<=", "value": 35},
                    ],
                    "label": "bb_reversion_entry",
                    "action": "enter_long",
                }
            ],
            "exit": [
                {
                    "any": [
                        {"field": "close", "op": "cross_above", "ref": "bb_mid"},
                        {"field": "rsi_14", "op": ">=", "value": 60},
                    ],
                    "label": "bb_reversion_exit",
                    "action": "exit_all",
                }
            ],
        }
        sizing["type"] = "fixed_pct"
        sizing.setdefault("position_pct", 1.0)
        return {"indicators": indicators, "rules": rules, "sizing": sizing}

    if any(word in q for word in ("breakout", "돌파", "신고점", "52주", "모멘텀", "momentum")):
        indicators = [
            {"name": "high20", "kind": "rolling", "period": 20, "method": "max", "source": "close", "output": "high20"},
            {"name": "dd20", "kind": "drawdown", "source": "close", "output": "dd20"},
        ]
        rules = {
            "entry": [
                {
                    "field": "close",
                    "op": "cross_above",
                    "ref": "high20",
                    "label": "breakout_entry",
                    "action": "enter_long",
                }
            ],
            "exit": [
                {
                    "any": [
                        {"field": "dd20", "op": "<=", "value": -0.08},
                        {"field": "close", "op": "cross_below", "ref": "high20"},
                    ],
                    "label": "breakout_exit",
                    "action": "exit_all",
                }
            ],
        }
        sizing["type"] = "fixed_pct"
        sizing.setdefault("position_pct", 1.0)
        return {"indicators": indicators, "rules": rules, "sizing": sizing}

    if any(word in q for word in ("vwap", "장중", "intraday", "분봉", "volume")):
        indicators = [
            {"name": "vwap_20", "kind": "vwap", "period": 20, "source": "close", "output": "vwap_20"},
            {"name": "vol_z", "kind": "volume_zscore", "period": 20, "source": "volume", "output": "vol_z"},
            {"name": "rsi_14", "kind": "rsi", "period": 14, "source": "close", "output": "rsi_14"},
        ]
        rules = {
            "entry": [
                {
                    "all": [
                        {"field": "close", "op": "cross_below", "ref": "vwap_20"},
                        {"field": "rsi_14", "op": "<=", "value": 35},
                    ],
                    "label": "vwap_entry",
                    "action": "enter_long",
                }
            ],
            "exit": [
                {
                    "any": [
                        {"field": "close", "op": "cross_above", "ref": "vwap_20"},
                        {"field": "rsi_14", "op": ">=", "value": 60},
                    ],
                    "label": "vwap_exit",
                    "action": "exit_all",
                }
            ],
        }
        sizing["type"] = "fixed_pct"
        sizing.setdefault("position_pct", 1.0)
        return {"indicators": indicators, "rules": rules, "sizing": sizing}

    if any(word in q for word in ("ema", "추세", "trend")):
        indicators = [
            {"name": "ema_fast", "kind": "ema", "period": 20, "source": "close", "output": "ema_fast"},
            {"name": "ema_slow", "kind": "ema", "period": 50, "source": "close", "output": "ema_slow"},
        ]
        rules = {
            "entry": [{"field": "close", "op": "cross_above", "ref": "ema_fast", "label": "trend_entry", "action": "enter_long"}],
            "exit": [{"field": "close", "op": "cross_below", "ref": "ema_slow", "label": "trend_exit", "action": "exit_all"}],
        }
        sizing.setdefault("type", "fixed_pct")
        sizing.setdefault("position_pct", 1.0)
        return {"indicators": indicators, "rules": rules, "sizing": sizing}

    if any(word in q for word in ("거래 횟수", "덜", "엄격", "필터", "진입 조건")):
        rules["entry"] = _tighten_entry_rules(rules.get("entry") or [])
        return {"rules": rules}

    return {}


def _tighten_entry_rules(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for rule in entries:
        if not isinstance(rule, dict):
            continue
        tightened = dict(rule)
        if tightened.get("value") is not None:
            try:
                tightened["value"] = float(tightened["value"]) * 0.95
            except (TypeError, ValueError):
                pass
        out.append(tightened)
    return out


def _load_prices(spec: StrategySpec, period: str | None = None) -> pd.DataFrame:
    symbols = [str(sym).upper().strip() for sym in (spec.universe or {}).get("symbols") or [] if str(sym).strip()]
    if not symbols and spec.base_symbol:
        symbols = [spec.base_symbol]
    period = period or _period_for_timeframe(spec.timeframe)
    frames: dict[str, pd.Series] = {}
    for symbol in symbols:
        if symbol == "CASH":
            continue
        try:
            frame = cached.ohlc(symbol, period=period)
        except Exception:
            frame = pd.DataFrame()
        if frame is None or frame.empty:
            continue
        normalized = frame.copy()
        normalized.columns = [str(col).strip().lower().replace(" ", "_") for col in normalized.columns]
        for field in normalized.columns:
            series = pd.to_numeric(normalized[field], errors="coerce")
            frames[f"{symbol}__{field}"] = series
    return pd.DataFrame(frames).sort_index().ffill().dropna(how="all")


def _period_for_timeframe(timeframe: str) -> str:
    tf = str(timeframe or "1d").lower().strip()
    mapping = {"1d": "2y", "5m": "60d", "1m": "30d", "1wk": "5y", "1w": "5y"}
    return mapping.get(tf, "2y")


def _json_safe(value: Any) -> Any:
    if isinstance(value, pd.DataFrame):
        return {
            "columns": list(value.columns),
            "index": [str(x) for x in value.index],
            "rows": value.reset_index(drop=True).to_dict("records"),
        }
    if isinstance(value, pd.Series):
        return {"index": [str(x) for x in value.index], "values": [None if pd.isna(x) else x for x in value.tolist()]}
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            return str(value)
    return value
