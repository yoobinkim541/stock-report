from __future__ import annotations

from dataclasses import dataclass, field
from math import sqrt
from typing import Any

import pandas as pd


MATRIX_DSL_LANGUAGE = "portfolio-matrix-dsl"


@dataclass
class MatrixDslRun:
    ok: bool
    equity: pd.DataFrame = field(default_factory=pd.DataFrame)
    metrics: pd.DataFrame = field(default_factory=pd.DataFrame)
    trades: list[dict] = field(default_factory=list)
    matrix: list[dict] = field(default_factory=list)
    note: str = ""
    error: str = ""


def rsi_cash_program(buy_rsi: int, sell_rsi: int, *, period: int = 14) -> list[dict]:
    return [
        {"op": "indicator", "name": "rsi", "period": period, "field": "close", "outputField": "rsi"},
        {
            "op": "rule",
            "when": f"rsi <= {int(buy_rsi)}",
            "emit": {"field": "target_weight", "value": 1, "ruleId": "buy_oversold"},
        },
        {
            "op": "rule",
            "when": f"rsi >= {int(sell_rsi)}",
            "emit": {"field": "target_weight", "value": 0, "ruleId": "sell_overbought"},
        },
    ]


def run_portfolio_matrix_dsl(
    close: pd.DataFrame,
    weights: dict[str, float],
    *,
    signal_symbol: str,
    program: list[dict],
    label: str = "Matrix DSL",
) -> MatrixDslRun:
    close = _clean_close(close)
    signal_symbol = str(signal_symbol or "").upper().strip()
    weights = _normalize_weights(weights)
    market_symbols = [symbol for symbol in weights if symbol != "CASH"]
    available_symbols = [symbol for symbol in market_symbols if symbol in close.columns]
    if close.empty:
        return MatrixDslRun(ok=False, error="시세를 불러오지 못했습니다.")
    if signal_symbol not in close.columns:
        return MatrixDslRun(ok=False, error=f"{signal_symbol} 신호 기준 시세가 없습니다.")
    if not available_symbols:
        return MatrixDslRun(ok=False, error="사용 가능한 포트폴리오 자산 시세가 없습니다.")

    returns = close[available_symbols].pct_change().fillna(0.0)
    portfolio_return = pd.Series(0.0, index=returns.index)
    valid_weight = sum(weights.get(symbol, 0.0) for symbol in available_symbols)
    for symbol in available_symbols:
        effective_weight = weights.get(symbol, 0.0)
        if valid_weight > 0:
            effective_weight += max(0.0, 1.0 - weights.get("CASH", 0.0) - valid_weight) * (
                weights.get(symbol, 0.0) / valid_weight
            )
        portfolio_return = portfolio_return.add(returns[symbol].fillna(0.0) * effective_weight, fill_value=0.0)

    buy_hold = (1 + portfolio_return).cumprod() * 100.0
    signals = _compile_signal_series(close[signal_symbol], buy_hold, program)
    if signals.get("error"):
        return MatrixDslRun(ok=False, error=signals["error"])

    target = signals["target"].reindex(portfolio_return.index).ffill().fillna(1.0).clip(lower=0.0, upper=1.0)
    exposure = target.shift(1).fillna(1.0)
    strategy_return = portfolio_return * exposure
    strategy = (1 + strategy_return).cumprod() * 100.0

    equity = pd.DataFrame(
        {
            "date": portfolio_return.index,
            "Buy & Hold": buy_hold,
            label: strategy,
            "노출": exposure * 100.0,
            "target_weight": target * 100.0,
            "RSI": signals.get("rsi"),
        }
    ).dropna(subset=["Buy & Hold", label])
    if len(equity) < 20:
        return MatrixDslRun(ok=False, error="백테스트에 필요한 데이터가 부족합니다.")

    trades = _trade_log(target, signals["reasons"])
    metrics = pd.DataFrame(
        [
            {"전략": "Buy & Hold", **standard_metrics(equity.set_index("date")["Buy & Hold"])},
            {"전략": label, **standard_metrics(equity.set_index("date")[label], equity.set_index("date")["Buy & Hold"])},
        ]
    )
    matrix = _backtest_matrix(equity, ("Buy & Hold", label))
    note = (
        f"{MATRIX_DSL_LANGUAGE} · {signal_symbol} · 노출일 {equity['노출'].mean():.0f}% · "
        f"거래 {len(trades)}회 · 지표 {', '.join(_program_summary(program))}"
    )
    return MatrixDslRun(ok=True, equity=equity, metrics=metrics, trades=trades, matrix=matrix, note=note)


def _clean_close(close: pd.DataFrame) -> pd.DataFrame:
    if close is None or close.empty:
        return pd.DataFrame()
    frame = close.copy()
    frame.columns = [str(col).upper().strip() for col in frame.columns]
    frame.index = pd.to_datetime(frame.index)
    return frame.sort_index().ffill().dropna(how="all")


def _normalize_weights(weights: dict[str, float]) -> dict[str, float]:
    rows = {str(k).upper().strip(): max(0.0, _number(v, 0.0)) for k, v in (weights or {}).items()}
    total = sum(rows.values())
    if total > 1.0001:
        rows = {symbol: weight / total for symbol, weight in rows.items()}
    return rows


def _compile_signal_series(signal_close: pd.Series, buy_hold: pd.Series, program: list[dict]) -> dict[str, Any]:
    fields: dict[str, pd.Series] = {
        "close": pd.to_numeric(signal_close, errors="coerce"),
        "portfolio": pd.to_numeric(buy_hold, errors="coerce"),
        "nav": pd.to_numeric(buy_hold, errors="coerce"),
    }
    target = pd.Series(float("nan"), index=signal_close.index, dtype="float64")
    reasons: dict[pd.Timestamp, str] = {}
    has_rule = False

    for raw in program or []:
        if not isinstance(raw, dict):
            continue
        op = str(raw.get("op") or raw.get("type") or "").lower().strip()
        if op == "indicator":
            name = str(raw.get("name") or raw.get("indicator") or "").lower().strip()
            field = str(raw.get("field") or "close").strip()
            output = str(raw.get("outputField") or raw.get("as") or name).strip()
            period = max(1, min(400, int(_number(raw.get("period") or raw.get("length"), 14))))
            if name == "rsi":
                fields[output] = rsi_series(fields.get(field, fields["close"]), period)
            elif name == "ema":
                fields[output] = fields.get(field, fields["close"]).ewm(span=period, adjust=False, min_periods=period).mean()
            else:
                return {"error": f"지원하지 않는 DSL indicator입니다: {name}"}
        elif op == "rolling":
            field = str(raw.get("field") or "close").strip()
            output = str(raw.get("outputField") or raw.get("as") or f"{field}_mean").strip()
            period = max(1, min(400, int(_number(raw.get("period") or raw.get("window"), 20))))
            method = str(raw.get("name") or raw.get("method") or "mean").lower().strip()
            series = fields.get(field, fields["close"])
            if method in {"mean", "avg"}:
                fields[output] = series.rolling(period, min_periods=period).mean()
            elif method == "min":
                fields[output] = series.rolling(period, min_periods=period).min()
            elif method == "max":
                fields[output] = series.rolling(period, min_periods=period).max()
            else:
                return {"error": f"지원하지 않는 DSL rolling method입니다: {method}"}
        elif op == "rule":
            has_rule = True
            expr = _parse_expression(raw.get("when") or raw.get("condition") or raw.get("if"))
            if not expr:
                return {"error": f"해석할 수 없는 DSL 조건입니다: {raw.get('when')}"}
            emit = raw.get("emit") if isinstance(raw.get("emit"), dict) else {}
            if str(emit.get("field") or "target_weight") != "target_weight":
                continue
            value = _target_weight(emit.get("value"))
            if value is None:
                continue
            mask = _evaluate_expression(expr, fields)
            target.loc[mask] = value
            rule_id = str(emit.get("ruleId") or raw.get("ruleId") or raw.get("when") or "rule")
            for dt in target.loc[mask].index:
                reasons[dt] = rule_id
        elif op in {"emit", ""}:
            continue
        else:
            return {"error": f"지원하지 않는 portfolio-matrix-dsl op입니다: {op}"}

    if not has_rule:
        target[:] = 1.0
    return {"target": target, "reasons": reasons, "rsi": fields.get("rsi")}


def rsi_series(close: pd.Series, period: int = 14) -> pd.Series:
    series = pd.to_numeric(close, errors="coerce")
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, 1e-12)
    return 100 - (100 / (1 + rs))


def standard_metrics(equity: pd.Series, benchmark: pd.Series | None = None) -> dict:
    eq = pd.to_numeric(equity, errors="coerce").dropna()
    if len(eq) < 2:
        return {
            "누적수익": "—", "CAGR": "—", "MDD": "—", "Vol": "—", "Sharpe": "—",
            "Sortino": "—", "Calmar": "—", "Ulcer": "—", "UPI": "—", "Beta": "—",
        }
    returns = eq.pct_change().dropna()
    days = max(1, int((eq.index[-1] - eq.index[0]).days))
    cumulative = float(eq.iloc[-1] / eq.iloc[0] - 1)
    cagr = float((eq.iloc[-1] / eq.iloc[0]) ** (365.25 / days) - 1) if eq.iloc[0] > 0 else 0.0
    drawdown = eq / eq.cummax() - 1
    mdd = float(drawdown.min())
    vol = float(returns.std() * sqrt(252)) if len(returns) > 1 else 0.0
    sharpe = float(returns.mean() / returns.std() * sqrt(252)) if len(returns) > 1 and returns.std() > 0 else None
    downside = returns[returns < 0]
    sortino = float(returns.mean() / downside.std() * sqrt(252)) if len(downside) > 1 and downside.std() > 0 else None
    calmar = cagr / abs(mdd) if mdd < 0 else None
    ulcer = float(((drawdown.clip(upper=0) * 100.0) ** 2).mean() ** 0.5)
    upi = (cagr * 100.0 / ulcer) if ulcer > 0 else None
    beta = _beta(eq, benchmark) if benchmark is not None else None
    return {
        "누적수익": f"{cumulative * 100:+.1f}%",
        "CAGR": f"{cagr * 100:+.1f}%",
        "MDD": f"{mdd * 100:.1f}%",
        "Vol": f"{vol * 100:.1f}%",
        "Sharpe": "—" if sharpe is None else f"{sharpe:.2f}",
        "Sortino": "—" if sortino is None else f"{sortino:.2f}",
        "Calmar": "—" if calmar is None else f"{calmar:.2f}",
        "Ulcer": f"{ulcer:.1f}%",
        "UPI": "—" if upi is None else f"{upi:.2f}",
        "Beta": "—" if beta is None else f"{beta:.2f}",
    }


def _beta(equity: pd.Series, benchmark: pd.Series | None) -> float | None:
    if benchmark is None:
        return None
    pairs = pd.concat(
        [equity.pct_change().rename("asset"), benchmark.pct_change().rename("bench")],
        axis=1,
    ).dropna()
    if len(pairs) < 3:
        return None
    variance = float(pairs["bench"].var())
    if variance <= 0:
        return None
    return float(pairs["asset"].cov(pairs["bench"]) / variance)


def _parse_expression(value) -> tuple[str, str, float] | None:
    text = str(value or "").strip()
    for op in ("<=", ">=", "==", "!=", "<", ">", "="):
        if op in text:
            left, right = text.split(op, 1)
            number = _number(right, None)
            if number is None:
                return None
            return left.strip(), op, float(number)
    return None


def _evaluate_expression(expr: tuple[str, str, float], fields: dict[str, pd.Series]) -> pd.Series:
    field, op, threshold = expr
    series = pd.to_numeric(fields.get(field, pd.Series(dtype="float64")), errors="coerce")
    if op == "<":
        return series < threshold
    if op == "<=":
        return series <= threshold
    if op == ">":
        return series > threshold
    if op == ">=":
        return series >= threshold
    if op in {"=", "=="}:
        return series == threshold
    return series != threshold


def _target_weight(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"buy", "long"}:
            return 1.0
        if text in {"sell", "cash", "flat", "close"}:
            return 0.0
        if text.endswith("%"):
            return max(0.0, min(1.0, _number(text[:-1], 0.0) / 100.0))
    return max(0.0, min(1.0, _number(value, 0.0)))


def _trade_log(target: pd.Series, reasons: dict[pd.Timestamp, str]) -> list[dict]:
    trades = []
    last = 1.0
    for dt, raw in target.dropna().items():
        value = float(raw)
        if value == last:
            continue
        trades.append(
            {
                "date": pd.Timestamp(dt).date().isoformat(),
                "action": "BUY" if value > last else "SELL",
                "targetWeight": round(value * 100.0, 2),
                "reason": reasons.get(dt, "portfolio-matrix-dsl target_weight"),
            }
        )
        last = value
    return trades


def _backtest_matrix(equity: pd.DataFrame, series_names: tuple[str, ...]) -> list[dict]:
    rows = []
    for name in series_names:
        if name not in equity.columns:
            continue
        values = pd.to_numeric(equity[name], errors="coerce")
        for dt, value in zip(equity["date"], values):
            rows.append(
                {
                    "date": pd.Timestamp(dt).date().isoformat(),
                    "seriesName": name,
                    "asset": name,
                    "field": "nav",
                    "value": None if pd.isna(value) else round(float(value), 6),
                }
            )
    return rows


def _program_summary(program: list[dict]) -> list[str]:
    labels = []
    for step in program or []:
        if not isinstance(step, dict):
            continue
        op = str(step.get("op") or step.get("type") or "").strip()
        if op == "indicator":
            labels.append(str(step.get("name") or "indicator"))
        elif op == "rule":
            labels.append(str(step.get("when") or "rule"))
        elif op:
            labels.append(op)
    return labels[:6] or ["buy_hold"]


def _number(value, default=0.0):
    try:
        number = float(str(value).replace("%", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return default
    return number
