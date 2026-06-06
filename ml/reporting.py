"""p11 — Report/Telegram integration for the quant ML strategy pipeline.

Public API
----------
chunk_text(text, limit)                — split long text for Telegram (≤4096 char limit)
build_ml_strategy_report(...)          — compose performance comparison report text
build_sample_ml_strategy_report()      — demo report using synthetic data (no network)
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

import pandas as pd

from ml.backtest import BacktestResult, buy_and_hold


# ---------------------------------------------------------------------------
# Telegram text chunker
# ---------------------------------------------------------------------------

def chunk_text(text: str, limit: int = 3900) -> list[str]:
    """Split *text* into chunks of at most *limit* characters.

    Splits on newlines where possible so lines stay intact.
    """
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for line in text.splitlines(keepends=True):
        if current_len + len(line) > limit and current:
            chunks.append("".join(current))
            current = []
            current_len = 0
        # If a single line exceeds limit, hard-split it
        while len(line) > limit:
            chunks.append(line[:limit])
            line = line[limit:]
        current.append(line)
        current_len += len(line)

    if current:
        chunks.append("".join(current))

    return chunks


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _fmt_pct(value: Optional[float], na: str = "n/a") -> str:
    return f"{value:.1%}" if value is not None else na


def _fmt_f2(value: Optional[float], na: str = "n/a") -> str:
    return f"{value:.2f}" if value is not None else na


def _fmt_result_block(result: BacktestResult, excess_vs: Optional[float] = None) -> list[str]:
    """Format a single BacktestResult as bullet lines."""
    lines = [
        f"  누적수익: {_fmt_pct(result.cumulative_return)}",
        f"  CAGR   : {_fmt_pct(result.cagr)}",
        f"  최대낙폭: {_fmt_pct(result.max_drawdown)}",
        f"  Sharpe : {_fmt_f2(result.sharpe)}",
    ]
    if result.turnover is not None:
        lines.append(f"  회전율  : {result.turnover:.4f} (일평균)")
    if excess_vs is not None:
        lines.append(f"  벤치대비: {excess_vs:+.1%}")
    lines.append(f"  기간    : {result.n_days}일")
    return lines


# ---------------------------------------------------------------------------
# Main report builder
# ---------------------------------------------------------------------------

def build_ml_strategy_report(
    ml_result: BacktestResult,
    qqq_result: Optional[BacktestResult] = None,
    spy_result: Optional[BacktestResult] = None,
    ib_metrics: Optional[dict] = None,
    weights: Optional[pd.Series] = None,
    wf_summary: Optional[dict] = None,
    as_of: Optional[str] = None,
) -> str:
    """Compose a mobile-readable Telegram plain-text performance report.

    Args:
        ml_result:   BacktestResult for the ML strategy.
        qqq_result:  BacktestResult for QQQ buy-and-hold benchmark (optional).
        spy_result:  BacktestResult for SPY buy-and-hold benchmark (optional).
        ib_metrics:  Dict with existing IB/barbell metrics (optional).
                     Expected keys: cum_return, cagr, max_drawdown, sharpe (all float|None).
        weights:     pd.Series of ticker → recommended weight (optional).
        wf_summary:  Dict with walk-forward summary (optional).
                     Expected keys: n_folds, mean_sharpe, std_sharpe, mean_cagr.
        as_of:       Date string (YYYY-MM-DD). Defaults to today.
    """
    today = as_of or date.today().isoformat()
    lines: list[str] = []

    # ── 1. Header ─────────────────────────────────────────────────────────
    lines += [
        "━━━━━━━━━━━━━━━━━━━━━━━",
        "📊 ML 전략 성과 리포트",
        f"기준일: {today}",
        "━━━━━━━━━━━━━━━━━━━━━━━",
        "",
    ]

    # ── 2. Performance comparison ──────────────────────────────────────────
    lines.append("[ 성과 비교 ]")

    # ML strategy
    qqq_cagr = qqq_result.cagr if qqq_result else None
    ml_excess = (ml_result.cagr - qqq_cagr) if (ml_result.cagr is not None and qqq_cagr is not None) else None
    lines.append(f"▶ ML 전략 ({ml_result.name})")
    lines += _fmt_result_block(ml_result, excess_vs=ml_excess)
    lines.append("")

    # QQQ benchmark
    if qqq_result:
        lines.append("▶ QQQ 매수보유")
        lines += _fmt_result_block(qqq_result)
        lines.append("")

    # SPY benchmark
    if spy_result:
        lines.append("▶ SPY 매수보유")
        spy_excess = (ml_result.cagr - spy_result.cagr) if (ml_result.cagr is not None and spy_result.cagr is not None) else None
        lines += _fmt_result_block(spy_result)
        if spy_excess is not None:
            lines.append(f"  ML 초과(vs SPY): {spy_excess:+.1%}")
        lines.append("")

    # Existing IB/barbell strategy
    if ib_metrics:
        lines.append("▶ 기존 Intelligence Barbell 전략")
        for label, key in [
            ("  누적수익", "cum_return"),
            ("  CAGR   ", "cagr"),
            ("  최대낙폭", "max_drawdown"),
            ("  Sharpe ", "sharpe"),
        ]:
            val = ib_metrics.get(key)
            fmt = _fmt_pct(val) if key in ("cum_return", "cagr", "max_drawdown") else _fmt_f2(val)
            lines.append(f"{label}: {fmt}")
        lines.append("")

    # ── 3. Key metrics quick summary ──────────────────────────────────────
    lines += [
        "[ 핵심 지표 요약 ]",
        f"  ML CAGR     : {_fmt_pct(ml_result.cagr)}",
        f"  ML 최대낙폭  : {_fmt_pct(ml_result.max_drawdown)}",
        f"  ML Sharpe   : {_fmt_f2(ml_result.sharpe)}",
        f"  QQQ CAGR    : {_fmt_pct(qqq_cagr)}",
        f"  벤치대비(CAGR): {_fmt_pct(ml_excess, '계산불가')}",
        "",
    ]

    # ── 4. Recommended weights ────────────────────────────────────────────
    if weights is not None and len(weights) > 0:
        lines.append("[ 권장 포트폴리오 비중 ]")
        sorted_w = weights.sort_values(ascending=False)
        for ticker, w in sorted_w.items():
            if w > 0:
                bar_n = int(w * 20)
                bar = "█" * bar_n + "░" * (20 - bar_n)
                lines.append(f"  {ticker:<6} {w:5.1%}  {bar}")
        lines.append("")

    # ── 5. Walk-forward / data integrity caveat ───────────────────────────
    lines.append("[ 검증 & 데이터 무결성 ]")
    if wf_summary:
        n = wf_summary.get("n_folds", "?")
        ms = wf_summary.get("mean_sharpe")
        ss = wf_summary.get("std_sharpe")
        mc = wf_summary.get("mean_cagr")
        lines += [
            f"  Walk-forward 폴드: {n}개",
            f"  평균 Sharpe : {_fmt_f2(ms)} ± {_fmt_f2(ss)}",
            f"  평균 CAGR   : {_fmt_pct(mc)}",
        ]
    else:
        lines.append("  Walk-forward: 데이터 없음")

    lines += [
        "  룩어헤드 방지: 신호 shift(1) 적용",
        "  훈련/테스트 분리: 시계열 순서 유지",
        "  ⚠️ 본 수치는 백테스트 결과이며 실제 수익을 보장하지 않습니다.",
        "",
    ]

    lines.append("━━━━━━━━━━━━━━━━━━━━━━━")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Sample report (no network, synthetic data)
# ---------------------------------------------------------------------------

_SAMPLE_REPORT_CACHE: dict = {}


def build_sample_ml_strategy_report() -> str:
    """Build a demo ML strategy report using the sweet-spot optimizer on synthetic data.

    Results reflect the actual optimizer output — negative returns are shown as-is,
    not hidden or adjusted.  Deterministic via seed=42.  Result is cached after first call.
    """
    if "report" in _SAMPLE_REPORT_CACHE:
        return _SAMPLE_REPORT_CACHE["report"]

    from ml.sweet_spot import generate_synthetic_market_data, optimize_sweet_spot

    data = generate_synthetic_market_data()
    result = optimize_sweet_spot(data)

    text = build_ml_strategy_report(
        ml_result=result.ml_result,      # actual OOS ExcessReturnModel result (not grid-searched threshold)
        qqq_result=result.qqq_result,
        spy_result=result.spy_result,
        weights=result.weights,
        wf_summary=result.wf_summary,
        as_of="샘플 (synthetic, 최적화 샘플)",
    )
    _SAMPLE_REPORT_CACHE["report"] = text
    return text
