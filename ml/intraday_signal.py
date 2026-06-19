"""ml/intraday_signal.py — 단기봉(1m/5m/15m) 실시간 신호 분석

특징:
  - yfinance interval="1m" (7일), "5m"(60일), "15m"(60일), "1h"(730일) 지원
  - 미국 장중(ET 09:30~16:00) / 한국 장중(KST 09:00~15:30) 자동 감지
  - 1분 캐시로 중복 API 호출 최소화
  - 관심 종목(score ≥ 0.5) 자동 선별 → 1분 주기 체크 대상

단기 신호 항목:
  VWAP 이격      — 현재가가 VWAP 위/아래 위치 (당일)
  거래량 급등    — 최근 5분 거래량 vs 20일 평균 5분 거래량
  RSI 반등       — 1시간봉 RSI가 30 이하에서 35 이상으로 반등
  모멘텀 급등    — 15분 수익률 > 1.5% (상승 가속)
  BB 스퀴즈      — 볼린저밴드 수렴 후 돌파 (변동성 폭발 전조)
  MA 크로스      — 5분 EMA(9) > EMA(21) 상향 돌파

공개 API:
  is_us_market_open()            → bool
  is_kr_market_open()            → bool
  fetch_intraday(ticker, iv, d)  → pd.DataFrame (OHLCV)
  analyze_intraday(ticker)       → IntradaySignal
  check_intraday_movers(tickers) → list[IntradaySignal]  ← 이상 감지된 것만
  format_intraday_alert(sig)     → str (텔레그램 메시지)
"""
from __future__ import annotations

import json
import logging
import os
import pickle
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))
ET  = timezone(timedelta(hours=-4))   # EDT (4~10월); EST는 -5

CACHE_DIR = Path(os.path.expanduser("~/reports/ml-cache"))
INTRADAY_TTL_SEC = 60   # 1분 캐시 (API 부하 제한)
INTRADAY_SENT_STATE_PATH = CACHE_DIR / "intraday_sent_signals.json"

# ── 장 운영 시간 판별 ─────────────────────────────────────────────────────────

def is_us_market_open() -> bool:
    """미국 뉴욕증권거래소 장중 여부 (ET 기준, 서머타임 자동 적용)."""
    now_et = datetime.now(ET)
    # 토/일 제외
    if now_et.weekday() >= 5:
        return False
    t = now_et.time()
    from datetime import time
    return time(9, 30) <= t <= time(16, 0)


def is_kr_market_open() -> bool:
    """한국 KRX 장중 여부 (KST 09:00~15:30, 월~금)."""
    now_kst = datetime.now(KST)
    if now_kst.weekday() >= 5:
        return False
    t = now_kst.time()
    from datetime import time
    return time(9, 0) <= t <= time(15, 30)


def market_status() -> dict:
    """현재 시장 상태 요약."""
    return {
        "us_open": is_us_market_open(),
        "kr_open": is_kr_market_open(),
        "now_et":  datetime.now(ET).strftime("%H:%M ET"),
        "now_kst": datetime.now(KST).strftime("%H:%M KST"),
    }


# ── 단기봉 데이터 로드 ────────────────────────────────────────────────────────

def _intraday_cache_key(ticker: str, interval: str) -> str:
    import hashlib
    h = hashlib.md5(f"{ticker}_{interval}".encode()).hexdigest()[:8]
    return f"intraday_{ticker[:20]}_{interval}_{h}"


def _load_intraday_cache(key: str) -> Optional[pd.DataFrame]:
    path = CACHE_DIR / f"{key}.pkl"
    if not path.exists():
        return None
    age = (datetime.now() - datetime.fromtimestamp(path.stat().st_mtime)).total_seconds()
    if age > INTRADAY_TTL_SEC:
        return None
    try:
        return pickle.loads(path.read_bytes())
    except Exception:
        return None


def _save_intraday_cache(key: str, df: pd.DataFrame) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        (CACHE_DIR / f"{key}.pkl").write_bytes(pickle.dumps(df))
    except Exception as e:
        logger.debug("캐시 저장 실패: %s", e)


def _normalize_intraday_df(df: pd.DataFrame) -> pd.DataFrame:
    """yfinance single-ticker intraday frame shape normalization."""
    if df.empty:
        return df
    if isinstance(df.columns, pd.MultiIndex):
        # yfinance may return columns like ("Close", "000660.KS") even for one ticker.
        if df.columns.nlevels >= 2 and len(df.columns.get_level_values(-1).unique()) == 1:
            df = df.droplevel(-1, axis=1)
    return df


def _format_bar_timestamp(index_value) -> str:
    try:
        ts = pd.Timestamp(index_value)
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        else:
            ts = ts.tz_convert("UTC")
        return ts.tz_convert("Asia/Seoul").strftime("%H:%M KST")
    except Exception:
        return datetime.now(KST).strftime("%H:%M KST")


def fetch_intraday(
    ticker:   str,
    interval: str = "5m",   # "1m" | "5m" | "15m" | "1h"
    days:     int  = 5,
) -> pd.DataFrame:
    """단기봉 OHLCV 다운로드 (1분 캐시).

    interval 별 최대 기간:
      1m  → 7일   (실시간에 가장 가까운 데이터, 15분 지연)
      5m  → 60일
      15m → 60일
      1h  → 730일
    """
    key    = _intraday_cache_key(ticker, interval)
    cached = _load_intraday_cache(key)
    if cached is not None:
        return _normalize_intraday_df(cached)

    try:
        import yfinance as yf
        period_map = {"1m": "7d", "5m": "60d", "15m": "60d", "1h": "730d"}
        period = period_map.get(interval, "5d")
        # days 파라미터로 제한 (너무 많은 데이터 방지)
        if interval == "1m":
            period = f"{min(days, 7)}d"
        elif interval in ("5m", "15m"):
            period = f"{min(days, 60)}d"

        df = yf.download(ticker, period=period, interval=interval,
                         progress=False, auto_adjust=True)
        if df.empty:
            return pd.DataFrame()

        df = _normalize_intraday_df(df)

        # tz 제거 (단순화)
        if hasattr(df.index, "tz") and df.index.tz is not None:
            df.index = df.index.tz_convert("UTC").tz_localize(None)

        _save_intraday_cache(key, df)
        return df

    except Exception as e:
        logger.warning("fetch_intraday(%s, %s) 실패: %s", ticker, interval, e)
        return pd.DataFrame()


# ── 단기 피처 계산 ────────────────────────────────────────────────────────────

def compute_intraday_features(df: pd.DataFrame) -> pd.DataFrame:
    """OHLCV DataFrame → 단기 기술 피처 계산."""
    if df.empty or len(df) < 10:
        return pd.DataFrame()

    close  = df["Close"].squeeze()
    high   = df["High"].squeeze()
    low    = df["Low"].squeeze()
    volume = df["Volume"].squeeze() if "Volume" in df.columns else pd.Series(0, index=df.index)

    feat = pd.DataFrame(index=df.index)

    # VWAP (당일 누적)
    try:
        typical  = (high + low + close) / 3
        cum_tv   = (typical * volume).cumsum()
        cum_vol  = volume.cumsum().replace(0, np.nan)
        vwap     = cum_tv / cum_vol
        feat["vwap"]         = vwap
        feat["vwap_dev"]     = (close / vwap.replace(0, np.nan) - 1)
    except Exception:
        feat["vwap"] = np.nan
        feat["vwap_dev"] = np.nan

    # RSI(14)
    delta = close.diff()
    gain  = delta.clip(lower=0).rolling(14).mean()
    loss  = (-delta.clip(upper=0)).rolling(14).mean()
    feat["rsi"] = 100 - 100 / (1 + gain / loss.replace(0, np.nan))

    # EMA 크로스 (9/21 EMA)
    ema9  = close.ewm(span=9,  adjust=False).mean()
    ema21 = close.ewm(span=21, adjust=False).mean()
    feat["ema9"]      = ema9
    feat["ema21"]     = ema21
    feat["ema_cross"] = (ema9 > ema21).astype(float)
    feat["ema_cross_up"] = ((ema9 > ema21) & (ema9.shift(1) <= ema21.shift(1))).astype(float)

    # 거래량 Z-score (20봉 대비)
    vol_ma  = volume.rolling(20).mean()
    vol_std = volume.rolling(20).std().replace(0, np.nan)
    feat["vol_zscore"] = (volume - vol_ma) / vol_std
    feat["vol_ratio"]  = volume / vol_ma.replace(0, np.nan)

    # 모멘텀 (3봉, 6봉, 12봉)
    for n in (3, 6, 12):
        feat[f"mom_{n}"] = close.pct_change(n)

    # 볼린저밴드 (20봉)
    ma20  = close.rolling(20).mean()
    std20 = close.rolling(20).std().replace(0, np.nan)
    feat["bb_upper"] = ma20 + 2 * std20
    feat["bb_lower"] = ma20 - 2 * std20
    feat["bb_pct_b"] = (close - feat["bb_lower"]) / (feat["bb_upper"] - feat["bb_lower"]).replace(0, np.nan)
    # BB 폭 (좁을수록 스퀴즈)
    feat["bb_width"] = (feat["bb_upper"] - feat["bb_lower"]) / ma20.replace(0, np.nan)

    # ATR(14) — 변동성 수준
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs(),
    ], axis=1).max(axis=1)
    feat["atr"] = tr.ewm(span=14, adjust=False).mean()

    return feat


# ── 단기 진입 신호 ────────────────────────────────────────────────────────────

@dataclass
class IntradaySignal:
    ticker:      str
    interval:    str        # "1m" | "5m" | "15m"
    currency:    str        # "USD" | "KRW"

    # 현재 상태
    price:       float
    change_pct:  float      # 당일 또는 최근 N봉 변화율
    vwap_dev:    float      # VWAP 이격 (양수=위, 음수=아래)
    rsi:         float
    vol_ratio:   float      # 현재 거래량 / 평균 거래량
    ema_cross_up: bool      # EMA 9/21 골든크로스 발생

    # 알림 트리거
    alerts:      list[str] = field(default_factory=list)
    score:       float = 0.0    # 0~1 단기 신호 강도
    timestamp:   str = ""


def intraday_signal_key(sig: IntradaySignal) -> str:
    alerts_key = "|".join(sig.alerts)
    return f"{sig.ticker}|{sig.interval}|{sig.timestamp}|{alerts_key}"


def _intraday_signal_state_slot(sig: IntradaySignal) -> str:
    return f"{sig.ticker}|{sig.interval}"


def _load_intraday_sent_state(state_path: Path) -> dict:
    try:
        state = json.loads(state_path.read_text()) if state_path.exists() else {}
        return state if isinstance(state, dict) else {}
    except Exception:
        return {}


def should_emit_intraday_signal(sig: IntradaySignal, state_path: Path = INTRADAY_SENT_STATE_PATH) -> bool:
    state = _load_intraday_sent_state(state_path)
    return state.get(_intraday_signal_state_slot(sig)) != intraday_signal_key(sig)


def mark_intraday_signal_emitted(sig: IntradaySignal, state_path: Path = INTRADAY_SENT_STATE_PATH) -> None:
    state = _load_intraday_sent_state(state_path)
    state[_intraday_signal_state_slot(sig)] = intraday_signal_key(sig)
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2))
    except Exception as e:
        logger.debug("단기 신호 중복 상태 저장 실패: %s", e)


def analyze_intraday(
    ticker:   str,
    interval: str = "5m",
    days:     int = 3,
) -> Optional[IntradaySignal]:
    """단일 종목 단기 신호 계산."""
    df = fetch_intraday(ticker, interval=interval, days=days)
    if df.empty or len(df) < 20:
        return None

    feat = compute_intraday_features(df)
    if feat.empty:
        return None

    close  = df["Close"].squeeze()
    cur    = feat.iloc[-1]
    cur_px = float(close.iloc[-1])

    # 당일 변화율 (오늘 첫 봉 대비)
    today = datetime.utcnow().date()
    today_mask = df.index.date == today
    if today_mask.any():
        open_px    = float(df["Close"][today_mask].iloc[0])
        change_pct = (cur_px / open_px - 1) if open_px > 0 else 0.0
    else:
        change_pct = float(cur.get("mom_12", 0))

    currency = "KRW" if (ticker.endswith(".KS") or ticker.endswith(".KQ")) else "USD"

    # ── 알림 조건 평가 ──────────────────────────────────────────────────────
    alerts: list[str] = []
    score_parts: list[float] = []

    # 1. 거래량 급등 (vol_ratio > 3×)
    vol_r = float(cur.get("vol_ratio", 1))
    if vol_r >= 5:
        alerts.append(f"🔥 거래량 급등 {vol_r:.0f}×")
        score_parts.append(0.35)
    elif vol_r >= 3:
        alerts.append(f"📈 거래량 증가 {vol_r:.0f}×")
        score_parts.append(0.20)

    # 2. EMA 골든크로스
    if bool(cur.get("ema_cross_up", 0)):
        alerts.append("⚡ EMA 9/21 상향 돌파")
        score_parts.append(0.30)

    # 3. RSI 반등 (30 이하→35 이상 돌파)
    rsi_v = float(cur.get("rsi", 50))
    prev_rsi = float(feat["rsi"].iloc[-2]) if len(feat) >= 2 else rsi_v
    if prev_rsi < 32 and rsi_v >= 35:
        alerts.append(f"🔄 RSI 반등 {prev_rsi:.0f}→{rsi_v:.0f}")
        score_parts.append(0.25)
    elif rsi_v < 28:
        alerts.append(f"⚠️ RSI 과매도 {rsi_v:.0f}")
        score_parts.append(0.10)

    # 4. VWAP 돌파 (아래→위)
    vwap_dev = float(cur.get("vwap_dev", 0))
    prev_vwap_dev = float(feat["vwap_dev"].iloc[-2]) if len(feat) >= 2 else vwap_dev
    if prev_vwap_dev < -0.003 and vwap_dev >= 0:
        alerts.append(f"🎯 VWAP 상향 돌파 ({vwap_dev*100:+.2f}%)")
        score_parts.append(0.20)

    # 5. BB 스퀴즈 후 돌파
    bb_pct_b = float(cur.get("bb_pct_b", 0.5))
    bb_width = float(cur.get("bb_width", 0.05))
    prev_bw  = float(feat["bb_width"].dropna().iloc[-6:-1].mean()) if len(feat) >= 6 else bb_width
    if bb_pct_b > 0.95 and bb_width > prev_bw * 1.3:
        alerts.append(f"💥 BB 상방 돌파 (스퀴즈 해소)")
        score_parts.append(0.25)

    # 6. 단기 모멘텀 급등
    mom12 = float(cur.get("mom_12", 0))
    if mom12 > 0.015:
        alerts.append(f"🚀 {interval} 모멘텀 {mom12*100:+.1f}%")
        score_parts.append(0.15)
    elif mom12 < -0.015:
        alerts.append(f"🔻 {interval} 급락 {mom12*100:+.1f}%")
        score_parts.append(0.05)

    score = min(1.0, sum(score_parts))

    return IntradaySignal(
        ticker       = ticker,
        interval     = interval,
        currency     = currency,
        price        = cur_px,
        change_pct   = change_pct,
        vwap_dev     = vwap_dev,
        rsi          = rsi_v,
        vol_ratio    = vol_r,
        ema_cross_up = bool(cur.get("ema_cross_up", 0)),
        alerts       = alerts,
        score        = round(score, 3),
        timestamp    = _format_bar_timestamp(df.index[-1]),
    )


# ── 전체 감시 ────────────────────────────────────────────────────────────────

def check_intraday_movers(
    tickers:       list[str],
    interval:      str   = "5m",
    min_score:     float = 0.25,   # 이 이상만 반환
    max_results:   int   = 10,
) -> list[IntradaySignal]:
    """여러 종목 단기 이상 감지 (score ≥ min_score 종목만 반환)."""
    results: list[IntradaySignal] = []
    for ticker in tickers:
        try:
            sig = analyze_intraday(ticker, interval=interval)
            if sig and sig.score >= min_score and sig.alerts:
                results.append(sig)
        except Exception as e:
            logger.debug("%s 단기 분석 실패: %s", ticker, e)

    return sorted(results, key=lambda x: -x.score)[:max_results]


# ── 텔레그램 포맷 ─────────────────────────────────────────────────────────────

def format_intraday_alert(sig: IntradaySignal) -> str:
    """단기 신호 알림 메시지."""
    from ml.entry_analyzer import KR_META, _TICKER_NAME
    if sig.currency == "KRW":
        kr_info = KR_META.get(sig.ticker)
        name    = f"{kr_info[0]}({kr_info[1]})" if kr_info else sig.ticker
        price_s = f"₩{sig.price:,.0f}"
    else:
        name    = _TICKER_NAME.get(sig.ticker, sig.ticker)
        price_s = f"${sig.price:.2f}"

    flag = "🇰🇷" if sig.currency == "KRW" else "🇺🇸"
    lines = [
        f"⚡ 단기 신호 감지 [{sig.interval}봉] {flag}",
        f"━━━━━━━━━━━━━━━━━━━━━━━",
        f"{sig.ticker}  {name}",
        f"현재가: {price_s}  당일 {sig.change_pct*100:+.1f}%",
        f"",
        f"[ 신호 강도: {sig.score:.2f} ]",
    ]
    for a in sig.alerts:
        lines.append(f"  {a}")

    lines += [
        f"",
        f"RSI {sig.rsi:.0f}  |  VWAP {sig.vwap_dev*100:+.2f}%  |  거래량 {sig.vol_ratio:.1f}×",
        f"({sig.timestamp})",
        f"",
        f"⚠️ 단기 신호 — 변동성 높음, 손절 필수",
    ]
    return "\n".join(lines)


def format_intraday_summary(signals: list[IntradaySignal]) -> str:
    """여러 단기 신호 요약 리포트."""
    if not signals:
        return "📊 현재 단기 이상 신호 없음"

    ts = datetime.now(KST).strftime("%H:%M KST")
    lines = [
        f"⚡ 단기 이상 감지 ({len(signals)}건)",
        f"━━━━━━━━━━━━━━━━━━━━━━━",
        f"({ts})",
        "",
    ]
    for sig in signals:
        flag = "🇰🇷" if sig.currency == "KRW" else "🇺🇸"
        from ml.entry_analyzer import _TICKER_NAME, KR_META
        if sig.currency == "KRW":
            kr_info = KR_META.get(sig.ticker)
            name    = kr_info[0] if kr_info else sig.ticker
        else:
            name = _TICKER_NAME.get(sig.ticker, sig.ticker)

        price_s = f"₩{sig.price:,.0f}" if sig.currency == "KRW" else f"${sig.price:.2f}"
        lines += [
            f"{flag} {sig.ticker} {name}  [점수:{sig.score:.2f}]  {price_s} ({sig.change_pct*100:+.1f}%)",
            "   " + "  |  ".join(sig.alerts[:2]),
        ]
    return "\n".join(lines)
