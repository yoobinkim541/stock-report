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

SUPPORTED_DATA_PROFILES = {"kr_intraday", "global_swing", "extended_us", "generic"}
SUPPORTED_SIGNAL_TYPES = {"rule", "factor", "model", "ensemble"}
SUPPORTED_EXECUTION_PROFILES = {"kr_intraday", "global_swing", "extended_us", "bar"}
SUPPORTED_PROMOTION_ENVIRONMENTS = {"sandbox", "paper", "live"}

_UNSAFE_PLUGIN_NAMES = {"python", "shell", "exec"}
_SUPPORTED_PLUGIN_NAMES = {
    "atr",
    "atr_trailing",
    "bar",
    "bollinger",
    "broker",
    "cross_sectional_rank",
    "drawdown",
    "ema",
    "ensemble",
    "factor",
    "fixed",
    "liquidity",
    "limit",
    "macd",
    "market",
    "market_breadth",
    "momentum",
    "model",
    "quality",
    "regime",
    "rsi",
    "rolling",
    "sma",
    "stop",
    "stop_limit",
    "seasonality",
    "twap",
    "value",
    "volatility",
    "volume_shock",
    "volume_zscore",
    "vwap",
}

_CONTRACT_FIELDS = {
    "data_profile",
    "execution_profile",
    "features",
    "signal",
    "portfolio",
    "execution",
    "promotion",
}
_SUPPORTED_FEATURE_TYPES = _SUPPORTED_PLUGIN_NAMES
_SUPPORTED_EXECUTION_TYPES = _SUPPORTED_PLUGIN_NAMES | SUPPORTED_EXECUTION_PROFILES


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
    data_profile: str = "generic"
    execution_profile: str = "bar"
    features: list[dict[str, Any]] = field(default_factory=list)
    signal: dict[str, Any] = field(default_factory=dict)
    portfolio: dict[str, Any] = field(default_factory=dict)
    execution: dict[str, Any] = field(default_factory=dict)
    promotion: dict[str, Any] = field(default_factory=dict)
    _explicit_contract_fields: frozenset[str] = field(default_factory=frozenset, init=False, repr=False, compare=False)

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
        features = _deep_copy(data.get("features") or [])
        signal = _deep_copy(data.get("signal") or {})
        portfolio = _deep_copy(data.get("portfolio") or {})
        execution = _deep_copy(data.get("execution") or {})
        promotion = _deep_copy(data.get("promotion") or {})
        name = str(data.get("name") or "").strip()
        if not name:
            raise ValueError("strategy name is required")
        market = str(data.get("market") or "us").strip().lower() or "us"
        timeframe = str(data.get("timeframe") or "1d").strip().lower() or "1d"
        base_symbol = str(data.get("base_symbol") or data.get("baseSymbol") or "").strip().upper()
        data_profile = str(data.get("data_profile") or "generic").strip().lower() or "generic"
        execution_profile = str(data.get("execution_profile") or "bar").strip().lower() or "bar"
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
            data_profile=data_profile,
            execution_profile=execution_profile,
            features=features,
            signal=signal,
            portfolio=portfolio,
            execution=execution,
            promotion=promotion,
            version=version,
            id=str(data.get("id") or "").strip() or None,
        )
        spec._explicit_contract_fields = frozenset(_CONTRACT_FIELDS.intersection(data))
        spec.validate()
        return spec

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        explicit_fields = set(data.pop("_explicit_contract_fields", ()))
        if not explicit_fields:
            explicit_fields = self._inferred_contract_fields()
        for field_name in _CONTRACT_FIELDS - explicit_fields:
            data.pop(field_name, None)
        if data.get("id") is None:
            data.pop("id", None)
        return _deep_copy(data)

    def _inferred_contract_fields(self) -> set[str]:
        fields: set[str] = set()
        if self.data_profile != "generic":
            fields.add("data_profile")
        if self.execution_profile != "bar":
            fields.add("execution_profile")
        if self.features:
            fields.add("features")
        if self.signal:
            fields.add("signal")
        if self.portfolio:
            fields.add("portfolio")
        if self.execution:
            fields.add("execution")
        if self.promotion:
            fields.add("promotion")
        return fields

    def validate(self) -> list[str]:
        errors: list[str] = []
        warnings: list[str] = []

        if not self.name.strip():
            errors.append("strategy name is required")
        if not self.base_symbol and self.market != "multi":
            warnings.append("base_symbol is empty; strategy will need an explicit signal symbol")

        data_profile = str(self.data_profile or "").strip().lower()
        if data_profile not in SUPPORTED_DATA_PROFILES:
            errors.append(f"unsupported data profile: {data_profile}")

        execution_profile = str(self.execution_profile or "").strip().lower()
        if execution_profile not in SUPPORTED_EXECUTION_PROFILES:
            errors.append(f"unsupported execution profile: {execution_profile}")

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

        self._validate_new_blocks(errors)

        if errors:
            raise ValueError("; ".join(errors))
        return warnings

    def _validate_new_blocks(self, errors: list[str]) -> None:
        if not isinstance(self.features, list):
            errors.append("features must be a list")
        else:
            for idx, feature in enumerate(self.features):
                if not isinstance(feature, dict):
                    errors.append(f"feature[{idx}] must be a dict")

        if not isinstance(self.signal, dict):
            errors.append("signal must be a dict")
        elif self.signal:
            signal_type = str(self.signal.get("type") or "").strip().lower()
            if signal_type not in SUPPORTED_SIGNAL_TYPES:
                errors.append(f"unsupported signal type: {signal_type}")
            members = self.signal.get("members")
            if members is not None:
                if not isinstance(members, list):
                    errors.append("signal.members must be a list")
                else:
                    for idx, member in enumerate(members):
                        if not isinstance(member, dict):
                            errors.append(f"signal.members[{idx}] must be a dict")
                            continue
                        member_type = str(member.get("type") or "").strip().lower()
                        if member_type and member_type not in SUPPORTED_SIGNAL_TYPES:
                            errors.append(f"unsupported signal type: {member_type}")

        for field_name, block in (("portfolio", self.portfolio), ("execution", self.execution), ("promotion", self.promotion)):
            if not isinstance(block, dict):
                errors.append(f"{field_name} must be a dict")

        if isinstance(self.execution, dict) and self.execution.get("profile") is not None:
            profile = str(self.execution.get("profile") or "").strip().lower()
            if profile not in SUPPORTED_EXECUTION_PROFILES:
                errors.append(f"unsupported execution profile: {profile}")

        if isinstance(self.promotion, dict) and self.promotion.get("environment") is not None:
            environment = str(self.promotion.get("environment") or "").strip().lower()
            if environment not in SUPPORTED_PROMOTION_ENVIRONMENTS:
                errors.append(f"unsupported promotion environment: {environment}")

        for block_name, block in (("signal", self.signal), ("features", self.features), ("execution", self.execution)):
            _validate_plugins(block, block_name, errors)


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


def _validate_plugins(value: Any, path: str, errors: list[str], block_name: str | None = None) -> None:
    block_name = block_name or path
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key).strip().lower()
            if key_text == "plugin":
                plugin = str(child or "").strip().lower()
                if plugin in _UNSAFE_PLUGIN_NAMES:
                    errors.append(f"unsupported plugin: {plugin} at {path}")
                elif plugin not in _SUPPORTED_PLUGIN_NAMES:
                    errors.append(f"unsupported plugin: {plugin} at {path}")
            elif key_text == "type":
                type_name = str(child or "").strip().lower()
                if type_name in _UNSAFE_PLUGIN_NAMES:
                    errors.append(f"unsupported plugin: {type_name} at {path}")
                elif block_name == "signal" and type_name not in SUPPORTED_SIGNAL_TYPES:
                    errors.append(f"unsupported signal type: {type_name}")
                elif block_name == "features" and type_name not in _SUPPORTED_FEATURE_TYPES:
                    errors.append(f"unsupported feature type: {type_name}")
                elif block_name == "execution" and type_name not in _SUPPORTED_EXECUTION_TYPES:
                    errors.append(f"unsupported execution type: {type_name}")
            _validate_plugins(child, f"{path}.{key}", errors, block_name)
    elif isinstance(value, list):
        for idx, child in enumerate(value):
            _validate_plugins(child, f"{path}[{idx}]", errors, block_name)
