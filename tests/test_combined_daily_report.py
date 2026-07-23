import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "reports"))

from combined_daily_report import build_combined_report, build_combined_summary


def test_build_combined_summary_prefers_one_telegram_message():
    summary = build_combined_summary(
        "📱 2026-07-23 투자 요약\n오늘 할 일: 리스크 점검\nSPY ▲1.0%",
        "📈 2026-07-23 시황 요약\n결론: 혼조세\n핵심 뉴스:\n- 금리 경계",
        date="2026-07-23",
    )

    assert summary.startswith("📊 2026-07-23 통합 데일리 리포트")
    assert "투자/포트폴리오" in summary
    assert "시장/뉴스" in summary
    assert "리스크 점검" in summary
    assert "금리 경계" in summary
    assert len(summary) < 3500


def test_build_combined_report_keeps_market_and_investment_sections():
    report = build_combined_report(
        date="2026-07-23",
        investment_report="# 일일 투자 자동화 레포트\n\n## 0. 오늘 한눈에\n- 포트 유지",
        market_report="# 주식시장 일일 리포트\n\n## 오늘 요약\n- 시장 혼조",
        barbell_report="Phase 2 · 중립",
        tracker_report="포트폴리오 히스토리 기록 완료",
    )

    assert report.startswith("# 통합 데일리 투자 리포트")
    assert "## 1. 투자/포트폴리오 리포트" in report
    assert "## 2. 시장/뉴스 리포트" in report
    assert "## 3. 전략·추적 부록" in report
    assert "포트 유지" in report
    assert "시장 혼조" in report
    assert "Phase 2" in report
