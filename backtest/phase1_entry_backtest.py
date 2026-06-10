#!/usr/bin/env python3
"""
Phase 1 진입 전략 백테스트 — QQQ / TQQQ / UPRO

Phase 결정: QQQ 낙폭 기준 (IB 전략 동일)
실제 매수: ticker별 분리 (QQQ, TQQQ, UPRO)
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
import yfinance as yf

BASE_DCA_KRW  = 40_000
EXCHANGE_RATE = 1_380
BASE_DCA_USD  = BASE_DCA_KRW / EXCHANGE_RATE

BASE_MULTS = {0: 1.0, 2: 2.0, 3: 2.5, 4: 3.0, 5: 5.0}


def get_phase(dd):
    if dd > -5:   return 0
    if dd > -10:  return 1
    if dd > -15:  return 2
    if dd > -20:  return 3
    if dd > -30:  return 4
    return 5


def run_backtest(inv_prices, qqq_drawdown, phase1_mult):
    monthly_inv = inv_prices.resample("MS").first().dropna()
    dd_monthly  = qqq_drawdown.reindex(monthly_inv.index, method="nearest")

    mults = BASE_MULTS.copy()
    mults[1] = phase1_mult

    shares = cash_reserve = total_deploy = 0.0
    prev_phase = 0
    records = []

    for date, price in monthly_inv.items():
        dd    = dd_monthly.get(date, 0.0)
        phase = get_phase(dd)
        mult  = mults.get(phase, 1.0)
        regular = BASE_DCA_USD * mult

        if phase == 1:
            cash_reserve += max(BASE_DCA_USD * (1.5 - phase1_mult), 0)

        extra = 0.0
        if phase >= 2 and prev_phase < 2 and cash_reserve > 0:
            extra = cash_reserve
            cash_reserve = 0.0

        deploy = regular + extra
        shares += deploy / price
        total_deploy += deploy
        records.append(dict(date=date, phase=phase, portfolio=shares * price))
        prev_phase = phase

    df = pd.DataFrame(records).set_index("date")
    final_price = monthly_inv.iloc[-1]
    if cash_reserve > 0:
        shares += cash_reserve / final_price
        total_deploy += cash_reserve

    final_value  = shares * final_price
    years        = (monthly_inv.index[-1] - monthly_inv.index[0]).days / 365.25
    total_return = (final_value / total_deploy - 1) * 100 if total_deploy > 0 else 0
    cagr         = ((final_value / total_deploy) ** (1 / years) - 1) * 100 if years > 0 else 0
    port   = df["portfolio"]
    mdd    = ((port - port.cummax()) / port.cummax()).min() * 100
    ret    = port.pct_change().dropna()
    sharpe = ret.mean() / ret.std() * np.sqrt(12) if ret.std() > 0 else 0

    return dict(final_usd=final_value, total_deploy=total_deploy,
                total_return=total_return, cagr=cagr, mdd=mdd, sharpe=sharpe)


def print_ticker_section(ticker, inv_prices, qqq_drawdown):
    start = inv_prices.index[0].date()
    end   = inv_prices.index[-1].date()
    years = (inv_prices.index[-1] - inv_prices.index[0]).days / 365.25

    print(f"\n{'=' * 68}")
    print(f"  {ticker}  ({start} ~ {end}, {years:.1f}년)")
    print(f"{'=' * 68}")

    strategies = [
        ("A. 현행 (Phase1=1.5x)", 1.5),
        ("B. 홀드 (Phase1=0.0x)", 0.0),
        ("C. 절충 (Phase1=0.5x)", 0.5),
        ("D. 절충 (Phase1=1.0x)", 1.0),
    ]
    print(f"\n{'전략':<28} {'총수익률':>9} {'CAGR':>7} {'MDD':>8} {'Sharpe':>8} {'최종$':>10}")
    print("-" * 73)
    for name, mult in strategies:
        r = run_backtest(inv_prices, qqq_drawdown, mult)
        print(f"  {name:<26} {r['total_return']:>8.1f}%  {r['cagr']:>5.1f}%  "
              f"{r['mdd']:>7.1f}%  {r['sharpe']:>7.3f}  ${r['final_usd']:>9,.0f}")

    print(f"\n  [그리드서치: Phase 1 배율별 성과]")
    print(f"  {'배율':>6}  {'총수익률':>9}  {'CAGR':>6}  {'MDD':>7}  {'Sharpe':>8}")
    print("  " + "-" * 46)
    grid = []
    for m in np.arange(0.0, 2.1, 0.1):
        m = round(m, 1)
        r = run_backtest(inv_prices, qqq_drawdown, m)
        grid.append(dict(mult=m, **{k: r[k] for k in ["total_return", "cagr", "mdd", "sharpe"]}))
        print(f"  {m:>5.1f}x  {r['total_return']:>9.1f}%  {r['cagr']:>5.1f}%  "
              f"{r['mdd']:>6.1f}%  {r['sharpe']:>8.3f}")

    gdf = pd.DataFrame(grid)
    br  = gdf.loc[gdf["total_return"].idxmax()]
    bs  = gdf.loc[gdf["sharpe"].idxmax()]
    print(f"\n  ▸ 총수익률 최적: {br['mult']:.1f}x  (수익률 {br['total_return']:.1f}%, CAGR {br['cagr']:.1f}%)")
    print(f"  ▸ Sharpe    최적: {bs['mult']:.1f}x  (Sharpe {bs['sharpe']:.3f}, MDD {bs['mdd']:.1f}%)")


def main():
    tickers = ["QQQ", "TQQQ", "UPRO"]
    START, END = "2010-02-01", "2026-06-09"

    print(f"데이터 다운로드: {', '.join(tickers)} ({START} ~ {END})")
    raw = yf.download(tickers, start=START, end=END, progress=False, auto_adjust=True)["Close"]
    print(f"  {len(raw):,}일 로드 완료")

    qqq_prices = raw["QQQ"].dropna()
    rolling_hi = qqq_prices.rolling(252, min_periods=60).max()
    qqq_dd     = (qqq_prices / rolling_hi - 1) * 100

    for ticker in tickers:
        prices = raw[ticker].dropna()
        common = prices.index.intersection(qqq_dd.index)
        print_ticker_section(ticker, prices.loc[common], qqq_dd.reindex(common))

    print(f"\n{'=' * 68}")
    print("  종목별 요약 (현행 1.5x, 2010-02 ~ 2026-06)")
    print(f"{'=' * 68}")
    print(f"\n  {'종목':<8} {'총수익률':>9} {'CAGR':>7} {'MDD':>8} {'Sharpe':>8} {'최종$':>10}")
    print("  " + "-" * 55)
    for ticker in tickers:
        prices = raw[ticker].dropna()
        common = prices.index.intersection(qqq_dd.index)
        r = run_backtest(prices.loc[common], qqq_dd.reindex(common), 1.5)
        print(f"  {ticker:<8} {r['total_return']:>9.1f}%  {r['cagr']:>5.1f}%  "
              f"{r['mdd']:>7.1f}%  {r['sharpe']:>7.3f}  ${r['final_usd']:>9,.0f}")

    print(f"\n  ※ Phase는 QQQ 낙폭 기준, MDD는 월별 포트폴리오 기준")


if __name__ == "__main__":
    main()
