"""위키 헬스 체크 — 주기적으로 위키 상태를 진단하고 스테일 페이지를 정리한다.

사용법:
    uv run python -m reports.wiki_health_check --dry-run
    uv run python -m reports.wiki_health_check
"""
from __future__ import annotations

import argparse
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
    print(format_report(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
