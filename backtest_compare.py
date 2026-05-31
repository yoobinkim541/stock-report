#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
backtest_compare.py — 글로벌 바벨/포트폴리오 전략 종합 비교 백테스트

비교 대상 (7개):
  1. QQQ 단순 DCA         — 벤치마크
  2. IB v2.1 현재         — Intelligence Barbell 현재 전략
  3. IB v2.2 VIX게이트    — VIX 피크 확인 후 레버리지 진입
  4. Taleb 바벨 90/10     — 90% SGOV + 10% TQQQ (탈레브 원전략)
  5. HFEA                 — 55% UPRO + 45% TMF 분기 리밸런싱 (헷지펀디)
  6. All Weather          — 달리오 30% QQQ+40% TLT+15% IEF+7.5% GLD+7.5% DBC
  7. 영구 포트폴리오       — 해리 브라운 25% QQQ+25% TLT+25% GLD+25% SGOV
  8. Dragon (간소화)       — 크리스 콜 24% QQQ+21% TLT+19% GLD+18% DBC+18% DBMF

Sources:
  - HFEA: https://www.optimizedportfolio.com/hedgefundie-adventure/
  - Taleb: https://www.quantifiedstrategies.com/nassim-taleb-strategy/
  - All Weather: https://www.optimizedportfolio.com/all-weather-portfolio/
  - Permanent: https://www.optimizedportfolio.com/permanent-portfolio/
  - Dragon: https://pictureperfectportfolios.com/dragon-portfolio-review-all-weather-asset-allocation-strategy/

Usage:
  python3 backtest_compare.py
  python3 backtest_compare.py --start 2022-01-01
  python3 backtest_compare.py --send
"""

import os, sys, json, argparse, logging
from datetime import datetime
from collections import deque

import numpy as np
import requests

try:
    import pandas as pd
    import yfinance as yf
except ImportError:
    print("yfinance/pandas 필요"); sys.exit(1)

try:
    from dotenv import load_dotenv; load_dotenv()
except ImportError:
    pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backtest import download_data, calc_signals, _ascii_chart

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

INITIAL_CASH     = 10_000.0
DAILY_DCA_USD    = 29.0
TELEGRAM_TOKEN   = os.getenv("STOCK_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = "5771238245"

# ══════════════════════════════════════════════════════════════════════
#  전략 정의
# ══════════════════════════════════════════════════════════════════════

# 각 전략: 티커 목록 + 목표 비중 + 리밸런싱 주기(거래일)
#   rebal_days: 0 = Phase 기반(IB 전용), 63 = 분기, 252 = 연간, 9999 = 안함
STRATEGY_DEFS = {
    "①QQQ DCA":      {"tickers": ["QQQ"],                          "weights": [1.00],                         "rebal_days": 0,    "ib": False},
    "②IB v2.1":      {"tickers": ["QQQ","QLD","TQQQ","SGOV"],      "weights": None,                            "rebal_days": 0,    "ib": True,  "vix_gate": False},
    "③IB v2.2 VIX":  {"tickers": ["QQQ","QLD","TQQQ","SGOV"],      "weights": None,                            "rebal_days": 0,    "ib": True,  "vix_gate": True},
    "④Taleb 90/10":  {"tickers": ["SGOV","TQQQ"],                  "weights": [0.90, 0.10],                    "rebal_days": 252,  "ib": False},
    "⑤HFEA":         {"tickers": ["UPRO","TMF"],                   "weights": [0.55, 0.45],                    "rebal_days": 63,   "ib": False},
    "⑥All Weather":  {"tickers": ["QQQ","TLT","IEF","GLD","DBC"],  "weights": [0.30,0.40,0.15,0.075,0.075],   "rebal_days": 252,  "ib": False},
    "⑦영구포트폴리오": {"tickers": ["QQQ","TLT","GLD","SGOV"],       "weights": [0.25, 0.25, 0.25, 0.25],        "rebal_days": 252,  "ib": False},
    "⑧Dragon":       {"tickers": ["QQQ","TLT","GLD","DBC","DBMF"], "weights": [0.24, 0.21, 0.19, 0.18, 0.18], "rebal_days": 252,  "ib": False},
}

# IB 배분 상수 (backtest_v2에서 가져옴)
IB_ALLOC = {
    "bull_2":(0.70,0.00,0.00,0.30), "bull_1":(0.82,0.00,0.00,0.18),
    0:(0.92,0.00,0.00,0.08), 1:(1.00,0.00,0.00,0.00),
    2:(0.65,0.28,0.00,0.07), 3:(0.52,0.45,0.00,0.03),
    4:(0.45,0.35,0.20,0.00), 5:(0.20,0.20,0.60,0.00),
}
IB_ALLOC_FALLBACK = {2:(1.00,0.00,0.00,0.00),3:(1.00,0.00,0.00,0.00),
                     4:(0.52,0.45,0.00,0.03),5:(0.52,0.45,0.00,0.03)}
IB_DCA_MULT   = {"bull_2":0.5,"bull_1":0.8,0:1.0,1:1.5,2:2.0,3:2.5,4:3.0,5:5.0}
IB_DCA_TARGET = {"bull_2":"SGOV","bull_1":"QQQ",0:"QQQ",1:"QQQ",2:"QLD",3:"QLD",4:"TQQQ",5:"TQQQ"}


# ══════════════════════════════════════════════════════════════════════
#  데이터 수집
# ══════════════════════════════════════════════════════════════════════

def download_all(start: str) -> pd.DataFrame:
    """모든 전략에 필요한 ETF 한 번에 다운로드."""
    all_tickers = sorted({
        t for s in STRATEGY_DEFS.values()
        for t in s["tickers"]
    } | {"^VIX"})

    from datetime import timedelta
    dl_start = (pd.Timestamp(start) - pd.Timedelta(days=400)).strftime("%Y-%m-%d")
    logger.info(f"다운로드: {', '.join(all_tickers)}")
    raw = yf.download(all_tickers, start=dl_start, auto_adjust=True, progress=False)
    close = raw["Close"].copy()

    # SGOV / SHV 보정
    if "SGOV" in close.columns and "SHV" in close.columns:
        close.loc[close["SGOV"].isna(), "SGOV"] = close.loc[close["SGOV"].isna(), "SHV"]
    elif "SGOV" not in close.columns:
        close["SGOV"] = 100.0

    # VIX
    close["VIX"] = close.get("^VIX", pd.Series(20.0, index=close.index)).fillna(20.0)

    # 앞 구간 DBMF 없을 수 있음 → DBC로 대체
    if "DBMF" in close.columns:
        close["DBMF"] = close["DBMF"].fillna(close.get("DBC", pd.Series(20.0, index=close.index)))
    else:
        close["DBMF"] = close.get("DBC", pd.Series(20.0, index=close.index))

    # UPRO / TMF 없으면 proxy
    if "UPRO" not in close.columns:
        close["UPRO"] = close["QQQ"] * 3 * 0.97
    if "TMF" not in close.columns:
        close["TMF"] = close.get("TLT", pd.Series(100.0, index=close.index)) * 3 * 0.97

    # 공통 forward-fill
    close = close.ffill().bfill()
    return close


# ══════════════════════════════════════════════════════════════════════
#  공통 시뮬레이터
# ══════════════════════════════════════════════════════════════════════

def _safe_price(val) -> float:
    try:
        f = float(val)
        return f if f > 0 and np.isfinite(f) else 1.0
    except Exception:
        return 1.0


def simulate_fixed(df: pd.DataFrame, start: str, tickers: list,
                   weights: list, rebal_days: int) -> list:
    """
    고정 비중 전략 시뮬레이션.
    DCA는 비중에 비례해서 매일 투입.
    """
    df = df[df.index >= pd.Timestamp(start)].copy()
    if df.empty:
        return []

    prices0 = {t: _safe_price(df.iloc[0].get(t)) for t in tickers}
    shares  = {t: INITIAL_CASH * w / prices0[t] for t, w in zip(tickers, weights)}
    log     = []
    days_since_rebal = 0

    for i, (date, row) in enumerate(df.iterrows()):
        prices = {t: _safe_price(row.get(t)) for t in tickers}
        port_val = sum(shares[t] * prices[t] for t in tickers)

        # 리밸런싱
        if rebal_days > 0 and days_since_rebal >= rebal_days:
            shares = {t: port_val * w / prices[t] for t, w in zip(tickers, weights)}
            days_since_rebal = 0
        days_since_rebal += 1

        # DCA: 비중 비례 투입
        for t, w in zip(tickers, weights):
            dca_t = DAILY_DCA_USD * w
            shares[t] += dca_t / prices[t]

        port_val = sum(shares[t] * prices[t] for t in tickers)
        if np.isfinite(port_val) and port_val > 0:
            log.append({"date": date.strftime("%Y-%m-%d"), "value": round(port_val, 2)})

    return log


def simulate_ib(df: pd.DataFrame, start: str, vix_gate: bool) -> list:
    """Intelligence Barbell 시뮬레이션 (Phase 기반)."""
    df = df[df.index >= pd.Timestamp(start)].copy()
    if df.empty:
        return []

    ASSETS = ["QQQ", "QLD", "TQQQ", "SGOV"]
    init   = IB_ALLOC[0]
    first  = df.iloc[0]
    shares = {t: INITIAL_CASH * init[i] / _safe_price(first.get(t)) for i, t in enumerate(ASSETS)}

    prev_phase  = 0
    vix_window: deque = deque(maxlen=30)
    log = []

    for date, row in df.iterrows():
        prices = {t: _safe_price(row.get(t)) for t in ASSETS}
        vix    = float(row.get("VIX", 20) or 20)
        rsi    = float(row.get("rsi", 50) or 50)
        phase  = row["phase"]

        vix_window.append(vix)
        vix_peak = max(vix_window)

        # VIX 게이트
        if vix_gate and isinstance(phase, int) and phase >= 2:
            min_vix = 22 if phase <= 3 else 32
            blocked = (vix < min_vix) or (vix < 45 and vix > vix_peak * 0.93)
            effective_alloc = IB_ALLOC_FALLBACK.get(phase, IB_ALLOC[1]) if blocked else IB_ALLOC.get(phase, IB_ALLOC[0])
            dca_tgt = "QQQ" if blocked else IB_DCA_TARGET.get(phase, "QQQ")
        else:
            effective_alloc = IB_ALLOC.get(phase, IB_ALLOC[0])
            dca_tgt = IB_DCA_TARGET.get(phase, "QQQ")

        # Phase 전환 → 리밸런싱
        if phase != prev_phase:
            port_val = sum(shares[t] * prices[t] for t in ASSETS)
            shares   = {t: port_val * effective_alloc[i] / prices[t] for i, t in enumerate(ASSETS)}
            prev_phase = phase

        # DCA
        mult = IB_DCA_MULT.get(phase, 1.0)
        shares[dca_tgt] += DAILY_DCA_USD * mult / prices[dca_tgt]

        port_val = sum(shares[t] * prices[t] for t in ASSETS)
        if np.isfinite(port_val) and port_val > 0:
            log.append({"date": date.strftime("%Y-%m-%d"), "value": round(port_val, 2)})

    return log


# ══════════════════════════════════════════════════════════════════════
#  성과 지표
# ══════════════════════════════════════════════════════════════════════

def metrics(log: list, total_invested: float | None = None) -> dict:
    vals = [x["value"] for x in log]
    if len(vals) < 10:
        return {}

    n_years = len(vals) / 252
    inv = total_invested or INITIAL_CASH + DAILY_DCA_USD * len(vals)
    final = vals[-1]

    # CAGR 기준: 투입 원금 대비
    cagr = ((final / inv) ** (1 / n_years) - 1) * 100 if n_years > 0 else 0

    # MDD
    peak, mdd = vals[0], 0.0
    for v in vals:
        peak = max(peak, v)
        mdd  = min(mdd, (v - peak) / peak * 100)

    # Sharpe (무위험 5%/252)
    rets = np.diff(vals) / np.array(vals[:-1])
    exc  = rets - 0.05 / 252
    sharpe = exc.mean() / exc.std() * np.sqrt(252) if exc.std() > 0 else 0

    # Calmar
    calmar = cagr / abs(mdd) if mdd != 0 else 0

    return {
        "final":        round(final, 2),
        "invested":     round(inv, 2),
        "total_return": round((final / inv - 1) * 100, 1),
        "cagr":         round(cagr, 1),
        "max_drawdown": round(mdd, 1),
        "sharpe":       round(sharpe, 2),
        "calmar":       round(calmar, 2),
    }


# ══════════════════════════════════════════════════════════════════════
#  비교 리포트
# ══════════════════════════════════════════════════════════════════════

RANK_EMOJI = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣"]

def build_report(all_metrics: dict, logs: dict, start: str) -> str:
    names  = list(all_metrics.keys())
    mlist  = list(all_metrics.values())

    end    = datetime.now().strftime("%Y-%m-%d")
    n_yrs  = round(len(logs[names[0]]) / 252, 1) if names and logs.get(names[0]) else "?"

    lines = [
        "🌍 글로벌 바벨/포트폴리오 전략 비교 백테스트",
        f"기간  {start} ~ {end}  ({n_yrs}년)",
        f"초기 ${INITIAL_CASH:,}  +  일 DCA ${DAILY_DCA_USD}",
        "",
    ]

    # ── 종합 성과표 ───────────────────────────────────────────────────
    lines.append("━━━ 종합 성과 비교 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    header = f"  {'전략':<16}  {'CAGR':>7}  {'MDD':>7}  {'Sharpe':>7}  {'Calmar':>7}  {'최종($)':>10}"
    lines.append(header)
    lines.append("  " + "─" * (len(header) - 2))

    # CAGR 기준 정렬
    sorted_items = sorted(zip(names, mlist), key=lambda x: x[1].get("cagr", -99), reverse=True)

    for rank, (name, m) in enumerate(sorted_items):
        if not m:
            continue
        em = RANK_EMOJI[rank] if rank < len(RANK_EMOJI) else "  "
        lines.append(
            f"  {em} {name:<14}  "
            f"{m['cagr']:>+6.1f}%  "
            f"{m['max_drawdown']:>+6.1f}%  "
            f"{m['sharpe']:>7.2f}  "
            f"{m['calmar']:>7.2f}  "
            f"${m['final']:>9,.0f}"
        )

    # ── 항목별 1위 ────────────────────────────────────────────────────
    lines += ["", "━━━ 항목별 1위 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"]
    def best(key, higher_better=True):
        valid = [(n, m[key]) for n, m in zip(names, mlist) if m and key in m]
        if not valid: return "N/A", 0
        return max(valid, key=lambda x: x[1] if higher_better else -x[1])

    b_cagr,   v_cagr   = best("cagr")
    b_mdd,    v_mdd    = best("max_drawdown")          # higher (less negative) = better
    b_sharpe, v_sharpe = best("sharpe")
    b_calmar, v_calmar = best("calmar")

    lines += [
        f"  📈 최고 CAGR       {b_cagr:<18}  {v_cagr:>+.1f}%",
        f"  🛡 최소 낙폭(MDD)  {b_mdd:<18}  {v_mdd:>+.1f}%",
        f"  ⚖️  최고 Sharpe     {b_sharpe:<18}  {v_sharpe:>.2f}",
        f"  🎯 최고 Calmar     {b_calmar:<18}  {v_calmar:>.2f}",
    ]

    # ── 세부 분석 (상위 3개) ─────────────────────────────────────────
    lines += ["", "━━━ 상위 3개 전략 세부 ━━━━━━━━━━━━━━━━━━━━━━━━━━"]
    for rank, (name, m) in enumerate(sorted_items[:3]):
        if not m: continue
        em = RANK_EMOJI[rank]
        lines += [
            f"  {em} {name}",
            f"     최종가치  ${m['final']:>9,.0f}  (투입 ${m['invested']:,.0f})",
            f"     총수익률  {m['total_return']:>+.1f}%   CAGR {m['cagr']:>+.1f}%",
            f"     MDD {m['max_drawdown']:>+.1f}%   Sharpe {m['sharpe']:.2f}   Calmar {m['calmar']:.2f}",
            "",
        ]

    # ── IB 전략 집중 비교 ─────────────────────────────────────────────
    ib_names = ["②IB v2.1", "③IB v2.2 VIX"]
    ib_present = [(n, all_metrics[n]) for n in ib_names if n in all_metrics and all_metrics[n]]
    if ib_present:
        lines += ["━━━ Intelligence Barbell 개선 효과 ━━━━━━━━━━━━━━━"]
        base_m = all_metrics.get("②IB v2.1", {})
        for name, m in ib_present:
            if not base_m or name == "②IB v2.1":
                lines.append(f"  {name}  CAGR {m['cagr']:>+.1f}%  MDD {m['max_drawdown']:>+.1f}%  Sharpe {m['sharpe']:.2f}")
            else:
                dcagr = m["cagr"] - base_m["cagr"]
                dmdd  = m["max_drawdown"] - base_m["max_drawdown"]
                dsh   = m["sharpe"] - base_m["sharpe"]
                lines.append(
                    f"  {name}  CAGR {m['cagr']:>+.1f}%({dcagr:>+.1f}%p)  "
                    f"MDD {m['max_drawdown']:>+.1f}%({dmdd:>+.1f}%p)  "
                    f"Sharpe {m['sharpe']:.2f}({dsh:>+.2f})"
                )

    # ── ASCII 차트 (상위 3개) ─────────────────────────────────────────
    lines += ["", "━━━ 포트폴리오 가치 추이 ━━━━━━━━━━━━━━━━━━━━━━━━"]
    for rank, (name, _) in enumerate(sorted_items[:3]):
        pvals = [x["value"] for x in logs.get(name, [])]
        if not pvals: continue
        lines += [f"  {RANK_EMOJI[rank]} {name}", _ascii_chart(pvals, width=40, height=5)]
        lines.append(f"  ${min(pvals):,.0f} → ${max(pvals):,.0f}  현재 ${pvals[-1]:,.0f}")
        lines.append("")

    # ── 전략 설명 요약 ────────────────────────────────────────────────
    lines += [
        "━━━ 전략 설명 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "  ②③ IB     QQQ Phase 기반 SGOV↔레버리지 동적 배분",
        "  ④ Taleb   90% T-bill + 10% 3× 레버리지 (탈레브)",
        "  ⑤ HFEA    55% UPRO(3×S&P) + 45% TMF(3×국채) 분기 리밸",
        "  ⑥ 달리오  30%QQQ+40%TLT+15%IEF+7.5%GLD+7.5%상품",
        "  ⑦ 브라운  25%씩 주식·장기채·금·현금 (영구포트폴리오)",
        "  ⑧ Dragon  24%QQQ+21%TLT+19%GLD+18%상품+18%MF (콜)",
    ]

    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════
#  메인
# ══════════════════════════════════════════════════════════════════════

def send_telegram(text: str):
    if not TELEGRAM_TOKEN: return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    for i in range(0, len(text), 4000):
        try:
            requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text[i:i+4000]}, timeout=10)
        except Exception as e:
            logger.error(f"전송 오류: {e}")


def main():
    parser = argparse.ArgumentParser(description="글로벌 전략 비교 백테스트")
    parser.add_argument("--start", default="2020-01-01")
    parser.add_argument("--send",  action="store_true")
    args = parser.parse_args()

    # 1. 데이터
    logger.info("데이터 다운로드...")
    raw   = download_all(args.start)
    close = calc_signals(raw)

    # 2. 전략별 시뮬레이션
    logs        = {}
    all_metrics = {}

    for name, cfg in STRATEGY_DEFS.items():
        logger.info(f"시뮬레이션: {name}")
        if cfg.get("ib"):
            log = simulate_ib(close, args.start, vix_gate=cfg["vix_gate"])
        else:
            tickers = cfg["tickers"]
            weights = cfg["weights"]
            # 누락 컬럼 있으면 스킵
            missing = [t for t in tickers if t not in close.columns]
            if missing:
                logger.warning(f"  {name}: 데이터 없음 {missing} — 스킵")
                logs[name]        = []
                all_metrics[name] = {}
                continue
            log = simulate_fixed(close, args.start, tickers, weights, cfg["rebal_days"])

        logs[name]        = log
        all_metrics[name] = metrics(log)

    # 3. 리포트
    report = build_report(all_metrics, logs, args.start)
    print(report)

    if args.send:
        send_telegram(report)
        logger.info("텔레그램 발송 완료")


if __name__ == "__main__":
    main()
