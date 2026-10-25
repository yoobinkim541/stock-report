from __future__ import annotations

from typing import Any, Callable

from lib.cron_common import send_cron_telegram


SendFn = Callable[[str], bool]


def dispatch_alert_events(events: list[dict[str, Any]], *, send_fn: SendFn | None = None) -> dict[str, Any]:
    """Send each chart alert event independently.

    A raising or falsy sender is recorded as that event's failure and the next
    event is still attempted; ``results`` carries one ack per event for the
    caller's delivery bookkeeping.
    """
    sender = send_fn or send_alert_message
    attempted = 0
    delivered = 0
    failures: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    for event in events or []:
        if not isinstance(event, dict):
            continue
        attempted += 1
        error = ""
        try:
            ok = bool(sender(format_alert_message(event)))
            if not ok:
                error = "sender returned false"
        except Exception as exc:
            ok = False
            error = f"{type(exc).__name__}: {exc}"
        outcome = {
            "event_id": str(event.get("event_id") or ""),
            "alert_id": str(event.get("alert_id") or ""),
            "symbol": str(event.get("symbol") or ""),
            "delivered": ok,
            "error": error,
        }
        results.append(outcome)
        if ok:
            delivered += 1
        else:
            failures.append({key: outcome[key] for key in ("event_id", "alert_id", "symbol", "error")})
    return {
        "attempted": attempted,
        "delivered": delivered,
        "failed": len(failures),
        "failures": failures,
        "results": results,
    }


def send_alert_message(text: str) -> bool:
    return send_cron_telegram(text)


def format_alert_message(event: dict[str, Any]) -> str:
    """Message from the evaluated bar pair (bar_time / current_price / previous_price).

    The runner fills these from the bars the condition was judged on — for a
    bar_close condition that is the last closed bar, not the still-forming one —
    so the text is labelled as that bar's close, not a live quote.
    """
    symbol = str((event or {}).get("symbol") or "").upper().strip() or "UNKNOWN"
    name = str((event or {}).get("name") or "").strip() or "Chart alert"
    current_price = _price_text((event or {}).get("current_price"))
    previous_price = _price_text((event or {}).get("previous_price"))
    matched = ", ".join(str(item) for item in (event or {}).get("matched_conditions") or [] if str(item).strip())
    message = str((event or {}).get("message") or "").strip()
    as_of = str((event or {}).get("as_of") or "").strip()
    bar_time = str((event or {}).get("bar_time") or "").strip()
    lines = [
        "🔔 차트 알림",
        f"{symbol} · {name}",
        f"평가 봉 종가 {current_price} · 이전 봉 {previous_price}",
    ]
    if bar_time:
        lines.append(f"평가 봉 {bar_time}")
    if matched:
        lines.append(f"조건 {matched}")
    if message:
        lines.append(message)
    if as_of:
        lines.append(as_of)
    return "\n".join(lines)


def _price_text(value: Any) -> str:
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "N/A"
