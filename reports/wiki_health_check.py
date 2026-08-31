"""위키 헬스 체크 — 주기적으로 위키 상태를 진단하고 스테일 페이지를 정리한다.

사용법:
    uv run python -m reports.wiki_health_check --dry-run
    uv run python -m reports.wiki_health_check
"""
from __future__ import annotations

import argparse
import json
import sys

from agent_console import wiki
from reports.wiki_pipeline_health import build_pipeline_health_report

STALE_MAX_AGE_DAYS = 14
VERY_UNUSED_DAYS = 60


def _pipeline_report(report: dict) -> dict:
    return report.get("pipeline_report") or report


def build_health_report(dry_run: bool = False) -> dict:
    pipeline_report = build_pipeline_health_report(dry_run=dry_run)
    wiki_health = pipeline_report.get("wiki_health") or {}
    stats_data = dict(wiki_health.get("stats") or wiki.stats())
    lint_data = dict(wiki_health.get("lint") or wiki.lint_pages())
    stale_pages = wiki.list_stale_pages(max_age_days=STALE_MAX_AGE_DAYS)
    unused_pages = wiki.list_unused_pages(days=30)
    very_unused_pages = [
        page for page in wiki.list_unused_pages(days=VERY_UNUSED_DAYS)
        if page.get("kind") != "source_digest"
    ]

    report = {
        "dry_run": dry_run,
        "pipeline_report": pipeline_report,
        "source_health": pipeline_report.get("source_health") or {},
        "wiki_health": wiki_health,
        "curation_health": pipeline_report.get("curation_health") or {},
        "stats": stats_data,
        "lint_issues": lint_data.get("issues", []),
        "stale_count": len(stale_pages),
        "unused_count": len(unused_pages),
        "unused_provenance_count": sum(1 for page in unused_pages if page.get("kind") == "source_digest"),
        "unused_actionable_count": sum(1 for page in unused_pages if page.get("kind") != "source_digest"),
        "very_unused_count": len(very_unused_pages),
        "recommendations": [],
    }

    for rec in pipeline_report.get("recommendations") or []:
        if not isinstance(rec, dict):
            continue
        report["recommendations"].append(
            f"[{rec.get('category', 'unknown')}] {rec.get('title', '—')}: {rec.get('detail', '—')}"
        )

    if not dry_run:
        archive_result = wiki.archive_stale_pages(max_age_days=STALE_MAX_AGE_DAYS)
        report["archive_result"] = archive_result
        if archive_result.get("archived") or archive_result.get("deleted"):
            report["recommendations"].append(
                f"stale 페이지 {archive_result.get('archived', 0)}개 archive, "
                f"만료 archive {archive_result.get('deleted', 0)}개 삭제"
            )

    if very_unused_pages:
        report["recommendations"].append(
            f"{len(very_unused_pages)}개 페이지가 {VERY_UNUSED_DAYS}일+ 미사용 — 삭제 검토 필요"
        )

    return report


def run_llm_health_review(report: dict) -> list[dict]:
    """LLM에게 위키 상태를 보여주고 구체적인 큐레이션 액션 추천받기"""
    from agent_console.agent import _try_llm_prompt

    pipeline = _pipeline_report(report)
    source_health = pipeline.get("source_health") or {}
    wiki_health = pipeline.get("wiki_health") or {}
    curation_health = pipeline.get("curation_health") or {}
    stats_data = report.get("stats") or wiki_health.get("stats") or {}
    source_overall = source_health.get("overall") or {}
    prompt = f"""위키 상태 리포트:
소스 tracked: {source_overall.get('tracked_sources', 0)} / expected {source_overall.get('expected_sources', 0)}
소스 stale: {source_overall.get('stale_sources', 0)} / no-success: {source_overall.get('missing_success_sources', 0)}
위키 total: {stats_data.get('total', 0)}
위키 unverified: {wiki_health.get('unverified_count', 0)}
위키 archived: {stats_data.get('status_counts', {}).get('archived', 0)}
스테일(14일+): {wiki_health.get('stale_count', report.get('stale_count', 0))}
미사용(30일+): {wiki_health.get('unused_actionable_count', report.get('unused_actionable_count', report.get('unused_count', 0)))}
원문 보존 미사용: {wiki_health.get('unused_provenance_count', report.get('unused_provenance_count', 0))}
source_digest unlinked: {curation_health.get('source_digest_unlinked_count', 0)}

린트 이슈:
{json.dumps((report.get('lint_issues') or [])[:10], indent=2, ensure_ascii=False)}

권장 액션:
{json.dumps((pipeline.get('recommendations') or [])[:5], indent=2, ensure_ascii=False)}

다음 중 어떤 액션이 필요할까요?
1. 어떤 페이지를 archived/삭제할까?
2. 어떤 페이지들을 병합할까?
3. 어떤 페이지를 재활성화(unarchive)할까?
4. 전반적인 위키 건강도 평가 (1-10)

JSON으로 응답해주세요:
{{"actions": [{{"page_id": "...", "action": "archive|delete|merge|reactivate", "reason": "..."}}], "health_score": 8, "summary": "..."}}"""

    try:
        llm_response = _try_llm_prompt(prompt)
        return json.loads(llm_response).get("actions", [])
    except Exception:
        return []


def format_report(report: dict) -> str:
    pipeline = _pipeline_report(report)
    source_section = pipeline.get("source_health") or {}
    wiki_section = pipeline.get("wiki_health") or {}
    curation_section = pipeline.get("curation_health") or {}
    stats_data = report.get("stats") or wiki_section.get("stats") or {}
    status_counts = stats_data.get("status_counts", {})
    lines = ["[위키 헬스 체크]"]
    lines.append("(DRY RUN — 실제 변경 없음)" if report.get("dry_run") else "(실행 모드 — 스테일 페이지 archive 적용됨)")
    lines.append("")
    lines.append(f"전체: {stats_data.get('total', 0)} 페이지")
    lines.append(f"  활성: {sum(status_counts.get(s, 0) for s in ('draft', 'reviewed', 'stable'))}")
    lines.append(f"  Archived: {status_counts.get('archived', 0)}")
    lines.append(f"  스테일({STALE_MAX_AGE_DAYS}일+): {report.get('stale_count', 0)}")
    lines.append(
        f"  미사용(30일+): {report.get('unused_actionable_count', report.get('unused_count', 0))}"
        f" · 원문 보존: {report.get('unused_provenance_count', 0)}"
    )
    lines.append("")

    source_overall = source_section.get("overall") or {}
    lines.append(
        f"소스 파이프라인: tracked {source_overall.get('tracked_sources', 0)} / "
        f"expected {source_overall.get('expected_sources', 0)} · "
        f"healthy {source_overall.get('healthy_sources', 0)} · stale {source_overall.get('stale_sources', 0)}"
    )
    lines.append(
        f"큐레이션: source_digest {curation_section.get('source_digest_count', 0)} · "
        f"linked {curation_section.get('source_digest_linked_count', 0)} · "
        f"unlinked {curation_section.get('source_digest_unlinked_count', 0)}"
    )
    lines.append("")

    lint_issues = report.get("lint_issues") or []
    if lint_issues:
        lines.append(f"린트 이슈: {len(lint_issues)}개")
        for issue in lint_issues[:5]:
            lines.append(f"  - {issue.get('title', '?')}: {issue.get('code', '?')}")
        lines.append("")

    recommendations = report.get("recommendations") or []
    if recommendations:
        lines.append("권장 액션:")
        for rec in recommendations:
            lines.append(f"  - {rec}")
        lines.append("")

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="위키 헬스 체크")
    parser.add_argument("--dry-run", action="store_true", help="변경 없이 리포트만 출력")
    args = parser.parse_args()

    report = build_health_report(dry_run=args.dry_run)

    if not args.dry_run:
        try:
            llm_actions = run_llm_health_review(report)
        except Exception:
            llm_actions = []
        for action in llm_actions:
            act = action.get("action")
            page_id = action.get("page_id")
            if act == "archive":
                page = wiki.get_page(page_id)
                if page:
                    page["status"] = "archived"
                    wiki.upsert_page(page)
            elif act == "delete":
                wiki.delete_page(page_id)
            elif act == "reactivate":
                page = wiki.get_page(page_id)
                if page:
                    # "active" 는 VALID_STATUSES 에 없어 normalize_trust_status() 가
                    # 조용히 "draft" 로 깎아내렸다(감사 2026-08-26) — reviewed 로 복귀.
                    page["status"] = "reviewed"
                    wiki.upsert_page(page)

    print(format_report(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
