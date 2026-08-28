"""LLM wiki pipeline health model.

Pure, structured health summary for source collection, wiki hygiene, and
curation/promotion flow.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any

from agent_console import evidence_usage, wiki
from providers import news_labels
from reports import source_collector

STALE_WIKI_AGE_DAYS = 14
UNUSED_WIKI_DAYS = 30
RECENT_EVENT_HOURS = 24
NEWS_LABEL_FALLBACK_ALERT_RATIO = 0.5


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clean(value: object, limit: int = 240) -> str:
    text = str(value or "").replace("\x00", " ").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _health_bool(record: dict[str, Any], key: str, fallback: bool = False) -> bool:
    value = record.get(key)
    if value is None:
        return fallback
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "no", "none", "null"}
    return bool(value)


def _source_root(source: str) -> str:
    return str(source or "").strip().lower().split(":", 1)[0]


def _source_profile(source: str) -> dict[str, str]:
    root = _source_root(source)
    profile = dict(source_collector.SOURCE_CLASSIFICATION.get(root, {}))
    return {
        "family": str(profile.get("family") or "other"),
        "kind": str(profile.get("kind") or "event"),
        "trust": str(profile.get("trust") or "C"),
        "horizon": str(profile.get("horizon") or "1d"),
    }


def _page_id(page: dict[str, Any]) -> str:
    return _clean(page.get("id") or "", 80)


def _page_kind(page: dict[str, Any]) -> str:
    return _clean(page.get("kind") or "note", 40).lower() or "note"


def _page_status(page: dict[str, Any]) -> str:
    return _clean(page.get("status") or "draft", 40).lower() or "draft"


def _page_title(page: dict[str, Any]) -> str:
    return _clean(page.get("title") or "위키 페이지", 160)


def _page_links(page: dict[str, Any]) -> list[str]:
    return [_clean(link, 80) for link in (page.get("links") or []) if _clean(link, 80)]


def _page_source_refs(page: dict[str, Any]) -> list[str]:
    return [_clean(ref, 120) for ref in (page.get("source_refs") or []) if _clean(ref, 120)]


def _has_non_conversation_source_refs(page: dict[str, Any]) -> bool:
    return wiki.has_non_conversation_source_refs(page)


def _build_reverse_links(pages: list[dict[str, Any]]) -> dict[str, set[str]]:
    reverse: dict[str, set[str]] = defaultdict(set)
    for page in pages:
        page_id = _page_id(page)
        if not page_id:
            continue
        for link in _page_links(page):
            reverse[link].add(page_id)
        for backlink in page.get("backlinks") or []:
            backlink_id = _clean(backlink, 80)
            if backlink_id:
                reverse[page_id].add(backlink_id)
    return reverse


def _source_order(source_health: dict[str, dict[str, Any]]) -> list[str]:
    sources = list(source_collector.expected_sources())
    for key in source_health:
        if key not in sources:
            sources.append(key)
    return sources


def _build_source_rows(
    source_health: dict[str, dict[str, Any]],
    stale_rows: list[dict[str, Any]],
    recent_events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    stale_by_source = {str(item.get("source") or ""): item for item in stale_rows or []}
    recent_counts = Counter(_clean(event.get("source") or "unknown", 120) for event in recent_events or [])
    rows: list[dict[str, Any]] = []
    for source in _source_order(source_health):
        record = dict(source_health.get(source) or {})
        profile = _source_profile(source)
        stale = stale_by_source.get(source)
        last_count = int(record.get("last_count") or 0)
        last_success = _clean(record.get("last_success") or "", 80)
        zero_event_streak = 0 if last_count > 0 else (1 if last_success else None)
        availability = _clean(record.get("availability") or "available", 40).lower()
        fetched_count = int(record.get("last_fetched_count") if record.get("last_fetched_count") is not None else last_count)
        persisted_count = int(record.get("last_persisted_count") if record.get("last_persisted_count") is not None else last_count)
        deduped_count = int(record.get("last_deduped_count") if record.get("last_deduped_count") is not None else record.get("deduped_count") or 0)
        collision_count = int(record.get("last_collision_count") if record.get("last_collision_count") is not None else record.get("collision_count") or 0)
        fetch_success = _health_bool(record, "fetch_success", bool(record.get("last_fetch_success")))
        persist_success = _health_bool(record, "persist_success", bool(record.get("last_persist_success")))
        duplicate_only = (
            fetched_count > 0 and persisted_count == 0 and deduped_count >= fetched_count
            and collision_count == 0 and persist_success
        )
        zero_persist_streak = int(record.get("zero_persist_streak") or 0)
        cardinality_drop_streak = int(record.get("cardinality_drop_streak") or 0)
        rows.append({
            "source": source,
            "observed": source in source_health,
            "family": profile["family"],
            "kind": profile["kind"],
            "trust": profile["trust"],
            "horizon": profile["horizon"],
            "first_run": _clean(record.get("first_run") or "", 80),
            "last_run": _clean(record.get("last_run") or "", 80),
            "last_count": last_count,
            "last_success": last_success,
            "last_success_count": int(record.get("last_success_count") or 0),
            "last_fetched_count": fetched_count,
            "last_persisted_count": persisted_count,
            "deduped_count": deduped_count,
            "collision_count": collision_count,
            "fetch_success": fetch_success,
            "persist_success": persist_success,
            "last_duration_ms": int(record.get("last_duration_ms") or 0),
            "zero_persist_streak": zero_persist_streak,
            "zero_persist_alert": zero_persist_streak >= 2,
            "cardinality_drop_streak": cardinality_drop_streak,
            "cardinality_drop_alert": cardinality_drop_streak >= 2,
            "cardinality_ratio": record.get("last_cardinality_ratio"),
            "duplicate_only": duplicate_only,
            "availability": availability,
            "availability_reason": _clean(record.get("availability_reason") or "", 200),
            "last_error": _clean(record.get("last_error") or stale.get("error") or "", 200) if stale else _clean(record.get("last_error") or "", 200),
            "is_stale": bool(stale),
            "stale_hours": stale.get("hours") if stale else None,
            "stale_threshold": stale.get("threshold") if stale else None,
            "zero_event_streak": zero_event_streak,
            "recent_event_count": int(recent_counts.get(source, 0)),
            "raw_artifacts_visible": bool(persisted_count or recent_counts.get(source, 0)),
            "has_success": bool(last_success),
        })
    return rows


def _summarize_source_health(
    source_health: dict[str, dict[str, Any]],
    stale_rows: list[dict[str, Any]],
    recent_events: list[dict[str, Any]],
) -> dict[str, Any]:
    rows = _build_source_rows(source_health, stale_rows, recent_events)
    stale_sources = [row for row in rows if row["is_stale"]]
    missing_success = [row for row in rows if row["observed"] and not row["has_success"]]
    zero_event = [row for row in rows if row["observed"] and row["last_count"] == 0]
    blocked = [row for row in rows if row["availability"] in {"blocked", "disabled"}]
    failed = [row for row in rows if row["availability"] == "error"]
    zero_persist = [
        row for row in rows
        if row["observed"] and row["zero_persist_alert"]
    ]
    cardinality_drops = [row for row in rows if row["observed"] and row["cardinality_drop_alert"]]
    collisions = [row for row in rows if row["observed"] and row["collision_count"] > 0]
    duplicate_only = [row for row in rows if row["observed"] and row["duplicate_only"]]
    healthy = [
        row for row in rows
        if row["observed"] and not row["is_stale"] and row["has_success"] and row["last_count"] > 0
        and row["availability"] == "available" and not row["zero_persist_alert"]
        and not row["cardinality_drop_alert"] and row["collision_count"] == 0
    ]
    recent_counts = Counter(_clean(event.get("source") or "unknown", 120) for event in recent_events or [])
    return {
        "overall": {
            "tracked_sources": len(source_health),
            "expected_sources": len(source_collector.expected_sources()),
            "healthy_sources": len(healthy),
            "stale_sources": len(stale_sources),
            "missing_success_sources": len(missing_success),
            "zero_event_sources": len(zero_event),
            "blocked_sources": len(blocked),
            "failed_sources": len(failed),
            "zero_persist_sources": len(zero_persist),
            "cardinality_drop_sources": len(cardinality_drops),
            "collision_sources": len(collisions),
            "duplicate_only_sources": len(duplicate_only),
            "recent_event_total": len(recent_events or []),
            "recent_source_total": len([source for source, count in recent_counts.items() if count]),
        },
        "sources": rows,
        "stale_sources": stale_rows,
        "recent": {
            "event_total": len(recent_events or []),
            "sources": dict(recent_counts),
        },
    }


def _summarize_wiki_health(
    pages: list[dict[str, Any]],
    stats_data: dict[str, Any],
    lint_data: dict[str, Any],
    stale_pages: list[dict[str, Any]],
    unused_pages: list[dict[str, Any]],
) -> dict[str, Any]:
    source_backed = [page for page in pages if _has_non_conversation_source_refs(page)]
    unverified = [page for page in pages if not _has_non_conversation_source_refs(page)]
    open_question_count = sum(len(page.get("openQuestions") or []) for page in pages)
    lint_issues = lint_data.get("issues") or []
    lint_codes = Counter(_clean(issue.get("code") or "", 80) for issue in lint_issues)
    return {
        "page_count": len(pages),
        "stats": stats_data,
        "lint": lint_data,
        "lint_by_code": dict(lint_codes),
        "source_backed_count": len(source_backed),
        "unverified_count": len(unverified),
        "stale_count": len(stale_pages or []),
        "unused_count": len(unused_pages or []),
        "open_question_count": open_question_count,
        "archived_count": int((stats_data.get("status_counts") or {}).get("archived", 0)),
        "promoted_count": sum(1 for page in pages if _page_status(page) in {"reviewed", "stable"}),
        "source_missing_for_promoted_count": int(lint_codes.get("source_missing_for_promoted", 0)),
        "high_negative_feedback_count": int(lint_codes.get("high_negative_feedback", 0)),
        "zero_usage_count": int(lint_codes.get("zero_usage", 0)),
    }


def _source_digest_backlinks(
    pages: list[dict[str, Any]],
    reverse_links: dict[str, set[str]],
) -> list[dict[str, Any]]:
    by_id = {_page_id(page): page for page in pages if _page_id(page)}
    rows: list[dict[str, Any]] = []
    for page in pages:
        if _page_kind(page) != "source_digest" or _page_status(page) == "archived":
            continue
        page_id = _page_id(page)
        judgment_backlinks = []
        for backlink_id in sorted(reverse_links.get(page_id, set())):
            backlink_page = by_id.get(backlink_id)
            if not backlink_page:
                continue
            if _page_kind(backlink_page) == "source_digest":
                continue
            if _page_status(backlink_page) == "archived":
                continue
            judgment_backlinks.append(backlink_id)
        rows.append({
            "id": page_id,
            "title": _page_title(page),
            "status": _page_status(page),
            "source_refs": _page_source_refs(page),
            "open_questions": len(page.get("openQuestions") or []),
            "judgment_backlinks": judgment_backlinks,
            "linked_to_judgment": bool(judgment_backlinks),
            "has_source_refs": _has_non_conversation_source_refs(page),
        })
    return rows


def _summarize_curation_health(pages: list[dict[str, Any]]) -> dict[str, Any]:
    reverse_links = _build_reverse_links(pages)
    source_digests = _source_digest_backlinks(pages, reverse_links)
    linked = [page for page in source_digests if page["linked_to_judgment"]]
    unlinked = [page for page in source_digests if not page["linked_to_judgment"]]
    ready_for_promotion = [
        page for page in source_digests
        if page["has_source_refs"] and page["open_questions"] == 0 and not page["linked_to_judgment"]
    ]
    return {
        "source_digest_count": len(source_digests),
        "source_digest_linked_count": len(linked),
        "source_digest_unlinked_count": len(unlinked),
        "source_digest_unlinked_pages": unlinked,
        "ready_for_promotion_count": len(ready_for_promotion),
        "ready_for_promotion_pages": ready_for_promotion,
        "linked_source_digest_pages": linked,
    }


def _summarize_news_label_health() -> dict[str, Any]:
    """최근 뉴스 라벨 상태를 pipeline health에 연결한다.

    빈 라벨 파일은 아직 라벨링 대상이 없다는 뜻일 수 있으므로 장애로 올리지 않는다.
    실제 라벨이 존재하는데 LLM 라벨이 없거나 휴리스틱 비율이 높을 때만 attention을 낸다.
    """
    try:
        summary = dict(news_labels.label_health(news_labels.load_labels()))
    except Exception as exc:
        return {
            "status": "error",
            "attention": True,
            "total": 0,
            "llm_count": 0,
            "heuristic_count": 0,
            "fallback_ratio": 0.0,
            "error": _clean(exc, 240),
        }

    total = int(summary.get("total") or 0)
    llm_count = int(summary.get("llm_count") or 0)
    fallback_ratio = float(summary.get("fallback_ratio") or 0.0)
    reasons = []
    if total > 0 and llm_count == 0:
        reasons.append("llm_count=0")
    if total > 0 and fallback_ratio >= NEWS_LABEL_FALLBACK_ALERT_RATIO:
        reasons.append(f"fallback_ratio={fallback_ratio:.1%}")
    summary.update({
        "status": "no_data" if total == 0 else ("attention" if reasons else "ok"),
        "attention": bool(reasons),
        "fallback_alert_ratio": NEWS_LABEL_FALLBACK_ALERT_RATIO,
        "attention_reasons": reasons,
    })
    return summary


def _recommendations(
    source_section: dict[str, Any],
    wiki_section: dict[str, Any],
    curation_section: dict[str, Any],
    news_label_section: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    recs: list[dict[str, Any]] = []
    if (news_label_section or {}).get("attention"):
        total = int(news_label_section.get("total") or 0)
        llm_count = int(news_label_section.get("llm_count") or 0)
        fallback_ratio = float(news_label_section.get("fallback_ratio") or 0.0)
        reason = " · ".join(news_label_section.get("attention_reasons") or [])
        recs.append({
            "priority": 1,
            "category": "news_labels",
            "title": "뉴스 LLM 라벨 파이프라인 점검",
            "detail": f"최근 total {total}건 · llm {llm_count}건 · "
                       f"fallback {fallback_ratio:.1%}"
                       + (f" · {reason}" if reason else ""),
            "action": "Codex/Hermes 인증·runner 오류를 확인하고 휴리스틱 폴백 원인을 복구하세요.",
        })
    stale_rows = list(source_section.get("stale_sources") or [])
    missing_success = [row for row in source_section.get("sources") or [] if row.get("observed") and not row.get("has_success")]
    if stale_rows or missing_success:
        top_sources = stale_rows or missing_success
        detail_bits = []
        for row in top_sources[:3]:
            source = row.get("source", "unknown")
            if row.get("stale_hours") is not None:
                detail_bits.append(f"{source} {row['stale_hours']}h/{row.get('stale_threshold')}h")
            elif row.get("last_error"):
                detail_bits.append(f"{source}: {row['last_error']}")
            else:
                detail_bits.append(source)
        recs.append({
            "priority": 1,
            "category": "collection",
            "title": "소스 수집 공백 점검",
            "detail": " · ".join(detail_bits),
            "action": "크론·인증·채널 상태를 먼저 복구해서 원문 유입을 다시 살리세요.",
        })

    unlinked = int(curation_section.get("source_digest_unlinked_count") or 0)
    promoted_missing = int(wiki_section.get("source_missing_for_promoted_count") or 0)
    if unlinked or promoted_missing:
        detail_bits = []
        if unlinked:
            detail_bits.append(f"source_digest {unlinked}개가 judgment page로 연결되지 않음")
        if promoted_missing:
            detail_bits.append(f"reviewed/stable {promoted_missing}개가 원문 출처 부족")
        recs.append({
            "priority": 2,
            "category": "curation",
            "title": "큐레이션 승격 경로 보강",
            "detail": " · ".join(detail_bits),
            "action": "source_digest를 playbook/risk/concept로 승격하고 judgment 링크를 채우세요.",
        })

    stale_count = int(wiki_section.get("stale_count") or 0)
    unused_count = int(wiki_section.get("unused_count") or 0)
    if stale_count or unused_count:
        recs.append({
            "priority": 3,
            "category": "hygiene",
            "title": "위키 위생 정리",
            "detail": f"stale {stale_count}개 · unused {unused_count}개",
            "action": "오래된 페이지는 archive 또는 삭제 검토로 밀도를 높이세요.",
        })

    if not recs:
        recs.append({
            "priority": 0,
            "category": "healthy",
            "title": "파이프라인 안정",
            "detail": "현재 기준으로는 수집·큐레이션·위키 위생에 즉시 조치가 필요하지 않습니다.",
            "action": "이 상태를 유지하도록 크론과 큐레이션 규칙을 계속 관찰하세요.",
        })
    return recs


def build_pipeline_health_report(*, dry_run: bool = False) -> dict[str, Any]:
    source_health = source_collector.load_source_health() or {}
    stale_rows = source_collector.stale_sources(source_health)
    recent_events = source_collector.load_recent_events(hours=RECENT_EVENT_HOURS)
    pages = wiki.list_pages(status="all", surface="all", limit=400)
    stats_data = wiki.stats()
    lint_data = wiki.lint_pages(pages)
    stale_pages = wiki.list_stale_pages(max_age_days=STALE_WIKI_AGE_DAYS)
    unused_pages = wiki.list_unused_pages(days=UNUSED_WIKI_DAYS)
    source_section = _summarize_source_health(source_health, stale_rows, recent_events)
    wiki_section = _summarize_wiki_health(pages, stats_data, lint_data, stale_pages, unused_pages)
    curation_section = _summarize_curation_health(pages)
    news_label_section = _summarize_news_label_health()
    recommendations = _recommendations(
        source_section, wiki_section, curation_section, news_label_section
    )
    overall_status = "attention" if any(
        int(rec.get("priority") or 0) > 0 for rec in recommendations
    ) else "ok"
    try:
        usage = evidence_usage.usage_summary(hours=24)
    except Exception as exc:
        usage = {"error": _clean(exc, 240)}
    return {
        "dry_run": dry_run,
        "generated_at": _now_iso(),
        "source_health": source_section,
        "wiki_health": wiki_section,
        "curation_health": curation_section,
        "news_label_health": news_label_section,
        "overall": {
            "status": overall_status,
            "news_labels": news_label_section,
        },
        "evidence_usage": usage,
        "recommendations": recommendations,
    }


def format_pipeline_health_report(report: dict[str, Any]) -> str:
    source_section = report.get("source_health") or {}
    wiki_section = report.get("wiki_health") or {}
    curation_section = report.get("curation_health") or {}
    overall = source_section.get("overall") or {}
    pipeline_overall = report.get("overall") or {}
    news_labels_section = report.get("news_label_health") or pipeline_overall.get("news_labels") or {}
    stats = wiki_section.get("stats") or {}
    status_counts = stats.get("status_counts") or {}
    lines = ["[LLM Wiki Pipeline Health]"]
    lines.append("DRY RUN" if report.get("dry_run") else "LIVE CHECK")
    lines.append(f"generated_at: {report.get('generated_at') or '—'}")
    lines.append("")
    lines.append(
        f"소스: tracked {overall.get('tracked_sources', 0)} / expected {overall.get('expected_sources', 0)} · "
        f"healthy {overall.get('healthy_sources', 0)} · stale {overall.get('stale_sources', 0)} · "
        f"no-success {overall.get('missing_success_sources', 0)}"
    )
    lines.append(
        f"위키: total {stats.get('total', 0)} · source-backed {wiki_section.get('source_backed_count', 0)} · "
        f"unverified {wiki_section.get('unverified_count', 0)} · stale {wiki_section.get('stale_count', 0)} · "
        f"unused {wiki_section.get('unused_count', 0)}"
    )
    lines.append(
        f"큐레이션: source_digest {curation_section.get('source_digest_count', 0)} · "
        f"linked {curation_section.get('source_digest_linked_count', 0)} · "
        f"unlinked {curation_section.get('source_digest_unlinked_count', 0)}"
    )
    lines.append(
        f"상태: reviewed {status_counts.get('reviewed', 0)} · stable {status_counts.get('stable', 0)} · "
        f"archived {status_counts.get('archived', 0)}"
    )
    if int(news_labels_section.get("total") or 0) > 0:
        lines.append(
            f"뉴스 LLM 라벨: total {news_labels_section.get('total', 0)} · "
            f"llm {news_labels_section.get('llm_count', 0)} · "
            f"fallback {float(news_labels_section.get('fallback_ratio') or 0.0):.1%} · "
            f"status {news_labels_section.get('status', 'unknown')}"
        )
    else:
        lines.append("뉴스 LLM 라벨: no_data · 라벨 파일이 비어 있음")
    lines.append("")
    lines.append("권장 액션:")
    for rec in (report.get("recommendations") or [])[:5]:
        lines.append(
            f"- [{rec.get('category', 'unknown')}] {rec.get('title', '—')} — {rec.get('detail', '—')}"
        )
    return "\n".join(lines).strip() + "\n"
