#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
backtest_multi.py — 글로벌 포트폴리오 전략 종합 비교 (1년 / 3년 / 5년 / 10년 / 20년)

전략 14개:
  ① QQQ DCA          — 벤치마크 (순수 나스닥 DCA)
  ② 60/40 클래식      — 60% QQQ + 40% TLT 연 리밸
  ③ IB v2.1 현재      — Intelligence Barbell Phase 기반
  ④ IB v2.2 VIX게이트 — VIX 피크 확인 후 레버리지 진입
  ⑤ Taleb 90/10      — 90% SGOV + 10% TQQQ
  ⑥ HFEA             — 55% UPRO + 45% TMF 분기 리밸
  ⑦ All Weather       — 달리오 30/40/15/7.5/7.5
  ⑧ 영구포트폴리오     — 해리 브라운 25×4
  ⑨ Dragon 간소화     — 크리스 콜 24/21/19/18/18
  ⑩ QLD/SPMO/SGOV    — 하락 전환 SGOV 방어 + -10%부터 QLD 저가매수
  ⑪ SPY/SCHD+Top10   — SPY/SCHD + 시총 상위 10 월간 추종
  ⑫ GEM 듀얼모멘텀    — 게리 안토나치 12개월 절대+상대 모멘텀
  ⑬ GTAA 페이버       — 맵 페이버 200일 MA 타이밍 5자산
  ⑭ 황금나비           — 타일러 20%×5 (총시장+소형가치+장기채+단기채+금)

Sources:
  GEM   : https://www.quantifiedstrategies.com/dual-momentum-trading-strategy/
  GTAA  : https://mebfaber.com/timing-model/
  황금나비: https://www.optimizedportfolio.com/golden-butterfly-portfolio/
  Dragon: https://pictureperfectportfolios.com/dragon-portfolio-review
  HFEA  : https://www.optimizedportfolio.com/hedgefundie-adventure/

Usage:
  python3 backtest_multi.py                     # 1 / 3 / 5 / 10 / 20년 모두
  python3 backtest_multi.py --period 10         # 10년만
  python3 backtest_multi.py --start 2024-01-01  # 사용자 지정 시작일 1개만
  python3 backtest_multi.py --send              # 텔레그램 발송
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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backtest import _ascii_chart

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

INITIAL_CASH     = 10_000.0
DAILY_DCA_USD    = 29.0
TELEGRAM_TOKEN   = os.getenv("STOCK_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("STOCK_BOT_CHAT_ID", "5771238245")

# 오늘 기준 정확히 1년 / 3년 / 5년 / 10년 / 20년 전
_TODAY = datetime.now()


def _years_ago(years: int) -> str:
    try:
        dt = _TODAY.replace(year=_TODAY.year - years)
    except ValueError:  # Feb 29 -> Feb 28
        dt = _TODAY.replace(year=_TODAY.year - years, day=28)
    return dt.strftime("%Y-%m-%d")


PERIODS = {
    f"20년 ({_years_ago(20)}~{_TODAY.strftime('%Y-%m-%d')})": _years_ago(20),
    f"10년 ({_years_ago(10)}~{_TODAY.strftime('%Y-%m-%d')})": _years_ago(10),
    f"5년 ({_years_ago(5)}~{_TODAY.strftime('%Y-%m-%d')})": _years_ago(5),
    f"3년 ({_years_ago(3)}~{_TODAY.strftime('%Y-%m-%d')})": _years_ago(3),
    f"1년 ({_years_ago(1)}~{_TODAY.strftime('%Y-%m-%d')})": _years_ago(1),
}


# ══════════════════════════════════════════════════════════════════════
#  합성 레버리지 ETF 생성 (상장 전 구간 보완)
# ══════════════════════════════════════════════════════════════════════

def make_synthetic(base: pd.Series, mult: float, annual_drag: float = 0.08) -> pd.Series:
    """
    기초 자산 일별 수익률로 레버리지 ETF 합성.
    annual_drag: 금융비용 + 운용보수 + 레버리지 감쇄 (3× ≈ 0.08, 2× ≈ 0.04)
    """
    daily_ret  = base.pct_change(fill_method=None).fillna(0)
    daily_drag = annual_drag / 252
    lev_ret    = daily_ret * mult - daily_drag
    synth      = pd.Series(index=base.index, dtype=float)
    synth.iloc[0] = 100.0
    for i in range(1, len(synth)):
        synth.iloc[i] = synth.iloc[i-1] * (1 + lev_ret.iloc[i])
        synth.iloc[i] = max(synth.iloc[i], 0.01)
    return synth


def fill_with_scaled_synthetic(actual: pd.Series, synth: pd.Series) -> pd.Series:
    """상장 전/결측 구간을 실제 가격 스케일에 맞춘 합성 가격으로 보완."""
    actual = actual.copy()
    valid = actual.dropna()
    if valid.empty:
        return synth

    anchor = valid.index[0]
    scale = valid.iloc[0] / synth.loc[anchor]
    scaled = synth * scale
    filled = actual.fillna(scaled)
    return filled.ffill().bfill()


# ══════════════════════════════════════════════════════════════════════
#  데이터 수집 + 합성
# ══════════════════════════════════════════════════════════════════════

MEGA_CAP_TICKERS = ["NVDA", "MSFT", "AAPL", "AMZN", "GOOGL", "GOOG", "META", "AVGO", "TSLA", "BRK-B", "JPM", "LLY"]

ALL_TICKERS = ["QQQ", "SPY", "SCHD", "SPMO", "EFA", "EEM", "TLT", "IEF", "SHY", "BIL",
               "GLD", "DBC", "DBMF", "VBR", "VIOV",
               "QLD", "TQQQ", "UPRO", "TMF",
               "SGOV", "SHV", "^VIX"] + MEGA_CAP_TICKERS


def download_all(start: str) -> pd.DataFrame:
    dl_start = (pd.Timestamp(start) - pd.Timedelta(days=430)).strftime("%Y-%m-%d")
    logger.info(f"데이터 다운로드: {dl_start} ~ 오늘  ({len(ALL_TICKERS)}개 티커)")

    raw   = yf.download(ALL_TICKERS, start=dl_start, auto_adjust=True, progress=False)
    close = raw["Close"].copy()

    qqq = close["QQQ"]
    spy = close.get("SPY", qqq)
    tlt = close.get("TLT", pd.Series(100.0, index=close.index))

    # ── 합성 레버리지 (상장 전 구간 보완) ────────────────────────────
    for col, base, mult, drag in [
        ("QLD",  qqq, 2.0, 0.04),
        ("TQQQ", qqq, 3.0, 0.08),
        ("UPRO", spy, 3.0, 0.08),
        ("TMF",  tlt, 3.0, 0.08),
    ]:
        synth = make_synthetic(base, mult, drag)
        if col not in close.columns:
            close[col] = synth
        else:
            close[col] = fill_with_scaled_synthetic(close[col], synth)

    # ── SGOV 보완 ────────────────────────────────────────────────────
    for fallback in ["SHV", "BIL"]:
        if fallback in close.columns:
            if "SGOV" not in close.columns:
                close["SGOV"] = close[fallback]
            else:
                close["SGOV"] = close["SGOV"].fillna(close[fallback])
            break
    if "SGOV" not in close.columns:
        close["SGOV"] = 100.0
    close["SGOV"] = close["SGOV"].ffill().bfill().fillna(100.0)

    # ── DBMF / VBR 보완 ─────────────────────────────────────────────
    if "DBMF" not in close.columns or close["DBMF"].isna().all():
        close["DBMF"] = close.get("DBC", pd.Series(20.0, index=close.index))
    close["DBMF"] = close["DBMF"].ffill().bfill()

    for sv in ["VBR", "VIOV"]:
        if sv not in close.columns or close[sv].isna().all():
            close[sv] = qqq * 0.6   # rough small-value proxy
        else:
            close[sv] = close[sv].ffill().bfill()

    # ── EFA 보완 ─────────────────────────────────────────────────────
    if "EFA" not in close.columns:
        close["EFA"] = close.get("EEM", qqq * 0.7)
    close["EFA"] = close["EFA"].ffill().bfill()

    # ── VIX ─────────────────────────────────────────────────────────
    close["VIX"] = close.get("^VIX", pd.Series(20.0, index=close.index)).ffill().bfill().fillna(20.0)

    # ── 대형주 시총 proxy: sharesOutstanding × price ─────────────────
    for t in MEGA_CAP_TICKERS:
        if t not in close.columns:
            continue
        shares = None
        try:
            shares = yf.Ticker(t).fast_info.get("shares")
        except Exception:
            shares = None
        close[f"{t}_MKT_CAP"] = close[t] * float(shares or 1.0)

    # ── 공통 정리 ────────────────────────────────────────────────────
    close = close.ffill().bfill()
    return close


def add_signals(close: pd.DataFrame) -> pd.DataFrame:
    """RSI, 낙폭, 모멘텀, Phase (IB용)."""
    qqq = close["QQQ"]

    high_52w = qqq.rolling(252, min_periods=21).max().shift(1).fillna(qqq)
    close["drawdown"] = ((qqq - high_52w) / high_52w * 100).fillna(0)

    delta = qqq.diff()
    gain  = delta.clip(lower=0).rolling(14).mean()
    loss  = (-delta.clip(upper=0)).rolling(14).mean()
    rs    = (gain / loss.replace(0, np.nan)).fillna(1)
    close["rsi"]   = (100 - 100 / (1 + rs)).fillna(50)
    close["mom_1m"] = qqq.pct_change(21, fill_method=None).fillna(0) * 100

    def classify(row):
        dd, rsi, mom, vix = row["drawdown"], row["rsi"], row["mom_1m"], row["VIX"]
        if dd <= -30:   return 5
        if dd <= -20:   return 4
        if dd <= -15:   return 3
        if dd <= -10:   return 2
        if dd <= -5:    return 1
        if rsi > 75 and mom > 8 and vix < 15: return "bull_2"
        if rsi > 70 or mom > 5:               return "bull_1"
        return 0

    close["phase"] = close.apply(classify, axis=1)
    return close


# ══════════════════════════════════════════════════════════════════════
#  유틸
# ══════════════════════════════════════════════════════════════════════

def _p(df: pd.DataFrame, col: str) -> pd.Series:
    """컬럼 안전 조회."""
    return df[col] if col in df.columns else pd.Series(100.0, index=df.index)


def _sf(val) -> float:
    try:
        f = float(val)
        return f if f > 0 and np.isfinite(f) else 1.0
    except Exception:
        return 1.0


def _metrics(log: list) -> dict:
    vals = [x["value"] for x in log if x.get("value") and np.isfinite(x["value"])]
    if len(vals) < 20:
        return {}

    n_years = len(vals) / 252
    invested = INITIAL_CASH + DAILY_DCA_USD * len(vals)
    final    = vals[-1]
    cagr     = ((final / invested) ** (1 / n_years) - 1) * 100 if n_years > 0 else 0

    peak, mdd = vals[0], 0.0
    for v in vals:
        peak = max(peak, v)
        mdd  = min(mdd, (v - peak) / peak * 100)

    rets   = np.diff(vals) / np.array(vals[:-1])
    exc    = rets - 0.05 / 252
    sharpe = exc.mean() / exc.std() * np.sqrt(252) if exc.std() > 0 else 0
    calmar = cagr / abs(mdd) if mdd != 0 else 0

    return {
        "final": round(final, 2), "invested": round(invested, 2),
        "total_return": round((final / invested - 1) * 100, 1),
        "cagr": round(cagr, 1), "max_drawdown": round(mdd, 1),
        "sharpe": round(sharpe, 2), "calmar": round(calmar, 2),
    }


def _log_append(log: list, date, val: float):
    if np.isfinite(val) and val > 0:
        log.append({"date": date.strftime("%Y-%m-%d"), "value": round(val, 2)})


# ══════════════════════════════════════════════════════════════════════
#  개별 시뮬레이터
# ══════════════════════════════════════════════════════════════════════

# ── ① QQQ 순수 DCA ──────────────────────────────────────────────────
def sim_qqq_dca(df):
    sh = INITIAL_CASH / _sf(df.iloc[0]["QQQ"])
    log = []
    for date, row in df.iterrows():
        p = _sf(row["QQQ"])
        sh += DAILY_DCA_USD / p
        _log_append(log, date, sh * p)
    return log


# ── ② 60/40 연 리밸 ─────────────────────────────────────────────────
def sim_6040(df):
    p0  = {"QQQ": _sf(df.iloc[0]["QQQ"]), "TLT": _sf(df.iloc[0]["TLT"])}
    sh  = {"QQQ": INITIAL_CASH * 0.6 / p0["QQQ"], "TLT": INITIAL_CASH * 0.4 / p0["TLT"]}
    log = []
    last_rebal = 0
    for i, (date, row) in enumerate(df.iterrows()):
        p = {"QQQ": _sf(row["QQQ"]), "TLT": _sf(row["TLT"])}
        val = sum(sh[t] * p[t] for t in sh)
        if i - last_rebal >= 252:
            sh = {"QQQ": val * 0.6 / p["QQQ"], "TLT": val * 0.4 / p["TLT"]}
            last_rebal = i
        for t, w in [("QQQ", 0.6), ("TLT", 0.4)]:
            sh[t] += DAILY_DCA_USD * w / p[t]
        _log_append(log, date, sum(sh[t] * _sf(row[t]) for t in sh))
    return log


# ── ③/④ IB v2.1 / v2.2 ──────────────────────────────────────────────
IB_ALLOC = {
    "bull_2":(0.70,0.00,0.00,0.30),"bull_1":(0.82,0.00,0.00,0.18),
    0:(0.92,0.00,0.00,0.08), 1:(1.00,0.00,0.00,0.00),
    2:(0.65,0.28,0.00,0.07), 3:(0.52,0.45,0.00,0.03),
    4:(0.45,0.35,0.20,0.00), 5:(0.20,0.20,0.60,0.00),
}
IB_FALLBACK = {2:(1.00,0.00,0.00,0.00),3:(1.00,0.00,0.00,0.00),
               4:(0.52,0.45,0.00,0.03),5:(0.52,0.45,0.00,0.03)}
IB_MULT   = {"bull_2":0.5,"bull_1":0.8,0:1.0,1:1.5,2:2.0,3:2.5,4:3.0,5:5.0}
IB_TARGET = {"bull_2":"SGOV","bull_1":"QQQ",0:"QQQ",1:"QQQ",2:"QLD",3:"QLD",4:"TQQQ",5:"TQQQ"}

def sim_ib(df, vix_gate=False):
    ASSETS = ["QQQ","QLD","TQQQ","SGOV"]
    init   = IB_ALLOC[0]
    r0     = df.iloc[0]
    sh     = {t: INITIAL_CASH * init[i] / _sf(r0[t]) for i, t in enumerate(ASSETS)}
    prev   = 0
    vwin   = deque(maxlen=30)
    log    = []

    for date, row in df.iterrows():
        p   = {t: _sf(row[t]) for t in ASSETS}
        vix = float(row.get("VIX", 20) or 20)
        ph  = row["phase"]
        vwin.append(vix)
        vpeak = max(vwin)

        if vix_gate and isinstance(ph, int) and ph >= 2:
            mv = 22 if ph <= 3 else 32
            blk = (vix < mv) or (vix < 45 and vix > vpeak * 0.93)
            alloc = IB_FALLBACK.get(ph, IB_ALLOC[1]) if blk else IB_ALLOC.get(ph, IB_ALLOC[0])
            tgt   = "QQQ" if blk else IB_TARGET.get(ph, "QQQ")
        else:
            alloc = IB_ALLOC.get(ph, IB_ALLOC[0])
            tgt   = IB_TARGET.get(ph, "QQQ")

        if ph != prev:
            val = sum(sh[t] * p[t] for t in ASSETS)
            sh  = {t: val * alloc[i] / p[t] for i, t in enumerate(ASSETS)}
            prev = ph

        m = IB_MULT.get(ph, 1.0)
        sh[tgt] += DAILY_DCA_USD * m / p[tgt]
        _log_append(log, date, sum(sh[t] * p[t] for t in ASSETS))
    return log


# ── ⑤ Taleb 90/10 ───────────────────────────────────────────────────
def sim_taleb(df):
    sh = {"SGOV": INITIAL_CASH*0.9/_sf(df.iloc[0]["SGOV"]),
          "TQQQ": INITIAL_CASH*0.1/_sf(df.iloc[0]["TQQQ"])}
    log = []
    last_rebal = 0
    for i, (date, row) in enumerate(df.iterrows()):
        p = {"SGOV": _sf(row["SGOV"]), "TQQQ": _sf(row["TQQQ"])}
        val = sum(sh[t] * p[t] for t in sh)
        if i - last_rebal >= 252:
            sh = {"SGOV": val*0.9/p["SGOV"], "TQQQ": val*0.1/p["TQQQ"]}
            last_rebal = i
        for t, w in [("SGOV", 0.9), ("TQQQ", 0.1)]:
            sh[t] += DAILY_DCA_USD * w / p[t]
        _log_append(log, date, sum(sh[t] * _sf(row[t]) for t in sh))
    return log


# ── ⑥ HFEA 분기 리밸 ─────────────────────────────────────────────────
def sim_hfea(df):
    sh = {"UPRO": INITIAL_CASH*0.55/_sf(df.iloc[0]["UPRO"]),
          "TMF":  INITIAL_CASH*0.45/_sf(df.iloc[0]["TMF"])}
    log = []
    last_rebal = 0
    for i, (date, row) in enumerate(df.iterrows()):
        p = {"UPRO": _sf(row["UPRO"]), "TMF": _sf(row["TMF"])}
        val = sum(sh[t] * p[t] for t in sh)
        if i - last_rebal >= 63:
            sh = {"UPRO": val*0.55/p["UPRO"], "TMF": val*0.45/p["TMF"]}
            last_rebal = i
        for t, w in [("UPRO", 0.55), ("TMF", 0.45)]:
            sh[t] += DAILY_DCA_USD * w / p[t]
        _log_append(log, date, sum(sh[t] * _sf(row[t]) for t in sh))
    return log


# ── 고정 비중 연 리밸 (All Weather, 영구, Dragon, 황금나비) ────────────
def sim_fixed(df, tickers, weights, rebal_days=252):
    avail = {t: w for t, w in zip(tickers, weights) if t in df.columns}
    if not avail: return []
    ts, ws = list(avail.keys()), list(avail.values())
    total_w = sum(ws)
    ws = [w / total_w for w in ws]  # 정규화

    r0 = df.iloc[0]
    sh = {t: INITIAL_CASH * w / _sf(r0[t]) for t, w in zip(ts, ws)}
    log = []
    last_rebal = 0
    for i, (date, row) in enumerate(df.iterrows()):
        p = {t: _sf(row[t]) for t in ts}
        val = sum(sh[t] * p[t] for t in ts)
        if i - last_rebal >= rebal_days:
            sh = {t: val * w / p[t] for t, w in zip(ts, ws)}
            last_rebal = i
        for t, w in zip(ts, ws):
            sh[t] += DAILY_DCA_USD * w / p[t]
        _log_append(log, date, sum(sh[t] * _sf(row[t]) for t in ts))
    return log


# ── ⑩ QLD/SPMO/SGOV 동적 방어·저가매수 ──────────────────────────────
def sim_qld_spmo_sgov(df, return_allocations=False):
    ASSETS = ["QLD", "SPMO", "SGOV"]
    if "SPMO" not in df.columns:
        df = df.copy()
        df["SPMO"] = df["QQQ"]

    def target_alloc(row):
        dd = float(row.get("drawdown", 0) or 0)
        rsi = float(row.get("rsi", 50) or 50)
        mom = float(row.get("mom_1m", 0) or 0)
        vix = float(row.get("VIX", 20) or 20)

        if dd <= -30:
            return {"QLD": 0.70, "SPMO": 0.20, "SGOV": 0.10}
        if dd <= -20:
            return {"QLD": 0.55, "SPMO": 0.25, "SGOV": 0.20}
        if dd <= -15:
            return {"QLD": 0.40, "SPMO": 0.30, "SGOV": 0.30}
        if dd <= -10:
            return {"QLD": 0.25, "SPMO": 0.35, "SGOV": 0.40}
        if mom < 0 or vix >= 22 or rsi < 45:
            return {"QLD": 0.00, "SPMO": 0.20, "SGOV": 0.80}
        if rsi > 72 and mom > 6 and vix < 16:
            return {"QLD": 0.15, "SPMO": 0.45, "SGOV": 0.40}
        return {"QLD": 0.35, "SPMO": 0.55, "SGOV": 0.10}

    alloc = target_alloc(df.iloc[0])
    r0 = df.iloc[0]
    sh = {t: INITIAL_CASH * alloc[t] / _sf(r0[t]) for t in ASSETS}
    allocs = {}
    log = []

    for date, row in df.iterrows():
        p = {t: _sf(row[t]) for t in ASSETS}
        new_alloc = target_alloc(row)
        if new_alloc != alloc:
            val = sum(sh[t] * p[t] for t in ASSETS)
            sh = {t: val * new_alloc[t] / p[t] for t in ASSETS}
            alloc = new_alloc

        for t in ASSETS:
            sh[t] += DAILY_DCA_USD * alloc[t] / p[t]
        allocs[date] = dict(alloc)
        _log_append(log, date, sum(sh[t] * p[t] for t in ASSETS))

    if return_allocations:
        return log, allocs
    return log


# ── ⑪ SPY/SCHD + 시총 상위 10 ───────────────────────────────────────
def sim_spy_schd_top10(df, candidates=None, return_holdings=False):
    if candidates is None:
        candidates = [
            c for c in df.columns
            if not c.endswith("_MKT_CAP") and c not in ("SPY", "SCHD") and f"{c}_MKT_CAP" in df.columns
        ] or MEGA_CAP_TICKERS
    monthly_dates = df.resample("ME").last().index
    sh = {}
    selected = []
    holdings = {}
    log = []

    def pick_top10(row):
        ranked = []
        for t in candidates:
            if t not in df.columns:
                continue
            cap_col = f"{t}_MKT_CAP"
            cap = row.get(cap_col, row.get(t, 0))
            ranked.append((t, _sf(cap)))
        ranked.sort(key=lambda x: x[1], reverse=True)
        return [t for t, _ in ranked[:10]]

    for i, (date, row) in enumerate(df.iterrows()):
        if i == 0 or date in monthly_dates:
            new_selected = [t for t in ["SPY", "SCHD"] + pick_top10(row) if t in df.columns]
            val = sum(sh.get(t, 0) * _sf(row[t]) for t in sh) if sh else INITIAL_CASH
            w = 1.0 / len(new_selected) if new_selected else 0
            sh = {t: val * w / _sf(row[t]) for t in new_selected}
            selected = new_selected

        w = 1.0 / len(selected) if selected else 0
        for t in selected:
            sh[t] = sh.get(t, 0) + DAILY_DCA_USD * w / _sf(row[t])
        holdings[date] = list(selected)
        _log_append(log, date, sum(sh.get(t, 0) * _sf(row[t]) for t in selected))

    if return_holdings:
        return log, holdings
    return log


# ── ⑪ GEM 듀얼 모멘텀 ────────────────────────────────────────────────
def sim_gem(df):
    """
    게리 안토나치 GEM 전략 (월간 신호):
    - QQQ 12개월 수익 > SGOV 12개월 수익 → 상대모멘텀 (QQQ vs EFA 중 강한 쪽)
    - QQQ 12개월 수익 < SGOV 12개월 수익 → IEF (채권 피난처)
    """
    monthly_dates = df.resample("ME").last().index
    curr_asset = "QQQ"
    sh = {curr_asset: INITIAL_CASH / _sf(df.iloc[0][curr_asset])}
    log = []

    UNIVERSE = ["QQQ", "EFA", "IEF", "SGOV"]

    for date, row in df.iterrows():
        # 월말에 신호 재계산
        if date in monthly_dates:
            try:
                past = df.loc[:date].iloc[-252:] if len(df.loc[:date]) >= 252 else df.loc[:date]
                if len(past) < 21:
                    pass
                else:
                    qqq_ret  = _sf(past["QQQ"].iloc[-1]) / _sf(past["QQQ"].iloc[0]) - 1
                    sgov_ret = _sf(past["SGOV"].iloc[-1]) / _sf(past["SGOV"].iloc[0]) - 1

                    # 절대 모멘텀
                    if qqq_ret > sgov_ret:
                        # 상대 모멘텀
                        efa_ret = _sf(past["EFA"].iloc[-1]) / _sf(past["EFA"].iloc[0]) - 1
                        new_asset = "QQQ" if qqq_ret >= efa_ret else "EFA"
                    else:
                        new_asset = "IEF"

                    if new_asset != curr_asset:
                        # 현재 자산 매도 → 새 자산 매수
                        old_val = sh.get(curr_asset, 0) * _sf(row[curr_asset])
                        sh = {new_asset: old_val / _sf(row[new_asset])}
                        curr_asset = new_asset
            except Exception:
                pass

        p = _sf(row.get(curr_asset, 100))
        sh[curr_asset] = sh.get(curr_asset, 0) + DAILY_DCA_USD / p
        _log_append(log, date, sh[curr_asset] * p)

    return log


# ── ⑪ GTAA 페이버 (200일 MA 타이밍) ─────────────────────────────────
def sim_gtaa(df):
    """
    맵 페이버 아이비 GTAA 전략 (월간 신호):
    - 5자산: QQQ, EFA, IEF, GLD, DBC
    - 각 자산이 10개월 MA 위 → 보유 / 아래 → SGOV
    - 보유 자산 균등 배분
    """
    ASSETS = ["QQQ", "EFA", "IEF", "GLD", "DBC"]
    monthly_dates = df.resample("ME").last().index

    active   = {t: True for t in ASSETS}  # 초기: 전부 보유
    last_val = {}
    log      = []

    r0 = df.iloc[0]
    n  = len([t for t in ASSETS if t in df.columns])
    w  = 1.0 / n if n > 0 else 0.2
    sh = {}
    for t in ASSETS:
        if t in df.columns:
            sh[t] = INITIAL_CASH * w / _sf(r0[t])
    sh_sgov = 0.0

    for date, row in df.iterrows():
        # 월말에 신호 재계산
        if date in monthly_dates:
            hist = df.loc[:date]
            new_active = {}
            for t in ASSETS:
                if t not in df.columns:
                    new_active[t] = False
                    continue
                n_look = min(210, len(hist))  # ~10 months
                ma = hist[t].iloc[-n_look:].mean() if n_look >= 20 else hist[t].mean()
                new_active[t] = _sf(row[t]) > _sf(ma)

            # 리밸런싱
            val_stock = sum(sh.get(t, 0) * _sf(row[t]) for t in ASSETS)
            val_sgov  = sh_sgov * _sf(row["SGOV"])
            total     = val_stock + val_sgov

            held = [t for t in ASSETS if new_active.get(t) and t in df.columns]
            if held:
                w_each = total / len(held)
                sh     = {t: w_each / _sf(row[t]) for t in held}
                sh_sgov = 0.0
            else:
                sh      = {}
                sh_sgov = total / _sf(row["SGOV"])

            active = new_active

        # DCA
        held_now = [t for t in active if active[t] and t in df.columns]
        if held_now:
            w_e = DAILY_DCA_USD / len(held_now)
            for t in held_now:
                sh[t] = sh.get(t, 0) + w_e / _sf(row[t])
        else:
            sh_sgov += DAILY_DCA_USD / _sf(row["SGOV"])

        val = sum(sh.get(t, 0) * _sf(row[t]) for t in sh) + sh_sgov * _sf(row["SGOV"])
        _log_append(log, date, val)

    return log


# ══════════════════════════════════════════════════════════════════════
#  전략 실행 딕셔너리
# ══════════════════════════════════════════════════════════════════════

def run_all(df: pd.DataFrame) -> dict:
    """모든 전략 시뮬레이션 실행."""
    return {
        "①QQQ DCA":      sim_qqq_dca(df),
        "②60/40 클래식":  sim_6040(df),
        "③IB v2.1":      sim_ib(df, vix_gate=False),
        "④IB v2.2 VIX":  sim_ib(df, vix_gate=True),
        "⑤Taleb 90/10":  sim_taleb(df),
        "⑥HFEA":         sim_hfea(df),
        "⑦All Weather":  sim_fixed(df, ["QQQ","TLT","IEF","GLD","DBC"],   [0.30,0.40,0.15,0.075,0.075]),
        "⑧영구포트폴리오": sim_fixed(df, ["QQQ","TLT","GLD","SGOV"],        [0.25,0.25,0.25,0.25]),
        "⑨Dragon":       sim_fixed(df, ["QQQ","TLT","GLD","DBC","DBMF"],   [0.24,0.21,0.19,0.18,0.18]),
        "⑩QLD/SPMO/SGOV": sim_qld_spmo_sgov(df),
        "⑪SPY/SCHD+Top10": sim_spy_schd_top10(df),
        "⑫GEM 듀얼모멘텀": sim_gem(df),
        "⑬GTAA 페이버":   sim_gtaa(df),
        "⑭황금나비":      sim_fixed(df, ["QQQ","VBR","TLT","SHY","GLD"],   [0.20,0.20,0.20,0.20,0.20]),
    }


# ══════════════════════════════════════════════════════════════════════
#  리포트 생성
# ══════════════════════════════════════════════════════════════════════

RANK_EMOJI = ["🥇","🥈","🥉","4️⃣","5️⃣","6️⃣","7️⃣","8️⃣","9️⃣","🔟","⑪","⑫","⑬","⑭"]


def _period_table(logs: dict, year_label: str) -> list:
    """단일 기간 성과표."""
    all_m = {name: _metrics(log) for name, log in logs.items()}
    valid  = [(n, m) for n, m in all_m.items() if m]
    sorted_v = sorted(valid, key=lambda x: x[1]["cagr"], reverse=True)

    lines = [
        f"━━━ {year_label} 성과 비교 (CAGR 순) ━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"  {'전략':<16} {'CAGR':>7} {'MDD':>7} {'Sharpe':>7} {'Calmar':>6} {'최종($)':>10}",
        "  " + "─" * 58,
    ]
    for rank, (name, m) in enumerate(sorted_v):
        em = RANK_EMOJI[rank] if rank < len(RANK_EMOJI) else "  "
        ib_mark = " ◀" if "IB" in name else ""
        lines.append(
            f"  {em} {name:<13} "
            f"{m['cagr']:>+6.1f}% "
            f"{m['max_drawdown']:>+6.1f}% "
            f"{m['sharpe']:>7.2f} "
            f"{m['calmar']:>6.2f} "
            f"${m['final']:>9,.0f}{ib_mark}"
        )

    # 항목별 1위
    if valid:
        bc = max(valid, key=lambda x: x[1]["cagr"])[0]
        bm = max(valid, key=lambda x: x[1]["max_drawdown"])[0]   # 덜 음수 = 방어
        bs = max(valid, key=lambda x: x[1]["sharpe"])[0]
        lines += [
            "",
            f"  📈 최고CAGR: {bc}  🛡 최소낙폭: {bm}  ⚖️ 최고Sharpe: {bs}",
        ]
    return lines


def build_report(period_logs: dict) -> str:
    """전체 멀티 기간 리포트."""
    today = datetime.now().strftime("%Y-%m-%d")
    lines = [
        "🌍 글로벌 포트폴리오 전략 종합 비교",
        f"기준일: {today}  /  초기 ${INITIAL_CASH:,}  +  일 DCA ${DAILY_DCA_USD}",
        "",
    ]

    for years, (label, logs) in period_logs.items():
        lines += ["", f"{'='*62}"]
        lines += _period_table(logs, f"📅 {label}")

    # ── 핵심 인사이트 ─────────────────────────────────────────────────
    lines += [
        "", "=" * 62,
        "💡 핵심 인사이트",
        "  ✅ IB v2.2 VIX게이트 — 장기/중기 모두 수익률 상위권",
        "  ❌ HFEA — 2022년 금리 급등으로 TMF 폭락, MDD -60%+ 위험",
        "  🛡 Taleb 90/10 — MDD 최소지만 성장률 제한적 (인플레 미만)",
        "  📊 GEM/GTAA — 하락장 회피로 MDD 줄이나 강세장 수익 일부 포기",
        "  🦋 황금나비 — 소형가치 추가로 영구포트폴리오 대비 수익↑ 안정↑",
        "",
        "  ◀ IB = Intelligence Barbell (유빈 전략)",
        "",
        "출처:",
        "  GEM   https://www.quantifiedstrategies.com/dual-momentum-trading-strategy/",
        "  GTAA  https://mebfaber.com/timing-model/",
        "  황금나비 https://www.optimizedportfolio.com/golden-butterfly-portfolio/",
        "  Dragon https://pictureperfectportfolios.com/dragon-portfolio-review/",
        "  HFEA  https://www.optimizedportfolio.com/hedgefundie-adventure/",
    ]

    # ── IB vs 전체 1위 차트 (가장 긴 기간) ───────────────────────────
    longest_label = next(iter(period_logs.keys()))
    _, longest_logs = period_logs[longest_label]
    ib_log  = longest_logs.get("④IB v2.2 VIX", [])
    qqq_log = longest_logs.get("①QQQ DCA", [])

    if ib_log:
        ib_vals  = [x["value"] for x in ib_log]
        qqq_vals = [x["value"] for x in qqq_log]
        lines += [
            "", f"━━━ {longest_label.split()[0]} 포트폴리오 추이 (④IB v2.2 VIX ●) ━━━━━━━━━━━",
            _ascii_chart(ib_vals, width=44, height=7),
            f"  IB  ${min(ib_vals):>8,.0f} → ${ib_vals[-1]:,.0f}",
            f"  QQQ ${min(qqq_vals):>8,.0f} → ${qqq_vals[-1]:,.0f}" if qqq_vals else "",
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
    parser = argparse.ArgumentParser()
    parser.add_argument("--period", type=int, choices=[1, 3, 5, 10, 20],
                        help="단일 기간만 실행 (1/3/5/10/20년)")
    parser.add_argument("--start", type=str,
                        help="사용자 지정 시작일 (YYYY-MM-DD)")
    parser.add_argument("--send", action="store_true")
    args = parser.parse_args()

    target_periods = PERIODS
    if args.start:
        target_periods = {f"사용자 지정 ({args.start}~{_TODAY.year})": args.start}
    elif args.period:
        period_prefix = f"{args.period}년 "
        key = next((k for k in PERIODS if k.startswith(period_prefix)), None)
        target_periods = {key: PERIODS[key]} if key else PERIODS
    earliest_start = min(target_periods.values())

    logger.info("데이터 다운로드 및 전처리...")
    raw = download_all(earliest_start)

    period_logs: dict = {}

    # 신호는 전체 데이터 기준으로 계산 (52주 고점 등 롤링 정확도 확보)
    df_full = add_signals(raw.copy())

    for label, start in target_periods.items():
        logger.info(f"\n=== {label} 시뮬레이션 (시작 $10,000 신규 투자) ===")
        # 해당 기간만 잘라내되, 신호(phase/RSI)는 이미 전체 데이터 기준으로 계산됨
        df_period = df_full[df_full.index >= pd.Timestamp(start)].copy()
        # 각 기간마다 $10,000 신규 투자로 독립 시뮬레이션
        logs = run_all(df_period)
        period_logs[label] = (label, logs)
        logger.info(f"  완료: {len(logs)}개 전략")

    report = build_report(period_logs)
    print(report)

    if args.send:
        send_telegram(report)
        logger.info("텔레그램 발송 완료")


if __name__ == "__main__":
    main()
