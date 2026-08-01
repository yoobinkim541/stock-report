from __future__ import annotations

from typing import Any, Callable

from lib.cron_common import send_cron_telegram


SendFn = Callable[[str], bool]


def dispatch_alert_events(events: list[dict[str, Any]], *, send_fn: SendFn | None = None) -> dict[str, Any]:
    """Send triggered chart alert events through the configured notification channel."""
    sender = send_fn or send_alert_message
    attempted = 0
    delivered = 0
    failures: list[dict[str, Any]] = []
    for event in events or []:
        if not isinstance(event, dict):
            continue
        attempted += 1
        text = format_alert_message(event)
        ok = bool(sender(text))
        if ok:
            delivered += 1
        else:
            failures.append({"alert_id": str(event.get("alert_id") or ""), "symbol": str(event.get("symbol") or "")})
    return {"attempted": attempted, "delivered": delivered, "failed": len(failures), "failures": failures}


def send_alert_message(text: str) -> bool:
    return send_cron_telegram(text)


def format_alert_message(event: dict[str, Any]) -> str:
    symbol = str((event or {}).get("symbol") or "").upper().strip() or "UNKNOWN"
    name = str((event or {}).get("name") or "").strip() or "Chart alert"
    current_price = _price_text((event or {}).get("current_price"))
    previous_price = _price_text((event or {}).get("previous_price"))
    matched = ", ".join(str(item) for item in (event or {}).get("matched_conditions") or [] if str(item).strip())
    message = str((event or {}).get("message") or "").strip()
    as_of = str((event or {}).get("as_of") or "").strip()
    lines = [
        "🔔 차트 알림",
        f"{symbol} · {name}",
        f"현재가 {current_price} · 이전 {previous_price}",
    ]
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
