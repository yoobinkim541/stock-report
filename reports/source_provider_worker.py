"""Run one importable source provider in a killable child process."""

from __future__ import annotations

import importlib
import json
import sys
from contextlib import redirect_stdout


def _status_code(exc: BaseException) -> int | None:
    direct = getattr(exc, "status_code", None)
    if direct is not None:
        try:
            return int(direct)
        except (TypeError, ValueError):
            pass
    response = getattr(exc, "response", None)
    try:
        return int(getattr(response, "status_code", None))
    except (TypeError, ValueError):
        return None


def _resolve(module_name: str, qualname: str):
    current = importlib.import_module(module_name)
    for part in qualname.split("."):
        current = getattr(current, part)
    return current


def main(argv: list[str] | None = None) -> int:
    args = list(argv or sys.argv[1:])
    if len(args) != 2:
        print(json.dumps({"ok": False, "error": "worker requires module and qualname"}))
        return 2
    try:
        fetch = _resolve(args[0], args[1])
        # Provider libraries may write diagnostics to stdout; reserve stdout for one JSON envelope.
        with redirect_stdout(sys.stderr):
            events = fetch()
        if not isinstance(events, list) or any(not isinstance(row, dict) for row in events):
            raise TypeError("provider fetcher must return list[dict]")
        print(json.dumps({"ok": True, "events": events}, ensure_ascii=False, default=str))
        return 0
    except BaseException as exc:
        status_code = _status_code(exc)
        availability = str(
            getattr(exc, "availability", "")
            or ("blocked" if status_code == 451 else "error")
        )
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": str(exc)[:500],
                    "status_code": status_code,
                    "availability": availability,
                },
                ensure_ascii=False,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
