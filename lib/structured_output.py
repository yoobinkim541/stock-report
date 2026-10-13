"""Side-effect-free validation of complete model JSON objects."""

import json
from math import isfinite
import re


def parse_object(text: str | None) -> dict | None:
    raw = str(text or "").strip()
    if not raw or len(raw) > 100_000:
        return None
    nesting = 0
    quoted = escaped = False
    for char in raw:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "[{":
            nesting += 1
            if nesting > 64:
                return None
        elif char in "]}":
            nesting -= 1
            if nesting < 0:
                return None
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", raw, flags=re.S | re.I)
    if fenced:
        raw = fenced.group(1).strip()

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def reject_constant(_value):
        raise ValueError("non-finite JSON constant")

    def finite_float(value):
        number = float(value)
        if not isfinite(number):
            raise ValueError("non-finite JSON number")
        return number

    try:
        value = json.loads(raw, object_pairs_hook=unique_object,
                           parse_constant=reject_constant, parse_float=finite_float)
    except (json.JSONDecodeError, TypeError, ValueError, RecursionError):
        return None
    return value if isinstance(value, dict) else None
