# Evidence Wiki Strategy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a shared EvidenceCard foundation so AI console answers, wiki updates, and intraday strategy experiments use the same source-backed facts.

**Architecture:** Add focused modules for evidence normalization, evidence-aware console context, and intraday experiment logging. Existing `reports/source_wiki_curator.py`, `agent_console/agent.py`, and ML adaptive modules remain the integration points rather than being rewritten.

**Tech Stack:** Python 3, dataclasses, stdlib JSON/path/datetime, pytest, existing `agent_console` and `ml` modules.

## Global Constraints

- Preserve raw source text and raw payload for every EvidenceCard.
- Treat missing context as a retrieval trigger, not an answer stop condition.
- Keep Telegram/community data collected but lower-confidence until cross-checked.
- Do not copy code from AGPL repositories.
- Do not add live broker trading automation in this implementation.
- Increase samples through shadow decisions before increasing real trade frequency.
- Keep commits frequent and scoped; commit messages start with `add)` or `fix)` and are written in Korean.
- Existing untracked files unrelated to this work, such as `crm_report.json`, must not be staged.

---

## File Structure

- Create `reports/evidence_cards.py`: Pure normalization functions and dataclasses for EvidenceCard and InsightCard metadata. No IO beyond optional JSON-safe conversion.
- Modify `reports/source_wiki_curator.py`: Convert source events through `reports.evidence_cards`, expose `evidence_ids`, `staleness_policy`, `answer_hints`, and lower confidence for community-only groups.
- Create `agent_console/evidence_context.py`: Build compact evidence usage summaries for prompts and API context payloads.
- Modify `agent_console/agent.py`: Normalize intent names, expose required retrieval contracts in prompts, add evidence usage counts to `answer()["context"]`, and keep rules fallback honest.
- Create `ml/intraday_experiment.py`: DecisionSnapshot, OutcomeLabel, shadow decision labeling, and RiskGovernor in a pure testable module.
- Modify `agent_console/context.py`: Surface recent strategy experiment summaries through the existing context pack without requiring live broker access.
- Add tests:
  - `tests/test_evidence_cards.py`
  - Extend `tests/test_source_wiki_curator.py`
  - Extend `tests/test_agent_console.py`
  - `tests/test_intraday_experiment.py`
  - Extend `tests/test_agent_realtime_market_context.py`

---

### Task 1: EvidenceCard Normalization

**Files:**
- Create: `reports/evidence_cards.py`
- Test: `tests/test_evidence_cards.py`

**Interfaces:**
- Produces: `EvidenceCard` dataclass with `to_dict() -> dict`
- Produces: `InsightCardMeta` dataclass with `to_dict() -> dict`
- Produces: `event_to_evidence_card(event: dict, *, now: datetime | None = None) -> EvidenceCard`
- Produces: `trade_log_to_evidence_card(row: dict, *, now: datetime | None = None) -> EvidenceCard`
- Produces: `cards_to_source_refs(cards: list[EvidenceCard]) -> list[str]`
- Consumes: raw source collector event dictionaries and mock trade log dictionaries

- [ ] **Step 1: Write failing EvidenceCard source event tests**

Add `tests/test_evidence_cards.py`:

```python
from datetime import datetime, timedelta, timezone

from reports.evidence_cards import cards_to_source_refs, event_to_evidence_card


KST = timezone(timedelta(hours=9))


def test_event_to_evidence_card_preserves_raw_text_and_payload():
    event = {
        "source": "telegram:insidertracking",
        "title": "AI 데이터센터 전력 수요 증가",
        "url": "https://t.me/insidertracking/1",
        "body_raw": "반도체와 데이터센터 전력 병목이 같이 언급됐다.",
        "topic": "기술/AI",
        "tags": ["기술/AI"],
        "tickers": ["NVDA"],
        "published_at": "2026-07-28T09:30:00+09:00",
        "classification": {"kind": "community_signal", "topic": "기술/AI", "trust": "C"},
    }

    card = event_to_evidence_card(event, now=datetime(2026, 7, 28, 10, 0, tzinfo=KST))
    data = card.to_dict()

    assert data["id"].startswith("evidence-")
    assert data["source_type"] == "telegram"
    assert data["source_name"] == "telegram:insidertracking"
    assert data["source_url"] == "https://t.me/insidertracking/1"
    assert data["raw_text"] == "AI 데이터센터 전력 수요 증가\n반도체와 데이터센터 전력 병목이 같이 언급됐다."
    assert data["raw_payload"]["classification"]["trust"] == "C"
    assert data["symbols"] == ["NVDA"]
    assert data["topics"] == ["기술/AI"]
    assert data["event_type"] == "rumor"
    assert data["confidence"] == 0.45
    assert data["freshness"] == "intraday"
    assert "growth" in data["impact_axes"]
    assert "AI 데이터센터 전력 수요 증가" in data["summary"]


def test_cards_to_source_refs_keeps_url_and_raw_paths_deduped():
    first = event_to_evidence_card({
        "source": "saveticker",
        "title": "엔비디아 AI 서버 수요 확대",
        "url": "https://saveticker.com/nvda",
        "body_raw": "AI 서버 수요 확대",
        "raw_path": "/tmp/nvda.json",
        "text_path": "/tmp/nvda.txt",
        "topic": "기술/AI",
        "tickers": ["NVDA"],
    })
    second = event_to_evidence_card({
        "source": "saveticker",
        "title": "엔비디아 AI 서버 수요 확대",
        "url": "https://saveticker.com/nvda",
        "body_raw": "AI 서버 수요 확대",
        "raw_path": "/tmp/nvda.json",
        "text_path": "/tmp/nvda.txt",
        "topic": "기술/AI",
        "tickers": ["NVDA"],
    })

    assert cards_to_source_refs([first, second]) == [
        "https://saveticker.com/nvda",
        "/tmp/nvda.txt",
        "/tmp/nvda.json",
    ]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_evidence_cards.py -q`

Expected: FAIL with `ModuleNotFoundError: No module named 'reports.evidence_cards'`.

- [ ] **Step 3: Implement minimal EvidenceCard module**

Create `reports/evidence_cards.py`:

```python
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
    title = _clean(event.get("title"), 300)
    body = _clean(event.get("body_raw") or event.get("body") or event.get("body_excerpt"), 1800)
    raw_text = "\n".join(part for part in [title, body] if part)
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
        refs.extend([
            card.source_url,
            card.raw_payload.get("text_path"),
            card.raw_payload.get("raw_path"),
        ])
    return _dedupe(refs, limit=40)
```

- [ ] **Step 4: Run EvidenceCard tests**

Run: `.venv/bin/python -m pytest tests/test_evidence_cards.py -q`

Expected: PASS.

- [ ] **Step 5: Commit Task 1**

```bash
git add reports/evidence_cards.py tests/test_evidence_cards.py
git commit -m "add) EvidenceCard 정규화 모듈 추가" -m "원문, 출처, freshness, confidence, event_type을 공통 EvidenceCard로 변환하는 순수 모듈과 테스트를 추가했습니다.\n\n성과는 Saveticker/Telegram/모의투자 로그를 같은 근거 단위로 다룰 기반을 만든 점입니다. trade-off는 초기 impact_axes 분류가 키워드 기반이라 이후 LLM/분류기 보강 여지를 남긴다는 점입니다."
```

---

### Task 2: Source Wiki Curator Uses Evidence Metadata

**Files:**
- Modify: `reports/source_wiki_curator.py`
- Test: `tests/test_source_wiki_curator.py`

**Interfaces:**
- Consumes: `event_to_evidence_card(event) -> EvidenceCard`
- Consumes: `cards_to_source_refs(cards) -> list[str]`
- Produces on each wiki page: `evidence_ids`, `staleness_policy`, `answer_hints`, `conflicting_evidence_ids`

- [ ] **Step 1: Write failing source wiki metadata test**

Append to `tests/test_source_wiki_curator.py`:

```python
def test_build_wiki_pages_from_events_attaches_evidence_metadata():
    events = [
        {
            "source": "telegram:insidertracking",
            "title": "AI 데이터센터 전력 수요 증가",
            "url": "https://t.me/insidertracking/1",
            "body_raw": "반도체와 데이터센터 전력 병목이 같이 언급됐다.",
            "topic": "기술/AI",
            "tags": ["기술/AI"],
            "tickers": ["NVDA"],
            "classification": {"kind": "community_signal", "topic": "기술/AI", "trust": "C"},
        },
        {
            "source": "telegram:insidertracking",
            "title": "AI CAPEX 과열 우려",
            "url": "https://t.me/insidertracking/2",
            "body_raw": "CAPEX 부담과 마진 둔화 가능성이 언급됐다.",
            "topic": "기술/AI",
            "tags": ["기술/AI"],
            "tickers": ["NVDA"],
            "classification": {"kind": "community_signal", "topic": "기술/AI", "trust": "C"},
        },
    ]

    pages = swc.build_wiki_pages_from_events(events, now=datetime(2026, 7, 28, 10, 0, tzinfo=KST))
    page = next(page for page in pages if page["id"] == "source-topic-기술-ai")

    assert page["status"] == "draft"
    assert page["confidence"] == 0.45
    assert len(page["evidence_ids"]) == 2
    assert page["conflicting_evidence_ids"] == []
    assert page["staleness_policy"] == "refresh_after_12h"
    assert page["answer_hints"] == [
        "커뮤니티/텔레그램 단독 신호는 가격·수급·공식 자료와 교차확인 전에는 보조 근거로 둡니다.",
        "최신성은 evidence freshness를 우선 확인합니다.",
    ]
```

- [ ] **Step 2: Run source wiki test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_source_wiki_curator.py::test_build_wiki_pages_from_events_attaches_evidence_metadata -q`

Expected: FAIL because `evidence_ids` is missing.

- [ ] **Step 3: Wire EvidenceCard into source wiki pages**

Modify `reports/source_wiki_curator.py`:

```python
from reports.evidence_cards import cards_to_source_refs, event_to_evidence_card
```

Inside the page loop in `build_wiki_pages_from_events()`, before `refs = ...`:

```python
        evidence_cards = [event_to_evidence_card(row, now=now) for row in rows]
        refs = cards_to_source_refs(evidence_cards)
```

Replace the old `refs = _source_refs(rows)` assignment. Add these helpers near `_status_for()`:

```python
def _community_only(events: list[dict]) -> bool:
    roots = {_root_source(event.get("source")) for event in events}
    return bool(roots) and roots <= {"telegram", "arca"}


def _confidence_for_group(events: list[dict], evidence_cards: list) -> float:
    if not evidence_cards:
        return 0.5
    if _community_only(events):
        return min(float(card.confidence) for card in evidence_cards)
    return 0.78 if _status_for(events, cards_to_source_refs(evidence_cards)) == "reviewed" else 0.55


def _staleness_policy_for(evidence_cards: list) -> str:
    if any(card.freshness == "intraday" for card in evidence_cards):
        return "refresh_after_12h"
    return "refresh_after_24h"


def _answer_hints_for(events: list[dict], evidence_cards: list) -> list[str]:
    hints = []
    if _community_only(events):
        hints.append("커뮤니티/텔레그램 단독 신호는 가격·수급·공식 자료와 교차확인 전에는 보조 근거로 둡니다.")
    hints.append("최신성은 evidence freshness를 우선 확인합니다.")
    return hints
```

In the `page` dictionary, add:

```python
            "evidence_ids": [card.id for card in evidence_cards],
            "conflicting_evidence_ids": [],
            "staleness_policy": _staleness_policy_for(evidence_cards),
            "answer_hints": _answer_hints_for(rows, evidence_cards),
            "confidence": _confidence_for_group(rows, evidence_cards),
```

Remove the old static `"confidence": 0.78 if ... else 0.55` entry.

- [ ] **Step 4: Run source wiki tests**

Run: `.venv/bin/python -m pytest tests/test_source_wiki_curator.py tests/test_evidence_cards.py -q`

Expected: PASS.

- [ ] **Step 5: Commit Task 2**

```bash
git add reports/source_wiki_curator.py tests/test_source_wiki_curator.py
git commit -m "add) 소스 위키에 Evidence 메타데이터 연결" -m "source wiki curator가 EvidenceCard를 사용해 evidence_ids, freshness 정책, answer_hints, confidence를 페이지에 기록하도록 확장했습니다.\n\n성과는 위키 페이지가 원문 근거와 신뢰도 정책을 명시적으로 보유한다는 점입니다. trade-off는 충돌 evidence 탐지는 아직 빈 배열로 시작해 후속 분류 품질 개선이 필요하다는 점입니다."
```

---

### Task 3: Evidence Context Summary For AI Console

**Files:**
- Create: `agent_console/evidence_context.py`
- Modify: `agent_console/agent.py`
- Test: `tests/test_agent_console.py`

**Interfaces:**
- Produces: `build_usage_summary(pack: dict, *, wiki_pages: list[dict] | None = None, intent: dict | None = None) -> dict`
- Produces: `format_usage_lines(summary: dict) -> list[str]`
- Consumes: existing context pack shape from `agent_console.context.context_pack`

- [ ] **Step 1: Write failing context summary unit test**

Append to `tests/test_agent_console.py`:

```python
def test_evidence_context_usage_summary_counts_real_sources(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console.evidence_context import build_usage_summary, format_usage_lines

    pack = {
        "sources": {"events": [{"title": "a"}, {"title": "b"}]},
        "memory": [{"title": "m"}],
        "market_snapshot": {"quotes": [{"symbol": "QQQ"}, {"symbol": "NVDA"}], "status": "ok"},
        "paper": {"kr": {"decisions": [{"id": 1}, {"id": 2}, {"id": 3}]}},
    }
    summary = build_usage_summary(
        pack,
        wiki_pages=[{"id": "w1"}, {"id": "w2"}],
        intent={"name": "market_analysis"},
    )

    assert summary == {
        "intent": "market_analysis",
        "events": 2,
        "wiki": 2,
        "realtime": 2,
        "logs": 3,
        "engine": "pending",
        "retrieval": {"quote": "ok", "news": "ok", "broker": "unavailable"},
    }
    assert format_usage_lines(summary) == [
        "맥락: 시장 events 2 / wiki 2 / 실시간 2 / 로그 3",
        "엔진: pending",
        "수집: quote ok, news ok, broker unavailable",
    ]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_agent_console.py::test_evidence_context_usage_summary_counts_real_sources -q`

Expected: FAIL with `ModuleNotFoundError: No module named 'agent_console.evidence_context'`.

- [ ] **Step 3: Implement evidence context module**

Create `agent_console/evidence_context.py`:

```python
from __future__ import annotations


def _len(value: object) -> int:
    return len(value) if isinstance(value, list) else 0


def _paper_decision_count(paper: dict) -> int:
    total = 0
    for key in ("kr", "us", "combined"):
        item = paper.get(key) if isinstance(paper, dict) else None
        if isinstance(item, dict):
            total += _len(item.get("decisions"))
    return total


def build_usage_summary(pack: dict, *, wiki_pages: list[dict] | None = None,
                        intent: dict | None = None, engine: str = "pending") -> dict:
    sources = pack.get("sources") or {}
    market_snapshot = pack.get("market_snapshot") or {}
    quote_count = _len(market_snapshot.get("quotes"))
    event_count = _len(sources.get("events"))
    return {
        "intent": (intent or {}).get("name") or "general",
        "events": event_count,
        "wiki": _len(wiki_pages),
        "realtime": quote_count,
        "logs": _paper_decision_count(pack.get("paper") or {}),
        "engine": engine,
        "retrieval": {
            "quote": "ok" if quote_count else "unavailable",
            "news": "ok" if event_count else "unavailable",
            "broker": "ok" if (market_snapshot.get("broker") or {}).get("ok") else "unavailable",
        },
    }


def format_usage_lines(summary: dict) -> list[str]:
    retrieval = summary.get("retrieval") or {}
    return [
        f"맥락: 시장 events {summary.get('events', 0)} / wiki {summary.get('wiki', 0)} / 실시간 {summary.get('realtime', 0)} / 로그 {summary.get('logs', 0)}",
        f"엔진: {summary.get('engine') or 'pending'}",
        "수집: "
        f"quote {retrieval.get('quote', 'unavailable')}, "
        f"news {retrieval.get('news', 'unavailable')}, "
        f"broker {retrieval.get('broker', 'unavailable')}",
    ]
```

- [ ] **Step 4: Add usage summary to `answer()` context**

Modify `agent_console/agent.py` import:

```python
from . import context, evidence_context, realtime_market, shared_memory, storage, wiki
```

In `answer()`, after `engine = _LAST_LLM_ENGINE or "local-rules"`:

```python
    intent = _classify_question_intent(question, pack, history)
    evidence_usage = evidence_context.build_usage_summary(pack, intent=intent, engine=engine)
```

Add to returned `"context"`:

```python
            "intent": intent.get("name"),
            "evidence_usage": evidence_usage,
            "evidence_usage_lines": evidence_context.format_usage_lines(evidence_usage),
```

- [ ] **Step 5: Write API context regression test**

Append to `tests/test_agent_console.py`:

```python
def test_answer_context_exposes_intent_and_evidence_usage(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import agent

    monkeypatch.setattr(agent, "_safe_context_pack", lambda surface: {
        "surface": surface,
        "sources": {"events": [{"title": "시장 뉴스"}], "source_counts": [], "symbol_counts": []},
        "memory": [],
        "shared_memory": {"recordCount": 0},
        "market_snapshot": {"quotes": [{"symbol": "QQQ"}], "status": "ok"},
        "paper": {"kr": {"decisions": [{"id": "d1"}]}},
        "portfolio": {"holdings": []},
        "ml_activity": [],
        "focus": [],
    })
    monkeypatch.setattr(agent, "_safe_list_conversation", lambda limit, surface: [])
    monkeypatch.setattr(agent, "_safe_add_conversation", lambda *args, **kwargs: None)
    monkeypatch.setattr(agent, "_postprocess_chat", lambda *args, **kwargs: {"wiki_autocurate": "disabled"})
    monkeypatch.setattr(agent, "_compose_answer", lambda question, pack, history=None: "한국 시장은 수급 확인이 필요합니다.")

    result = agent.answer("한국증시는 어땠어", "market")

    assert result["context"]["intent"] == "market_analysis"
    assert result["context"]["evidence_usage"]["events"] == 1
    assert result["context"]["evidence_usage"]["realtime"] == 1
    assert result["context"]["evidence_usage_lines"][0] == "맥락: 시장 events 1 / wiki 0 / 실시간 1 / 로그 1"
```

- [ ] **Step 6: Run agent context tests**

Run: `.venv/bin/python -m pytest tests/test_agent_console.py::test_evidence_context_usage_summary_counts_real_sources tests/test_agent_console.py::test_answer_context_exposes_intent_and_evidence_usage -q`

Expected: PASS.

- [ ] **Step 7: Commit Task 3**

```bash
git add agent_console/evidence_context.py agent_console/agent.py tests/test_agent_console.py
git commit -m "add) AI 콘솔 Evidence 사용량 표시 추가" -m "AI 콘솔 응답 context에 intent와 실제 사용한 events/wiki/realtime/logs 요약을 노출했습니다.\n\n성과는 UI가 고정 숫자처럼 보이는 맥락 표시 대신 실제 조회량과 엔진 상태를 보여줄 수 있게 된 점입니다. trade-off는 wiki 사용량은 첫 단계에서 명시 전달된 페이지 수 기준이라 prompt 내부 검색 결과와 완전 동기화는 후속 작업에서 보강해야 한다는 점입니다."
```

---

### Task 4: Intent Names And Forbidden Template Contracts

**Files:**
- Modify: `agent_console/agent.py`
- Test: `tests/test_agent_console.py`

**Interfaces:**
- Produces stable intent names: `meta_debug`, `stock_compare`, `portfolio_review`, `market_analysis`, `technical_analysis`, `strategy_review`, `live_market_check`, `wiki_lookup`, `ticker_research`, `general`
- Produces: `_intent_contract_lines(intent: dict) -> list[str]` with retrieval and forbidden template requirements

- [ ] **Step 1: Write failing intent contract tests**

Append to `tests/test_agent_console.py`:

```python
def test_intent_names_match_evidence_strategy_spec(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import agent

    assert agent._classify_question_intent("왜 이렇게 답했어?")["name"] == "meta_debug"
    assert agent._classify_question_intent("JP모건 다른 IB랑 비교해줘")["name"] == "stock_compare"
    assert agent._classify_question_intent("한국증시는 어땠어")["name"] == "market_analysis"
    assert agent._classify_question_intent("단기투자 실적이 안좋은 이유가 뭘까")["name"] == "strategy_review"
    assert agent._classify_question_intent("지금 수급이랑 코스피 선물 확인해줘")["name"] == "live_market_check"
    assert agent._classify_question_intent("LLM wiki에 뭐가 쌓였어")["name"] == "wiki_lookup"


def test_stock_compare_contract_forbids_market_template_and_sets_peers(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import agent

    intent = agent._classify_question_intent("JP모건 다른 IB랑 비교해줘")
    lines = "\n".join(agent._intent_contract_lines(intent))

    assert intent["subject"] == "JPM"
    assert intent["default_peers"] == ["JPM", "GS", "MS", "BAC", "C"]
    assert "Yahoo Finance 최신 시세" in lines
    assert "현재 시장 상황 인식" in lines
    assert "시장 신호 점수" in lines
    assert "피어 비교표" in lines
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_agent_console.py::test_intent_names_match_evidence_strategy_spec tests/test_agent_console.py::test_stock_compare_contract_forbids_market_template_and_sets_peers -q`

Expected: FAIL because current names include `meta`, `peer_compare`, and `market_brief`.

- [ ] **Step 3: Rename and extend intent detection**

In `agent_console/agent.py`:

- Rename returned `"meta"` to `"meta_debug"`.
- Rename `"peer_compare"` to `"stock_compare"`.
- Rename `"market_brief"` to `"market_analysis"`.
- Add `_looks_like_strategy_review(ql: str) -> bool`:

```python
def _looks_like_strategy_review(ql: str) -> bool:
    return any(word in ql for word in ("단기투자", "모의투자", "데이트레이딩", "실적", "손실 원인", "왜 졌", "성과"))
```

- Add `_looks_like_live_market_check(ql: str) -> bool`:

```python
def _looks_like_live_market_check(ql: str) -> bool:
    return any(word in ql for word in ("지금 수급", "코스피 선물", "상승 하락 종목", "상승/하락", "장중", "실시간 시장", "현재 수급"))
```

- Add `_looks_like_wiki_lookup(ql: str) -> bool`:

```python
def _looks_like_wiki_lookup(ql: str) -> bool:
    return any(word in ql for word in ("wiki", "위키", "지식 카드", "쌓였", "근거 카드"))
```

Place checks in `_classify_question_intent()` after technical analysis and before portfolio review:

```python
    if _looks_like_live_market_check(ql):
        return _intent_contract(
            "live_market_check",
            answer_style="장중 시세·수급·선물·시장폭 freshness를 먼저 확인",
            required_steps=["실시간 스냅샷 확인", "수급/선물/시장폭 가능 여부 표시", "데이터 unavailable을 결론과 분리"],
            retrieval_plan=["market_snapshot", "broker/KRX 수급", "KOSPI200 선물", "상승/하락 종목 수", "USD/KRW"],
            forbidden_templates=["현재 시장 상황 인식", "시장 신호 점수"],
        )
    if _looks_like_strategy_review(ql):
        return _intent_contract(
            "strategy_review",
            answer_style="모의투자 로그, signal decision, outcome label, 비용/슬리피지 기반 개선 분석",
            required_steps=["모의투자 로그 확인", "결정 시점 feature와 outcome 분리", "비용/슬리피지 반영", "개선 가설 제시"],
            retrieval_plan=["kr/us mock ledger", "DecisionSnapshot", "OutcomeLabel", "RiskGovernor 경고"],
            forbidden_templates=["현재 시장 상황 인식", "MIXED", "시장 신호 점수"],
        )
    if _looks_like_wiki_lookup(ql):
        return _intent_contract(
            "wiki_lookup",
            answer_style="위키/근거 카드 현황과 검증 상태를 요약",
            required_steps=["source-backed와 unverified 분리", "stale 위키 표시", "원문 출처 링크 우선"],
            retrieval_plan=["LLM wiki", "source wiki", "QMD local search"],
            forbidden_templates=["현재 시장 상황 인식", "시장 신호 점수"],
        )
```

Update `_intent_contract_lines()` peer-specific condition:

```python
    if intent.get("name") == "stock_compare":
```

- [ ] **Step 4: Run intent tests**

Run: `.venv/bin/python -m pytest tests/test_agent_console.py::test_intent_names_match_evidence_strategy_spec tests/test_agent_console.py::test_stock_compare_contract_forbids_market_template_and_sets_peers -q`

Expected: PASS.

- [ ] **Step 5: Commit Task 4**

```bash
git add agent_console/agent.py tests/test_agent_console.py
git commit -m "fix) AI 콘솔 의도 라우팅 이름과 계약 정리" -m "질문 의도 이름을 설계 spec과 맞추고 live market, strategy review, wiki lookup 의도를 추가했습니다.\n\n성과는 화면 맥락보다 질문 의도가 우선하도록 테스트 계약이 더 선명해진 점입니다. trade-off는 키워드 기반 분류라 표현 다양성은 후속 LLM-router나 classifier로 확장해야 한다는 점입니다."
```

---

### Task 5: LLM Primary And Rules Fallback Guardrail

**Files:**
- Modify: `agent_console/agent.py`
- Test: `tests/test_agent_console.py`

**Interfaces:**
- Consumes: `_try_llm_chat(question, pack, history) -> str | None`
- Produces: `_violates_forbidden_templates(text: str, intent: dict) -> bool`
- Produces: `_compose_answer()` behavior that only uses rules fallback after LLM failure or forbidden-template rejection

- [ ] **Step 1: Write failing fallback guard tests**

Append to `tests/test_agent_console.py`:

```python
def test_general_llm_answer_rejects_forbidden_market_template(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import agent

    pack = {
        "surface": "market",
        "sources": {"events": []},
        "memory": [],
        "portfolio": {"holdings": []},
        "paper": {},
        "models": {"items": []},
        "market_snapshot": {"quotes": []},
    }
    monkeypatch.setattr(agent, "_try_llm_chat", lambda *args, **kwargs: "현재 시장 상황 인식\n시장 신호 점수\n엉뚱한 답")
    monkeypatch.setattr(agent, "_compose_market_context_fallback", lambda question, pack: "직접 답변 fallback")

    out = agent._compose_answer("왜 이렇게 답했어?", pack, history=[])

    assert out == "직접 답변 fallback"


def test_llm_primary_answer_survives_when_not_forbidden(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    from agent_console import agent

    pack = {
        "surface": "market",
        "sources": {"events": []},
        "memory": [],
        "portfolio": {"holdings": []},
        "paper": {},
        "models": {"items": []},
        "market_snapshot": {"quotes": []},
    }
    monkeypatch.setattr(agent, "_try_llm_chat", lambda *args, **kwargs: "JP모건은 GS/MS/BAC/C와 비교해야 합니다.")

    out = agent._compose_answer("JP모건 다른 IB랑 비교해줘", pack, history=[])

    assert "JP모건" in out
    assert "시장 신호 점수" not in out
```

- [ ] **Step 2: Run fallback tests to verify failure**

Run: `.venv/bin/python -m pytest tests/test_agent_console.py::test_general_llm_answer_rejects_forbidden_market_template tests/test_agent_console.py::test_llm_primary_answer_survives_when_not_forbidden -q`

Expected: At least one FAIL because forbidden-template rejection is not centralized.

- [ ] **Step 3: Add forbidden template validator**

In `agent_console/agent.py`, add near `_intent_contract_lines()`:

```python
def _violates_forbidden_templates(text: str, intent: dict) -> bool:
    body = str(text or "")
    return any(template and template in body for template in intent.get("forbidden_templates") or [])
```

At the start of `_compose_answer(question, pack, history=None)`, compute:

```python
    intent = _classify_question_intent(question, pack, history)
```

Whenever `_try_llm_chat()` returns `llm`, check:

```python
    if llm and not _violates_forbidden_templates(llm, intent):
        return llm
```

If an LLM result violates the forbidden template contract, continue to the existing fallback path. Do not mark `_LAST_LLM_ENGINE` as rules if an acceptable LLM already returned.

- [ ] **Step 4: Run fallback tests**

Run: `.venv/bin/python -m pytest tests/test_agent_console.py::test_general_llm_answer_rejects_forbidden_market_template tests/test_agent_console.py::test_llm_primary_answer_survives_when_not_forbidden -q`

Expected: PASS.

- [ ] **Step 5: Run broader AI console tests**

Run: `.venv/bin/python -m pytest tests/test_agent_console.py tests/test_agent_realtime_market_context.py tests/test_wiki_browser.py -q`

Expected: PASS.

- [ ] **Step 6: Commit Task 5**

```bash
git add agent_console/agent.py tests/test_agent_console.py
git commit -m "fix) AI 콘솔 LLM 우선 응답과 템플릿 가드 강화" -m "LLM 답변이 intent별 금지 템플릿을 위반할 때만 fallback으로 넘기고, 정상 LLM 답변은 규칙 기반 템플릿보다 우선하도록 테스트를 추가했습니다.\n\n성과는 LLM 연결 상태에서도 규칙 기반 시장 템플릿이 반복되는 문제를 줄인 점입니다. trade-off는 금지어 기반 검출이라 우회 표현은 후속 구조화 응답 검증으로 보강해야 한다는 점입니다."
```

---

### Task 6: Intraday Shadow Decision And Outcome Labels

**Files:**
- Create: `ml/intraday_experiment.py`
- Test: `tests/test_intraday_experiment.py`

**Interfaces:**
- Produces: `DecisionSnapshot` dataclass with `to_dict() -> dict`
- Produces: `OutcomeLabel` dataclass with `to_dict() -> dict`
- Produces: `label_shadow_decision(decision: DecisionSnapshot, prices: list[dict], *, horizons: tuple[int, ...] = (5, 15, 30)) -> list[OutcomeLabel]`
- Produces: `RiskGovernor` class with `assess(decisions: list[DecisionSnapshot], labels: list[OutcomeLabel], *, data_fresh: bool = True) -> dict`

- [ ] **Step 1: Write failing intraday experiment tests**

Create `tests/test_intraday_experiment.py`:

```python
from ml.intraday_experiment import DecisionSnapshot, RiskGovernor, label_shadow_decision


def test_label_shadow_decision_creates_multiple_horizons():
    decision = DecisionSnapshot(
        id="d1",
        timestamp="2026-07-28T09:00:00+09:00",
        symbol="005930",
        session_phase="open",
        features={"price": 70000},
        signals={"momentum": 0.7},
        decision="long",
        position_context={"current_qty": 0},
        expected_edge=0.004,
        risk_budget=0.01,
        cost_estimate=0.001,
        reason_codes=["open_momentum"],
    )
    prices = [
        {"minute": 0, "price": 70000},
        {"minute": 5, "price": 70400},
        {"minute": 15, "price": 69800},
        {"minute": 30, "price": 70700},
    ]

    labels = label_shadow_decision(decision, prices, horizons=(5, 15, 30))

    assert [label.horizon for label in labels] == ["5m", "15m", "30m"]
    assert round(labels[0].realized_return, 6) == round((70400 / 70000) - 1 - 0.001, 6)
    assert labels[1].quality_label == "bad"
    assert labels[2].max_favorable_excursion > 0


def test_risk_governor_blocks_stale_or_loss_cluster():
    decision = DecisionSnapshot(
        id="d1",
        timestamp="2026-07-28T09:00:00+09:00",
        symbol="005930",
        session_phase="open",
        features={"price": 70000},
        signals={"momentum": 0.7},
        decision="long",
        position_context={},
        expected_edge=0.004,
        risk_budget=0.01,
        cost_estimate=0.001,
        reason_codes=["open_momentum"],
    )
    labels = label_shadow_decision(decision, [
        {"minute": 0, "price": 70000},
        {"minute": 5, "price": 69000},
        {"minute": 15, "price": 68800},
        {"minute": 30, "price": 68700},
    ])

    stale = RiskGovernor(max_bad_labels=2).assess([decision], labels, data_fresh=False)
    loss_cluster = RiskGovernor(max_bad_labels=2).assess([decision], labels, data_fresh=True)

    assert stale["action"] == "shadow_only"
    assert "stale_data" in stale["reasons"]
    assert loss_cluster["action"] == "size_down"
    assert "loss_cluster" in loss_cluster["reasons"]
```

- [ ] **Step 2: Run tests to verify failure**

Run: `.venv/bin/python -m pytest tests/test_intraday_experiment.py -q`

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement intraday experiment module**

Create `ml/intraday_experiment.py`:

```python
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class DecisionSnapshot:
    id: str
    timestamp: str
    symbol: str
    session_phase: str
    features: dict[str, Any]
    signals: dict[str, Any]
    decision: str
    position_context: dict[str, Any]
    expected_edge: float
    risk_budget: float
    cost_estimate: float
    reason_codes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class OutcomeLabel:
    decision_id: str
    horizon: str
    realized_return: float
    max_adverse_excursion: float
    max_favorable_excursion: float
    slippage: float
    fees: float
    stop_hit: bool
    take_profit_hit: bool
    quality_label: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _price_at(prices: list[dict], minute: int) -> float | None:
    for row in prices:
        if int(row.get("minute", -1)) == minute:
            return float(row.get("price"))
    return None


def _path_returns(entry: float, prices: list[dict], horizon: int) -> list[float]:
    rows = [row for row in prices if int(row.get("minute", -1)) <= horizon]
    return [(float(row.get("price")) / entry) - 1 for row in rows if row.get("price") is not None]


def label_shadow_decision(decision: DecisionSnapshot, prices: list[dict], *,
                          horizons: tuple[int, ...] = (5, 15, 30)) -> list[OutcomeLabel]:
    entry = float(decision.features.get("price") or _price_at(prices, 0) or 0.0)
    if entry <= 0:
        return []
    labels: list[OutcomeLabel] = []
    side = -1.0 if decision.decision == "short" else 1.0
    for horizon in horizons:
        exit_price = _price_at(prices, horizon)
        if exit_price is None:
            continue
        gross = ((float(exit_price) / entry) - 1) * side
        net = gross - float(decision.cost_estimate or 0.0)
        path = [ret * side for ret in _path_returns(entry, prices, horizon)]
        mae = min(path) if path else 0.0
        mfe = max(path) if path else 0.0
        labels.append(OutcomeLabel(
            decision_id=decision.id,
            horizon=f"{horizon}m",
            realized_return=net,
            max_adverse_excursion=mae,
            max_favorable_excursion=mfe,
            slippage=float(decision.cost_estimate or 0.0) / 2,
            fees=float(decision.cost_estimate or 0.0) / 2,
            stop_hit=mae <= -abs(float(decision.risk_budget or 0.0)),
            take_profit_hit=mfe >= abs(float(decision.expected_edge or 0.0)) * 2,
            quality_label="good" if net > 0 else "bad",
        ))
    return labels


class RiskGovernor:
    def __init__(self, *, max_bad_labels: int = 3, max_turnover: int = 20) -> None:
        self.max_bad_labels = int(max_bad_labels)
        self.max_turnover = int(max_turnover)

    def assess(self, decisions: list[DecisionSnapshot], labels: list[OutcomeLabel], *,
               data_fresh: bool = True) -> dict:
        reasons: list[str] = []
        if not data_fresh:
            reasons.append("stale_data")
            return {"action": "shadow_only", "reasons": reasons}
        bad_count = sum(1 for label in labels if label.quality_label == "bad")
        if bad_count >= self.max_bad_labels:
            reasons.append("loss_cluster")
        if len(decisions) > self.max_turnover:
            reasons.append("excess_turnover")
        if not reasons:
            return {"action": "allow", "reasons": []}
        return {"action": "size_down", "reasons": reasons}
```

- [ ] **Step 4: Run intraday experiment tests**

Run: `.venv/bin/python -m pytest tests/test_intraday_experiment.py -q`

Expected: PASS.

- [ ] **Step 5: Commit Task 6**

```bash
git add ml/intraday_experiment.py tests/test_intraday_experiment.py
git commit -m "add) 장중 shadow decision 실험층 추가" -m "DecisionSnapshot, OutcomeLabel, shadow decision 라벨링, RiskGovernor를 순수 모듈로 추가했습니다.\n\n성과는 실거래 빈도를 늘리지 않고도 후보 전략 표본을 빠르게 쌓을 수 있는 기반을 만든 점입니다. trade-off는 초기 라벨러가 가격 리스트 기반이라 실제 1분봉 파일/Redis 연동은 다음 작업에서 연결해야 한다는 점입니다."
```

---

### Task 7: Strategy Experiment Summary In Context Pack

**Files:**
- Modify: `agent_console/context.py`
- Test: `tests/test_agent_realtime_market_context.py`

**Interfaces:**
- Produces in context pack: `strategy_experiments: {"ok": bool, "recent_decisions": int, "recent_labels": int, "risk_action": str, "reasons": list[str]}`
- Consumes: existing ML data directory from `AGENT_CONSOLE_ML_DATA_DIR`

- [ ] **Step 1: Write failing context pack strategy summary test**

Append to `tests/test_agent_realtime_market_context.py`:

```python
def test_context_pack_includes_strategy_experiment_summary(monkeypatch, tmp_path):
    from agent_console import context

    monkeypatch.setenv("AGENT_CONSOLE_ML_DATA_DIR", str(tmp_path / "ml-data"))
    ml_dir = tmp_path / "ml-data"
    ml_dir.mkdir()
    (ml_dir / "intraday_shadow_decisions.jsonl").write_text(
        '{"id":"d1","symbol":"005930"}\n{"id":"d2","symbol":"000660"}\n',
        encoding="utf-8",
    )
    (ml_dir / "intraday_outcome_labels.jsonl").write_text(
        '{"decision_id":"d1","quality_label":"bad"}\n',
        encoding="utf-8",
    )

    monkeypatch.setattr(context, "recent_source_events", lambda **kwargs: [])
    monkeypatch.setattr(context, "world_memory_rows", lambda **kwargs: [])
    monkeypatch.setattr(context, "latest_reports", lambda *args, **kwargs: [])
    monkeypatch.setattr(context, "ml_activity", lambda *args, **kwargs: [])
    monkeypatch.setattr(context, "portfolio_state", lambda: {"holdings": []})
    monkeypatch.setattr(context, "paper_state", lambda: {})
    monkeypatch.setattr(context, "model_state", lambda: {})
    monkeypatch.setattr(context.realtime_market, "load_market_snapshot", lambda: {"quotes": [], "status": "empty"})

    pack = context.context_pack("market")

    assert pack["strategy_experiments"] == {
        "ok": True,
        "recent_decisions": 2,
        "recent_labels": 1,
        "risk_action": "observe",
        "reasons": [],
    }
```

- [ ] **Step 2: Run test to verify failure**

Run: `.venv/bin/python -m pytest tests/test_agent_realtime_market_context.py::test_context_pack_includes_strategy_experiment_summary -q`

Expected: FAIL because `strategy_experiments` is missing.

- [ ] **Step 3: Add strategy experiment loader**

In `agent_console/context.py`, add:

```python
def _count_jsonl(path: Path, *, limit: int = 500) -> int:
    try:
        if not path.exists():
            return 0
        count = 0
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for count, _line in enumerate(fh, start=1):
                if count >= limit:
                    break
        return count
    except Exception:
        return 0


def strategy_experiment_state() -> dict:
    root = ml_data_dir()
    decisions = _count_jsonl(root / "intraday_shadow_decisions.jsonl")
    labels = _count_jsonl(root / "intraday_outcome_labels.jsonl")
    return {
        "ok": True,
        "recent_decisions": decisions,
        "recent_labels": labels,
        "risk_action": "observe",
        "reasons": [],
    }
```

In `context_pack()`, add:

```python
        "strategy_experiments": strategy_experiment_state(),
```

If `context_pack()` builds the result through a local variable, add it beside `paper`, `models`, or `ml_activity`.

- [ ] **Step 4: Add compact prompt lines for strategy experiments**

In `agent_console/agent.py`, add to `_compact_paper_context(pack)` after paper lines:

```python
    experiments = pack.get("strategy_experiments") or {}
    if experiments.get("ok"):
        lines.append(
            "- 전략 실험 "
            f"shadow decisions {experiments.get('recent_decisions', 0)}건 · "
            f"outcome labels {experiments.get('recent_labels', 0)}건 · "
            f"risk {experiments.get('risk_action', 'observe')}"
        )
```

- [ ] **Step 5: Run context tests**

Run: `.venv/bin/python -m pytest tests/test_agent_realtime_market_context.py tests/test_intraday_experiment.py -q`

Expected: PASS.

- [ ] **Step 6: Commit Task 7**

```bash
git add agent_console/context.py agent_console/agent.py tests/test_agent_realtime_market_context.py
git commit -m "add) 전략 실험 요약을 AI 콘솔 맥락에 연결" -m "intraday shadow decision과 outcome label 개수를 context_pack과 AI 콘솔 프롬프트에 노출했습니다.\n\n성과는 단기투자 질문에서 원장 표본 상태를 먼저 확인할 수 있게 된 점입니다. trade-off는 초기 요약이 파일 개수 기반이라 전략별 세부 성과 분해는 후속 리포팅 작업이 필요하다는 점입니다."
```

---

### Task 8: End-To-End Regression Run And Docs

**Files:**
- Modify: `docs/agent-console.md`
- Test: no new test file

**Interfaces:**
- Consumes all prior task interfaces
- Produces documented behavior for EvidenceCard, AI console intent routing, and strategy experiment summary

- [ ] **Step 1: Update agent console docs**

Append to `docs/agent-console.md`:

```markdown
## Evidence/Wiki/Strategy Context

AI 콘솔은 원문 소스, 위키 페이지, 실시간 시세, 모의투자 로그를 Evidence 기반 맥락으로 묶어 사용한다.

- `reports.evidence_cards.EvidenceCard`는 원문과 raw payload를 보존한다.
- `reports.source_wiki_curator`는 수집 이벤트를 EvidenceCard로 정규화한 뒤 source-backed wiki page를 만든다.
- 질문 의도는 화면 맥락보다 사용자 최신 문장을 우선한다.
- `stock_compare`, `meta_debug`, `technical_analysis`, `strategy_review`에서는 시장 상황 템플릿을 금지한다.
- 단기투자 표본은 실거래 빈도 증가가 아니라 shadow decision과 outcome label로 먼저 축적한다.
```

- [ ] **Step 2: Run focused regression suite**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_evidence_cards.py \
  tests/test_source_wiki_curator.py \
  tests/test_agent_console.py \
  tests/test_agent_realtime_market_context.py \
  tests/test_intraday_experiment.py \
  tests/test_wiki_browser.py \
  -q
```

Expected: PASS.

- [ ] **Step 3: Run broader safety smoke tests**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_kis_quote.py \
  tests/test_kiwoom_mock.py \
  tests/test_bot_healthcheck.py \
  tests/test_market_snapshot_store.py \
  tests/test_ml_walk_forward.py \
  tests/test_adaptive_framework.py \
  -q
```

Expected: PASS.

- [ ] **Step 4: Inspect git status**

Run: `git status --short`

Expected: only intended files modified, and unrelated `crm_report.json` still untracked if it existed before.

- [ ] **Step 5: Commit docs and final verification**

```bash
git add docs/agent-console.md
git commit -m "add) Evidence 기반 AI 콘솔 동작 문서화" -m "AI 콘솔이 EvidenceCard, source wiki, intent routing, shadow decision 표본을 어떻게 사용하는지 문서화했습니다.\n\n성과는 운영자가 규칙 기반 fallback과 LLM 우선 경로를 구분해 점검할 수 있게 된 점입니다. trade-off는 문서가 실제 구현과 함께 유지되어야 하므로 이후 라우팅 추가 시 테스트와 문서를 같이 갱신해야 한다는 점입니다."
```

- [ ] **Step 6: Final branch state check**

Run: `git log --oneline -8`

Expected: Task 1 through Task 8 commits appear in order above the design/spec commits.
