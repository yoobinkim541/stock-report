from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any


_SUPPORTED_INDICATORS = {
    "rsi",
    "ema",
    "sma",
    "atr",
    "macd",
    "bollinger",
    "vwap",
    "volume_zscore",
    "rolling",
    "drawdown",
}

_SUPPORTED_SIZING = {
    "fixed_pct",
    "risk_budget",
    "equal_weight",
}

_SUPPORTED_VALIDATION = {
    "single_pass",
    "walk_forward",
}


@dataclass(slots=True)
class StrategySpec:
    name: str
    market: str = "us"
    timeframe: str = "1d"
    base_symbol: str = ""
    universe: dict[str, Any] = field(default_factory=dict)
    indicators: list[dict[str, Any]] = field(default_factory=list)
    rules: dict[str, Any] = field(default_factory=dict)
    sizing: dict[str, Any] = field(default_factory=dict)
    costs: dict[str, Any] = field(default_factory=dict)
    optimization: dict[str, Any] = field(default_factory=dict)
    validation: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    version: int = 1
    id: str | None = None

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | "StrategySpec") -> "StrategySpec":
        if isinstance(payload, StrategySpec):
            return payload
        if not isinstance(payload, dict):
            raise TypeError("StrategySpec payload must be a dict")
        data = dict(payload)
        indicators = [dict(item) for item in _ensure_list(data.get("indicators")) if isinstance(item, dict)]
        rules = _deep_copy(data.get("rules") or {})
        sizing = _deep_copy(data.get("sizing") or {})
        costs = _deep_copy(data.get("costs") or {})
        optimization = _deep_copy(data.get("optimization") or {})
        validation = _deep_copy(data.get("validation") or {})
        metadata = _deep_copy(data.get("metadata") or {})
        universe = _deep_copy(data.get("universe") or {})
        name = str(data.get("name") or "").strip()
        if not name:
            raise ValueError("strategy name is required")
        market = str(data.get("market") or "us").strip().lower() or "us"
        timeframe = str(data.get("timeframe") or "1d").strip().lower() or "1d"
        base_symbol = str(data.get("base_symbol") or data.get("baseSymbol") or "").strip().upper()
        version = int(data.get("version") or 1)
        spec = cls(
            name=name,
            market=market,
            timeframe=timeframe,
            base_symbol=base_symbol,
            universe=universe,
            indicators=indicators,
            rules=rules,
            sizing=sizing,
            costs=costs,
            optimization=optimization,
            validation=validation,
            metadata=metadata,
            version=version,
            id=str(data.get("id") or "").strip() or None,
        )
        spec.validate()
        return spec

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if data.get("id") is None:
            data.pop("id", None)
        return _deep_copy(data)

    def validate(self) -> list[str]:
        errors: list[str] = []
        warnings: list[str] = []

        if not self.name.strip():
            errors.append("strategy name is required")
        if not self.base_symbol and self.market != "multi":
            warnings.append("base_symbol is empty; strategy will need an explicit signal symbol")

        if not isinstance(self.universe, dict):
            errors.append("universe must be a dict")
        else:
            utype = str(self.universe.get("type") or "list").strip().lower()
            symbols = self.universe.get("symbols") or []
            if utype not in {"list", "screen", "watchlist", "benchmark-relative"}:
                errors.append(f"unsupported universe type: {utype}")
            if symbols is not None and not isinstance(symbols, list):
                errors.append("universe.symbols must be a list")

        if not isinstance(self.indicators, list):
            errors.append("indicators must be a list")
        else:
            for idx, indicator in enumerate(self.indicators):
                if not isinstance(indicator, dict):
                    errors.append(f"indicator[{idx}] must be a dict")
                    continue
                kind = str(indicator.get("kind") or indicator.get("name") or "").strip().lower()
                if kind not in _SUPPORTED_INDICATORS:
                    errors.append(f"unsupported indicator kind: {kind}")
                if kind == "python":
                    errors.append("unsupported indicator kind: python")
                if not str(indicator.get("name") or indicator.get("output") or indicator.get("outputField") or "").strip():
                    warnings.append(f"indicator[{idx}] has no explicit output name; defaulting to kind")

        if not isinstance(self.rules, dict):
            errors.append("rules must be a dict")
        else:
            for key in ("entry", "exit", "trim"):
                if key in self.rules and not isinstance(self.rules.get(key), list):
                    errors.append(f"rules.{key} must be a list")

        sizing_type = str((self.sizing or {}).get("type") or "fixed_pct").strip().lower()
        if sizing_type not in _SUPPORTED_SIZING:
            errors.append(f"unsupported sizing type: {sizing_type}")

        validation_mode = str((self.validation or {}).get("mode") or "single_pass").strip().lower()
        if validation_mode not in _SUPPORTED_VALIDATION:
            errors.append(f"unsupported validation mode: {validation_mode}")

        for key in ("fees_bps", "slippage_bps", "spread_bps"):
            val = (self.costs or {}).get(key, 0)
            try:
                if float(val) < 0:
                    errors.append(f"costs.{key} must be >= 0")
            except (TypeError, ValueError):
                errors.append(f"costs.{key} must be numeric")

        if errors:
            raise ValueError("; ".join(errors))
        return warnings


def validate_strategy_spec(spec: dict[str, Any] | StrategySpec) -> list[str]:
    return StrategySpec.from_dict(spec).validate()


def strategy_spec_hash(spec: dict[str, Any] | StrategySpec) -> str:
    payload = spec.to_dict() if isinstance(spec, StrategySpec) else StrategySpec.from_dict(spec).to_dict()
    blob = json.dumps(_deep_sort(payload), ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:20]


def _deep_copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))


def _deep_sort(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _deep_sort(value[k]) for k in sorted(value)}
    if isinstance(value, list):
        return [_deep_sort(item) for item in value]
    return value


def _ensure_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]
