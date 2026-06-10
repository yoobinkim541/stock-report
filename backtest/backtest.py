#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
backtest.py — Intelligence Barbell v2.1 백테스트

2020-01-01 ~ 오늘까지 전략 시뮬레이션.
벤치마크(QQQ 단순 DCA)와 수익률·낙폭·Sharpe 비교.

Usage:
  python3 backtest.py                     # 2020-01-01 ~ 오늘
  python3 backtest.py --start 2022-01-01  # 시작일 지정
  python3 backtest.py --send              # 결과 텔레그램 발송
  python3 backtest.py --save              # JSON 파일 저장
"""

import os, sys, json, argparse, logging
from datetime import datetime, timedelta
from collections import Counter

import numpy as np
import requests

try:
    import pandas as pd
    import yfinance as yf
except ImportError:
    print("yfinance/pandas 필요: uv pip install yfinance pandas")
    sys.exit(1)

try:
    from dotenv import load_dotenv; load_dotenv()
except ImportError:
    pass

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# ── 상수 ─────────────────────────────────────────────────────────────
INITIAL_CASH   = 10_000.0   # USD 초기 투자금
DAILY_DCA_USD  = 29.0       # 일일 DCA ($29 ≈ 40,000원 @1,380원)
REPORTS_DIR    = os.path.expanduser("~/reports")

TELEGRAM_TOKEN   = os.getenv("STOCK_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("STOCK_BOT_CHAT_ID", "5771238245")

# Phase별 목표 자산 배분 (QQQ, QLD, TQQQ, SGOV) — 반드시 합계 = 1.0
ALLOC: dict = {
    "bull_2":  (0.70, 0.00, 0.00, 0.30),
    "bull_1":  (0.82, 0.00, 0.00, 0.18),
    0:         (0.92, 0.00, 0.00, 0.08),   # neutral / Phase 0
    1:         (1.00, 0.00, 0.00, 0.00),
    2:         (0.65, 0.28, 0.00, 0.07),
    3:         (0.52, 0.45, 0.00, 0.03),
    4:         (0.45, 0.35, 0.20, 0.00),
    5:         (0.20, 0.20, 0.60, 0.00),
}

# Phase별 DCA 배율
DCA_MULT: dict = {
    "bull_2": 0.5, "bull_1": 0.8,
    0: 1.0, 1: 1.5, 2: 2.0, 3: 2.5, 4: 3.0, 5: 5.0,
}

# Phase별 DCA 투입 대상
DCA_TARGET: dict = {
    "bull_2": "SGOV", "bull_1": "QQQ",
    0: "QQQ", 1: "QQQ", 2: "QLD", 3: "QLD", 4: "TQQQ", 5: "TQQQ",
}

PHASE_LABEL: dict = {
    "bull_2": "🫧 Bull-2 (과열)",
    "bull_1": "🐂 Bull-1 (강세)",
    "0": "🟢 Phase 0 (중립)",
    "1": "🟡 Phase 1 (-5~-10%)",
    "2": "🟠 Phase 2 (-10~-15%)",
    "3": "🔴 Phase 3 (-15~-20%)",
    "4": "🚨 Phase 4 (-20~-30%)",
    "5": "💥 Phase 5 (-30%+)",
}


# ══════════════════════════════════════════════════════════════════════
#  데이터 수집
# ══════════════════════════════════════════════════════════════════════

def download_data(start: str) -> pd.DataFrame:
    """
    QQQ, QLD, TQQQ, SGOV, SHV, ^VIX 다운로드.
    SGOV는 2022-05 이전 SHV로 보정.
    """
    # 52주 고점 계산을 위해 1년 더 앞서 다운로드
    dl_start = (pd.Timestamp(start) - pd.Timedelta(days=400)).strftime("%Y-%m-%d")
    logger.info(f"데이터 다운로드: {dl_start} ~ 오늘")

    raw = yf.download(
        ["QQQ", "QLD", "TQQQ", "SGOV", "SHV", "^VIX"],
        start=dl_start,
        auto_adjust=True,
        progress=False,
    )
    close: pd.DataFrame = raw["Close"].copy()

    # SGOV 앞 구간 SHV로 보정 (SHV ≈ SGOV 유사 상품)
    if "SGOV" in close.columns and "SHV" in close.columns:
        missing = close["SGOV"].isna()
        close.loc[missing, "SGOV"] = close.loc[missing, "SHV"]

    # 그래도 없으면 5% 연율 대체
    if "SGOV" not in close.columns:
        close["SGOV"] = 100.0
    close["SGOV"] = close["SGOV"].ffill().fillna(100.0)

    # QLD/TQQQ 없는 날 forward-fill
    for t in ["QLD", "TQQQ"]:
        if t in close.columns:
            close[t] = close[t].ffill()
        else:
            close[t] = close["QQQ"] * (2.0 if t == "QLD" else 3.0)

    # VIX
    vix_col = "^VIX"
    if vix_col in close.columns:
        close["VIX"] = close[vix_col].ffill().fillna(20.0)
    else:
        close["VIX"] = 20.0

    return close


def _execution_phase_series(phases: pd.Series) -> pd.Series:
    """신호 발생 다음 거래일부터 실행되도록 1일 지연시킨 phase 시계열."""
    return phases.shift(1).fillna(0)


# ── 라이브(barbell_strategy.py)와 동기화된 신호 로직 ──────────────────
# backtest_multi.py에서도 import하여 재사용

_BEAR_ENTRY_THR = {1: -5, 2: -10, 3: -15, 4: -20, 5: -30}   # 진입 임계값 (%)
_PHASE_EXIT_BUFFER_PP = 1.5    # 하향 시 추가 회복 요구 (%p)
_ANCHOR_RESET_RECOVERY = 0.95  # 앵커 -5% 이내 회복 시 롤링 고점으로 리셋


def _anchor_drawdown(qqq: pd.Series) -> pd.Series:
    """라이브 _update_drawdown_anchor와 동일한 앵커 낙폭(%) 시계열.

    앵커는 단조 증가 고점이며, 가격이 앵커 -5% 이내로 회복했을 때만
    52주 rolling high로 리셋 (장기 약세장 Phase 드리프트 방지).
    백테스트는 영속 상태가 없으므로 시계열 내에서 순차 계산한다.
    """
    high_52w = qqq.rolling(252, min_periods=21).max().shift(1).fillna(qqq)
    px_arr, h52_arr = qqq.to_numpy(float), high_52w.to_numpy(float)
    dd = np.zeros(len(px_arr))
    anchor = 0.0
    for i in range(len(px_arr)):
        anchor = max(anchor, h52_arr[i])
        if anchor > 0 and px_arr[i] >= anchor * _ANCHOR_RESET_RECOVERY:
            anchor = h52_arr[i]
        dd[i] = (px_arr[i] - anchor) / anchor * 100 if anchor > 0 else 0.0
    return pd.Series(dd, index=qqq.index)


def _wilder_rsi(qqq: pd.Series, period: int = 14) -> pd.Series:
    """Wilder RSI (ewm alpha=1/N) — 라이브 fetch_rsi와 동일."""
    delta = qqq.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50)


def _classify_with_hysteresis(close: pd.DataFrame) -> pd.Series:
    """라이브 classify_market과 동일한 순차 히스테리시스 분류.

    - bear 진입·상향: 즉시
    - bear 하향: 진입 임계값 +1.5%p 이상 회복해야 허용
    - VIX≥30 패닉 시 깊은 Phase(2+) 하향 보류 (dd≤-5 한정)
    """
    phases = []
    prev_bear = None
    for dd, rsi, mom, vix in zip(close["drawdown"], close["rsi"],
                                 close["mom_1m"], close["VIX"]):
        if dd <= -30:   raw = 5
        elif dd <= -20: raw = 4
        elif dd <= -15: raw = 3
        elif dd <= -10: raw = 2
        elif dd <= -5:  raw = 1
        elif rsi > 75 and mom > 8 and vix < 15:
            raw = "bull_2"
        elif rsi > 70 or mom > 5:
            raw = "bull_1"
        else:
            raw = 0

        phase = raw
        if prev_bear is not None:
            raw_bear = raw if isinstance(raw, int) and raw >= 1 else 0
            if raw_bear < prev_bear:
                recovered = dd > _BEAR_ENTRY_THR[prev_bear] + _PHASE_EXIT_BUFFER_PP
                vix_panic = prev_bear >= 2 and vix >= 30 and dd <= -5
                if not recovered or vix_panic:
                    phase = prev_bear
        prev_bear = phase if isinstance(phase, int) and phase >= 1 else None
        phases.append(phase)
    return pd.Series(phases, index=close.index, dtype=object)


def calc_signals(close: pd.DataFrame) -> pd.DataFrame:
    """앵커 낙폭, Wilder RSI, 모멘텀 계산 → 히스테리시스 phase 열 추가."""
    qqq = close["QQQ"]
    close["drawdown"] = _anchor_drawdown(qqq)
    close["rsi"] = _wilder_rsi(qqq)
    close["mom_1m"] = qqq.pct_change(21, fill_method=None).fillna(0) * 100
    close["phase"] = _classify_with_hysteresis(close)
    return close


# ══════════════════════════════════════════════════════════════════════
#  포트폴리오 시뮬레이션
# ══════════════════════════════════════════════════════════════════════

def run_simulation(df: pd.DataFrame, start: str) -> dict:
    """
    백테스트 메인 루프.
    - 초기 자산 INITIAL_CASH를 Phase 0 배분으로 투자
    - 매 거래일 DCA 추가 (Phase별 배율·대상)
    - Phase 전환 시 전체 리밸런싱
    """
    df = df[df.index >= pd.Timestamp(start)].copy()
    if df.empty:
        return {"error": "해당 기간 데이터 없음"}

    ASSETS = ["QQQ", "QLD", "TQQQ", "SGOV"]

    # 초기 배분
    init_alloc = ALLOC[0]
    first_row  = df.iloc[0]
    shares = {
        t: (INITIAL_CASH * init_alloc[i]) / float(first_row.get(t, 100) or 100)
        for i, t in enumerate(ASSETS)
    }

    trade_phases  = _execution_phase_series(df["phase"])
    prev_phase     = 0
    total_invested = INITIAL_CASH
    portfolio_log  = []
    transitions    = []

    for (date, row), trade_phase in zip(df.iterrows(), trade_phases):
        prices = {t: float(row.get(t, 0) or 0) for t in ASSETS}
        for t in ASSETS:
            if prices[t] <= 0:
                prices[t] = 100.0

        phase = trade_phase

        # Phase 전환 → 리밸런싱
        if phase != prev_phase:
            port_val = sum(shares[t] * prices[t] for t in ASSETS)
            target   = ALLOC.get(phase, ALLOC[0])
            shares   = {t: (port_val * target[i]) / prices[t] for i, t in enumerate(ASSETS)}
            transitions.append({
                "date":      date.strftime("%Y-%m-%d"),
                "from":      str(prev_phase),
                "to":        str(phase),
                "drawdown":  round(float(row["drawdown"]), 2),
                "rsi":       round(float(row["rsi"]), 1),
                "portfolio": round(port_val, 2),
            })
            prev_phase = phase

        # DCA
        mult   = DCA_MULT.get(phase, 1.0)
        dca    = DAILY_DCA_USD * mult
        target = DCA_TARGET.get(phase, "QQQ")
        shares[target] = shares.get(target, 0) + dca / prices[target]
        total_invested += dca

        # 포트폴리오 가치
        port_val = sum(shares[t] * prices[t] for t in ASSETS)
        if not np.isfinite(port_val) or port_val <= 0:
            continue
        portfolio_log.append({
            "date":  date.strftime("%Y-%m-%d"),
            "value": round(port_val, 2),
            "phase": str(phase),
            "qqq":   round(float(row["QQQ"]), 2),
        })

    # ── 벤치마크: QQQ 균일 DCA ─────────────────────────────────────────
    bm_shares   = INITIAL_CASH / float(df.iloc[0]["QQQ"])
    bm_invested = INITIAL_CASH
    bm_log      = []

    for date, row in df.iterrows():
        p = float(row.get("QQQ", 0) or 1)
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
#  성과 지표
# ══════════════════════════════════════════════════════════════════════

def calc_metrics(sim: dict, start: str) -> dict:
    # NaN/Inf 제거
    pv = [x["value"] for x in sim["portfolio"] if x["value"] and np.isfinite(x["value"])]
    bv = [x["value"] for x in sim["benchmark"]  if x["value"] and np.isfinite(x["value"])]
    if not pv:
        return {}

    n_years = len(pv) / 252

    def cagr(final, invested):
        return ((final / invested) ** (1 / n_years) - 1) * 100 if n_years > 0 and invested > 0 else 0

    def max_dd(vals):
        peak, mdd = vals[0], 0.0
        for v in vals:
            peak = max(peak, v)
            mdd  = min(mdd, (v - peak) / peak * 100)
        return mdd

    def sharpe(vals):
        rets = np.diff(vals) / np.array(vals[:-1])
        excess = rets - 0.05 / 252
        return (excess.mean() / excess.std() * np.sqrt(252)) if excess.std() > 0 else 0

    # Phase 분포
    phase_counts = Counter(str(p) for p in sim["phase_series"])
    total_days   = len(sim["phase_series"])
    phase_pct    = {k: round(v / total_days * 100, 1) for k, v in sorted(phase_counts.items())}

    # 가장 높은 Phase (최대 위기)
    int_phases = [p for p in sim["phase_series"] if isinstance(p, int)]
    max_phase  = max(int_phases) if int_phases else 0

    ti = sim["total_invested"]
    bi = sim["bm_invested"]

    return {
        "start":    start,
        "end":      datetime.now().strftime("%Y-%m-%d"),
        "n_years":  round(n_years, 1),
        "strategy": {
            "final":        round(pv[-1], 2),
            "invested":     round(ti, 2),
            "total_return": round((pv[-1] / ti - 1) * 100, 1),
            "cagr":         round(cagr(pv[-1], ti), 1),
            "max_drawdown": round(max_dd(pv), 1),
            "sharpe":       round(sharpe(pv), 2),
        },
        "benchmark": {
            "final":        round(bv[-1], 2),
            "invested":     round(bi, 2),
            "total_return": round((bv[-1] / bi - 1) * 100, 1),
            "cagr":         round(cagr(bv[-1], bi), 1),
            "max_drawdown": round(max_dd(bv), 1),
            "sharpe":       round(sharpe(bv), 2),
        },
        "phase_distribution": phase_pct,
        "transitions":        sim["transitions"][-15:],
        "max_phase_reached":  max_phase,
        "portfolio_values":   sim["portfolio"],
    }


# ══════════════════════════════════════════════════════════════════════
#  리포트 생성
# ══════════════════════════════════════════════════════════════════════

def _ascii_chart(values: list, width: int = 38, height: int = 8) -> str:
    """간단한 ASCII 라인 차트 (두 시리즈: strategy ●, benchmark ·)."""
    if not values:
        return ""
    mn, mx = min(values), max(values)
    rng = mx - mn or 1
    step = max(1, len(values) // width)
    sampled = [values[i] for i in range(0, len(values), step)][:width]

    rows = []
    for r in range(height - 1, -1, -1):
        line = ""
        for v in sampled:
            h = (v - mn) / rng * (height - 1)
            if abs(h - r) < 0.5:
                line += "●"
            elif h > r:
                line += "┃"
            else:
                line += " "
        rows.append(line)
    return "\n".join(rows)


def build_report(metrics: dict, sim: dict) -> str:
    s = metrics["strategy"]
    b = metrics["benchmark"]

    cagr_w  = "✅ 전략 우세" if s["cagr"] > b["cagr"]             else "🔻 벤치마크 우세"
    mdd_w   = "✅ 낙폭 방어" if s["max_drawdown"] > b["max_drawdown"] else "🔻 낙폭 더 큼"
    sharp_w = "✅" if s["sharpe"] > b["sharpe"] else "🔻"

    lines = [
        "📊 Intelligence Barbell v2.1 — 백테스트 결과",
        f"기간  {metrics['start']} ~ {metrics['end']}  ({metrics['n_years']}년)",
        f"초기 ${INITIAL_CASH:,}  |  일 DCA ${DAILY_DCA_USD}  |  총 투입 ${s['invested']:,.0f}",
        "",
        "━━━ 성과 비교 ━━━━━━━━━━━━━━━━━━━━━━━━",
        f"              전략 (Barbell)   QQQ 단순 DCA",
        f"  최종 가치   ${s['final']:>10,.0f}   ${b['final']:>10,.0f}",
        f"  총 수익률   {s['total_return']:>+10.1f}%   {b['total_return']:>+10.1f}%",
        f"  CAGR        {s['cagr']:>+10.1f}%   {b['cagr']:>+10.1f}%   {cagr_w}",
        f"  최대 낙폭   {s['max_drawdown']:>+10.1f}%   {b['max_drawdown']:>+10.1f}%   {mdd_w}",
        f"  Sharpe      {s['sharpe']:>10.2f}   {b['sharpe']:>10.2f}   {sharp_w}",
        "",
        f"  최고 Phase  {'💥 Phase ' + str(metrics['max_phase_reached']) if metrics['max_phase_reached'] >= 3 else '🟢~🟡 (대형 조정 없음)'}",
    ]

    # Phase 분포 막대
    lines += ["", "━━━ Phase 분포 ━━━━━━━━━━━━━━━━━━━━━━━━"]
    for pk, pct in metrics["phase_distribution"].items():
        label = PHASE_LABEL.get(pk, pk)
        bar   = "█" * int(pct / 2.5) + "░" * (20 - int(pct / 2.5))
        lines.append(f"  {label:<22}  {bar}  {pct:.1f}%")

    # Phase 전환 로그 (최근 10개)
    trans = metrics["transitions"]
    if trans:
        lines += ["", "━━━ 주요 Phase 전환 ━━━━━━━━━━━━━━━━━━━━"]
        for t in trans[-10:]:
            arrow  = "↘" if str(t["to"]) > str(t["from"]) else "↗"
            change = "악화" if arrow == "↘" else "회복"
            lines.append(
                f"  {t['date']}  "
                f"{t['from']}→{t['to']} {arrow}{change}  "
                f"QQQ {t['drawdown']:+.1f}%  "
                f"RSI {t['rsi']:.0f}  "
                f"포트 ${t['portfolio']:,.0f}"
            )

    # ASCII 차트
    pvals = [x["value"] for x in metrics["portfolio_values"]]
    if pvals:
        lines += ["", "━━━ 포트폴리오 가치 추이 (●) ━━━━━━━━━━━━"]
        lines.append(_ascii_chart(pvals))
        lines += [
            f"  ${min(pvals):>8,.0f} ← 최저   최고 → ${max(pvals):,.0f}",
            f"  현재: ${pvals[-1]:,.0f}",
        ]

    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════
#  텔레그램 발송
# ══════════════════════════════════════════════════════════════════════

def send_telegram(text: str):
    if not TELEGRAM_TOKEN:
        logger.warning("TELEGRAM_TOKEN 없음 — 콘솔 출력만")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    for i in range(0, len(text), 4000):
        try:
            requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text[i:i+4000]}, timeout=10)
        except Exception as e:
            logger.error(f"텔레그램 전송 실패: {e}")


# ══════════════════════════════════════════════════════════════════════
#  메인
# ══════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Intelligence Barbell v2.1 백테스트")
    parser.add_argument("--start", default="2020-01-01", help="시작일 (YYYY-MM-DD)")
    parser.add_argument("--send",  action="store_true",  help="결과 텔레그램 발송")
    parser.add_argument("--save",  action="store_true",  help="결과 JSON 저장")
    args = parser.parse_args()

    logger.info(f"백테스트 시작: {args.start} ~ 오늘")

    # 1. 데이터 수집 + 신호 계산
    raw  = download_data(args.start)
    df   = calc_signals(raw)

    # 2. 시뮬레이션
    logger.info("포트폴리오 시뮬레이션 중...")
    sim  = run_simulation(df, args.start)
    if "error" in sim:
        print(f"❌ {sim['error']}")
        return

    # 3. 지표 계산
    metrics = calc_metrics(sim, args.start)

    # 4. 리포트 출력
    report = build_report(metrics, sim)
    print(report)

    # 5. 저장
    if args.save:
        os.makedirs(REPORTS_DIR, exist_ok=True)
        today    = datetime.now().strftime("%Y-%m-%d")
        out_path = os.path.join(REPORTS_DIR, f"backtest-{today}.json")
        # portfolio_values는 용량이 크므로 요약만 저장
        save_data = {k: v for k, v in metrics.items() if k != "portfolio_values"}
        save_data["portfolio_summary"] = {
            "first": metrics["portfolio_values"][0] if metrics["portfolio_values"] else {},
            "last":  metrics["portfolio_values"][-1] if metrics["portfolio_values"] else {},
            "count": len(metrics["portfolio_values"]),
        }
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(save_data, f, indent=2, ensure_ascii=False)
        logger.info(f"결과 저장: {out_path}")

    # 6. 텔레그램 발송
    if args.send:
        send_telegram(report)
        logger.info("텔레그램 발송 완료")


if __name__ == "__main__":
    main()
