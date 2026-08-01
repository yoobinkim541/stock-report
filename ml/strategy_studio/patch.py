from __future__ import annotations

from typing import Any

from .spec import StrategySpec


def apply_strategy_patch(spec: dict[str, Any] | StrategySpec, patch: dict[str, Any]) -> dict[str, Any]:
    base = StrategySpec.from_dict(spec).to_dict()
    merged = _merge_dicts(base, patch or {})
    return StrategySpec.from_dict(merged).to_dict()


def diff_strategy_specs(before: dict[str, Any] | StrategySpec, after: dict[str, Any] | StrategySpec) -> list[dict[str, Any]]:
    left = StrategySpec.from_dict(before).to_dict()
    right = StrategySpec.from_dict(after).to_dict()
    rows: list[dict[str, Any]] = []
    _diff_value(left, right, "", rows)
    return rows


def _merge_dicts(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in (patch or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge_dicts(result.get(key) or {}, value)
        elif isinstance(value, list):
            result[key] = [ _clone(item) for item in value ]
        else:
            result[key] = _clone(value)
    return result


def _diff_value(left: Any, right: Any, path: str, rows: list[dict[str, Any]]) -> None:
    if left is None and isinstance(right, dict):
        for key in sorted(right):
            _diff_value(None, right[key], f"{path}.{key}" if path else key, rows)
        return
    if right is None and isinstance(left, dict):
        for key in sorted(left):
            _diff_value(left[key], None, f"{path}.{key}" if path else key, rows)
        return
    if left is None and isinstance(right, list):
        for idx, value in enumerate(right):
            _diff_value(None, value, f"{path}[{idx}]", rows)
        return
    if right is None and isinstance(left, list):
        for idx, value in enumerate(left):
            _diff_value(value, None, f"{path}[{idx}]", rows)
        return
    if type(left) != type(right):
        rows.append({"path": path or "$", "before": left, "after": right})
        return
    if isinstance(left, dict):
        keys = sorted(set(left) | set(right))
        for key in keys:
            next_path = f"{path}.{key}" if path else key
            if key not in left:
                _diff_value(None, right[key], next_path, rows)
            elif key not in right:
                _diff_value(left[key], None, next_path, rows)
            else:
                _diff_value(left[key], right[key], next_path, rows)
        return
    if isinstance(left, list):
        max_len = max(len(left), len(right))
        for idx in range(max_len):
            next_path = f"{path}[{idx}]"
            if idx >= len(left):
                _diff_value(None, right[idx], next_path, rows)
            elif idx >= len(right):
                _diff_value(left[idx], None, next_path, rows)
            else:
                _diff_value(left[idx], right[idx], next_path, rows)
        return
    if left != right:
        rows.append({"path": path or "$", "before": left, "after": right})


def _clone(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _clone(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clone(item) for item in value]
    return value
