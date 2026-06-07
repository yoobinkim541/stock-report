"""p11 — Report/Telegram integration for the quant ML strategy pipeline.

Public API
----------
chunk_text(text, limit)                — split long text for Telegram (≤4096 char limit)
build_ml_strategy_report(...)          — compose performance comparison report text
build_benchmark_report_section(bench)  — format BenchmarkComparison as a text section
build_sample_ml_strategy_report()      — demo report using synthetic data (no network)
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional, TYPE_CHECKING

import pandas as pd

from ml.backtest import BacktestResult, buy_and_hold

if TYPE_CHECKING:
    from ml.benchmarks import BenchmarkComparison


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


def _ml_adoption_verdict(
    ml: BacktestResult,
    qqq: BacktestResult,
) -> tuple[str, list[str]]:
    """ML 전략 채택 여부 판정 (3조건 기준).

    채택 조건:
      1. ML CAGR > QQQ CAGR  (절대 수익 우위)
      2. ML Sharpe > QQQ Sharpe  (위험조정 수익 우위)
      3. ML MDD 개선: |ML MDD| < |QQQ MDD| * 0.9  (낙폭 10%이상 감소)

    3/3 → 채택, 2/3 → 조건부, 1이하 → 비채택
    """
    reasons: list[str] = []
    passed = 0

    ml_cagr  = ml.cagr  or 0.0
    qqq_cagr = qqq.cagr or 0.0
    if ml_cagr > qqq_cagr:
        passed += 1
        reasons.append(f"CAGR 우위: ML {ml_cagr:.1%} > QQQ {qqq_cagr:.1%}")
    else:
        reasons.append(f"CAGR 열위: ML {ml_cagr:.1%} < QQQ {qqq_cagr:.1%}")

    ml_sharpe  = ml.sharpe  or 0.0
    qqq_sharpe = qqq.sharpe or 0.0
    if ml_sharpe > qqq_sharpe:
        passed += 1
        reasons.append(f"Sharpe 우위: ML {ml_sharpe:.2f} > QQQ {qqq_sharpe:.2f}")
    else:
        reasons.append(f"Sharpe 열위: ML {ml_sharpe:.2f} < QQQ {qqq_sharpe:.2f}")

    ml_mdd  = abs(ml.max_drawdown)
    qqq_mdd = abs(qqq.max_drawdown)
    if ml_mdd < qqq_mdd * 0.9:
        passed += 1
        reasons.append(f"MDD 개선: ML {-ml_mdd:.1%} vs QQQ {-qqq_mdd:.1%}")
    else:
        reasons.append(f"MDD 미개선: ML {-ml_mdd:.1%} vs QQQ {-qqq_mdd:.1%}")

    if passed == 3:
        verdict = "✅ 채택 — 3/3 조건 충족"
    elif passed == 2:
        verdict = "⚠️ 조건부 채택 — 2/3 조건 충족"
    elif passed == 1:
        verdict = "❌ 비채택 — 1/3 조건 (QQQ 보유 권장)"
    else:
        verdict = "❌ 비채택 — 0/3 조건 (QQQ 보유 권장)"

    return verdict, reasons


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
    overlay_result: Optional[BacktestResult] = None,
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

    # Risk overlay result
    if overlay_result:
        ol_excess = ((overlay_result.cagr or 0) - (qqq_cagr or 0)) if qqq_cagr is not None else None
        lines.append(f"▶ {overlay_result.name}")
        lines += _fmt_result_block(overlay_result, excess_vs=ol_excess)
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

    # ── 6. ML 채택 판정 ────────────────────────────────────────────────────
    if qqq_result is not None:
        verdict, reasons = _ml_adoption_verdict(ml_result, qqq_result)
        lines += [
            "[ ML 전략 채택 판정 ]",
            f"  {verdict}",
        ] + [f"  • {r}" for r in reasons] + [""]

    lines.append("━━━━━━━━━━━━━━━━━━━━━━━")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Benchmark section formatter
# ---------------------------------------------------------------------------

def build_benchmark_report_section(bench: "BenchmarkComparison") -> str:
    """Format a BenchmarkComparison as a compact text section for Telegram.

    Lists all benchmark strategies (QQQ, SPY, QLD, TQQQ, 바벨, 올웨더, …) with
    CAGR / MDD / Sharpe on one line each.  Skips results where CAGR is unavailable
    (< 365 days).  Portfolio note always included.

    Intentionally compact — designed to be appended after build_ml_strategy_report().
    """
    lines: list[str] = [
        "",
        "━━━━━━━━━━━━━━━━━━━━━━━",
        "[ 광범위 벤치마크 비교 (최적화 샘플 / synthetic smoke) ]",
        "",
    ]
    for r in bench.results:
        cagr_str = f"{r.cagr:.1%}" if r.cagr is not None else "n/a(<1yr)"
        mdd_str = f"{r.max_drawdown:.1%}"
        sharpe_str = f"{r.sharpe:.2f}" if r.sharpe is not None else "n/a"
        lines.append(f"▶ {r.name}")
        lines.append(f"  CAGR={cagr_str}  MDD={mdd_str}  Sharpe={sharpe_str}  ({r.n_days}d)")
    lines += [
        "",
        f"  ※ {bench.current_portfolio_note}",
        "  ⚠️ 모든 수치는 합성 데이터 기반 백테스트 (실매매 아님)",
        "━━━━━━━━━━━━━━━━━━━━━━━",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Sample report (no network, synthetic data)
# ---------------------------------------------------------------------------

_SAMPLE_REPORT_CACHE: dict = {}
_REAL_REPORT_CACHE:   dict = {}


def build_sample_ml_strategy_report() -> str:
    """Build a demo ML strategy report using the sweet-spot optimizer on synthetic data.

    Results reflect the actual optimizer output — negative returns are shown as-is,
    not hidden or adjusted.  Includes a full benchmark comparison section.
    Deterministic via seed=42.  Result is cached after first call.
    """
    if "report" in _SAMPLE_REPORT_CACHE:
        return _SAMPLE_REPORT_CACHE["report"]

    from ml.sweet_spot import generate_synthetic_market_data, optimize_sweet_spot
    from ml.benchmarks import build_benchmark_comparison

    data = generate_synthetic_market_data()
    result = optimize_sweet_spot(data)
    bench = build_benchmark_comparison(data, ml_result=result.ml_result)

    main_text = build_ml_strategy_report(
        ml_result=result.ml_result,
        qqq_result=result.qqq_result,
        spy_result=result.spy_result,
        overlay_result=result.overlay_result,
        weights=result.weights,
        wf_summary=result.wf_summary,
        as_of="샘플 (최적화 샘플 / synthetic smoke)",
    )
    bench_text = build_benchmark_report_section(bench)
    text = main_text + bench_text
    _SAMPLE_REPORT_CACHE["report"] = text
    return text


def build_real_ml_strategy_report(asset_ticker: str = "QQQ", days: int = 756) -> str:
    """실시장 데이터(yfinance)로 ML 전략 리포트 생성.

    asset_ticker: 전략 대상 종목 (기본 QQQ)
    days:         사용 기간 (기본 756 영업일 ≈ 3년)
    결과는 프로세스 내 캐시 (재호출 시 즉시 반환).
    """
    cache_key = f"{asset_ticker}_{days}"
    if cache_key in _REAL_REPORT_CACHE:
        return _REAL_REPORT_CACHE[cache_key]

    from ml.data_pipeline import build_real_sweetspot_data
    from ml.sweet_spot import optimize_sweet_spot

    data   = build_real_sweetspot_data(asset_ticker=asset_ticker, days=days)
    result = optimize_sweet_spot(data)

    today = date.today().isoformat()
    text = build_ml_strategy_report(
        ml_result=result.ml_result,
        qqq_result=result.qqq_result,
        spy_result=result.spy_result,
        overlay_result=result.overlay_result,
        weights=result.weights,
        wf_summary=result.wf_summary,
        as_of=f"실데이터 {asset_ticker} {days}일 ({today})",
    )
    _REAL_REPORT_CACHE[cache_key] = text
    return text
