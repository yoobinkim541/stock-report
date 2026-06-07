#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
backtest_v2.py — Intelligence Barbell 전략 개선 비교 백테스트

MDD를 줄이면서 수익률을 높이는 두 가지 개선 아이디어 검증:
  A. VIX 피크 게이트  : 레버리지 진입 전 VIX 피크 확인 → 낙폭 바닥 근처에 진입
  B. 레버리지 청산규칙 : Phase 회복 + RSI 반등 시 레버리지 부분 청산 → 수익 포획

비교 대상 (4개):
  v2.1 기존    — 현재 전략 (베이스라인)
  v2.2 VIX게이트  — QLD: VIX>22 & 피크 하락 확인 / TQQQ: VIX>32 & 피크 하락 확인
  v2.3 청산규칙   — Phase 회복 + RSI>58 시 레버리지 40% 청산
  v2.4 통합       — VIX게이트 + 청산규칙

Usage:
  python3 backtest_v2.py
  python3 backtest_v2.py --start 2022-01-01
  python3 backtest_v2.py --send
"""

import os, sys, json, argparse, logging
from datetime import datetime, timedelta
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

# backtest.py의 공통 함수 재사용
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backtest import download_data, calc_signals, calc_metrics, _ascii_chart, send_telegram

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

INITIAL_CASH   = 10_000.0
DAILY_DCA_USD  = 29.0
TELEGRAM_TOKEN   = os.getenv("STOCK_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("STOCK_BOT_CHAT_ID", "5771238245")

# ── 자산 배분 (합계 = 1.0) ────────────────────────────────────────────
ALLOC = {
    "bull_2":  (0.70, 0.00, 0.00, 0.30),
    "bull_1":  (0.82, 0.00, 0.00, 0.18),
    0:         (0.92, 0.00, 0.00, 0.08),
    1:         (1.00, 0.00, 0.00, 0.00),
    2:         (0.65, 0.28, 0.00, 0.07),
    3:         (0.52, 0.45, 0.00, 0.03),
    4:         (0.45, 0.35, 0.20, 0.00),
    5:         (0.20, 0.20, 0.60, 0.00),
}
# 게이트 차단 시 fallback 배분 (레버리지 없이 Phase 1 수준 유지)
ALLOC_FALLBACK = {
    2: (1.00, 0.00, 0.00, 0.00),   # QLD 차단 → 100% QQQ
    3: (1.00, 0.00, 0.00, 0.00),
    4: (0.52, 0.45, 0.00, 0.03),   # TQQQ 차단 → QLD만
    5: (0.52, 0.45, 0.00, 0.03),
}
DCA_MULT  = {"bull_2":0.5,"bull_1":0.8, 0:1.0, 1:1.5, 2:2.0, 3:2.5, 4:3.0, 5:5.0}
DCA_TARGET= {"bull_2":"SGOV","bull_1":"QQQ", 0:"QQQ", 1:"QQQ", 2:"QLD", 3:"QLD", 4:"TQQQ", 5:"TQQQ"}


# ══════════════════════════════════════════════════════════════════════
#  전략 설정
# ══════════════════════════════════════════════════════════════════════

STRATEGIES = {
    "v2.1 기존":      {"vix_qld": 0,  "vix_tqqq": 0,  "vix_peak_pct": 1.0, "exit_rsi": 0,   "exit_pct": 0.0},
    "v2.2 VIX게이트": {"vix_qld": 22, "vix_tqqq": 32, "vix_peak_pct": 0.93,"exit_rsi": 0,   "exit_pct": 0.0},
    "v2.3 청산규칙":  {"vix_qld": 0,  "vix_tqqq": 0,  "vix_peak_pct": 1.0, "exit_rsi": 58,  "exit_pct": 0.40},
    "v2.4 통합":      {"vix_qld": 22, "vix_tqqq": 32, "vix_peak_pct": 0.93,"exit_rsi": 58,  "exit_pct": 0.40},
}


# ══════════════════════════════════════════════════════════════════════
#  시뮬레이션
# ══════════════════════════════════════════════════════════════════════

def _vix_gate_ok(phase, vix: float, vix_peak: float, cfg: dict) -> bool:
    """
    레버리지 진입 허용 여부.
      - vix_qld/vix_tqqq: 최소 VIX 수준
      - vix_peak_pct: 최근 피크 대비 (vix <= peak * pct) 이면 피크에서 하락 중
    """
    # 게이트 없으면 항상 허용
    if cfg["vix_qld"] == 0:
        return True

    if isinstance(phase, int) and phase in (2, 3):
        min_vix = cfg["vix_qld"]
    elif isinstance(phase, int) and phase in (4, 5):
        min_vix = cfg["vix_tqqq"]
    else:
        return True

    # VIX 수준 미달 → 차단
    if vix < min_vix:
        return False

    # VIX 피크 하락 미확인 → 차단 (단, 극단적 공포(VIX>45)는 즉시 허용)
    if vix < 45 and vix > vix_peak * cfg["vix_peak_pct"]:
        return False

    return True


def run_strategy(df: pd.DataFrame, start: str, cfg: dict) -> dict:
    """단일 전략 시뮬레이션."""
    df = df[df.index >= pd.Timestamp(start)].copy()
    if df.empty:
        return {"error": "데이터 없음"}

    ASSETS = ["QQQ", "QLD", "TQQQ", "SGOV"]
    init_alloc = ALLOC[0]
    first_row  = df.iloc[0]
    shares = {t: (INITIAL_CASH * init_alloc[i]) / max(float(first_row.get(t, 100) or 100), 0.01)
              for i, t in enumerate(ASSETS)}

    prev_phase     = 0
    total_invested = INITIAL_CASH
    portfolio_log  = []
    transitions    = []

    # VIX 피크 추적 (30일 이동 최대)
    vix_window: deque = deque(maxlen=30)

    for date, row in df.iterrows():
        prices = {t: max(float(row.get(t, 0) or 0), 0.01) for t in ASSETS}
        vix    = float(row.get("VIX", 20) or 20)
        rsi    = float(row.get("rsi", 50) or 50)
        phase  = row["phase"]

        vix_window.append(vix)
        vix_peak = max(vix_window)

        # ── 레버리지 게이트 적용 ─────────────────────────────────────
        gate_ok = _vix_gate_ok(phase, vix, vix_peak, cfg)
        eff_phase = phase  # 실제 배분에 사용할 Phase

        if not gate_ok and isinstance(phase, int) and phase >= 2:
            # 게이트 차단 → fallback 배분 사용
            effective_alloc = ALLOC_FALLBACK.get(phase, ALLOC[1])
            # DCA도 QQQ로
            eff_dca_target = "QQQ"
        else:
            effective_alloc = ALLOC.get(phase, ALLOC[0])
            eff_dca_target  = DCA_TARGET.get(phase, "QQQ")
            # 게이트 차단 시 TQQQ DCA도 QLD로 대체
            if not gate_ok and eff_dca_target == "TQQQ":
                eff_dca_target = "QLD"

        # ── Phase 전환 → 리밸런싱 ────────────────────────────────────
        if phase != prev_phase:
            port_val = sum(shares[t] * prices[t] for t in ASSETS)

            # 청산규칙: Phase 회복 + RSI 반등 시 레버리지 부분 청산
            if cfg["exit_pct"] > 0 and cfg["exit_rsi"] > 0:
                phase_improved = False
                if isinstance(prev_phase, int) and isinstance(phase, int):
                    phase_improved = phase < prev_phase
                elif isinstance(prev_phase, int) and not isinstance(phase, int):
                    phase_improved = True  # bear → bull

                if phase_improved and rsi > cfg["exit_rsi"]:
                    # QLD, TQQQ의 exit_pct% 청산 → QQQ로 전환
                    for lev_t in ("QLD", "TQQQ"):
                        sell_shares = shares[lev_t] * cfg["exit_pct"]
                        sell_val    = sell_shares * prices[lev_t]
                        shares[lev_t] -= sell_shares
                        shares["QQQ"] += sell_val / prices["QQQ"]
                    # 청산 후 포트 가치 재계산
                    port_val = sum(shares[t] * prices[t] for t in ASSETS)

            # 리밸런싱
            shares = {t: (port_val * effective_alloc[i]) / prices[t]
                      for i, t in enumerate(ASSETS)}

            transitions.append({
                "date":      date.strftime("%Y-%m-%d"),
                "from":      str(prev_phase),
                "to":        str(phase),
                "drawdown":  round(float(row["drawdown"]), 2),
                "rsi":       round(rsi, 1),
                "vix":       round(vix, 1),
                "gate_ok":   gate_ok,
                "portfolio": round(port_val, 2),
            })
            prev_phase = phase

        # ── DCA ──────────────────────────────────────────────────────
        mult  = DCA_MULT.get(phase, 1.0)
        dca   = DAILY_DCA_USD * mult
        shares[eff_dca_target] = shares.get(eff_dca_target, 0) + dca / prices[eff_dca_target]
        total_invested += dca

        port_val = sum(shares[t] * prices[t] for t in ASSETS)
        if not np.isfinite(port_val) or port_val <= 0:
            continue
        portfolio_log.append({
            "date":  date.strftime("%Y-%m-%d"),
            "value": round(port_val, 2),
            "phase": str(phase),
        })

    # ── 벤치마크 ─────────────────────────────────────────────────────
    bm_shares = INITIAL_CASH / float(df.iloc[0]["QQQ"])
    bm_invested, bm_log = INITIAL_CASH, []
    for date, row in df.iterrows():
        p = max(float(row.get("QQQ", 1) or 1), 0.01)
        bm_shares   += DAILY_DCA_USD / p
        bm_invested += DAILY_DCA_USD
        bm_log.append({"date": date.strftime("%Y-%m-%d"), "value": round(bm_shares * p, 2)})

    return {
        "portfolio":     portfolio_log,
        "benchmark":     bm_log,
        "transitions":   transitions,
        "total_invested": round(total_invested, 2),
        "bm_invested":   round(bm_invested, 2),
        "phase_series":  df["phase"].tolist(),
    }


# ══════════════════════════════════════════════════════════════════════
#  비교 리포트
# ══════════════════════════════════════════════════════════════════════

def build_comparison_report(results: dict, start: str) -> str:
    """4가지 전략 비교 리포트."""

    header = [
        "📊 Intelligence Barbell — 전략 개선 백테스트",
        f"기간  {start} ~ {datetime.now().strftime('%Y-%m-%d')}",
        f"초기 ${INITIAL_CASH:,}  +  일 DCA ${DAILY_DCA_USD}",
        "",
    ]

    # ── 성과 비교 표 ──────────────────────────────────────────────────
    col_w = 14
    names = list(results.keys())

    header += [
        "━━━ 성과 비교 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "              " + "".join(f"{n:>{col_w}}" for n in names),
    ]

    metrics_list = [results[n] for n in names]

    rows = [
        ("최종가치 ($)",    [f"${m['strategy']['final']:>9,.0f}" for m in metrics_list]),
        ("총수익률",        [f"{m['strategy']['total_return']:>+9.1f}%" for m in metrics_list]),
        ("CAGR",           [f"{m['strategy']['cagr']:>+9.1f}%" for m in metrics_list]),
        ("최대낙폭 MDD",    [f"{m['strategy']['max_drawdown']:>+9.1f}%" for m in metrics_list]),
        ("Sharpe",         [f"{m['strategy']['sharpe']:>10.2f}" for m in metrics_list]),
    ]

    for label, vals in rows:
        line = f"  {label:<14}" + "".join(f"{v:>{col_w}}" for v in vals)

        # 최고값 강조 표시
        try:
            raw = [float(v.replace("$","").replace("%","").replace(",","").strip()) for v in vals]
            # MDD는 낮을수록(덜 음수) 좋음
            if "MDD" in label:
                best_i = raw.index(max(raw))
            else:
                best_i = raw.index(max(raw))
            line += f"  ← {'✅ ' + names[best_i]}" if best_i > 0 else ""
        except Exception:
            pass
        header.append(line)

    # ── 개선 요약 ─────────────────────────────────────────────────────
    base = metrics_list[0]
    header += ["", "━━━ v2.1 기존 대비 개선폭 ━━━━━━━━━━━━━━━━━━━━━━"]
    for i, name in enumerate(names[1:], 1):
        m = metrics_list[i]
        dcagr  = m["strategy"]["cagr"]  - base["strategy"]["cagr"]
        dmdd   = m["strategy"]["max_drawdown"] - base["strategy"]["max_drawdown"]
        dsharp = m["strategy"]["sharpe"] - base["strategy"]["sharpe"]
        cagr_s = f"{dcagr:>+.1f}%p" if dcagr != 0 else " ─    "
        mdd_s  = f"{dmdd:>+.1f}%p"  if dmdd  != 0 else " ─    "
        sharp_s= f"{dsharp:>+.2f}"  if dsharp!= 0 else " ─    "

        # 방어성 계산: MDD 개선 + CAGR 개선 둘 다 좋으면 ✅✅
        cagr_ok = dcagr > 0
        mdd_ok  = dmdd > 0   # 덜 음수 = 방어
        emoji   = "✅✅" if (cagr_ok and mdd_ok) else ("✅" if (cagr_ok or mdd_ok) else "🔻")
        header.append(f"  {name:<14}  CAGR {cagr_s}  MDD {mdd_s}  Sharpe {sharp_s}  {emoji}")

    # ── 베스트 전략 선정 ──────────────────────────────────────────────
    # 점수: CAGR*0.4 + (-MDD)*0.4 + Sharpe*0.2 (정규화 없이 방향성으로만)
    scores = []
    for m in metrics_list:
        s = m["strategy"]
        score = s["cagr"] * 0.4 + (-s["max_drawdown"]) * 0.4 + s["sharpe"] * 2.0
        scores.append(score)
    best_i = scores.index(max(scores))
    header += [
        "",
        f"━━━ 종합 추천 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"  🏆 {names[best_i]}",
        f"     종합점수: {scores[best_i]:.2f}  (CAGR 40% + MDD방어 40% + Sharpe 20%)",
    ]
    best = metrics_list[best_i]["strategy"]
    header += [
        f"     CAGR {best['cagr']:+.1f}%  MDD {best['max_drawdown']:.1f}%  Sharpe {best['sharpe']:.2f}",
    ]

    # ── 주요 Phase 전환 비교 (v2.4 기준) ────────────────────────────
    if "v2.4 통합" in results:
        trans = results["v2.4 통합"].get("transitions", [])
        if trans:
            header += ["", "━━━ v2.4 통합 전략 주요 전환 (최근 8개) ━━━━━━━━━"]
            for t in trans[-8:]:
                gate = "" if t.get("gate_ok", True) else " [게이트차단]"
                arrow = "↘악화" if str(t["to"]) > str(t["from"]) else "↗회복"
                header.append(
                    f"  {t['date']}  {t['from']}→{t['to']} {arrow}  "
                    f"QQQ{t['drawdown']:+.1f}%  VIX{t['vix']:.0f}  RSI{t['rsi']:.0f}{gate}"
                )

    # ── 가치 추이 차트 비교 ───────────────────────────────────────────
    for name in names:
        pvals = [x["value"] for x in results[name].get("portfolio_values", [])]
        if not pvals:
            continue
        header += ["", f"━━━ {name} 포트폴리오 추이 ━━━━━━━━━━━━━━━━━━━━━━"]
        header.append(_ascii_chart(pvals, width=40, height=6))
        m = results[name]["strategy"]
        header.append(f"  ${min(pvals):,.0f} → ${max(pvals):,.0f}  /  현재 ${pvals[-1]:,.0f}")

    return "\n".join(header)


# ══════════════════════════════════════════════════════════════════════
#  메인
# ══════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Barbell 전략 개선 비교 백테스트")
    parser.add_argument("--start", default="2020-01-01")
    parser.add_argument("--send",  action="store_true")
    args = parser.parse_args()

    logger.info(f"데이터 로드: {args.start} ~ 오늘")
    raw = download_data(args.start)
    df  = calc_signals(raw)

    all_results = {}

    for name, cfg in STRATEGIES.items():
        logger.info(f"시뮬레이션: {name}")
        sim     = run_strategy(df, args.start, cfg)
        if "error" in sim:
            logger.error(f"  {sim['error']}")
            continue
        metrics = calc_metrics(sim, args.start)
        metrics["portfolio_values"] = sim["portfolio"]
        metrics["transitions"]      = sim["transitions"]
        all_results[name] = metrics

    report = build_comparison_report(all_results, args.start)
    print(report)

    if args.send:
        send_telegram(report)
        logger.info("텔레그램 발송 완료")


if __name__ == "__main__":
    main()
