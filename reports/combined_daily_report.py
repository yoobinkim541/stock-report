#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""combined_daily_report.py — investment_report + market_report 전송용 통합 레이어.

기존 계산 엔진은 그대로 두고, 텔레그램 발송 단위만 하나의 요약/문서로 합친다.
"""
from __future__ import annotations

import argparse
from pathlib import Path


def _clean_lines(text: str, *, limit: int) -> list[str]:
    rows: list[str] = []
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line or set(line) <= {"-", "━", "="}:
            continue
        if line.startswith("#"):
            continue
        rows.append(line)
        if len(rows) >= limit:
            break
    return rows


def _section(label: str, text: str, *, limit: int) -> list[str]:
    lines = [f"[{label}]"]
    picked = _clean_lines(text, limit=limit)
    lines.extend(picked or ["- 요약 데이터 없음"])
    return lines


def _clamp(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 34)].rstrip() + "\n…(통합 요약 길이 제한으로 축약)"


def build_combined_summary(investment_summary: str, market_summary: str, *,
                           date: str, limit: int = 3500) -> str:
    """Telegram-first one-message summary for the daily report."""
    lines = [
        f"📊 {date} 통합 데일리 리포트",
        "",
        *_section("투자/포트폴리오", investment_summary, limit=14),
        "",
        *_section("시장/뉴스", market_summary, limit=12),
        "",
        "전체 문서는 첨부 파일 1개로 통합했습니다.",
    ]
    return _clamp("\n".join(lines), limit)


def build_combined_report(*, date: str, investment_report: str, market_report: str,
                          barbell_report: str = "", tracker_report: str = "") -> str:
    """Full Markdown report that keeps both source reports under one document."""
    parts = [
        f"# 통합 데일리 투자 리포트",
        f"날짜: {date}",
        "",
        "기존 투자/포트폴리오 리포트와 시장/뉴스 리포트를 하나의 전송 문서로 묶었습니다.",
        "",
        "---",
        "",
        "## 1. 투자/포트폴리오 리포트",
        "",
        (investment_report or "투자 리포트 데이터 없음").strip(),
        "",
        "---",
        "",
        "## 2. 시장/뉴스 리포트",
        "",
        (market_report or "시장 리포트 데이터 없음").strip(),
    ]

    appendix: list[str] = []
    if barbell_report.strip():
        appendix.extend(["### 바벨 전략 분석", "", barbell_report.strip(), ""])
    if tracker_report.strip():
        appendix.extend(["### 포트폴리오 트래커", "", tracker_report.strip(), ""])
    if appendix:
        parts.extend(["", "---", "", "## 3. 전략·추적 부록", "", *appendix])

    parts.extend([
        "",
        "---",
        "",
        "*본 통합 리포트는 자동 생성된 참고 자료입니다. 투자 결정은 본인의 판단에 따라 신중히 내리세요.*",
        "",
    ])
    return "\n".join(parts)


def _read_optional(path: str | None, *, label: str) -> str:
    if not path:
        return f"{label} 파일 경로 없음"
    p = Path(path).expanduser()
    if not p.exists():
        return f"{label} 파일 없음: {p}"
    return p.read_text(encoding="utf-8")


def write_combined_files(*, date: str, investment_report_path: str, investment_summary_path: str,
                         market_report_path: str, market_summary_path: str, out_report_path: str,
                         out_summary_path: str, barbell_report_path: str | None = None,
                         tracker_report_path: str | None = None) -> tuple[str, str]:
    investment_report = _read_optional(investment_report_path, label="투자 리포트")
    investment_summary = _read_optional(investment_summary_path, label="투자 요약")
    market_report = _read_optional(market_report_path, label="시장 리포트")
    market_summary = _read_optional(market_summary_path, label="시장 요약")
    barbell_report = _read_optional(barbell_report_path, label="바벨 전략") if barbell_report_path else ""
    tracker_report = _read_optional(tracker_report_path, label="포트폴리오 트래커") if tracker_report_path else ""

    report = build_combined_report(
        date=date,
        investment_report=investment_report,
        market_report=market_report,
        barbell_report=barbell_report,
        tracker_report=tracker_report,
    )
    summary = build_combined_summary(investment_summary, market_summary, date=date)

    out_report = Path(out_report_path).expanduser()
    out_summary = Path(out_summary_path).expanduser()
    out_report.parent.mkdir(parents=True, exist_ok=True)
    out_summary.parent.mkdir(parents=True, exist_ok=True)
    out_report.write_text(report, encoding="utf-8")
    out_summary.write_text(summary, encoding="utf-8")
    return str(out_report), str(out_summary)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build one Telegram delivery report from daily investment + market reports.")
    parser.add_argument("--date", required=True)
    parser.add_argument("--investment-report", required=True)
    parser.add_argument("--investment-summary", required=True)
    parser.add_argument("--market-report", required=True)
    parser.add_argument("--market-summary", required=True)
    parser.add_argument("--out-report", required=True)
    parser.add_argument("--out-summary", required=True)
    parser.add_argument("--barbell-report")
    parser.add_argument("--tracker-report")
    args = parser.parse_args(argv)

    out_report, out_summary = write_combined_files(
        date=args.date,
        investment_report_path=args.investment_report,
        investment_summary_path=args.investment_summary,
        market_report_path=args.market_report,
        market_summary_path=args.market_summary,
        out_report_path=args.out_report,
        out_summary_path=args.out_summary,
        barbell_report_path=args.barbell_report,
        tracker_report_path=args.tracker_report,
    )
    print(f"combined report: {out_report}")
    print(f"combined summary: {out_summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
