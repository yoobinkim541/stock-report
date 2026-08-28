from __future__ import annotations

import argparse
import importlib
import inspect
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import requests

from dotenv import load_dotenv          # crons/*.py 관례 — uv run 은 .env 를 자동 주입 안 함
load_dotenv()

from reports import source_collector
from reports.source_runs import record_source_run


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    sources: tuple[str, ...]
    group: str
    fetch: Callable[[], list[dict]]
    timeout_seconds: float = 30.0
    retries: int = 0
    mutable: bool = False


def default_registry() -> list[ProviderSpec]:
    return [
        ProviderSpec("saveticker", ("saveticker",), "news", source_collector.fetch_saveticker_events, retries=1),
        ProviderSpec(
            "arca",
            ("arca",),
            "news",
            source_collector.fetch_arca_provider,
            retries=1,
        ),
        ProviderSpec(
            "telegram",
            tuple(f"telegram:{channel}" for channel in source_collector.TELEGRAM_NEWS_CHANNELS),
            "news",
            source_collector.fetch_telegram_channel_events,
            retries=1,
        ),
        ProviderSpec("yahoo_finance", ("yahoo_finance",), "market", source_collector.fetch_market_snapshot_events, retries=1, mutable=True),
        ProviderSpec("fred", ("fred",), "macro", source_collector.fetch_fred_macro_events, retries=1, mutable=True),
        ProviderSpec("worldgovernmentbonds", ("worldgovernmentbonds",), "macro", source_collector.fetch_world_gov_bond_events, retries=1, mutable=True),
        ProviderSpec("polymarket", ("polymarket",), "prediction", source_collector.fetch_polymarket_events, mutable=True),
        ProviderSpec("kalshi", ("kalshi",), "prediction", source_collector.fetch_kalshi_events, retries=1, mutable=True),
        ProviderSpec("economic_calendar", ("economic_calendar",), "calendar", source_collector.fetch_economic_calendar_events, retries=1),
    ]


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


def _importable_fetch_target(fetch: Callable[[], list[dict]]) -> tuple[str, str] | None:
    """Return a stable import path; local closures stay on the compatibility path."""
    if not inspect.isfunction(fetch):
        return None
    module = str(getattr(fetch, "__module__", "") or "")
    qualname = str(getattr(fetch, "__qualname__", "") or "")
    if not module or not qualname or "<locals>" in qualname or "<lambda>" in qualname:
        return None
    try:
        current = importlib.import_module(module)
        for part in qualname.split("."):
            current = getattr(current, part)
    except (AttributeError, ImportError, TypeError):
        return None
    return (module, qualname) if current is fetch else None


def _run_subprocess_fetch(spec: ProviderSpec, target: tuple[str, str]) -> dict:
    started_clock = time.monotonic()
    command = [
        sys.executable,
        "-m",
        "reports.source_provider_worker",
        target[0],
        target[1],
    ]
    process = None
    try:
        env = os.environ.copy()
        inherited_paths = [str(path) for path in sys.path if str(path)]
        if inherited_paths:
            env["PYTHONPATH"] = os.pathsep.join(inherited_paths)
        process = subprocess.Popen(
            command,
            cwd=str(Path(__file__).resolve().parent.parent),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        try:
            stdout, stderr = process.communicate(timeout=max(0.01, float(spec.timeout_seconds)))
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                stdout, stderr = process.communicate(timeout=1.0)
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate()
            duration_ms = max(0, round((time.monotonic() - started_clock) * 1000))
            return {
                "events": [],
                "error": f"provider deadline exceeded after {spec.timeout_seconds:g}s",
                "status_code": None,
                "availability": "timeout",
                "transport": "subprocess",
                "timed_out": True,
                "terminated": process.returncode is not None,
                "duration_ms": duration_ms,
            }
    except BaseException as exc:
        return {
            "events": [],
            "error": str(exc)[:500],
            "status_code": _status_code(exc),
            "availability": "error",
            "transport": "subprocess",
            "timed_out": False,
            "terminated": False,
            "duration_ms": max(0, round((time.monotonic() - started_clock) * 1000)),
        }

    duration_ms = max(0, round((time.monotonic() - started_clock) * 1000))
    raw = (stdout or "").strip()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        detail = (stderr or raw or f"worker exited with code {process.returncode}").strip()
        return {
            "events": [],
            "error": detail[:500],
            "status_code": None,
            "availability": "error",
            "transport": "subprocess",
            "timed_out": False,
            "terminated": False,
            "duration_ms": duration_ms,
        }
    if not isinstance(payload, dict) or not payload.get("ok"):
        error = str((payload or {}).get("error") or stderr or "provider worker failed")
        status_code = (payload or {}).get("status_code")
        try:
            status_code = int(status_code) if status_code is not None else None
        except (TypeError, ValueError):
            status_code = None
        availability = str((payload or {}).get("availability") or ("blocked" if status_code == 451 else "error"))
        return {
            "events": [],
            "error": error[:500],
            "status_code": status_code,
            "availability": availability,
            "transport": "subprocess",
            "timed_out": False,
            "terminated": False,
            "duration_ms": duration_ms,
        }
    events = payload.get("events")
    if not isinstance(events, list) or any(not isinstance(row, dict) for row in events):
        return {
            "events": [],
            "error": f"{spec.name} worker returned invalid list[dict]",
            "status_code": None,
            "availability": "error",
            "transport": "subprocess",
            "timed_out": False,
            "terminated": False,
            "duration_ms": duration_ms,
        }
    return {
        "events": events,
        "error": "",
        "status_code": None,
        "availability": "available",
        "transport": "subprocess",
        "timed_out": False,
        "terminated": False,
        "duration_ms": duration_ms,
    }


def _run_direct_fetch(spec: ProviderSpec) -> dict:
    try:
        events = spec.fetch()
        if not isinstance(events, list) or any(not isinstance(row, dict) for row in events):
            raise TypeError(f"{spec.name} fetcher must return list[dict]")
        return {
            "events": events,
            "error": "",
            "status_code": None,
            "availability": "available",
            "transport": "in-process",
            "timed_out": False,
            "terminated": False,
        }
    except BaseException as exc:
        status_code = _status_code(exc)
        return {
            "events": [],
            "error": str(exc)[:500],
            "status_code": status_code,
            "availability": str(getattr(exc, "availability", "") or ("blocked" if status_code == 451 else "error")),
            "transport": "in-process",
            "timed_out": False,
            "terminated": False,
        }


def _fetch_with_retry(spec: ProviderSpec) -> dict:
    started = datetime.now(timezone.utc)
    started_clock = time.monotonic()
    attempts = 0
    events: list[dict] = []
    error = ""
    status_code = None
    availability = "available"
    timed_out = False
    terminated = False
    target = _importable_fetch_target(spec.fetch)
    for attempt in range(max(0, int(spec.retries)) + 1):
        attempts = attempt + 1
        outcome = _run_subprocess_fetch(spec, target) if target else _run_direct_fetch(spec)
        events = outcome["events"]
        error = outcome["error"]
        status_code = outcome["status_code"]
        availability = outcome["availability"]
        timed_out = bool(outcome.get("timed_out"))
        terminated = bool(outcome.get("terminated"))
        if not error:
            error = ""
            break
        if status_code in {400, 401, 403, 404, 451} or attempt >= int(spec.retries):
            break
    finished = datetime.now(timezone.utc)
    return {
        "provider": spec.name,
        "started_at": started.isoformat(timespec="seconds"),
        "finished_at": finished.isoformat(timespec="seconds"),
        "duration_ms": max(0, round((time.monotonic() - started_clock) * 1000)),
        "attempts": attempts,
        "events": events,
        "fetched": len(events),
        "persisted": 0,
        "availability": availability,
        "status_code": status_code,
        "error": error,
        "transport": outcome.get("transport", "direct"),
        "timeout_seconds": float(spec.timeout_seconds),
        "timed_out": timed_out,
        "terminated": terminated,
        "isolated": target is not None,
    }


def _select_specs(
    registry: list[ProviderSpec],
    *,
    group: str | None,
    sources: list[str] | None,
) -> list[ProviderSpec]:
    requested = {str(value).strip().lower() for value in (sources or []) if str(value).strip()}
    selected = []
    for spec in registry:
        if group and spec.group != group:
            continue
        if requested and spec.name.lower() not in requested and not requested.intersection(src.lower() for src in spec.sources):
            continue
        selected.append(spec)
    return selected


def run_providers(
    *,
    registry: list[ProviderSpec] | None = None,
    group: str | None = None,
    sources: list[str] | None = None,
    max_workers: int = 4,
    cache_dir: Path | str = source_collector.DEFAULT_CACHE_DIR,
    now: datetime | None = None,
) -> dict:
    specs = _select_specs(registry or default_registry(), group=group, sources=sources)
    cache_dir = Path(cache_dir)
    now = now or datetime.now(source_collector.KST)
    results: dict[str, dict] = {}
    if specs:
        executor = ThreadPoolExecutor(max_workers=max(1, min(int(max_workers or 1), len(specs))))
        try:
            futures = {executor.submit(_fetch_with_retry, spec): spec for spec in specs}
            for future in as_completed(futures):
                spec = futures[future]
                results[spec.name] = future.result()
        finally:
            executor.shutdown(wait=True, cancel_futures=True)

    all_events: list[dict] = []
    attempted_sources: list[str] = []
    health_stats: dict[str, dict] = {}
    for spec in specs:
        result = results[spec.name]
        events = result.pop("events")
        all_events.extend(events)
        attempted_sources.extend(spec.sources)
        total_persisted = 0
        provider_availabilities: list[str] = []
        provider_errors: list[str] = []
        for source in spec.sources:
            source_events = [row for row in events if str(row.get("source") or "") == source]
            persisted = source_collector.append_events(source_events, cache_dir=cache_dir, now=now) if source_events else 0
            total_persisted += persisted
            source_availability = source_collector._SOURCE_AVAILABILITY.get(source) or source_collector._SOURCE_AVAILABILITY.get(source.split(":", 1)[0]) or {}
            source_error = (
                result["error"]
                or source_availability.get("availability_reason")
                or source_collector._LAST_ERRORS.get(source)
                or source_collector._LAST_ERRORS.get(source.split(":", 1)[0])
                or ""
            )
            if source_error:
                provider_errors.append(str(source_error))
            availability = source_availability.get("availability")
            if not availability:
                availability = result["availability"]
                if availability == "available" and source_error and not source_events:
                    availability = "error"
            provider_availabilities.append(str(availability))
            health_stats[source] = {
                "fetched": len(source_events),
                "persisted": persisted,
                "duration_ms": result["duration_ms"],
                "availability": availability,
                "availability_reason": source_availability.get("availability_reason") or source_error,
                "error": source_error,
                "transport": result["transport"],
                "status_code": result["status_code"],
            }
        if provider_availabilities:
            if any(value == "available" for value in provider_availabilities):
                result["availability"] = "available"
            elif any(value == "blocked" for value in provider_availabilities):
                result["availability"] = "blocked"
            else:
                result["availability"] = provider_availabilities[0]
        if not result["error"] and provider_errors:
            result["error"] = provider_errors[0][:500]
        transports = {
            str((row.get("metrics") or {}).get("transport") or row.get("transport") or "")
            for row in events
            if (row.get("metrics") or {}).get("transport") or row.get("transport")
        }
        if len(transports) == 1:
            result["transport"] = transports.pop()
        result["persisted"] = total_persisted
        record_source_run(cache_dir, result)

    health = source_collector.update_source_health(
        all_events,
        cache_dir=cache_dir,
        now=now,
        attempted_sources=attempted_sources,
        run_stats=health_stats,
    ) if attempted_sources else source_collector.load_source_health(cache_dir)
    available_count = sum(result.get("availability") == "available" for result in results.values())
    if results and available_count == len(results):
        group_availability = "available"
    elif available_count:
        group_availability = "degraded"
    else:
        group_availability = "unavailable"
    return {
        "ok": bool(available_count) or not results,
        "availability": group_availability,
        "selected": [spec.name for spec in specs],
        "providers": {spec.name: results[spec.name] for spec in specs},
        "health": {source: health[source] for source in attempted_sources if source in health},
        "fetched": sum(result.get("fetched", 0) for result in results.values()),
        "persisted": sum(result.get("persisted", 0) for result in results.values()),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run isolated stock-report source providers.")
    parser.add_argument("--group", choices=("news", "market", "macro", "prediction", "calendar"))
    parser.add_argument("--source", action="append", dest="sources")
    parser.add_argument("--cache-dir", default=str(source_collector.DEFAULT_CACHE_DIR))
    parser.add_argument("--max-workers", type=int, default=4)
    args = parser.parse_args(argv)
    result = run_providers(
        group=args.group,
        sources=args.sources,
        cache_dir=args.cache_dir,
        max_workers=args.max_workers,
    )
    for name, row in result["providers"].items():
        print(
            f"{name}: fetched={row['fetched']} persisted={row['persisted']} "
            f"duration={row['duration_ms']}ms availability={row['availability']}"
        )
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
