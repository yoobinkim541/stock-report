#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tests/test_attachment_parser.py — attachment_parser 단위 테스트
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import attachment_parser as ap


class TestDetectContentType(unittest.TestCase):
    def test_sell_keyword_in_text(self):
        self.assertEqual(ap.detect_content_type("오늘 NVDA 매도 완료"), "sell")

    def test_portfolio_keyword_in_caption(self):
        self.assertEqual(ap.detect_content_type("some text", caption="포트폴리오 현황"), "portfolio")

    def test_unknown_no_keywords(self):
        self.assertEqual(ap.detect_content_type("random text here"), "unknown")

    def test_sell_beats_portfolio_when_more_keywords(self):
        text = "매도 체결 처분 sell 거래"
        self.assertEqual(ap.detect_content_type(text), "sell")


class TestParsePortfolioFromText(unittest.TestCase):
    def test_basic_line(self):
        text = "NVDA 엔비디아 2 184.13 211.14"
        holdings = ap.parse_portfolio_from_text(text)
        self.assertEqual(len(holdings), 1)
        h = holdings[0]
        self.assertEqual(h["ticker"], "NVDA")
        self.assertEqual(h["shares"], 2.0)
        self.assertAlmostEqual(h["avg_price_usd"], 184.13)
        self.assertAlmostEqual(h["current_price_usd"], 211.14)

    def test_multiple_tickers(self):
        text = (
            "NVDA 엔비디아 2 184.13 211.14\n"
            "ORCL 오라클 4 182.85 225.78\n"
        )
        holdings = ap.parse_portfolio_from_text(text)
        tickers = [h["ticker"] for h in holdings]
        self.assertIn("NVDA", tickers)
        self.assertIn("ORCL", tickers)

    def test_dedup_same_ticker(self):
        text = "NVDA 2 184.13 211.14\nNVDA 3 180.00 211.14"
        holdings = ap.parse_portfolio_from_text(text)
        self.assertEqual(len(holdings), 1)
        self.assertEqual(holdings[0]["shares"], 2.0)

    def test_unknown_ticker_ignored(self):
        text = "FAKE 100 50.00 60.00"
        holdings = ap.parse_portfolio_from_text(text)
        self.assertEqual(len(holdings), 0)

    def test_name_populated(self):
        text = "MSFT 2 400.00 450.00"
        holdings = ap.parse_portfolio_from_text(text)
        self.assertEqual(holdings[0]["name"], ap.KNOWN_TICKERS["MSFT"])


class TestParseSellsFromText(unittest.TestCase):
    def test_basic_line_with_date(self):
        text = "2026-05-15 NVDA 10 400.00 520.00"
        sells = ap.parse_sells_from_text(text)
        self.assertEqual(len(sells), 1)
        s = sells[0]
        self.assertEqual(s["ticker"], "NVDA")
        self.assertEqual(s["date"], "2026-05-15")
        self.assertEqual(s["qty"], 10.0)
        self.assertAlmostEqual(s["buy_price_usd"], 400.0)
        self.assertAlmostEqual(s["sell_price_usd"], 520.0)

    def test_without_date_uses_today(self):
        from datetime import datetime
        text = "ORCL 4 182.85 225.78"
        sells = ap.parse_sells_from_text(text)
        self.assertEqual(len(sells), 1)
        today = datetime.now().strftime("%Y-%m-%d")
        self.assertEqual(sells[0]["date"], today)

    def test_insufficient_nums_skipped(self):
        text = "NVDA 10 400.00"  # only 2 numbers → skip
        sells = ap.parse_sells_from_text(text)
        self.assertEqual(len(sells), 0)

    def test_name_populated(self):
        text = "CPNG 15 19.99 16.60 commission 1.00"
        sells = ap.parse_sells_from_text(text)
        self.assertEqual(len(sells), 1)
        self.assertEqual(sells[0]["name"], ap.KNOWN_TICKERS["CPNG"])


class TestPendingSnapshot(unittest.TestCase):
    def setUp(self):
        self._orig_snap = ap.PENDING_SNAPSHOT_FILE
        self._tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self._tmp.close()
        ap.PENDING_SNAPSHOT_FILE = Path(self._tmp.name)

    def tearDown(self):
        ap.PENDING_SNAPSHOT_FILE = self._orig_snap
        if os.path.exists(self._tmp.name):
            os.unlink(self._tmp.name)

    def test_save_and_load(self):
        holdings = [{"ticker": "NVDA", "name": "엔비디아", "shares": 2.0,
                     "avg_price_usd": 184.13, "current_price_usd": 211.14}]
        ap.save_pending_snapshot(holdings)
        loaded = ap.load_pending_snapshot()
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["holdings"][0]["ticker"], "NVDA")

    def test_clear(self):
        holdings = [{"ticker": "ORCL", "name": "오라클", "shares": 4.0,
                     "avg_price_usd": 182.85, "current_price_usd": 225.78}]
        ap.save_pending_snapshot(holdings)
        ap.clear_pending_snapshot()
        self.assertIsNone(ap.load_pending_snapshot())

    def test_expired_returns_none(self):
        from datetime import timedelta
        holdings = [{"ticker": "NVDA", "name": "엔비디아", "shares": 2.0,
                     "avg_price_usd": 184.13, "current_price_usd": 211.14}]
        ap.save_pending_snapshot(holdings)
        # 만료된 시간으로 덮어쓰기
        data = json.loads(ap.PENDING_SNAPSHOT_FILE.read_text())
        from datetime import datetime
        old_time = (datetime.now() - timedelta(hours=73)).isoformat()
        data["parsed_at"] = old_time
        ap.PENDING_SNAPSHOT_FILE.write_text(json.dumps(data))
        self.assertIsNone(ap.load_pending_snapshot())


class TestPendingSells(unittest.TestCase):
    def setUp(self):
        self._orig_sells = ap.PENDING_SELLS_FILE
        self._tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self._tmp.close()
        ap.PENDING_SELLS_FILE = Path(self._tmp.name)

    def tearDown(self):
        ap.PENDING_SELLS_FILE = self._orig_sells
        if os.path.exists(self._tmp.name):
            os.unlink(self._tmp.name)

    def test_save_and_load(self):
        sells = [{"date": "2026-05-15", "ticker": "NVDA", "name": "엔비디아",
                  "qty": 10.0, "buy_price_usd": 400.0, "sell_price_usd": 520.0}]
        ap.save_pending_sells(sells)
        loaded = ap.load_pending_sells()
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["sells"][0]["ticker"], "NVDA")

    def test_clear(self):
        sells = [{"date": "2026-05-15", "ticker": "ORCL", "name": "오라클",
                  "qty": 4.0, "buy_price_usd": 182.85, "sell_price_usd": 225.78}]
        ap.save_pending_sells(sells)
        ap.clear_pending_sells()
        self.assertIsNone(ap.load_pending_sells())


class TestSummaryBuilders(unittest.TestCase):
    def test_snapshot_summary(self):
        pending = {
            "parsed_at": "2026-05-31T10:00:00",
            "holdings": [
                {"ticker": "NVDA", "name": "엔비디아",
                 "shares": 2.0, "avg_price_usd": 184.13, "current_price_usd": 211.14}
            ],
        }
        msg = ap.build_pending_snapshot_summary(pending)
        self.assertIn("NVDA", msg)
        self.assertIn("엔비디아", msg)
        self.assertIn("/apply_snapshot", msg)

    def test_sells_summary(self):
        pending = {
            "parsed_at": "2026-05-31T10:00:00",
            "sells": [
                {"date": "2026-05-15", "ticker": "ORCL", "name": "오라클",
                 "qty": 4.0, "buy_price_usd": 182.85, "sell_price_usd": 225.78}
            ],
        }
        msg = ap.build_pending_sells_summary(pending)
        self.assertIn("ORCL", msg)
        self.assertIn("오라클", msg)
        self.assertIn("/tax import apply", msg)


if __name__ == "__main__":
    unittest.main()
