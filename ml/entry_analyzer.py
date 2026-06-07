"""ml/entry_analyzer.py — 레버리지 ETF + 개별주 진입 타점 분석

현재 시장 지표 + 과거 유사 조건 분포 → 진입 확률·기대수익·신호 산출.

알고리즘:
  1. 현재 특징 벡터 추출 (낙폭, RSI, 모멘텀, VIX, 거래량)
  2. 과거 5년 동일 특징 벡터 계산
  3. 코사인 유사도 상위 N개 유사 기간 탐색 (k-NN)
  4. 유사 기간의 10/20/60일 선행 수익률 분포 → 승률·기대수익·손실위험 계산
  5. 복합 진입 점수 산출 → 신호 분류 (enter / wait / avoid)

공개 API:
  analyze_entry(ticker, ...)         → EntryScore 단일 종목
  analyze_all_entries()              → list[EntryScore] 전체 포트폴리오
  format_entry_report(scores)        → 텔레그램 텍스트
  check_alert_signals(prev_state)    → 신규 알림 대상 list[EntryScore]
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))

# ── 대상 종목 ─────────────────────────────────────────────────────────────────

PORTFOLIO_STOCKS = ["MSFT", "NVDA", "GOOGL", "ORCL", "SAP", "UNH", "SPMO", "QQQI"]
LEVERAGE_ETFS    = ["QLD", "TQQQ", "UPRO"]
LEVERAGE_UNDERLYING = {"QLD": "QQQ", "TQQQ": "QQQ", "UPRO": "SPY"}

ALERT_STATE_PATH = Path(os.path.expanduser("~/.cache/entry_alert_state.json"))
ALERT_COOLDOWN_H = 6      # 동일 종목 재알림 최소 간격 (시간)
ALERT_SCORE_MIN  = 0.60   # 알림 발송 최소 점수

# ── 데이터 컨테이너 ───────────────────────────────────────────────────────────

@dataclass
class EntryScore:
    ticker:      str
    category:    str          # "leverage" | "stock"
    underlying:  str          # 기초지수 (QQQ/SPY) 또는 자기자신

    # ── 현재 상태 ──
    current_drawdown: float   # 52주 고점 대비 낙폭 (음수)
    current_rsi:      float
    current_vix:      float
    current_mom_20d:  float
    current_mom_60d:  float
    current_price:    float

    # ── 유사 기간 분석 ──
    n_similar:        int
    win_prob_20d:     float   # 20일 후 양수 수익 확률
    win_prob_60d:     float   # 60일 후 양수 수익 확률
    expected_ret_20d: float   # 20일 중앙값 기대수익
    expected_ret_60d: float   # 60일 중앙값 기대수익
    downside_p25_20d: float   # 20일 25분위 (하방 위험)
    upside_p75_20d:   float   # 20일 75분위 (상방 기대)

    # ── 신호 ──
    score:    float            # 0~1 진입 점수
    signal:   str              # "enter" / "wait" / "avoid"
    reasons:  list[str] = field(default_factory=list)
    timestamp: str = ""


# ── 피처 계산 ─────────────────────────────────────────────────────────────────

def _compute_ticker_features(price: pd.Series, vix: pd.Series) -> pd.DataFrame:
    """단일 종목 특징 벡터 시계열 계산."""
    feat = pd.DataFrame(index=price.index)

    # 낙폭 (52주 고점 대비)
    high_52w = price.rolling(252, min_periods=60).max()
    feat["drawdown"] = (price / high_52w - 1).fillna(0)

    # RSI(14)
    delta = price.diff()
    gain  = delta.clip(lower=0).rolling(14).mean()
    loss  = (-delta.clip(upper=0)).rolling(14).mean()
    feat["rsi"] = (100 - 100 / (1 + gain / loss.replace(0, np.nan))).fillna(50)

    # 모멘텀
    feat["mom_20d"] = price.pct_change(20).fillna(0)
    feat["mom_60d"] = price.pct_change(60).fillna(0)

    # 변동성 (20일)
    feat["vol_20d"] = price.pct_change().rolling(20).std().fillna(0.02)

    # VIX (시장 공포)
    vix_r = vix.reindex(price.index).ffill()
    feat["vix"] = vix_r.fillna(20)

    # BB %B (20일)
    ma20  = price.rolling(20).mean()
    std20 = price.rolling(20).std().replace(0, np.nan)
    feat["bb_pct_b"] = ((price - (ma20 - 2 * std20)) / (4 * std20)).clip(0, 1).fillna(0.5)

    return feat.dropna(how="any")


def _find_similar(
    current: pd.Series,
    history: pd.DataFrame,
    n: int = 30,
    lookback: int = 10,
) -> pd.Index:
    """현재 특징 벡터와 유사한 과거 기간 탐색 (정규화 유클리드 거리).

    lookback: 최근 n일은 제외 (최신 데이터 리크 방지).
    """
    hist = history.iloc[:-lookback] if len(history) > lookback else history
    if hist.empty:
        return pd.Index([])

    # z-score 정규화
    mu  = hist.mean()
    std = hist.std().replace(0, 1)
    h_n = (hist - mu) / std
    c_n = (current - mu) / std

    dists = np.sqrt(((h_n - c_n) ** 2).sum(axis=1))
    return dists.nsmallest(n).index


# ── 단일 종목 분석 ────────────────────────────────────────────────────────────

def analyze_entry(
    ticker:    str,
    price_df:  pd.DataFrame,
    vix:       pd.Series,
    n_similar: int = 30,
    category:  str = "stock",
    underlying_price: pd.Series | None = None,
) -> Optional[EntryScore]:
    """단일 종목 진입 점수 계산.

    레버리지 ETF: underlying_price(QQQ/SPY)로 낙폭·신호 계산.
    개별주:       자기 자신 가격으로 낙폭·신호 계산.
    """
    if price_df is None or len(price_df) < 120:
        return None

    price = price_df["Close"].dropna()
    if len(price) < 120:
        return None

    # 신호 계산 기준 가격 (레버리지는 기초지수 기준)
    signal_price = underlying_price if underlying_price is not None else price

    try:
        feat = _compute_ticker_features(signal_price, vix)
        if feat.empty or len(feat) < 60:
            return None

        # 현재 특징
        cur = feat.iloc[-1]

        # 유사 기간 탐색
        sim_idx = _find_similar(cur, feat, n=n_similar)
        if len(sim_idx) == 0:
            return None

        # 선행 수익률 분포 (레버리지 ETF는 실제 ETF 가격 기준)
        fwd_price = price.reindex(feat.index)
        fwd_10d   = fwd_price.pct_change(10).shift(-10)
        fwd_20d   = fwd_price.pct_change(20).shift(-20)
        fwd_60d   = fwd_price.pct_change(60).shift(-60)

        rets_20 = fwd_20d.reindex(sim_idx).dropna()
        rets_60 = fwd_60d.reindex(sim_idx).dropna()

        if len(rets_20) < 3:
            return None

        win_20  = float((rets_20 > 0).mean())
        win_60  = float((rets_60 > 0).mean()) if len(rets_60) >= 3 else win_20
        exp_20  = float(rets_20.median())
        exp_60  = float(rets_60.median()) if len(rets_60) >= 3 else exp_20
        p25_20  = float(rets_20.quantile(0.25))
        p75_20  = float(rets_20.quantile(0.75))

        # 현재 시장 상태
        high_52w   = price.rolling(252, min_periods=60).max().iloc[-1]
        cur_dd     = float(price.iloc[-1] / high_52w - 1) if high_52w > 0 else 0.0
        cur_price  = float(price.iloc[-1])
        und_label  = LEVERAGE_UNDERLYING.get(ticker, ticker)

        # ── 진입 점수 계산 ──────────────────────────────────────────────────
        reasons: list[str] = []
        score_parts: list[float] = []

        # 1. 승률 (40%)
        win_s = max(0.0, min(1.0, (win_20 - 0.3) / 0.5))
        score_parts.append(win_s * 0.40)
        if win_20 >= 0.65:
            reasons.append(f"승률 {win_20*100:.0f}% (강세)")
        elif win_20 >= 0.55:
            reasons.append(f"승률 {win_20*100:.0f}% (보통)")
        else:
            reasons.append(f"승률 {win_20*100:.0f}% (약세)")

        # 2. 손익비 (30%)
        rr = abs(exp_20 / p25_20) if p25_20 < 0 and np.isfinite(p25_20) else 1.0
        rr_s = max(0.0, min(1.0, (rr - 0.5) / 2.5))
        score_parts.append(rr_s * 0.30)
        if rr >= 2.0:
            reasons.append(f"손익비 {rr:.1f}× (양호)")
        elif rr >= 1.2:
            reasons.append(f"손익비 {rr:.1f}× (보통)")
        else:
            reasons.append(f"손익비 {rr:.1f}× (불리)")

        # 3. RSI 과매도 보너스 (15%)
        rsi_v = float(cur["rsi"])
        rsi_s = max(0.0, min(1.0, (55 - rsi_v) / 35))
        score_parts.append(rsi_s * 0.15)
        if rsi_v < 35:
            reasons.append(f"RSI {rsi_v:.0f} (과매도)")
        elif rsi_v > 65:
            reasons.append(f"RSI {rsi_v:.0f} (과매수)")

        # 4. 낙폭 위치 (15%) — 많이 빠질수록 유리 (단, 과도한 낙폭 제외)
        dd_v = float(cur["drawdown"])
        if category == "leverage":
            # 레버리지: -5% ~ -20% 구간이 최적
            dd_s = max(0.0, min(1.0, (-dd_v - 0.03) / 0.18)) if dd_v < -0.03 else 0.0
        else:
            # 개별주: -8% ~ -30% 구간이 최적
            dd_s = max(0.0, min(1.0, (-dd_v - 0.05) / 0.25)) if dd_v < -0.05 else 0.0
        score_parts.append(dd_s * 0.15)

        score = sum(score_parts)

        # 신호 분류
        if score >= 0.62:
            signal = "enter"
        elif score >= 0.40:
            signal = "wait"
        else:
            signal = "avoid"

        return EntryScore(
            ticker           = ticker,
            category         = category,
            underlying       = und_label,
            current_drawdown = cur_dd,
            current_rsi      = rsi_v,
            current_vix      = float(cur["vix"]),
            current_mom_20d  = float(cur["mom_20d"]),
            current_mom_60d  = float(cur["mom_60d"]),
            current_price    = cur_price,
            n_similar        = len(rets_20),
            win_prob_20d     = round(win_20, 3),
            win_prob_60d     = round(win_60, 3),
            expected_ret_20d = round(exp_20, 4),
            expected_ret_60d = round(exp_60, 4),
            downside_p25_20d = round(p25_20, 4),
            upside_p75_20d   = round(p75_20, 4),
            score            = round(score, 3),
            signal           = signal,
            reasons          = reasons,
            timestamp        = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST"),
        )

    except Exception as e:
        logger.warning("analyze_entry(%s) 실패: %s", ticker, e)
        return None


# ── 전체 분석 ─────────────────────────────────────────────────────────────────

def analyze_all_entries(
    days:      int = 756,
    n_similar: int = 30,
) -> list[EntryScore]:
    """포트폴리오 전체 종목 + 레버리지 ETF 진입 분석."""
    from ml.data_pipeline import fetch_prices

    all_tickers = list(set(
        PORTFOLIO_STOCKS + LEVERAGE_ETFS + ["QQQ", "SPY", "^VIX"]
    ))
    logger.info("진입 분석 가격 로드: %d종목", len(all_tickers))
    prices = fetch_prices(all_tickers, days=days)

    vix_s = prices.get("^VIX", pd.DataFrame()).get("Close", pd.Series(dtype=float))
    vix_s = vix_s if len(vix_s) > 0 else pd.Series(20.0, index=pd.date_range("2020-01-01", periods=1))

    qqq = prices.get("QQQ", pd.DataFrame()).get("Close")
    spy = prices.get("SPY", pd.DataFrame()).get("Close")

    scores: list[EntryScore] = []

    # 레버리지 ETF
    for ticker in LEVERAGE_ETFS:
        df = prices.get(ticker)
        if df is None:
            continue
        und = LEVERAGE_UNDERLYING.get(ticker, "QQQ")
        und_price = qqq if und == "QQQ" else spy
        s = analyze_entry(ticker, df, vix_s, n_similar=n_similar,
                          category="leverage", underlying_price=und_price)
        if s:
            scores.append(s)

    # 개별주
    for ticker in PORTFOLIO_STOCKS:
        df = prices.get(ticker)
        if df is None:
            continue
        s = analyze_entry(ticker, df, vix_s, n_similar=n_similar, category="stock")
        if s:
            scores.append(s)

    logger.info("진입 분석 완료: %d종목", len(scores))
    return scores


# ── 텔레그램 포맷 ─────────────────────────────────────────────────────────────

_SIGNAL_EMOJI  = {"enter": "🟢", "wait": "🟡", "avoid": "🔴"}
_SIGNAL_LABEL  = {"enter": "진입 유리", "wait": "대기", "avoid": "진입 불리"}
_TICKER_NAME   = {
    "MSFT": "Microsoft", "NVDA": "NVIDIA",   "GOOGL": "Alphabet",
    "ORCL": "Oracle",    "SAP":  "SAP",      "UNH":   "UnitedHealth",
    "SPMO": "S&P Momentum", "QQQI": "QQQI",
    "QLD":  "QLD(2×QQQ)",  "TQQQ": "TQQQ(3×QQQ)", "UPRO": "UPRO(3×SPY)",
}


def _fmt_pct(v: float) -> str:
    return f"{v*100:+.1f}%"


def format_entry_report(scores: list[EntryScore]) -> str:
    """진입 분석 전체 텔레그램 리포트."""
    if not scores:
        return "⚠️ 진입 분석 데이터 없음"

    ts = scores[0].timestamp if scores else ""
    lines = [
        "📊 진입 타점 분석",
        "━━━━━━━━━━━━━━━━━━━━━━━",
        f"({ts})",
        "",
        "[ 레버리지 ETF ]",
    ]

    lev    = [s for s in scores if s.category == "leverage"]
    stocks = [s for s in scores if s.category == "stock"]

    def _render(s: EntryScore) -> list[str]:
        emoji  = _SIGNAL_EMOJI[s.signal]
        label  = _SIGNAL_LABEL[s.signal]
        name   = _TICKER_NAME.get(s.ticker, s.ticker)
        rr     = abs(s.expected_ret_20d / s.downside_p25_20d) if s.downside_p25_20d < 0 else 0
        rr_str = f"{rr:.1f}×" if rr > 0 else "—"
        out = [
            f"{emoji} {s.ticker} ({name})  [{label}]  점수:{s.score:.2f}",
            f"   낙폭 {_fmt_pct(s.current_drawdown)}  RSI {s.current_rsi:.0f}  "
            f"20d {_fmt_pct(s.current_mom_20d)}  VIX {s.current_vix:.1f}",
            f"   유사기간 {s.n_similar}건 | "
            f"승률 20d {s.win_prob_20d*100:.0f}% / 60d {s.win_prob_60d*100:.0f}%",
            f"   기대수익 {_fmt_pct(s.expected_ret_20d)} (20d) / {_fmt_pct(s.expected_ret_60d)} (60d)",
            f"   하방25% {_fmt_pct(s.downside_p25_20d)}  상방75% {_fmt_pct(s.upside_p75_20d)}  "
            f"손익비 {rr_str}",
        ]
        if s.reasons:
            out.append(f"   💡 {' · '.join(s.reasons[:2])}")
        return out

    for s in sorted(lev, key=lambda x: -x.score):
        lines.extend(_render(s))
        lines.append("")

    lines += ["[ 개별주 ]", ""]
    for s in sorted(stocks, key=lambda x: -x.score):
        lines.extend(_render(s))
        lines.append("")

    # 요약 추천
    enters = [s for s in scores if s.signal == "enter"]
    if enters:
        names = ", ".join(s.ticker for s in sorted(enters, key=lambda x: -x.score)[:3])
        lines += ["━━━━━━━━━━━━━━━━━━━━━━━",
                  f"⚡ 진입 검토 대상: {names}",
                  "⚠️ 분할 매수 + 손절 설정 필수"]
    else:
        lines += ["━━━━━━━━━━━━━━━━━━━━━━━",
                  "⏳ 현재 진입 유리한 종목 없음 — 추가 조정 대기"]

    return "\n".join(lines)


def format_alert_message(s: EntryScore) -> str:
    """단일 종목 알림 메시지."""
    emoji = _SIGNAL_EMOJI[s.signal]
    name  = _TICKER_NAME.get(s.ticker, s.ticker)
    rr    = abs(s.expected_ret_20d / s.downside_p25_20d) if s.downside_p25_20d < 0 else 0

    lines = [
        f"🔔 진입 기회 감지 — {s.ticker}",
        f"{emoji} {name}",
        f"",
        f"[ 현재 상태 ]",
        f"  낙폭:  {_fmt_pct(s.current_drawdown)}  RSI: {s.current_rsi:.0f}",
        f"  20d:   {_fmt_pct(s.current_mom_20d)}    VIX: {s.current_vix:.1f}",
        f"",
        f"[ 유사 과거 {s.n_similar}건 분석 ]",
        f"  승률:     20일 {s.win_prob_20d*100:.0f}% / 60일 {s.win_prob_60d*100:.0f}%",
        f"  기대수익: {_fmt_pct(s.expected_ret_20d)} (20d) / {_fmt_pct(s.expected_ret_60d)} (60d)",
        f"  하방위험: {_fmt_pct(s.downside_p25_20d)} (P25)",
        f"  손익비:   {rr:.1f}×" if rr > 0 else "  손익비:   —",
        f"",
        f"진입 점수: {s.score:.2f} / 1.00",
        f"💡 {' · '.join(s.reasons[:3])}",
        f"",
        f"⚠️ 분할 매수 / 손절 필수 — 과거 분포 기반, 미래 보장 없음",
    ]
    return "\n".join(lines)


# ── 알림 상태 관리 ────────────────────────────────────────────────────────────

def _load_alert_state() -> dict:
    if ALERT_STATE_PATH.exists():
        try:
            return json.loads(ALERT_STATE_PATH.read_text())
        except Exception:
            pass
    return {}


def _save_alert_state(state: dict) -> None:
    ALERT_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = ALERT_STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2))
    tmp.rename(ALERT_STATE_PATH)


def check_alert_signals(scores: list[EntryScore]) -> list[EntryScore]:
    """알림 발송 대상 필터링.

    조건:
      1. signal == "enter" and score >= ALERT_SCORE_MIN
      2. 이전 신호가 "enter"가 아니었거나 (새로 진입 신호)
      3. 마지막 알림 이후 ALERT_COOLDOWN_H 이상 경과
    """
    state   = _load_alert_state()
    now     = datetime.now(KST)
    to_alert: list[EntryScore] = []

    for s in scores:
        if s.signal != "enter" or s.score < ALERT_SCORE_MIN:
            continue

        prev = state.get(s.ticker, {})
        last_alert_str = prev.get("last_alert", "")
        last_signal    = prev.get("last_signal", "")

        # 쿨다운 체크
        if last_alert_str:
            try:
                last_dt = datetime.fromisoformat(last_alert_str)
                if (now - last_dt).total_seconds() < ALERT_COOLDOWN_H * 3600:
                    continue   # 아직 쿨다운 중
            except Exception:
                pass

        # 신호 변화 체크 (wait/avoid → enter 전환 or 처음)
        if last_signal == "enter":
            continue   # 이미 enter 신호였으면 스킵

        to_alert.append(s)

    # 상태 업데이트 (모든 종목의 현재 신호 기록)
    for s in scores:
        entry = state.setdefault(s.ticker, {})
        if s.ticker in [a.ticker for a in to_alert]:
            entry["last_alert"]  = now.isoformat()
        entry["last_signal"] = s.signal

    _save_alert_state(state)
    return to_alert


def reset_alert_state(ticker: str | None = None) -> None:
    """알림 상태 초기화 (특정 종목 또는 전체)."""
    state = _load_alert_state()
    if ticker:
        state.pop(ticker, None)
    else:
        state = {}
    _save_alert_state(state)
