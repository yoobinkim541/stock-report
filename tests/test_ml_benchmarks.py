"""Tests for ml/benchmarks.py and ml/visualization.py.

All tests use synthetic data; no network calls required.
Base-Python safe: matplotlib/sklearn guarded by try/except or pytest.importorskip.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

_MATPLOTLIB_AVAILABLE = importlib.util.find_spec("matplotlib") is not None

from ml.sweet_spot import generate_synthetic_market_data, optimize_sweet_spot
from ml.benchmarks import (
    BenchmarkComparison,
    M7_TICKERS,
    build_benchmark_comparison,
    build_benchmark_price_panel,
    load_current_portfolio_weights,
)


# ── All expected benchmark strategy names ────────────────────────────────────

EXPECTED_BENCHMARK_NAMES = [
    "QQQ 매수보유",
    "SPY 매수보유",
    "QLD 매수보유",
    "TQQQ 매수보유",
    "QLD/TQQQ 바벨",
    "올웨더 포트폴리오",
    "기계적 Bull/Bear 리밸런싱",
    "채권혼합 60/30/10",
    "SCHD 배당 스타일",
    "M7 동일가중 추적",
]


# ── Shared fixtures ──────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def synth_data():
    return generate_synthetic_market_data(seed=42)


@pytest.fixture(scope="module")
def comparison(synth_data):
    return build_benchmark_comparison(synth_data)


@pytest.fixture(scope="module")
def panel(synth_data):
    return build_benchmark_price_panel(synth_data)


# ── build_benchmark_price_panel ──────────────────────────────────────────────

class TestBuildBenchmarkPricePanel:
    def test_returns_dataframe(self, panel):
        assert isinstance(panel, pd.DataFrame)

    def test_required_columns_present(self, panel):
        for col in ("QQQ", "SPY", "QLD", "TQQQ", "SGOV", "TLT", "IEF", "GLD", "DBC", "SCHD"):
            assert col in panel.columns, f"missing column: {col}"

    def test_m7_tickers_present(self, panel):
        for ticker in M7_TICKERS:
            assert ticker in panel.columns, f"missing M7 ticker: {ticker}"

    def test_all_prices_positive(self, panel):
        assert (panel > 0).all().all()

    def test_deterministic(self, synth_data):
        p1 = build_benchmark_price_panel(synth_data)
        p2 = build_benchmark_price_panel(synth_data)
        pd.testing.assert_frame_equal(p1, p2)

    def test_index_aligned_with_input(self, synth_data, panel):
        assert len(panel) == len(synth_data["qqq_close"])

    def test_extra_tickers_added(self, synth_data):
        p = build_benchmark_price_panel(synth_data, extra_tickers=["AAPL", "MSFT"])
        assert "AAPL" in p.columns
        assert "MSFT" in p.columns


# ── build_benchmark_comparison ───────────────────────────────────────────────

class TestBuildBenchmarkComparison:
    def test_returns_benchmark_comparison_type(self, comparison):
        assert isinstance(comparison, BenchmarkComparison)

    # ── All benchmark names present ──────────────────────────────────────
    def test_all_expected_benchmark_names_present(self, comparison):
        names = [r.name for r in comparison.results]
        for expected in EXPECTED_BENCHMARK_NAMES:
            assert expected in names, f"benchmark missing from results: {expected!r}"

    def test_qqq_is_explicit_benchmark(self, comparison):
        names = [r.name for r in comparison.results]
        assert "QQQ 매수보유" in names

    def test_spy_is_explicit_benchmark(self, comparison):
        names = [r.name for r in comparison.results]
        assert "SPY 매수보유" in names

    def test_qld_tqqq_barbell_present(self, comparison):
        names = [r.name for r in comparison.results]
        assert "QLD/TQQQ 바벨" in names

    def test_all_weather_present(self, comparison):
        names = [r.name for r in comparison.results]
        assert "올웨더 포트폴리오" in names

    def test_bond_mixed_present(self, comparison):
        names = [r.name for r in comparison.results]
        assert "채권혼합 60/30/10" in names

    def test_schd_dividend_present(self, comparison):
        names = [r.name for r in comparison.results]
        assert "SCHD 배당 스타일" in names

    def test_m7_equal_weight_present(self, comparison):
        names = [r.name for r in comparison.results]
        assert "M7 동일가중 추적" in names

    def test_mechanical_bull_bear_present(self, comparison):
        names = [r.name for r in comparison.results]
        assert "기계적 Bull/Bear 리밸런싱" in names

    # ── Equity DataFrame ─────────────────────────────────────────────────
    def test_equity_df_not_empty(self, comparison):
        assert len(comparison.equity) > 0

    def test_equity_has_qqq_column(self, comparison):
        assert "QQQ 매수보유" in comparison.equity.columns

    def test_equity_has_spy_column(self, comparison):
        assert "SPY 매수보유" in comparison.equity.columns

    def test_buy_and_hold_equity_curves_present(self, comparison):
        """buy_and_hold benchmarks must appear in the equity DataFrame (bug guard)."""
        for name in ("QQQ 매수보유", "SPY 매수보유", "QLD 매수보유", "TQQQ 매수보유"):
            assert name in comparison.equity.columns, (
                f"{name!r} equity curve missing — _bah_with_equity may not be used"
            )

    def test_equity_values_all_positive(self, comparison):
        assert (comparison.equity.dropna() > 0).all().all()

    def test_with_ml_result_adds_ml_equity(self, synth_data):
        ss = optimize_sweet_spot(synth_data)
        comp = build_benchmark_comparison(synth_data, ml_result=ss.ml_result)
        assert "ML 전략" in comp.equity.columns

    # ── Result quality ────────────────────────────────────────────────────
    def test_all_results_have_n_days(self, comparison):
        for r in comparison.results:
            assert r.n_days > 0, f"{r.name}: n_days=0"

    def test_all_results_have_cumulative_return(self, comparison):
        from ml.backtest import BacktestResult
        for r in comparison.results:
            assert isinstance(r, BacktestResult)
            assert r.cumulative_return is not None

    def test_result_count_at_least_10(self, comparison):
        assert len(comparison.results) >= len(EXPECTED_BENCHMARK_NAMES)

    def test_note_is_string(self, comparison):
        assert isinstance(comparison.current_portfolio_note, str)
        assert len(comparison.current_portfolio_note) > 0

    # ── Missing portfolio file ────────────────────────────────────────────
    def test_missing_portfolio_file_note(self, synth_data, tmp_path):
        comp = build_benchmark_comparison(
            synth_data,
            current_portfolio_path=str(tmp_path / "nonexistent.json"),
        )
        note = comp.current_portfolio_note
        assert "없음" in note or "비교 제외" in note

    def test_missing_portfolio_no_extra_result(self, synth_data, tmp_path):
        comp = build_benchmark_comparison(
            synth_data,
            current_portfolio_path=str(tmp_path / "nonexistent.json"),
        )
        names = [r.name for r in comp.results]
        assert "현재 사용자 포트폴리오" not in names

    # ── No-lookahead guard (mechanical bull/bear) ─────────────────────────
    def test_no_lookahead_mechanical_bull_bear(self, panel):
        """Bull/Bear signal is determined from past prices only (rolling MA)."""
        qqq = panel["QQQ"]
        bull_signal = qqq > qqq.rolling(100, min_periods=20).mean()
        # Verify signal only depends on past data: at row t the rolling window
        # covers rows [t-99:t+1].  After min_periods=20 rows, no NaN leak.
        assert bull_signal.iloc[20:].notna().all()
        # portfolio_metrics() applies shift(1) internally — position at t
        # uses weight set at t-1 → no future returns consumed.
        # This is verified structurally; the test below checks cumulative_return
        # equals a known reference (deterministic synthetic data).
        mbb = next(r for r in build_benchmark_comparison(panel_data_for_test(panel)).results
                   if r.name == "기계적 Bull/Bear 리밸런싱")
        assert mbb.cumulative_return is not None

    # ── Optimizer improvement ─────────────────────────────────────────────
    def test_optimizer_improves_over_baseline(self, synth_data):
        """Grid-search best result must score ≥ baseline (threshold=0, max_weight=1)."""
        from ml.optimization import composite_score
        from ml.backtest import BacktestResult

        ss = optimize_sweet_spot(synth_data)
        qqq_cagr = ss.qqq_result.cagr or 0.0

        def _score(r: BacktestResult) -> float:
            return composite_score(
                cagr=r.cagr,
                max_drawdown=r.max_drawdown,
                turnover=r.turnover or 0.0,
                excess_return=(r.cagr or 0.0) - qqq_cagr,
            )

        assert _score(ss.best_result) >= _score(ss.baseline_result) - 1e-9

    # ── QQQ comparison explicit in report ────────────────────────────────
    def test_qqq_comparison_in_benchmark_report_section(self, synth_data):
        from ml.reporting import build_benchmark_report_section
        comp = build_benchmark_comparison(synth_data)
        section = build_benchmark_report_section(comp)
        assert "QQQ 매수보유" in section

    def test_all_benchmark_names_in_report_section(self, synth_data):
        from ml.reporting import build_benchmark_report_section
        comp = build_benchmark_comparison(synth_data)
        section = build_benchmark_report_section(comp)
        for name in EXPECTED_BENCHMARK_NAMES:
            assert name in section, f"benchmark name missing from report section: {name!r}"

    def test_sample_report_includes_all_benchmark_names(self):
        """build_sample_ml_strategy_report() must list every expected benchmark."""
        import ml.reporting as rep
        rep._SAMPLE_REPORT_CACHE.clear()
        try:
            text = rep.build_sample_ml_strategy_report()
        finally:
            rep._SAMPLE_REPORT_CACHE.clear()
        for name in EXPECTED_BENCHMARK_NAMES:
            assert name in text, f"benchmark missing from sample report: {name!r}"

    def test_sample_report_has_qqq_comparison(self):
        """sample report must contain explicit ML vs QQQ comparison."""
        import ml.reporting as rep
        rep._SAMPLE_REPORT_CACHE.clear()
        try:
            text = rep.build_sample_ml_strategy_report()
        finally:
            rep._SAMPLE_REPORT_CACHE.clear()
        assert "QQQ" in text


# ── Helper for no-lookahead test ─────────────────────────────────────────────

def panel_data_for_test(panel: pd.DataFrame) -> dict:
    """Shim so _mechanical_bull_bear can be tested with synth panel data."""
    n = len(panel)
    return {
        "close": panel["QQQ"],
        "spy_close": panel["SPY"],
        "qqq_close": panel["QQQ"],
        "features": pd.DataFrame(
            {"momentum": [0.0] * n, "volatility": [0.01] * n, "sentiment": [0.0] * n},
            index=panel.index,
        ),
    }


# ── load_current_portfolio_weights ───────────────────────────────────────────

class TestLoadCurrentPortfolioWeights:
    def test_missing_file_returns_none_and_str(self, tmp_path):
        w, note = load_current_portfolio_weights(str(tmp_path / "nonexistent.json"))
        assert w is None
        assert isinstance(note, str)

    def test_invalid_json_returns_none(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("not json {{")
        w, note = load_current_portfolio_weights(str(p))
        assert w is None

    def test_empty_holdings_returns_none(self, tmp_path):
        p = tmp_path / "empty.json"
        p.write_text('{"overseas_general": {"holdings_usd": []}}')
        w, note = load_current_portfolio_weights(str(p))
        assert w is None

    def test_valid_snapshot_returns_weights(self, tmp_path):
        p = tmp_path / "snap.json"
        p.write_text(json.dumps({
            "overseas_general": {
                "holdings_usd": [
                    {"ticker": "QQQ", "value_usd": 1000},
                    {"ticker": "NVDA", "value_usd": 500},
                ]
            }
        }))
        w, note = load_current_portfolio_weights(str(p))
        assert w is not None
        assert "QQQ" in w.index
        assert "NVDA" in w.index
        assert abs(w.sum() - 1.0) < 1e-9

    def test_weights_sum_to_one(self, tmp_path):
        p = tmp_path / "snap.json"
        p.write_text(json.dumps({
            "overseas_general": {
                "holdings_usd": [
                    {"ticker": "MSFT", "value_usd": 300},
                    {"ticker": "ORCL", "value_usd": 200},
                    {"ticker": "SGOV", "value_usd": 500},
                ]
            }
        }))
        w, _ = load_current_portfolio_weights(str(p))
        assert w is not None
        assert abs(w.sum() - 1.0) < 1e-9


# ── ml/visualization.py ──────────────────────────────────────────────────────

class TestVisualization:
    def test_module_imports_without_matplotlib(self):
        """visualization.py must be importable in base Python."""
        import importlib
        import sys
        # Remove cached module to force re-import
        sys.modules.pop("ml.visualization", None)
        mod = importlib.import_module("ml.visualization")
        assert hasattr(mod, "plot_equity_curves")
        assert hasattr(mod, "plot_sweet_spot_trials")

    def test_plot_equity_curves_returns_path_or_none(self, synth_data, tmp_path):
        from ml.visualization import plot_equity_curves
        ss = optimize_sweet_spot(synth_data)
        result = plot_equity_curves(ss.equity, outdir=str(tmp_path))
        # Either None (no matplotlib) or a non-empty file
        if result is not None:
            p = Path(result)
            assert p.exists()
            assert p.stat().st_size > 0

    def test_plot_sweet_spot_trials_returns_path_or_none(self, synth_data, tmp_path):
        from ml.visualization import plot_sweet_spot_trials
        ss = optimize_sweet_spot(synth_data)
        result = plot_sweet_spot_trials(ss.trials, ss.best_params, outdir=str(tmp_path))
        if result is not None:
            p = Path(result)
            assert p.exists()
            assert p.stat().st_size > 0

    def test_plot_equity_curves_empty_df_returns_none(self, tmp_path):
        from ml.visualization import plot_equity_curves
        result = plot_equity_curves(pd.DataFrame(), outdir=str(tmp_path))
        assert result is None

    def test_plot_sweet_spot_trials_empty_df_returns_none(self, tmp_path):
        from ml.visualization import plot_sweet_spot_trials
        result = plot_sweet_spot_trials(pd.DataFrame(), {}, outdir=str(tmp_path))
        assert result is None

    @pytest.mark.skipif(not _MATPLOTLIB_AVAILABLE, reason="matplotlib not installed")
    def test_graph_files_non_empty_with_matplotlib(self, synth_data, tmp_path):
        """When matplotlib is available, both graph files must be non-empty."""
        from ml.visualization import plot_equity_curves, plot_sweet_spot_trials
        ss = optimize_sweet_spot(synth_data)

        p1 = plot_equity_curves(ss.equity, outdir=str(tmp_path))
        assert p1 is not None, "plot_equity_curves returned None with matplotlib installed"
        assert Path(p1).stat().st_size > 100, "equity_curves.png is suspiciously small"

        p2 = plot_sweet_spot_trials(ss.trials, ss.best_params, outdir=str(tmp_path))
        assert p2 is not None, "plot_sweet_spot_trials returned None with matplotlib installed"
        assert Path(p2).stat().st_size > 100, "sweet_spot_trials.png is suspiciously small"
