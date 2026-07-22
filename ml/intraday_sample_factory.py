from __future__ import annotations

from typing import Any

from ml.intraday_axes import friction_per_share

_SETUP_ORDER = (
    "opening_range_breakout",
    "vwap_reclaim",
    "volume_shock",
)


def _axis_value(axes: dict, key: str) -> float:
    value = axes.get(key)
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _bars_empty(bars) -> bool:
    return bars is None or getattr(bars, "empty", True) or len(bars) == 0


def _last_close(bars) -> float | None:
    if _bars_empty(bars) or "Close" not in bars:
        return None
    return _safe_float(bars["Close"].iloc[-1], default=None)


def _recent_closes(bars, count: int) -> list[float]:
    if _bars_empty(bars) or "Close" not in bars:
        return []
    tail = bars["Close"].tail(max(1, int(count)))
    return [_safe_float(value) for value in tail.tolist()]


def _recent_volumes(bars, count: int) -> list[float]:
    if _bars_empty(bars) or "Volume" not in bars:
        return []
    tail = bars["Volume"].tail(max(1, int(count)))
    return [_safe_float(value) for value in tail.tolist()]


def _opening_range_high(bars, window: int = 5) -> float | None:
    if _bars_empty(bars) or "High" not in bars or len(bars) < 2:
        return None
    prior = bars.iloc[:-1].head(max(2, min(int(window), len(bars) - 1)))
    if prior.empty:
        return None
    return _safe_float(prior["High"].max(), default=None)


def _count_consecutive_above(values: list[float], level: float) -> int:
    count = 0
    for value in reversed(values):
        if value > level:
            count += 1
        else:
            break
    return count


def _volume_ratio(bars) -> float | None:
    if _bars_empty(bars) or "Volume" not in bars or len(bars) < 2:
        return None
    current = _safe_float(bars["Volume"].iloc[-1], default=None)
    history = [v for v in _recent_volumes(bars.iloc[:-1], 10) if v > 0]
    if current is None or not history:
        return None
    baseline = sum(history) / len(history)
    if baseline <= 0:
        return None
    return current / baseline


def _market_thresholds(market: str) -> dict[str, float]:
    mk = str(market or "").upper()
    if mk == "KR":
        return {"micro_ratio": 2.5, "normal_ratio": 4.5}
    return {"micro_ratio": 2.0, "normal_ratio": 4.0}


def candidate_id(date: str, market: str, ticker: str, epoch_min: int, setup_type: str) -> str:
    return f"{date}:{str(market).upper()}:{str(ticker).upper()}:{int(epoch_min)}:{setup_type}"


def estimated_cost_per_share(price: float, market: str, spread: float | None) -> float:
    return float(friction_per_share(float(price), str(market).upper(), spread=spread))


def detect_setups(axes: dict, bars, *, market: str) -> list[dict]:
    axes = axes or {}
    meta = axes.get("_meta") or {}
    close = _safe_float(meta.get("close"), default=None)
    if close is None:
        close = _last_close(bars)
    atr = _safe_float(meta.get("atr"), default=0.0)
    if close is None or close <= 0:
        return []

    orb_axis = _axis_value(axes, "orb")
    vwap_axis = _axis_value(axes, "vwap")
    volspike_axis = _axis_value(axes, "volspike")
    recent_closes = _recent_closes(bars, 5)
    recent_mean = sum(recent_closes[:-1] or recent_closes) / max(len(recent_closes[:-1] or recent_closes), 1)
    opening_range_high = _opening_range_high(bars, window=5)
    volume_ratio = _volume_ratio(bars)
    setup_rows: list[dict] = []

    if (
        opening_range_high is not None
        and close > opening_range_high
        and (orb_axis >= 0.6 or volspike_axis >= 0.8)
    ):
        confirm_bars = _count_consecutive_above(recent_closes, opening_range_high)
        setup_rows.append(
            {
                "market": str(market).upper(),
                "setup_type": "opening_range_breakout",
                "confirm_bars": max(1, confirm_bars),
                "expected_move": round(max(atr * 1.25, close - opening_range_high), 6),
                "entry_price": close,
                "reference_level": round(opening_range_high, 6),
                "volume_ratio": None if volume_ratio is None else round(volume_ratio, 6),
            }
        )

    if vwap_axis >= 0.6 and len(recent_closes) >= 2 and close > recent_mean:
        confirm_bars = _count_consecutive_above(recent_closes, recent_mean)
        setup_rows.append(
            {
                "market": str(market).upper(),
                "setup_type": "vwap_reclaim",
                "confirm_bars": max(1, confirm_bars),
                "expected_move": round(max(atr * 1.1, close - recent_mean), 6),
                "entry_price": close,
                "reference_level": round(recent_mean, 6),
                "volume_ratio": None if volume_ratio is None else round(volume_ratio, 6),
            }
        )

    if volspike_axis >= 0.8 and volume_ratio is not None and volume_ratio >= 2.5:
        setup_rows.append(
            {
                "market": str(market).upper(),
                "setup_type": "volume_shock",
                "confirm_bars": 1,
                "expected_move": round(max(atr * 1.0, close * min(0.03, 0.01 + 0.002 * min(volume_ratio, 6.0))), 6),
                "entry_price": close,
                "volume_ratio": round(volume_ratio, 6),
            }
        )

    order = {name: idx for idx, name in enumerate(_SETUP_ORDER)}
    setup_rows.sort(key=lambda row: order.get(row.get("setup_type"), len(order)))
    return setup_rows


def classify_sample(
    setup: dict,
    *,
    market: str,
    confirm_bars: int,
    expected_move: float,
    estimated_cost: float,
) -> dict:
    thresholds = _market_thresholds(market)
    confirm_bars = int(confirm_bars or 0)
    expected_move = _safe_float(expected_move)
    estimated_cost = max(_safe_float(estimated_cost), 0.0)
    cost_ratio = round(expected_move / estimated_cost, 4) if estimated_cost > 0 else float("inf")

    sample_mode = "observe_only"
    blocked_by: list[str] = []

    if confirm_bars >= 2 and cost_ratio >= thresholds["normal_ratio"]:
        sample_mode = "normal"
    elif confirm_bars >= 1 and cost_ratio >= thresholds["micro_ratio"]:
        sample_mode = "micro"
        if confirm_bars < 2:
            blocked_by.append("confirm_bars_lt_2")
        if cost_ratio < thresholds["normal_ratio"]:
            blocked_by.append("cost_ratio_lt_normal")
    else:
        if confirm_bars < 1:
            blocked_by.append("confirm_bars_lt_1")
        if cost_ratio < thresholds["micro_ratio"]:
            blocked_by.append("cost_ratio_lt_micro")
        if confirm_bars < 2:
            blocked_by.append("confirm_bars_lt_2")
        if cost_ratio < thresholds["normal_ratio"]:
            blocked_by.append("cost_ratio_lt_normal")

    out = dict(setup)
    out.update(
        {
            "market": str(market).upper(),
            "sample_mode": sample_mode,
            "confirm_bars": confirm_bars,
            "expected_move": round(expected_move, 6),
            "estimated_cost": round(estimated_cost, 6),
            "cost_ratio": cost_ratio,
            "blocked_by": blocked_by,
        }
    )
    return out
