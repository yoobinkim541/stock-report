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

STALE_MAX_AGE_DAYS = 14
VERY_UNUSED_DAYS = 60


def build_health_report(dry_run: bool = False) -> dict:
    stats_data = wiki.stats()
    lint_data = wiki.lint_pages()
    stale_pages = wiki.list_stale_pages(max_age_days=STALE_MAX_AGE_DAYS)
    unused_pages = wiki.list_unused_pages(days=30)
    very_unused_pages = wiki.list_unused_pages(days=VERY_UNUSED_DAYS)

    report = {
        "dry_run": dry_run,
        "stats": stats_data,
        "lint_issues": lint_data.get("issues", []),
        "stale_count": len(stale_pages),
        "unused_count": len(unused_pages),
        "very_unused_count": len(very_unused_pages),
        "recommendations": [],
    }

    if not dry_run:
        archive_result = wiki.archive_stale_pages(max_age_days=STALE_MAX_AGE_DAYS)
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

    stats_data = report["stats"]
    prompt = f"""위키 상태 리포트:
- 전체: {stats_data['total']}페이지
- 미검증: {stats_data['trust_counts']['unverified']}
- Archived: {stats_data['status_counts']['archived']}
- 스테일(14일+): {report['stale_count']}
- 미사용(30일+): {report['unused_count']}

린트 이슈:
{json.dumps(report['lint_issues'][:10], indent=2, ensure_ascii=False)}

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
    stats_data = report.get("stats", {})
    status_counts = stats_data.get("status_counts", {})
    lines = ["[위키 헬스 체크]"]
    lines.append("(DRY RUN — 실제 변경 없음)" if report.get("dry_run") else "(실행 모드 — 스테일 페이지 archive 적용됨)")
    lines.append("")
    lines.append(f"전체: {stats_data.get('total', 0)} 페이지")
    lines.append(f"  활성: {sum(status_counts.get(s, 0) for s in ('draft', 'reviewed', 'stable'))}")
    lines.append(f"  Archived: {status_counts.get('archived', 0)}")
    lines.append(f"  스테일({STALE_MAX_AGE_DAYS}일+): {report.get('stale_count', 0)}")
    lines.append(f"  미사용(30일+): {report.get('unused_count', 0)}")
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
                wiki.archive_stale_pages(max_age_days=0)
            elif act == "delete":
                wiki.delete_page(page_id)
            elif act == "reactivate":
                page = wiki.get_page(page_id)
                if page:
                    page["status"] = "active"
                    wiki.upsert_page(page)

    print(format_report(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
