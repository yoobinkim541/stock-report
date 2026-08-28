"""Read-only trace for source event -> wiki -> retrieval -> answer citation."""

from __future__ import annotations

import argparse
import json
from typing import Iterable


def _ids(values: object) -> list[str]:
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, (list, tuple, set)):
        try:
            values = list(values)  # type: ignore[arg-type]
        except TypeError:
            return []
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = str(value or "").strip()[:120]
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _event_id(event: dict) -> str:
    for key in ("event_id", "id", "content_id"):
        value = str(event.get(key) or "").strip()
        if value:
            return value[:120]
    return ""


def _event_evidence_id(event: dict) -> str:
    explicit = str(event.get("evidence_id") or "").strip()
    if explicit:
        return explicit[:120]
    from reports.evidence_cards import event_to_evidence_card

    return event_to_evidence_card(event).id


def _page_evidence_ids(page: dict) -> list[str]:
    return _ids(page.get("evidence_ids") or [])


def _relevant_answer_rows(usage_rows: Iterable[dict], query_ids: set[str], evidence_id: str,
                          query_id: str | None) -> list[dict]:
    rows: list[dict] = []
    for row in usage_rows:
        if row.get("kind") != "answer_use":
            continue
        row_query_id = str(row.get("query_id") or "")
        if query_id and row_query_id != query_id:
            continue
        provided = set(_ids(row.get("provided_evidence_ids") or []))
        cited = set(_ids(row.get("cited_evidence_ids") or []))
        if row_query_id in query_ids or evidence_id in provided or evidence_id in cited:
            rows.append(row)
    return rows


def _relevant_context_rows(usage_rows: Iterable[dict], query_ids: set[str], wiki_page_ids: set[str],
                           evidence_id: str, query_id: str | None) -> list[dict]:
    rows: list[dict] = []
    for row in usage_rows:
        if row.get("kind") != "context_use":
            continue
        row_query_id = str(row.get("query_id") or "")
        if query_id and row_query_id != query_id:
            continue
        row_pages = set(_ids(row.get("page_ids") or row.get("retrieved_page_ids") or []))
        row_evidence = set(_ids(row.get("provided_evidence_ids") or []))
        if row_query_id in query_ids and wiki_page_ids.intersection(row_pages) and evidence_id in row_evidence:
            rows.append(row)
    return rows


def trace_source_evidence(events: list[dict], pages: list[dict], usage_rows: list[dict], *,
                          event_id: str = "", evidence_id: str = "", query_id: str | None = None) -> dict:
    """Trace a source without invoking retrieval, telemetry, or any write API."""
    requested_event_id = str(event_id or "").strip()
    requested_evidence_id = str(evidence_id or "").strip()
    event = None
    resolved_evidence_id = requested_evidence_id
    for candidate in events or []:
        if not isinstance(candidate, dict):
            continue
        candidate_event_id = _event_id(candidate)
        candidate_evidence_id = _event_evidence_id(candidate)
        if ((requested_event_id and requested_event_id in {candidate_event_id, candidate_evidence_id})
                or (requested_evidence_id and requested_evidence_id == candidate_evidence_id)):
            event = candidate
            resolved_evidence_id = candidate_evidence_id
            break

    missing: list[str] = []
    if event is None:
        missing.extend(["event", "evidence"])
        return {
            "ok": False,
            "missing": missing,
            "chain": {
                "event_id": requested_event_id,
                "evidence_id": resolved_evidence_id,
                "wiki_page_ids": [],
                "retrieval_query_ids": [],
                "context_evidence_ids": [],
                "cited_evidence_ids": [],
            },
        }

    if not resolved_evidence_id:
        missing.append("evidence")
    matching_pages = [page for page in pages or []
                      if resolved_evidence_id and resolved_evidence_id in _page_evidence_ids(page)]
    wiki_page_ids = _ids([page.get("id") for page in matching_pages])
    if not wiki_page_ids:
        missing.append("wiki")

    retrieval_rows = []
    for row in usage_rows or []:
        if row.get("kind") != "retrieval":
            continue
        row_query_id = str(row.get("query_id") or "")
        if query_id and row_query_id != query_id:
            continue
        if set(_ids(row.get("page_ids") or [])) & set(wiki_page_ids):
            retrieval_rows.append(row)
    retrieval_query_ids = _ids([row.get("query_id") for row in retrieval_rows])
    if not retrieval_query_ids:
        missing.append("retrieval")

    context_rows = _relevant_context_rows(
        usage_rows or [], set(retrieval_query_ids), set(wiki_page_ids), resolved_evidence_id, query_id
    )
    context_evidence_ids = _ids(
        evidence_id
        for row in context_rows
        for evidence_id in _ids(row.get("provided_evidence_ids") or [])
    )
    if not context_rows:
        missing.append("context_use")
    if not context_evidence_ids or resolved_evidence_id not in set(context_evidence_ids):
        missing.append("context_evidence")

    answer_rows = _relevant_answer_rows(usage_rows or [], set(retrieval_query_ids), resolved_evidence_id, query_id)
    cited_evidence_ids = _ids(
        evidence_id
        for row in answer_rows
        for evidence_id in _ids(row.get("cited_evidence_ids") or [])
    )
    if not answer_rows:
        missing.append("answer_use")
    if not cited_evidence_ids or resolved_evidence_id not in set(cited_evidence_ids):
        missing.append("answer_citation")
    if any(set(_ids(row.get("cited_evidence_ids") or [])) - set(_ids(row.get("provided_evidence_ids") or []))
           for row in answer_rows):
        missing.append("valid_answer_citation")

    return {
        "ok": not missing,
        "missing": _ids(missing),
        "event": {
            "id": _event_id(event),
            "evidence_id": resolved_evidence_id,
            "source": event.get("source") or "",
            "title": event.get("title") or event.get("summary") or "",
            "url": event.get("url") or event.get("source_url") or "",
            "observed_at": event.get("observed_at") or event.get("collected_at") or event.get("published_at") or "",
        },
        "chain": {
            "event_id": _event_id(event),
            "evidence_id": resolved_evidence_id,
            "wiki_page_ids": wiki_page_ids,
            "retrieval_query_ids": retrieval_query_ids,
            "context_evidence_ids": context_evidence_ids,
            "cited_evidence_ids": cited_evidence_ids,
        },
    }


def _load_trace_inputs(hours: int, limit: int) -> tuple[list[dict], list[dict], list[dict]]:
    from agent_console import context, evidence_usage, wiki

    events = context.recent_source_events(hours=max(1, hours), limit=max(1, limit))
    pages = []
    for record in wiki._wiki_records():
        pages.append(wiki._record_to_page(record))
    return events, pages, evidence_usage.read_usage_rows()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--event-id")
    target.add_argument("--evidence-id")
    parser.add_argument("--query-id")
    parser.add_argument("--surface", default="market", help="Reserved for query identity documentation")
    parser.add_argument("--hours", type=int, default=24 * 30)
    parser.add_argument("--limit", type=int, default=5000)
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("--allow-partial", action="store_true")
    args = parser.parse_args(argv)

    try:
        events, pages, usage_rows = _load_trace_inputs(args.hours, args.limit)
        result = trace_source_evidence(
            events,
            pages,
            usage_rows,
            event_id=args.event_id or "",
            evidence_id=args.evidence_id or "",
            query_id=args.query_id,
        )
    except Exception as exc:
        result = {"ok": False, "missing": ["runtime"], "error": f"{type(exc).__name__}: {exc}"}

    if args.as_json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    else:
        print("trace: " + ("ok" if result.get("ok") else "incomplete"))
        if result.get("missing"):
            print("missing: " + ", ".join(result["missing"]))
        chain = result.get("chain") or {}
        print("event -> evidence -> wiki -> retrieval -> context -> answer")
        print(" -> ".join([
            str(chain.get("event_id") or "-"),
            str(chain.get("evidence_id") or "-"),
            ",".join(chain.get("wiki_page_ids") or []) or "-",
            ",".join(chain.get("retrieval_query_ids") or []) or "-",
            ",".join(chain.get("context_evidence_ids") or []) or "-",
            ",".join(chain.get("cited_evidence_ids") or []) or "-",
        ]))
    has_root = "event" not in (result.get("missing") or []) and "evidence" not in (result.get("missing") or [])
    return 0 if result.get("ok") or (args.allow_partial and has_root) else 1


if __name__ == "__main__":
    raise SystemExit(main())
