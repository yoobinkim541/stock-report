"""Renderer-neutral shared clock for replay workspaces."""
from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from typing import Any

import pandas as pd

from dashboard import chart_document


CONTROLLER_VERSION = 1
REALTIME_ALERT_NAMESPACE = "realtime"
_COMMANDS = frozenset({"play", "pause", "step", "speed", "seek", "jump_live"})


def new_controller(
    session_id: str,
    *,
    cursor: int = 0,
    speed: int = 1,
    playing: bool = False,
    parent_id: str | None = None,
) -> dict[str, Any]:
    session_id = str(session_id or "").strip()
    cursor = int(cursor)
    speed = int(speed)
    if not session_id:
        raise ValueError("session_id is required")
    if cursor < 0:
        raise ValueError("cursor must be nonnegative")
    if speed < 1 or speed > 100:
        raise ValueError("speed must be between 1 and 100")
    return {
        "version": CONTROLLER_VERSION,
        "session_id": session_id,
        "parent_id": str(parent_id or "").strip() or None,
        "cursor": cursor,
        "as_of": None,
        "playing": bool(playing),
        "speed": speed,
        "alert_namespace": REALTIME_ALERT_NAMESPACE,
    }


def _timeline(timeline: Sequence[Any]) -> list[pd.Timestamp]:
    try:
        values = [pd.Timestamp(value) for value in timeline]
    except (TypeError, ValueError) as exc:
        raise ValueError("timeline must contain timestamps") from exc
    if not values:
        raise ValueError("timeline cannot be empty")
    normalized = pd.to_datetime(pd.Index(values), utc=True)
    if not normalized.is_monotonic_increasing or normalized.has_duplicates:
        raise ValueError("timeline must be strictly increasing")
    return values


def synchronize(controller: Mapping[str, Any], timeline: Sequence[Any]) -> dict[str, Any]:
    values = _timeline(timeline)
    out = copy.deepcopy(dict(controller))
    cursor = min(max(int(out.get("cursor") or 0), 0), len(values) - 1)
    out["cursor"] = cursor
    out["as_of"] = values[cursor].isoformat()
    out.setdefault("alert_namespace", REALTIME_ALERT_NAMESPACE)
    return out


def apply_command(
    controller: Mapping[str, Any],
    timeline: Sequence[Any],
    command: str,
    *,
    value: int | None = None,
) -> dict[str, Any]:
    command = str(command or "").strip().lower()
    if command not in _COMMANDS:
        raise ValueError(f"unsupported replay command: {command}")
    values = _timeline(timeline)
    out = synchronize(controller, values)
    if command == "play":
        out["playing"] = True
    elif command == "pause":
        out["playing"] = False
    elif command == "speed":
        speed = int(value or 0)
        if speed < 1 or speed > 100:
            raise ValueError("speed must be between 1 and 100")
        out["speed"] = speed
    elif command == "step":
        steps = int(value if value is not None else 1)
        if steps < 0:
            raise ValueError("rewind requires a new branch")
        out["cursor"] = min(len(values) - 1, int(out["cursor"]) + steps)
    elif command == "seek":
        target = int(value if value is not None else -1)
        if target < int(out["cursor"]):
            raise ValueError("rewind requires a new branch")
        if target < 0:
            raise ValueError("cursor must be nonnegative")
        out["cursor"] = min(target, len(values) - 1)
    elif command == "jump_live":
        out["cursor"] = len(values) - 1
        out["playing"] = False
    return synchronize(out, values)


def tick(controller: Mapping[str, Any], timeline: Sequence[Any]) -> dict[str, Any]:
    out = synchronize(controller, timeline)
    if not bool(out.get("playing")):
        return out
    return apply_command(out, timeline, "step", value=int(out.get("speed") or 1))


def branch_controller(
    controller: Mapping[str, Any],
    *,
    cursor: int,
    session_id: str,
) -> dict[str, Any]:
    cursor = int(cursor)
    if cursor < 0 or cursor > int(controller.get("cursor") or 0):
        raise ValueError("branch cursor is outside controller history")
    return new_controller(
        session_id,
        cursor=cursor,
        speed=int(controller.get("speed") or 1),
        playing=False,
        parent_id=str(controller.get("session_id") or ""),
    )


def _slice_frame(frame: Any, as_of: pd.Timestamp):
    if frame is None or getattr(frame, "empty", True):
        return frame.copy() if hasattr(frame, "copy") else frame
    try:
        normalized_index = pd.to_datetime(frame.index, utc=True)
    except (TypeError, ValueError) as exc:
        raise ValueError("panel frame must use a datetime index") from exc
    cutoff = pd.Timestamp(as_of)
    cutoff = cutoff.tz_localize("UTC") if cutoff.tzinfo is None else cutoff.tz_convert("UTC")
    return frame.iloc[normalized_index <= cutoff].copy()


def workspace_snapshot(
    controller: Mapping[str, Any],
    timeline: Sequence[Any],
    panels: Mapping[str, Any],
) -> dict[str, Any]:
    synced = synchronize(controller, timeline)
    as_of = pd.Timestamp(synced["as_of"])
    return {
        "controller": synced,
        "as_of": synced["as_of"],
        "panels": {
            str(panel_id): _slice_frame(frame, as_of)
            for panel_id, frame in panels.items()
        },
    }


def apply_to_document(document: Mapping[str, Any], controller: Mapping[str, Any]) -> dict[str, Any]:
    out = chart_document.normalize_chart_document(document)
    out["replay"] = {
        "active": True,
        "cursor": int(controller.get("cursor") or 0),
        "as_of": controller.get("as_of"),
        "session_id": str(controller.get("session_id") or ""),
    }
    return out
