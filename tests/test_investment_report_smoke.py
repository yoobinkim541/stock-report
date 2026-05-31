import os
import sys
import tempfile
from datetime import datetime as real_datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import investment_report as ir


class FixedDateTime:
    @classmethod
    def now(cls):
        return real_datetime(2026, 5, 31, 9, 0, 0)


def test_generate_report_writes_expected_files_without_network():
    with tempfile.TemporaryDirectory() as tmpdir:
        old_reports_dir = ir.REPORTS_DIR
        old_portfolio = ir.PORTFOLIO_TICKERS
        old_nasdaq = ir.NASDAQ_100
        old_datetime = ir.datetime
        old_score_ticker = ir.score_ticker
        old_detect_signals = ir.detect_signals
        old_market_summary = ir._market_summary
        old_korea_indices = ir._fetch_korea_indices
        old_arca_posts = ir._fetch_arca_posts
        old_company_name = ir._company_name
        old_manual_scores = ir.MANUAL_SCORES

        try:
            ir.REPORTS_DIR = tmpdir
            ir.PORTFOLIO_TICKERS = ["MSFT"]
            ir.NASDAQ_100 = ["MSFT"]
            ir.datetime = FixedDateTime
            ir.MANUAL_SCORES = {}
            ir.score_ticker = lambda ticker: {
                "ticker": ticker,
                "total_score": 80,
                "grade": "A",
                "sections": {},
                "notes": ["우수"],
            }
            ir.detect_signals = lambda ticker: {
                "overall_signal": "Positive",
                "signals_found": ["모멘텀 강세"],
                "warnings": [],
                "critical": [],
                "price_info": {"1d_change_pct": 4.2, "1mo_change_pct": 8.0, "current_price": 100.0},
                "volume_info": {"ratio": 1.1},
            }
            ir._market_summary = lambda: {"spy_price": 500.0, "spy_change": 1.2, "spy_name": "SPY"}
            ir._fetch_korea_indices = lambda: ("2,500.00", "850.00", "1,300.00")
            ir._fetch_arca_posts = lambda: [{"title": "테스트", "url": "https://example.com", "category": "📰뉴스", "when": "09:00", "views": "10", "likes": "2"}]
            ir._company_name = lambda ticker: f"{ticker} Corporation"

            report_path, json_path = ir.generate_report()

            report_file = Path(report_path)
            json_file = Path(json_path)
            summary_file = Path(tmpdir) / "investment-summary-2026-05-31.txt"
            clean_file = Path(tmpdir) / "investment-summary-2026-05-31.json"

            assert report_file.exists()
            assert json_file.exists()
            assert summary_file.exists()
            assert clean_file.exists()

            report_text = report_file.read_text(encoding="utf-8")
            summary_text = summary_file.read_text(encoding="utf-8")
            assert "일일 투자 자동화 레포트" in report_text
            assert "아카라이브 커뮤니티 동향" in report_text
            assert "MSFT — MSFT Corporation" in report_text
            assert "SPY $500.0 (+1.20%)" in summary_text
        finally:
            ir.REPORTS_DIR = old_reports_dir
            ir.PORTFOLIO_TICKERS = old_portfolio
            ir.NASDAQ_100 = old_nasdaq
            ir.datetime = old_datetime
            ir.score_ticker = old_score_ticker
            ir.detect_signals = old_detect_signals
            ir._market_summary = old_market_summary
            ir._fetch_korea_indices = old_korea_indices
            ir._fetch_arca_posts = old_arca_posts
            ir._company_name = old_company_name
            ir.MANUAL_SCORES = old_manual_scores
