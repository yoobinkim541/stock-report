"""Execution profiles and operational freshness policy.

Profiles describe market-specific execution assumptions.  They do not own a
market-data store; collectors attach their observed timestamps and health to
the existing normalized frames/snapshots consumed by the execution engine.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timezone
from math import isfinite
from typing import TYPE_CHECKING, Any, Mapping
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from .execution import ExecutionConfig


@dataclass(frozen=True, slots=True)
class ProfileHealth:
    """Replayable decision about whether a profile may accept new entries."""

    status: str
    reason: str
    age_seconds: float | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", str(self.status or "unknown").strip().lower())
        object.__setattr__(self, "reason", str(self.reason or "unknown").strip())
        if self.age_seconds is not None:
            if isinstance(self.age_seconds, bool):
                raise ValueError("age_seconds must be a finite non-negative number")
            try:
                age = float(self.age_seconds)
            except (TypeError, ValueError) as exc:
                raise ValueError("age_seconds must be a finite non-negative number") from exc
            if not isfinite(age):
                raise ValueError("age_seconds must be a finite non-negative number")
            if age < 0.0:
                age = 0.0
            object.__setattr__(self, "age_seconds", age)

    @property
    def allows_new_entries(self) -> bool:
        return self.status == "fresh"

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "reason": self.reason,
            "age_seconds": self.age_seconds,
            "allows_new_entries": self.allows_new_entries,
        }


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

_SESSION_ALIASES = {
    "regular": "regular",
    "open": "opening_auction",
    "opening": "opening_auction",
    "opening_auction": "opening_auction",
    "close": "closing_auction",
    "closing": "closing_auction",
    "closing_auction": "closing_auction",
    "extended": "extended",
    "pre_market": "premarket",
    "premarket": "premarket",
    "after_hours": "aftermarket",
    "aftermarket": "aftermarket",
}

_PROFILE_TZ = {
    "kr_intraday": ZoneInfo("Asia/Seoul"),
    "global_swing": ZoneInfo("America/New_York"),
    "extended_us": ZoneInfo("America/New_York"),
}


def _parse_timestamp(value: object, field_name: str) -> datetime:
    if value is None or not str(value).strip():
        raise ValueError(f"{field_name} is required")
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"{field_name} must be an ISO timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _session_is_closed(profile: str, now: datetime) -> bool:
    if profile == "bar":
        return False
    local = now.astimezone(_PROFILE_TZ[profile])
    if local.weekday() >= 5:
        return True
    if profile == "global_swing":
        return False
    if profile == "kr_intraday":
        return not time(9, 0) <= local.time() < time(15, 40)
    return not time(4, 0) <= local.time() < time(20, 0)


def _stale_reason(profile: str) -> str:
    return {
        "kr_intraday": "stale_intraday_bar",
        "global_swing": "stale_swing_bar",
        "extended_us": "stale_extended_us_bar",
        "bar": "stale_bar",
    }[profile]


def profile_health(
    profile: str,
    *,
    last_bar_at: str,
    now: str,
    max_age_seconds: int,
) -> ProfileHealth:
    """Evaluate bar freshness while respecting the profile's market session.

    A closed market is reported as ``closed`` rather than stale.  The
    execution engine still blocks new entries for that status, but existing
    exits can be replayed.  Missing or future timestamps remain pauses.
    """

    key = str(profile or "").strip().lower()
    if key not in _PROFILE_DEFAULTS:
        raise ValueError(f"unsupported execution profile: {key}")
    if isinstance(max_age_seconds, bool):
        raise ValueError("max_age_seconds must be a finite non-negative number")
    try:
        limit = float(max_age_seconds)
    except (TypeError, ValueError) as exc:
        raise ValueError("max_age_seconds must be numeric") from exc
    if not isfinite(limit) or limit < 0.0:
        raise ValueError("max_age_seconds must be a finite non-negative number")

    current = _parse_timestamp(now, "now")
    last = None
    if last_bar_at is not None and str(last_bar_at).strip():
        try:
            last = _parse_timestamp(last_bar_at, "last_bar_at")
        except ValueError:
            return ProfileHealth("pause", "invalid_bar_timestamp", None)
    if last is not None:
        age = (current - last).total_seconds()
        if age < -1.0:
            return ProfileHealth("pause", "future_bar_timestamp", 0.0)
        age = max(0.0, age)
    else:
        age = None
    if _session_is_closed(key, current):
        return ProfileHealth("closed", "market_closed", age)
    if last is None:
        return ProfileHealth("pause", "missing_bar", None)
    if age > limit:
        return ProfileHealth("pause", _stale_reason(key), age)
    return ProfileHealth("fresh", "fresh", age)


def _normalise_session(session: str) -> str:
    key = str(session or "regular").strip().lower()
    if not key:
        key = "regular"
    try:
        return _SESSION_ALIASES[key]
    except KeyError as exc:
        raise ValueError(f"unsupported session: {session}") from exc


def execution_defaults(profile: str, session: str = "regular") -> "ExecutionConfig":
    """Return validated defaults for a known data/execution profile."""

    key = str(profile or "").strip().lower()
    if key not in _PROFILE_DEFAULTS:
        raise ValueError(f"unsupported execution profile: {key}")
    session_key = _normalise_session(session)
    values = dict(_PROFILE_DEFAULTS[key])

    if key == "extended_us" and session_key in {"extended", "premarket", "aftermarket"}:
        values["spread_bps"] = max(float(values["spread_bps"]), 12.0)
        values["max_participation_rate"] = min(float(values["max_participation_rate"]), 0.05)
    elif key == "kr_intraday" and session_key in {"opening_auction", "closing_auction"}:
        values["spread_bps"] = max(float(values["spread_bps"]), 8.0)
        values["max_participation_rate"] = min(float(values["max_participation_rate"]), 0.05)

    # Import lazily so execution.py can re-export the policy without a module
    # initialization cycle.
    from .execution import ExecutionConfig

    return ExecutionConfig(profile=key, session=session_key, **values)


def health_from_payload(value: object) -> ProfileHealth | None:
    """Convert a saved health mapping to the immutable replay DTO."""

    if isinstance(value, ProfileHealth):
        return value
    if not isinstance(value, Mapping):
        return None
    status = value.get("status")
    reason = value.get("reason")
    if status is None or reason is None:
        return None
    age = value.get("age_seconds")
    try:
        age_value = None if age is None else float(age)
    except (TypeError, ValueError):
        age_value = None
    return ProfileHealth(str(status), str(reason), age_value)


__all__ = ["ProfileHealth", "execution_defaults", "health_from_payload", "profile_health"]
