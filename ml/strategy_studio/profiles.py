"""Compatibility execution defaults.

The operational profile health and collector integration belong to Task 9.  This
module only provides deterministic execution settings for the shared simulator.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .execution import ExecutionConfig


_PROFILE_DEFAULTS: dict[str, dict[str, Any]] = {
    "bar": {
        "latency_bars": 1,
        "fees_bps": 0.0,
        "slippage_bps": 0.0,
        "spread_bps": 0.0,
        "max_participation_rate": 1.0,
        "partial_fill": True,
    },
    "kr_intraday": {
        "latency_bars": 1,
        "fees_bps": 3.0,
        "slippage_bps": 5.0,
        "spread_bps": 5.0,
        "max_participation_rate": 0.10,
        "partial_fill": True,
    },
    "global_swing": {
        "latency_bars": 1,
        "fees_bps": 2.0,
        "slippage_bps": 5.0,
        "spread_bps": 2.0,
        "max_participation_rate": 0.25,
        "partial_fill": True,
    },
    "extended_us": {
        "latency_bars": 1,
        "fees_bps": 1.0,
        "slippage_bps": 5.0,
        "spread_bps": 8.0,
        "max_participation_rate": 0.10,
        "partial_fill": True,
    },
}


def execution_defaults(profile: str, session: str = "regular") -> "ExecutionConfig":
    """Return a validated :class:`ExecutionConfig` for a known profile.

    ``session`` is intentionally limited to cost policy adjustments here.  The
    session-aware data health and market calendar behavior is part of Task 9.
    """

    key = str(profile or "").strip().lower()
    if key not in _PROFILE_DEFAULTS:
        raise ValueError(f"unsupported execution profile: {key}")
    session_key = str(session or "regular").strip().lower()
    if not session_key:
        session_key = "regular"

    values = dict(_PROFILE_DEFAULTS[key])
    if key == "extended_us" and session_key in {"extended", "pre_market", "premarket", "after_hours", "aftermarket"}:
        values["spread_bps"] = max(float(values["spread_bps"]), 12.0)
        values["max_participation_rate"] = min(float(values["max_participation_rate"]), 0.05)

    # Import lazily so execution.py can re-export this function without a
    # module initialization cycle.
    from .execution import ExecutionConfig

    return ExecutionConfig(profile=key, session=session_key, **values)


__all__ = ["execution_defaults"]
