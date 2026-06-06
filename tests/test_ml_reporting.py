"""Tests for ml/reporting.py — p11 report/Telegram integration.

All tests use synthetic data; no network calls, no real Telegram token needed.
"""

import sys
from pathlib import Path

import pandas as pd
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from ml.backtest import BacktestResult, buy_and_hold
from ml.reporting import (
    build_ml_strategy_report,
    build_sample_ml_strategy_report,
    chunk_text,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _synthetic_result(name: str, n: int = 500, drift: float = 0.0004) -> BacktestResult:
    rng = np.random.default_rng(0)
    idx = pd.date_range("2022-01-03", periods=n, freq="B")
    prices = pd.Series(100 * np.cumprod(1 + rng.normal(drift, 0.012, n)), index=idx, name=name)
    return buy_and_hold(prices, name=name)


# ---------------------------------------------------------------------------
# chunk_text
# ---------------------------------------------------------------------------

class TestChunkText:
    def test_short_text_not_split(self):
        text = "hello\nworld"
        assert chunk_text(text, limit=3900) == [text]

    def test_long_text_split_into_multiple_chunks(self):
        text = "\n".join(["a" * 100] * 50)  # 5100 chars with newlines
        chunks = chunk_text(text, limit=3900)
        assert len(chunks) > 1
        for c in chunks:
            assert len(c) <= 3900

    def test_chunks_reassemble_to_original(self):
        text = "\n".join([f"line {i}" for i in range(200)])
        chunks = chunk_text(text, limit=500)
        assert "".join(chunks) == text

    def test_single_very_long_line_hard_split(self):
        text = "x" * 10000
        chunks = chunk_text(text, limit=3900)
        assert all(len(c) <= 3900 for c in chunks)
        assert "".join(chunks) == text

    def test_exact_limit_not_split(self):
        text = "a" * 3900
        chunks = chunk_text(text, limit=3900)
        assert len(chunks) == 1


# ---------------------------------------------------------------------------
# build_ml_strategy_report
# ---------------------------------------------------------------------------

class TestBuildMlStrategyReport:
    def test_minimal_report_contains_header(self):
        ml = _synthetic_result("ML 전략")
        text = build_ml_strategy_report(ml)
        assert "ML 전략 성과 리포트" in text

    def test_report_includes_ml_name(self):
        ml = _synthetic_result("MyMLStrat")
        text = build_ml_strategy_report(ml)
        assert "MyMLStrat" in text

    def test_report_with_benchmarks(self):
        ml = _synthetic_result("ML", drift=0.0005)
        qqq = _synthetic_result("QQQ", drift=0.0004)
        spy = _synthetic_result("SPY", drift=0.0003)
        text = build_ml_strategy_report(ml, qqq_result=qqq, spy_result=spy)
        assert "QQQ 매수보유" in text
        assert "SPY 매수보유" in text
        assert "ML 초과" in text

    def test_report_with_ib_metrics(self):
        ml = _synthetic_result("ML")
        ib = {"cum_return": 0.5, "cagr": 0.15, "max_drawdown": -0.20, "sharpe": 1.1}
        text = build_ml_strategy_report(ml, ib_metrics=ib)
        assert "Intelligence Barbell" in text

    def test_report_with_weights(self):
        ml = _synthetic_result("ML")
        weights = pd.Series({"SGOV": 0.10, "QQQ": 0.20, "NVDA": 0.70})
        text = build_ml_strategy_report(ml, weights=weights)
        assert "권장 포트폴리오 비중" in text
        assert "SGOV" in text
        assert "NVDA" in text

    def test_report_with_walk_forward_summary(self):
        ml = _synthetic_result("ML")
        wf = {"n_folds": 5, "mean_sharpe": 1.0, "std_sharpe": 0.2, "mean_cagr": 0.12}
        text = build_ml_strategy_report(ml, wf_summary=wf)
        assert "Walk-forward" in text
        assert "5" in text

    def test_report_has_leakage_caveat(self):
        ml = _synthetic_result("ML")
        text = build_ml_strategy_report(ml)
        assert "룩어헤드" in text
        assert "shift(1)" in text

    def test_report_no_none_literals_in_output(self):
        ml = _synthetic_result("ML")
        # cagr will be None if <365 days — use short series to force that
        idx = pd.date_range("2024-01-01", periods=100, freq="B")
        prices = pd.Series(range(100, 200), index=idx, dtype=float, name="Short")
        short_result = buy_and_hold(prices, name="Short")
        text = build_ml_strategy_report(short_result)
        assert "None" not in text

    def test_report_as_of_date_appears(self):
        ml = _synthetic_result("ML")
        text = build_ml_strategy_report(ml, as_of="2025-06-01")
        assert "2025-06-01" in text

    def test_full_report_fits_telegram_limit_or_chunks_cleanly(self):
        ml = _synthetic_result("ML")
        qqq = _synthetic_result("QQQ")
        spy = _synthetic_result("SPY")
        ib = {"cum_return": 0.4, "cagr": 0.13, "max_drawdown": -0.18, "sharpe": 0.9}
        weights = pd.Series({"SGOV": 0.10, "QQQ": 0.15, "NVDA": 0.20, "MSFT": 0.15, "GOOGL": 0.40})
        wf = {"n_folds": 6, "mean_sharpe": 1.1, "std_sharpe": 0.25, "mean_cagr": 0.14}
        text = build_ml_strategy_report(
            ml, qqq_result=qqq, spy_result=spy,
            ib_metrics=ib, weights=weights, wf_summary=wf,
            as_of="2025-06-01",
        )
        chunks = chunk_text(text)
        assert all(len(c) <= 3900 for c in chunks)


# ---------------------------------------------------------------------------
# build_sample_ml_strategy_report
# ---------------------------------------------------------------------------

class TestBuildSampleMlStrategyReport:
    def test_returns_non_empty_string(self):
        text = build_sample_ml_strategy_report()
        assert isinstance(text, str)
        assert len(text) > 100

    def test_contains_expected_sections(self):
        text = build_sample_ml_strategy_report()
        assert "성과 비교" in text
        assert "핵심 지표 요약" in text
        assert "권장 포트폴리오 비중" in text
        assert "검증" in text

    def test_contains_sample_tickers(self):
        text = build_sample_ml_strategy_report()
        assert "SGOV" in text
        assert "NVDA" in text

    def test_deterministic(self):
        assert build_sample_ml_strategy_report() == build_sample_ml_strategy_report()

    def test_sample_uses_optimizer_not_hardcoded(self):
        """Report must come from sweet-spot optimizer, not hard-coded positive paths."""
        text = build_sample_ml_strategy_report()
        # Optimizer label must appear (not the old fixed-drift demo label)
        assert "최적화 샘플" in text
        assert "2025-01-03 (샘플)" not in text

    def test_sample_report_is_cached(self):
        """Second call must return the identical object (module-level cache)."""
        import ml.reporting as rep
        rep._SAMPLE_REPORT_CACHE.clear()
        r1 = build_sample_ml_strategy_report()
        r2 = build_sample_ml_strategy_report()
        assert r1 is r2  # same object → cache hit


# ---------------------------------------------------------------------------
# Telegram bot wiring (no real network)
# ---------------------------------------------------------------------------

class TestBotWiring:
    def test_cmd_mlreport_calls_send_fn(self):
        """cmd_mlreport must call send_fn with report chunks, never hit real Telegram."""
        from telegram_bot import cmd_mlreport

        sent_messages: list[str] = []

        def fake_send(chat_id: str, text: str):
            sent_messages.append(text)

        cmd_mlreport("fake_chat_id", send_fn=fake_send)

        assert len(sent_messages) >= 1
        combined = "\n".join(sent_messages)
        assert "ML 전략 성과 리포트" in combined

    def test_dispatch_routes_mlreport(self):
        """dispatch('/mlreport', ...) must route to _dispatch_mlreport without calling real send."""
        import telegram_bot as bot

        sent: list[str] = []

        original_send = bot.send
        original_typing = bot.typing

        bot.send = lambda chat_id, text: sent.append(text)
        bot.typing = lambda chat_id: None

        try:
            bot.dispatch("/mlreport", "fake_chat")
        finally:
            bot.send = original_send
            bot.typing = original_typing

        combined = "\n".join(sent)
        assert "ML 전략 성과 리포트" in combined

    def test_mlreport_in_bot_commands(self):
        from telegram_bot import BOT_COMMANDS
        commands = [c["command"] for c in BOT_COMMANDS]
        assert "mlreport" in commands

    def test_mlreport_in_command_handlers(self):
        from telegram_bot import _COMMAND_HANDLERS
        assert "/mlreport" in _COMMAND_HANDLERS
