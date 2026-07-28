from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any


KST = timezone(timedelta(hours=9))


@dataclass(frozen=True)
class EvidenceCard:
    id: str
    source_type: str
    source_name: str
    source_url: str
    captured_at: str
    event_time: str
    raw_text: str
    raw_payload: dict[str, Any]
    symbols: list[str] = field(default_factory=list)
    markets: list[str] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    event_type: str = "event"
    confidence: float = 0.5
    freshness: str = "daily"
    impact_axes: list[str] = field(default_factory=list)
    summary: str = ""
    claims: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class InsightCardMeta:
    topic_key: str
    title: str
    current_view: str
    supporting_evidence_ids: list[str] = field(default_factory=list)
    conflicting_evidence_ids: list[str] = field(default_factory=list)
    last_updated_at: str = ""
    staleness_policy: str = "refresh_after_24h"
    open_questions: list[str] = field(default_factory=list)
    answer_hints: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _clean(value: object, limit: int = 2000) -> str:
    text = re.sub(r"\s+", " ", str(value or "").replace("\x00", " ")).strip()
    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + "…"


def _dedupe(values: list[object], *, limit: int = 12) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _clean(value, 260)
        if text and text not in seen:
            seen.add(text)
            out.append(text)
        if len(out) >= limit:
            break
    return out


def _id_for(payload: dict[str, Any]) -> str:
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return "evidence-" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:20]


def _parse_time(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _freshness(event_time: datetime | None, now: datetime) -> str:
    if not event_time:
        return "daily"
    age = now.astimezone(timezone.utc) - event_time.astimezone(timezone.utc)
    if age <= timedelta(hours=12):
        return "intraday"
    if age <= timedelta(days=3):
        return "daily"
    return "stale"


def _source_type(source: object) -> str:
    return str(source or "unknown").split(":", 1)[0].strip().lower() or "unknown"


def _event_type(event: dict[str, Any]) -> str:
    kind = _clean((event.get("classification") or {}).get("kind") or event.get("kind"), 80).lower()
    root = _source_type(event.get("source"))
    if root in {"telegram", "arca"} or kind in {"community_signal", "rumor"}:
        return "rumor"
    if "supply" in kind or "flow" in kind or "수급" in _clean(event.get("topic")):
        return "supply_demand"
    if "earn" in kind or "실적" in _clean(event.get("topic")):
        return "earnings"
    if "macro" in kind or "금리" in _clean(event.get("topic")):
        return "macro"
    return kind or "event"


def _confidence(event: dict[str, Any], event_type: str) -> float:
    trust = _clean((event.get("classification") or {}).get("trust"), 8).upper()
    if event_type == "rumor":
        return 0.45
    if trust == "A":
        return 0.9
    if trust == "B":
        return 0.78
    if trust == "C":
        return 0.55
    return 0.7 if _source_type(event.get("source")) not in {"telegram", "arca"} else 0.5


def _impact_axes(text: str) -> list[str]:
    axes: list[str] = []
    lowered = text.lower()
    checks = [
        ("liquidity", ("유동성", "credit", "크레딧", "달러", "금리")),
        ("growth", ("ai", "성장", "반도체", "데이터센터", "capex")),
        ("valuation", ("밸류", "valuation", "per", "멀티플")),
        ("positioning", ("수급", "외국인", "기관", "선물")),
        ("sentiment", ("심리", "risk-on", "risk-off", "공포")),
    ]
    for axis, words in checks:
        if any(word in lowered or word in text for word in words):
            axes.append(axis)
    return axes or ["sentiment"]


def event_to_evidence_card(event: dict, *, now: datetime | None = None) -> EvidenceCard:
    now = now or datetime.now(timezone.utc)
    title_raw = event.get("title") or ""
    body_raw = event.get("body_raw") or event.get("body") or event.get("body_excerpt") or ""
    title = _clean(title_raw, 300)
    body = _clean(body_raw, 1800)
    raw_text = "\n".join(part for part in [str(title_raw), str(body_raw)] if part)
    event_time = _parse_time(event.get("published_at") or event.get("collected_at")) or now
    topic = _clean(event.get("topic") or (event.get("classification") or {}).get("topic"), 120)
    event_type = _event_type(event)
    payload = {
        "source": event.get("source"),
        "url": event.get("url"),
        "title": event.get("title"),
        "body_raw": event.get("body_raw") or event.get("body") or event.get("body_excerpt"),
        "raw_path": event.get("raw_path"),
        "text_path": event.get("text_path"),
        "classification": event.get("classification") or {},
    }
    return EvidenceCard(
        id=_id_for(payload),
        source_type=_source_type(event.get("source")),
        source_name=_clean(event.get("source"), 120) or "unknown",
        source_url=_clean(event.get("url"), 500),
        captured_at=now.astimezone(timezone.utc).isoformat(timespec="seconds"),
        event_time=event_time.astimezone(timezone.utc).isoformat(timespec="seconds"),
        raw_text=raw_text,
        raw_payload=dict(event),
        symbols=_dedupe([str(t).upper() for t in event.get("tickers") or []], limit=20),
        markets=_dedupe(event.get("markets") or ["KR" if re.search(r"[가-힣]", raw_text) else "US"], limit=8),
        topics=_dedupe([topic, *(event.get("tags") or [])], limit=12),
        event_type=event_type,
        confidence=_confidence(event, event_type),
        freshness=_freshness(event_time, now),
        impact_axes=_impact_axes(raw_text + " " + topic),
        summary=title or body[:180],
        claims=_dedupe([title, body], limit=4),
    )


def trade_log_to_evidence_card(row: dict, *, now: datetime | None = None) -> EvidenceCard:
    now = now or datetime.now(timezone.utc)
    symbol = _clean(row.get("symbol") or row.get("ticker"), 24).upper()
    title = f"{symbol or 'UNKNOWN'} 모의투자 결과"
    body = _clean(row.get("reason") or row.get("reason_codes") or row.get("note"), 800)
    payload = dict(row)
    return EvidenceCard(
        id=_id_for({"trade": payload}),
        source_type="mock_trade_log",
        source_name=_clean(row.get("source") or "mock_trade_log", 120),
        source_url="",
        captured_at=now.astimezone(timezone.utc).isoformat(timespec="seconds"),
        event_time=_clean(row.get("timestamp") or now.isoformat(), 80),
        raw_text="\n".join(part for part in [title, body] if part),
        raw_payload=payload,
        symbols=[symbol] if symbol else [],
        markets=[_clean(row.get("market") or "KR", 16)],
        topics=["short_term_failure", "strategy_outcome"],
        event_type="strategy_outcome",
        confidence=0.82,
        freshness="intraday",
        impact_axes=["positioning", "sentiment"],
        summary=title,
        claims=[body] if body else [],
    )


def cards_to_source_refs(cards: list[EvidenceCard]) -> list[str]:
    refs: list[object] = []
    for card in cards:
        refs.extend([card.source_url, card.raw_payload.get("text_path"), card.raw_payload.get("raw_path")])
    return _dedupe(refs, limit=40)
