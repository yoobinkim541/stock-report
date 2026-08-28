"""Deterministic strategy signal panels and registered signal providers."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

import numpy as np
import pandas as pd

from .registry import SignalProvider, get_model, get_signal_provider, register_signal_provider


@dataclass(slots=True)
class SignalPanel:
    """Wide signal data keyed by timestamp rows and symbol columns.

    ``to_frame`` exposes the same data in the public ``(timestamp, symbol)``
    long-key form while the wide frames keep the existing strategy API easy to
    inspect and align with price panels.
    """

    provider: str
    score: pd.DataFrame
    confidence: pd.DataFrame
    reason: pd.DataFrame = field(default_factory=pd.DataFrame)
    as_of: pd.DataFrame = field(default_factory=pd.DataFrame)
    feature_version: pd.DataFrame = field(default_factory=pd.DataFrame)
    model_version: pd.DataFrame = field(default_factory=pd.DataFrame)
    diagnostics: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.provider = str(self.provider or "").strip()
        self.diagnostics = [str(item) for item in self.diagnostics if str(item).strip()]
        self.score = _normalise_frame(self.score, numeric=True)
        nonfinite_score = self.score.notna() & ~np.isfinite(self.score)
        if nonfinite_score.any().any():
            self.score = self.score.mask(nonfinite_score)
            self.diagnostics.append("score contains non-finite values")
        self.confidence = _metadata_frame(self.confidence, self.score, numeric=True, default=np.nan)
        invalid_confidence = self.confidence.notna() & (
            ~np.isfinite(self.confidence) | (self.confidence < 0) | (self.confidence > 1)
        )
        if invalid_confidence.any().any():
            raise ValueError("confidence must be between 0 and 1")
        self.reason = _metadata_frame(self.reason, self.score, numeric=False, default="")
        self.as_of = _metadata_frame(self.as_of, self.score, numeric=False, default=None)
        self.feature_version = _metadata_frame(self.feature_version, self.score, numeric=False, default="")
        self.model_version = _metadata_frame(self.model_version, self.score, numeric=False, default="")

    @classmethod
    def from_score(
        cls,
        provider: str,
        scores: pd.DataFrame,
        confidence: float | pd.DataFrame,
    ) -> "SignalPanel":
        """Build a panel from a wide score frame and scalar or wide confidence."""

        score = _normalise_frame(scores, numeric=True)
        confidence_frame = _metadata_frame(confidence, score, numeric=True, default=np.nan)
        invalid_confidence = confidence_frame.notna() & ~np.isfinite(confidence_frame)
        if invalid_confidence.any().any():
            raise ValueError("confidence must contain finite values")
        if ((confidence_frame.dropna(how="all") < 0) | (confidence_frame.dropna(how="all") > 1)).any().any():
            raise ValueError("confidence must be between 0 and 1")
        as_of = _as_of_frame(score)
        reason = pd.DataFrame(
            np.where(score.notna(), str(provider).strip(), "invalid score"),
            index=score.index,
            columns=score.columns,
        )
        return cls(
            provider=str(provider or "").strip(),
            score=score,
            confidence=confidence_frame,
            reason=reason,
            as_of=as_of,
            feature_version=_constant_frame(score, _feature_version_for_provider(provider)),
        )

    @classmethod
    def invalid(cls, provider: str, diagnostic: str) -> "SignalPanel":
        """Create an empty panel that carries a user-visible failure reason."""

        return cls(provider=str(provider or "").strip(), score=pd.DataFrame(), confidence=pd.DataFrame(), diagnostics=[diagnostic])

    @property
    def valid_mask(self) -> pd.DataFrame:
        """Rows whose score and confidence are both usable for allocation."""

        if self.score.empty:
            return pd.DataFrame(index=self.score.index, columns=self.score.columns, dtype=bool)
        valid_score = self.score.notna() & np.isfinite(self.score)
        valid_confidence = self.confidence.notna() & np.isfinite(self.confidence)
        valid_confidence &= (self.confidence >= 0) & (self.confidence <= 1)
        return valid_score & valid_confidence

    @property
    def has_valid_signals(self) -> bool:
        return not self.score.empty and self.valid_mask.any().any()

    @property
    def has_valid_scores(self) -> bool:
        """Backward-compatible alias for the confidence-aware validity check."""

        return self.has_valid_signals

    def to_frame(self) -> pd.DataFrame:
        """Return score metadata keyed by ``(timestamp, symbol)``."""

        fields = {
            "score": self.score,
            "confidence": self.confidence,
            "reason": self.reason,
            "as_of": self.as_of,
            "feature_version": self.feature_version,
            "model_version": self.model_version,
        }
        if self.score.empty:
            return pd.DataFrame(columns=[*fields])
        pieces = []
        for name, frame in fields.items():
            series = frame.stack(future_stack=True).rename(name)
            pieces.append(series)
        output = pd.concat(pieces, axis=1)
        output.index.names = ["timestamp", "symbol"]
        return output

    def to_dict(self) -> dict[str, Any]:
        """Return a report-friendly mapping without losing panel metadata."""

        return {
            "provider": self.provider,
            "score": self.score.copy(),
            "confidence": self.confidence.copy(),
            "reason": self.reason.copy(),
            "as_of": self.as_of.copy(),
            "feature_version": self.feature_version.copy(),
            "model_version": self.model_version.copy(),
            "diagnostics": list(self.diagnostics),
        }


def combine_signal_panels(
    panels: list[SignalPanel],
    weights: list[float],
    *,
    min_confidence: float = 0.0,
) -> SignalPanel:
    """Combine panels deterministically using the supplied weighted average."""

    if not panels:
        return SignalPanel.invalid("ensemble", "ensemble requires at least one signal panel")
    if len(panels) != len(weights):
        raise ValueError("panels and weights must have the same length")
    numeric_weights = [float(weight) for weight in weights]
    if any(not np.isfinite(weight) or weight < 0 for weight in numeric_weights):
        raise ValueError("ensemble weights must be finite and non-negative")
    total_weight = sum(numeric_weights)
    if total_weight <= 0:
        raise ValueError("ensemble weights must sum to a positive value")
    numeric_weights = [weight / total_weight for weight in numeric_weights]
    threshold = float(min_confidence)
    if not 0 <= threshold <= 1:
        raise ValueError("min_confidence must be between 0 and 1")

    index = _union_indexes([panel.score.index for panel in panels])
    columns = _union_columns([panel.score.columns for panel in panels])
    score = pd.DataFrame(0.0, index=index, columns=columns)
    confidence = pd.DataFrame(0.0, index=index, columns=columns)
    reason = pd.DataFrame("", index=index, columns=columns)
    as_of = pd.DataFrame(None, index=index, columns=columns, dtype=object)
    feature_version = pd.DataFrame("", index=index, columns=columns)
    model_version = pd.DataFrame("", index=index, columns=columns)
    diagnostics: list[str] = []
    usable = pd.DataFrame(False, index=index, columns=columns)

    for position, (panel, weight) in enumerate(zip(panels, numeric_weights)):
        if panel.diagnostics:
            diagnostics.extend(f"ensemble member {position} ({panel.provider}): {item}" for item in panel.diagnostics)
        member_score = panel.score.reindex(index=index, columns=columns)
        member_confidence = panel.confidence.reindex(index=index, columns=columns)
        valid_score = member_score.notna() & np.isfinite(member_score)
        valid_confidence = member_confidence.notna() & np.isfinite(member_confidence)
        valid_confidence &= (member_confidence >= 0) & (member_confidence <= 1)
        valid_member = valid_score & valid_confidence
        if (valid_score & ~valid_confidence).any().any() and weight > 0:
            diagnostics.append(f"ensemble member {position} ({panel.provider}) has invalid confidence rows")
        if not valid_member.any().any() and weight > 0:
            diagnostics.append(f"ensemble member {position} ({panel.provider}) has no valid scores")
        usable |= valid_member
        score = score.add(member_score.where(valid_member, 0.0) * weight, fill_value=0.0)
        confidence = confidence.add(member_confidence.where(valid_member, 0.0) * weight, fill_value=0.0)
        for dt in index:
            for symbol in columns:
                if not valid_member.at[dt, symbol]:
                    continue
                label = str(panel.reason.at[dt, symbol] or "").strip()
                if label:
                    prior = str(reason.at[dt, symbol] or "").strip()
                    reason.at[dt, symbol] = "; ".join(item for item in (prior, label) if item)
                member_as_of = panel.as_of.at[dt, symbol]
                if not pd.isna(member_as_of) and (
                    pd.isna(as_of.at[dt, symbol]) or _timestamp_key(member_as_of) > _timestamp_key(as_of.at[dt, symbol])
                ):
                    as_of.at[dt, symbol] = member_as_of
                feature_version.at[dt, symbol] = _append_provenance(
                    feature_version.at[dt, symbol], panel.feature_version.at[dt, symbol]
                )
                model_version.at[dt, symbol] = _append_provenance(
                    model_version.at[dt, symbol], panel.model_version.at[dt, symbol]
                )

    score = score.where(usable)
    gated = usable & (confidence < threshold)
    if gated.any().any():
        reason = reason.mask(gated, "minimum confidence gate")
        score = score.mask(gated)
        confidence = confidence.mask(gated, 0.0)
    return SignalPanel(
        provider="ensemble",
        score=score,
        confidence=confidence,
        reason=reason,
        as_of=as_of,
        feature_version=feature_version,
        model_version=model_version,
        diagnostics=diagnostics,
    )


def build_signal_panel(strategy: Any, compiled: Any) -> SignalPanel:
    """Resolve a declarative signal spec through the provider registry."""

    signal = dict(strategy.signal or {})
    if not signal:
        provider_name = "rule"
    else:
        signal_type = str(signal.get("type") or "").strip().lower()
        provider_name = str(signal.get("plugin") or signal.get("ref") or signal_type).strip().lower()
        if signal_type in {"rule", "model", "ensemble"}:
            provider_name = signal_type
    try:
        provider = get_signal_provider(provider_name)
    except LookupError as exc:
        return SignalPanel.invalid(provider_name, str(exc))
    try:
        panel = provider(strategy, compiled)
    except Exception as exc:
        return SignalPanel.invalid(provider_name, f"{provider_name} provider failed: {exc}")
    if not isinstance(panel, SignalPanel):
        return SignalPanel.invalid(provider_name, f"{provider_name} provider returned an invalid panel")
    return panel


def _rule_provider(strategy: Any, compiled: Any) -> SignalPanel:
    from .engine import _first_matching_rule

    index, symbols = _panel_shape(compiled)
    scores = pd.DataFrame(0.0, index=index, columns=symbols)
    confidence = pd.DataFrame(0.0, index=index, columns=symbols)
    reason = pd.DataFrame("", index=index, columns=symbols)
    for symbol in symbols:
        for dt in index:
            exit_rule = _matching_rule(strategy, compiled, _first_matching_rule, "exit", symbol, dt)
            trim_rule = _matching_rule(strategy, compiled, _first_matching_rule, "trim", symbol, dt)
            entry_rule = _matching_rule(strategy, compiled, _first_matching_rule, "entry", symbol, dt)
            selected = exit_rule or trim_rule or entry_rule
            if selected is None:
                continue
            if selected is exit_rule:
                scores.at[dt, symbol] = -1.0
                bucket = "exit"
            elif selected is trim_rule:
                scores.at[dt, symbol] = 0.0
                bucket = "trim"
            else:
                scores.at[dt, symbol] = 1.0
                bucket = "entry"
            reason.at[dt, symbol] = str(selected.get("label") or bucket)
    available = pd.DataFrame(False, index=index, columns=symbols)
    for symbol in symbols:
        close = compiled.contexts[symbol].get("close")
        if close is not None:
            available[symbol] = close.reindex(index).notna()
    confidence = confidence.mask(available, 1.0)
    return SignalPanel(
        "rule", scores, confidence, reason=reason, as_of=_as_of_frame(scores),
        feature_version=_constant_frame(scores, _feature_version_for_provider("rule")),
    )


def _momentum_provider(strategy: Any, compiled: Any) -> SignalPanel:
    index, symbols = _panel_shape(compiled)
    lookback = _positive_int((strategy.signal or {}).get("lookback"), default=1)
    scores = pd.DataFrame(index=index, columns=symbols, dtype="float64")
    for symbol in symbols:
        close = pd.to_numeric(compiled.contexts[symbol]["close"], errors="coerce").reindex(index)
        scores[symbol] = close / close.shift(lookback) - 1.0
    confidence = scores.notna().astype(float)
    reason = _reason_frame(scores, "momentum", "warmup")
    return SignalPanel(
        "momentum", scores, confidence, reason=reason, as_of=_as_of_frame(scores),
        feature_version=_constant_frame(scores, _feature_version_for_provider("momentum")),
    )


def _volatility_provider(strategy: Any, compiled: Any) -> SignalPanel:
    index, symbols = _panel_shape(compiled)
    config = strategy.signal or {}
    lookback = _positive_int(config.get("lookback") or config.get("window"), default=20)
    scores = pd.DataFrame(index=index, columns=symbols, dtype="float64")
    diagnostics: list[str] = []
    for symbol in symbols:
        close = pd.to_numeric(compiled.contexts[symbol]["close"], errors="coerce").reindex(index)
        volatility = close.pct_change().rolling(lookback, min_periods=lookback).std()
        zero_rows = volatility.eq(0)
        if zero_rows.any():
            diagnostics.append(f"{symbol} zero volatility rows are invalid")
        scores[symbol] = (1.0 / volatility.where(volatility > 0)).replace([np.inf, -np.inf], np.nan)
    confidence = scores.notna().astype(float)
    reason = _reason_frame(scores, "inverse_volatility", "warmup or zero volatility")
    return SignalPanel(
        "volatility", scores, confidence, reason=reason, as_of=_as_of_frame(scores),
        feature_version=_constant_frame(scores, _feature_version_for_provider("volatility")),
        diagnostics=diagnostics,
    )


def _cross_sectional_rank_provider(strategy: Any, compiled: Any) -> SignalPanel:
    index, symbols = _panel_shape(compiled)
    config = strategy.signal or {}
    source = str(config.get("source") or config.get("field") or "momentum").strip().lower()
    source_scores = _source_scores(source, strategy, compiled, index, symbols)
    ranked = source_scores.rank(axis=1, method="average", pct=False)
    counts = source_scores.notna().sum(axis=1).replace(0, np.nan)
    scores = (ranked.sub(1.0).div(counts.sub(1.0).replace(0, np.nan), axis=0)).where(source_scores.notna())
    scores = scores.mask(source_scores.notna() & counts.eq(1).to_numpy()[:, None], 0.0)
    confidence = scores.notna().astype(float)
    return SignalPanel(
        "cross_sectional_rank", scores, confidence,
        reason=_reason_frame(scores, "cross_sectional_rank", "warmup"), as_of=_as_of_frame(scores),
        feature_version=_constant_frame(scores, _feature_version_for_provider("cross_sectional_rank")),
    )


def _model_provider(strategy: Any, compiled: Any) -> SignalPanel:
    config = strategy.signal or {}
    model_id = str(config.get("ref") or config.get("model_id") or config.get("name") or "").strip().lower()
    registered = get_model(model_id) if model_id else None
    if registered is None:
        raise LookupError(f"model not registered: {model_id or '<empty>'}")
    features = _feature_frame(strategy, compiled)
    from ml.models import predict_with_metadata

    output = predict_with_metadata(registered.model, features, {**registered.metadata, "model_id": model_id})
    predictions = _long_values_to_panel(output["predictions"], features, compiled)
    confidence = _long_values_to_panel(output["confidence"], features, compiled)
    index, symbols = _panel_shape(compiled)
    score = predictions.reindex(index=index, columns=symbols)
    confidence = confidence.reindex(index=index, columns=symbols)
    as_of = _metadata_to_panel(output["as_of"], score, default=None).reindex(index=index, columns=symbols)
    diagnostics: list[str] = []
    diagnostics.extend(str(value) for value in output.get("diagnostics", []) if str(value).strip())
    valid_score = score.notna() & np.isfinite(score)
    valid_confidence = confidence.notna() & np.isfinite(confidence)
    valid_confidence &= (confidence >= 0) & (confidence <= 1)
    invalid_score = ~valid_score
    invalid_confidence = ~valid_confidence
    invalid_rows = invalid_score | invalid_confidence
    if invalid_confidence.any().any():
        diagnostics.extend(_row_diagnostics("model confidence is missing or invalid", invalid_confidence))
    if invalid_score.any().any():
        diagnostics.extend(_row_diagnostics("model prediction is missing or invalid", invalid_score))
    if invalid_rows.any().any():
        score = score.mask(invalid_rows)
        confidence = confidence.mask(invalid_rows)
    invalid_as_of = score.notna() & ~_as_of_valid_mask(score, as_of)
    if invalid_as_of.any().any():
        diagnostics.extend(_row_diagnostics("model as_of is later than or invalid for signal timestamp", invalid_as_of))
        score = score.mask(invalid_as_of)
        confidence = confidence.mask(invalid_as_of)
    confidence = confidence.where(score.notna())
    reason = _reason_frame(score, f"model:{model_id}", "invalid prediction")
    if invalid_confidence.any().any():
        reason = reason.mask(invalid_confidence, "confidence missing or invalid")
    if invalid_as_of.any().any():
        reason = reason.mask(invalid_as_of, "as_of later than signal timestamp")
    feature_version = _constant_frame(score, output["feature_version"])
    model_version = _constant_frame(score, output["model_version"])
    return SignalPanel(
        "model", score.reindex(index=index, columns=symbols), confidence.reindex(index=index, columns=symbols),
        reason=reason.reindex(index=index, columns=symbols), as_of=as_of.reindex(index=index, columns=symbols),
        feature_version=feature_version.reindex(index=index, columns=symbols),
        model_version=model_version.reindex(index=index, columns=symbols),
        diagnostics=diagnostics,
    )


def _ensemble_provider(strategy: Any, compiled: Any) -> SignalPanel:
    config = strategy.signal or {}
    members = config.get("members") or []
    if not isinstance(members, list) or not members:
        raise ValueError("ensemble requires non-empty signal.members")
    panels: list[SignalPanel] = []
    weights: list[float] = []
    for member in members:
        if not isinstance(member, dict):
            panels.append(SignalPanel.invalid("member", "ensemble member must be a dict"))
            weights.append(0.0)
            continue
        member_spec = replace(strategy, signal=dict(member))
        panels.append(build_signal_panel(member_spec, compiled))
        weights.append(float(member["weight"]) if "weight" in member else 1.0)
    config_weights = config.get("weights")
    if isinstance(config_weights, list) and len(config_weights) == len(panels):
        weights = [float(value) for value in config_weights]
    return combine_signal_panels(panels, weights, min_confidence=float(config.get("min_confidence") or 0.0))


def _matching_rule(strategy: Any, compiled: Any, matcher: Any, bucket: str, symbol: str, dt: Any) -> dict[str, Any] | None:
    signal = strategy.signal or {}
    ref = str(signal.get("ref") or "").strip().lower()
    rules = list((strategy.rules or {}).get(bucket) or [])
    if ref:
        rules = [rule for rule in rules if isinstance(rule, dict) and _rule_identity(rule) == ref]
    return matcher(rules, compiled.contexts, symbol, dt)


def _rule_identity(rule: dict[str, Any]) -> str:
    return str(rule.get("ruleId") or rule.get("id") or rule.get("label") or rule.get("name") or "").strip().lower()


def _source_scores(source: str, strategy: Any, compiled: Any, index: pd.Index, symbols: list[str]) -> pd.DataFrame:
    if source in {"close", "price"}:
        return pd.DataFrame({symbol: compiled.contexts[symbol]["close"].reindex(index) for symbol in symbols}, index=index)
    if source in {"momentum", "mom"}:
        lookback = _positive_int((strategy.signal or {}).get("lookback"), default=1)
        return pd.DataFrame({symbol: compiled.contexts[symbol]["close"].reindex(index).pct_change(lookback) for symbol in symbols}, index=index)
    return pd.DataFrame({symbol: compiled.contexts[symbol].get(source, pd.Series(index=index, dtype=float)).reindex(index) for symbol in symbols}, index=index)


def _feature_frame(strategy: Any, compiled: Any) -> pd.DataFrame:
    index, symbols = _panel_shape(compiled)
    feature_defs = strategy.features or []
    columns: dict[str, pd.Series] = {}
    if not feature_defs:
        feature_defs = [{"plugin": "close", "name": "close"}]
    for feature in feature_defs:
        if not isinstance(feature, dict):
            raise ValueError("feature definition must be a dict")
        name = str(feature.get("name") or feature.get("output") or feature.get("as") or feature.get("plugin") or "").strip()
        plugin = str(feature.get("plugin") or feature.get("kind") or name).strip().lower()
        if not name:
            raise ValueError("feature name is required")
        if plugin in {"close", "price"}:
            for symbol in symbols:
                columns[f"{symbol}:{name}"] = compiled.contexts[symbol]["close"].reindex(index)
        elif plugin in {"momentum", "volatility"}:
            lookback = _positive_int(feature.get("lookback") or feature.get("window"), default=1 if plugin == "momentum" else 20)
            for symbol in symbols:
                close = compiled.contexts[symbol]["close"].reindex(index)
                value = close.pct_change(lookback) if plugin == "momentum" else close.pct_change().rolling(lookback, min_periods=lookback).std()
                columns[f"{symbol}:{name}"] = value
        else:
            for symbol in symbols:
                value = compiled.contexts[symbol].get(plugin)
                if value is None:
                    raise ValueError(f"feature field not available: {plugin}")
                columns[f"{symbol}:{name}"] = value.reindex(index)
    rows = []
    for dt in index:
        for symbol in symbols:
            row = {name: series.at[dt] for name, series in columns.items() if name.startswith(f"{symbol}:")}
            row = {name.split(":", 1)[1]: value for name, value in row.items()}
            row["__timestamp"] = dt
            row["__symbol"] = symbol
            rows.append(row)
    frame = pd.DataFrame(rows).set_index(["__timestamp", "__symbol"])
    frame.index.names = ["timestamp", "symbol"]
    source_attrs = getattr(compiled.store, "attrs", {})
    if not isinstance(source_attrs, dict):
        source_attrs = {}
    close_attrs = getattr(compiled.prices, "attrs", {})
    if not isinstance(close_attrs, dict):
        close_attrs = {}
    for key in ("data_snapshot", "data_snapshots", "source_coverage", "provenance"):
        value = source_attrs.get(key, close_attrs.get(key))
        if value is not None:
            frame.attrs[key] = value
    return frame


def _long_values_to_panel(values: Any, features: pd.DataFrame, compiled: Any) -> pd.DataFrame:
    index, symbols = _panel_shape(compiled)
    if isinstance(values, pd.DataFrame):
        if set(values.columns) == set(symbols):
            return values.reindex(index=index, columns=symbols).apply(pd.to_numeric, errors="coerce")
        values = values.squeeze(axis=1) if values.shape[1] == 1 else values.iloc[:, 0]
    if isinstance(values, pd.Series):
        series = pd.to_numeric(values, errors="coerce")
        if isinstance(series.index, pd.MultiIndex):
            return _multi_index_values_to_panel(series, index, symbols, numeric=True)
        if series.index.equals(features.index):
            series = series.to_numpy()
        else:
            values = series.to_numpy()
    else:
        values = np.asarray(values, dtype=float).ravel()
    array = np.asarray(values, dtype=float).ravel()
    if len(array) == 1 and len(features) > 1:
        array = np.repeat(array, len(features))
    if len(array) != len(features):
        raise ValueError("model prediction length does not match feature rows")
    return pd.Series(array, index=features.index).unstack("symbol").reindex(index=index, columns=symbols)


def _metadata_to_panel(value: Any, score: pd.DataFrame, *, default: Any) -> pd.DataFrame:
    if isinstance(value, pd.DataFrame):
        return value.reindex(index=score.index, columns=score.columns)
    if isinstance(value, pd.Series):
        if isinstance(value.index, pd.MultiIndex):
            return _multi_index_values_to_panel(value, score.index, list(score.columns), numeric=False)
        if value.index.equals(score.index):
            return pd.DataFrame({column: value for column in score.columns}, index=score.index)
    return pd.DataFrame(value if value is not None else default, index=score.index, columns=score.columns)


def _multi_index_values_to_panel(
    values: pd.Series,
    index: pd.Index,
    symbols: list[Any],
    *,
    numeric: bool,
) -> pd.DataFrame:
    result = pd.DataFrame(index=index, columns=symbols, dtype="float64" if numeric else object)
    for key, value in values.items():
        if not isinstance(key, tuple) or len(key) < 2:
            continue
        timestamp = _matching_timestamp(index, key[0])
        symbol = _matching_symbol(symbols, key[1])
        if timestamp is None or symbol is None:
            continue
        result.at[timestamp, symbol] = pd.to_numeric(value, errors="coerce") if numeric else value
    return result


def _matching_timestamp(index: pd.Index, value: Any) -> Any | None:
    wanted = _timestamp_key(value)
    if pd.isna(wanted):
        return None
    for candidate in index:
        if _timestamp_key(candidate) == wanted:
            return candidate
    return None


def _matching_symbol(symbols: list[Any], value: Any) -> Any | None:
    for symbol in symbols:
        if str(symbol) == str(value):
            return symbol
    return None


def _as_of_valid_mask(score: pd.DataFrame, as_of: pd.DataFrame) -> pd.DataFrame:
    valid = pd.DataFrame(False, index=score.index, columns=score.columns)
    for dt in score.index:
        signal_timestamp = _timestamp_key(dt)
        if pd.isna(signal_timestamp):
            continue
        for symbol in score.columns:
            value = _timestamp_key(as_of.at[dt, symbol])
            valid.at[dt, symbol] = not pd.isna(value) and value <= signal_timestamp
    return valid


def _row_diagnostics(prefix: str, mask: pd.DataFrame) -> list[str]:
    rows = mask.stack(future_stack=True)
    return [f"{prefix}: {symbol} @ {pd.Timestamp(timestamp).isoformat()}" for (timestamp, symbol), value in rows.items() if bool(value)]


def _timestamp_key(value: Any) -> pd.Timestamp:
    try:
        timestamp = pd.Timestamp(value)
    except (TypeError, ValueError):
        return pd.NaT
    if pd.isna(timestamp):
        return pd.NaT
    if timestamp.tzinfo is None:
        return timestamp.tz_localize("UTC")
    return timestamp.tz_convert("UTC")


def _append_provenance(existing: Any, value: Any) -> str:
    existing_text = "" if value_is_missing(existing) else str(existing).strip()
    value_text = "" if value_is_missing(value) else str(value).strip()
    if not value_text:
        return existing_text
    parts = [part for part in existing_text.split("|") if part]
    if value_text not in parts:
        parts.append(value_text)
    return "|".join(parts)


def value_is_missing(value: Any) -> bool:
    try:
        result = pd.isna(value)
    except (TypeError, ValueError):
        return False
    return bool(result) if isinstance(result, (bool, np.bool_)) else False


def _feature_version_for_provider(provider: Any) -> str:
    name = str(provider or "").strip().lower()
    return {
        "rule": "rule-v1",
        "momentum": "momentum-v1",
        "volatility": "volatility-v1",
        "cross_sectional_rank": "cross_sectional_rank-v1",
    }.get(name, f"{name}-v1" if name else "")


def _panel_shape(compiled: Any) -> tuple[pd.Index, list[str]]:
    index = pd.Index(compiled.prices.index)
    symbols = list(compiled.contexts) or [str(column) for column in compiled.prices.columns]
    return index, symbols


def _normalise_frame(value: Any, *, numeric: bool) -> pd.DataFrame:
    if value is None:
        return pd.DataFrame()
    if not isinstance(value, pd.DataFrame):
        raise TypeError("signal scores and metadata must be pandas DataFrames")
    frame = value.copy()
    if frame.index.has_duplicates:
        raise ValueError("signal timestamp index must be unique")
    if numeric:
        frame = frame.apply(pd.to_numeric, errors="coerce")
    return frame


def _metadata_frame(value: Any, score: pd.DataFrame, *, numeric: bool, default: Any) -> pd.DataFrame:
    if isinstance(value, pd.DataFrame):
        frame = value.reindex(index=score.index, columns=score.columns).copy() if not value.empty else None
    elif value is None:
        frame = None
    else:
        frame = pd.DataFrame(value, index=score.index, columns=score.columns)
    if frame is None:
        frame = pd.DataFrame(default, index=score.index, columns=score.columns)
    if numeric:
        frame = frame.apply(pd.to_numeric, errors="coerce")
    return frame


def _as_of_frame(score: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame(
        [[dt for _ in score.columns] for dt in score.index],
        index=score.index,
        columns=score.columns,
    )


def _constant_frame(score: pd.DataFrame, value: Any) -> pd.DataFrame:
    return pd.DataFrame(value, index=score.index, columns=score.columns)


def _reason_frame(score: pd.DataFrame, valid: str, invalid: str) -> pd.DataFrame:
    return pd.DataFrame(np.where(score.notna(), valid, invalid), index=score.index, columns=score.columns)


def _positive_int(value: Any, *, default: int) -> int:
    try:
        parsed = int(value or default)
    except (TypeError, ValueError):
        parsed = default
    return max(1, parsed)


def _union_indexes(indexes: list[pd.Index]) -> pd.Index:
    result = pd.Index([])
    for index in indexes:
        result = result.union(index)
    return result


def _union_columns(columns: list[pd.Index]) -> pd.Index:
    result = pd.Index([])
    for column in columns:
        result = result.union(column, sort=False)
    return result


register_signal_provider("rule", _rule_provider)
register_signal_provider("momentum", _momentum_provider)
register_signal_provider("volatility", _volatility_provider)
register_signal_provider("cross_sectional_rank", _cross_sectional_rank_provider)
register_signal_provider("model", _model_provider)
register_signal_provider("ensemble", _ensemble_provider)


__all__ = [
    "SignalPanel",
    "SignalProvider",
    "build_signal_panel",
    "combine_signal_panels",
]
