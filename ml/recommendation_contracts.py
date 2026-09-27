"""Canonical contracts shared by recommendation ledgers and feedback."""
from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from numbers import Real


_HORIZON_ALIASES = {
    "20": "20d",
    "20d": "20d",
    "60": "60d",
    "60d": "60d",
}


def canonical_horizon(value: int | str) -> str:
    """Return the stable string spelling for a feedback horizon."""
    text = str(value).strip().lower()
    return _HORIZON_ALIASES.get(text, text)


def finite_json_value(value: object) -> object | None:
    """Recursively turn a value into JSON-safe data, nulling non-finite numbers."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Real):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, Mapping):
        return {key: finite_json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [finite_json_value(item) for item in value]
    return None


def sanitize_record(value: object) -> object:
    return finite_json_value(value)


def _base_decision_id(row: Mapping) -> str:
    base = row.get("base_decision_id")
    if base:
        return str(base)
    decision_id = str(row.get("decision_id") or "")
    return decision_id.rsplit(":h", 1)[0] if ":h" in decision_id else decision_id


def _row_horizon(row: Mapping) -> str:
    horizon = row.get("horizon")
    if horizon is not None:
        return canonical_horizon(horizon)
    decision_id = str(row.get("decision_id") or "")
    return canonical_horizon(decision_id.rsplit(":h", 1)[1]) if ":h" in decision_id else ""


def canonical_outcome_key(base_decision_id: str, horizon: int | str) -> tuple[str, str]:
    return str(base_decision_id), canonical_horizon(horizon)


def dedupe_canonical_outcomes(rows: Iterable[dict]) -> list[dict]:
    """Keep one deterministic row for each decision and canonical horizon."""
    winners: dict[tuple[str, str], tuple[tuple[int, int, str, int], dict]] = {}
    for index, row in enumerate(rows):
        base_id = _base_decision_id(row)
        horizon = _row_horizon(row)
        key = canonical_outcome_key(base_id, horizon)
        raw_horizon = str(row.get("horizon") or "").strip().lower()
        is_canonical = int(raw_horizon == horizon)
        is_valid = int(str(row.get("status") or "").lower() == "matured")
        matured_at = str(row.get("matured_at") or "")
        priority = (is_canonical, is_valid, matured_at, -index)
        current = winners.get(key)
        if current is None or priority > current[0]:
            winners[key] = (priority, row)
    return [item[1] for item in winners.values()]
