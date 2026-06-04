#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
barbell_strategy.py — Intelligence Barbell v2.1
유빈의 상승장/조정장/하락장 통합 자산 배분 알고리즘

전략 구조:
  ┌─────────────────────────────────────────────────┐
  │  상승장  │  중립  │  조정  │  하락  │  크래시   │
  │  SGOV↑   │  DCA   │SGOV→QLD│SGOV 전환│ TQQQ 전면│
  └─────────────────────────────────────────────────┘

  - QQQ 낙폭 기준 6단계 하락 대응 (Phase 0~5)
  - QQQ 상승 강도 기준 2단계 상승 대응 (Bull 1~2)
  - 보조 지표: RSI, VIX, 200일 MA, 모멘텀 스코어
  - 포트폴리오 실시간 총액 자동계산 (yfinance 기반)
  - USD/KRW 환율 실시간 반영
  - QQQI 월간 배당 자동 추산
  - QLD/TQQQ 레버리지 포지션 추적
  - Phase 변화 감지 → 텔레그램 자동 알림 (중복 방지)
"""

import os
import json
import logging
import time
from datetime import datetime

import numpy as np
import requests

try:
    import yfinance as yf
except ImportError:
    yf = None

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

TELEGRAM_TOKEN   = os.getenv("STOCK_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("STOCK_BOT_CHAT_ID", "5771238245")
PORTFOLIO_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "portfolio_snapshot.json")
LEVERAGE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "leverage_state.json")
STATE_FILE = os.path.expanduser("~/.cache/barbell_state.json")

# ── 기본값 (실시간 로드 실패 시 fallback) ───────────────────────────────
SGOV_SHARES_DEFAULT = 10.0
QQQI_SHARES_DEFAULT = 35.2987
DCA_DAILY_BASE_KRW = 40_000
TARGET_SGOV_RATIO = 0.08
MAX_SGOV_RATIO = 0.20
QQQI_ANNUAL_YIELD = 0.12      # QQQI 연간 배당수익률 ~12% (추산)

RSI_OVERSOLD = 30
RSI_NEAR_OVERSOLD = 40
RSI_OVERBOUGHT = 70
RSI_EXTREME_OB = 75
VIX_HIGH = 30
VIX_EXTREME = 40
VIX_LOW = 15

# ── 상승장 Phase 정의 ─────────────────────────────────────────────────
BULL_PHASES = {
    "bull_2": {
        "label": "Bull-2 — 과열/버블 경고",
        "emoji": "🫧",
        "trigger": "QQQ가 52주 고점 경신 + RSI > 75 + 모멘텀 과열",
        "sgov_target_ratio": MAX_SGOV_RATIO,
        "dca_multiplier": 0.5,
        "description": "버블 징후. DCA 축소, SGOV 최대로 비축.",
        "action_items": [
            "소수점 DCA 0.5배 축소 (4만 → 2만원)",
            "QQQI 배당금 → SGOV 재투자 (실탄 비축)",
            "NOW, ORCL 목표가 도달 시 5~10% 부분 익절",
            "신규 매수 중단 — 기존 포지션만 유지",
            "SGOV 목표 비중: 포트폴리오의 20%",
        ],
    },
    "bull_1": {
        "label": "Bull-1 — 강세장 유지",
        "emoji": "🐂",
        "trigger": "QQQ 52주 고점 5% 이내 + RSI 60~75",
        "sgov_target_ratio": 0.12,
        "dca_multiplier": 0.8,
        "description": "강세 지속. DCA 소폭 축소, 실탄 점진 비축.",
        "action_items": [
            "소수점 DCA 0.8배 (4만 → 3.2만원)",
            "매월 QQQI 배당 50% → SGOV, 50% → DCA",
            "SGOV 목표 비중: 포트폴리오의 12%",
            "CPNG는 이 구간에서 손절 후 SGOV로 전환 적기",
            "오버웨이트 종목(ORCL +41%) 일부 리밸런싱 고려",
        ],
    },
}

# ── 하락/조정장 Phase 정의 ────────────────────────────────────────────
BEAR_PHASES = {
    0: {
        "label": "Phase 0 — 정상 모드",
        "range": (0, -5),
        "emoji": "🟢",
        "sgov_target_ratio": TARGET_SGOV_RATIO,
        "sgov_sell_pct": 0,
        "leverage_target": None,
        "dca_multiplier": 1.0,
        "description": "정상 DCA 유지. 변화 없음.",
        "action_items": [
            "일일 소수점 DCA 4만원 유지 (8종목 분산)",
            "QQQI 배당금 → ORCL/NOW 소수점 재투자",
            "SGOV 전량 보유 — 실탄 온존",
            "월 1회 포트폴리오 리밸런싱 점검",
        ],
    },
    1: {
        "label": "Phase 1 — 조정 초입 (-5~-10%)",
        "range": (-5, -10),
        "emoji": "🟡",
        "sgov_target_ratio": TARGET_SGOV_RATIO,
        "sgov_sell_pct": 0,
        "leverage_target": None,
        "dca_multiplier": 1.5,
        "description": "조정 시작. DCA 증액, 고확신 종목 집중.",
        "action_items": [
            "소수점 DCA 1.5배 (4만 → 6만원)",
            "ORCL, NOW 우선 배정 (비중 +5%씩)",
            "SGOV 유지 — 추가 하락 대기",
            "RSI < 40 여부, VIX 추이 일일 체크",
            "CPNG 손절 검토 (손실 고착화 방지)",
        ],
    },
    2: {
        "label": "Phase 2 — 조정장 (-10~-15%)",
        "range": (-10, -15),
        "emoji": "🟠",
        "sgov_target_ratio": 0.056,
        "sgov_sell_pct": 30,
        "leverage_target": "QLD",
        "dca_multiplier": 2.0,
        "description": "본격 조정. SGOV 30% → QLD 전환.",
        "action_items": [
            "SGOV 30% 매도 → QLD 매수",
            "소수점 DCA 2배 (4만 → 8만원)",
            "NVDA, ORCL, MSFT 비중 집중",
            "CPNG 손절 후 재원을 QLD에 추가",
            "QQQI 배당금 전액 QLD 재투자",
        ],
    },
    3: {
        "label": "Phase 3 — 베어 진입 (-15~-20%)",
        "range": (-15, -20),
        "emoji": "🔴",
        "sgov_target_ratio": 0.028,
        "sgov_sell_pct": 35,
        "leverage_target": "QLD",
        "dca_multiplier": 2.5,
        "description": "베어 진입. SGOV 누적 65% 전환.",
        "action_items": [
            "SGOV 잔여분의 50% 추가 매도 → QLD",
            "총 누적 SGOV→QLD 전환율: ~65%",
            "소수점 DCA 2.5배 (4만 → 10만원)",
            "QQQI 배당 전액 + 원금 5% → QLD",
            "국내 SOL AI반도체 익절 후 미장 투입 검토",
        ],
    },
    4: {
        "label": "Phase 4 — 베어마켓 (-20~-30%)",
        "range": (-20, -30),
        "emoji": "🚨",
        "sgov_target_ratio": 0.0,
        "sgov_sell_pct": 35,
        "leverage_target": "QLD+TQQQ (7:3)",
        "dca_multiplier": 3.0,
        "description": "베어마켓. SGOV 전량 레버리지 전환.",
        "action_items": [
            "SGOV 잔여 전량 매도",
            "QLD 70% + TQQQ 30% 비율로 분할 매수",
            "소수점 DCA 3배 (4만 → 12만원)",
            "국내 주식 전량 정리 → 미장 투입",
            "QQQI 원금 10% → TQQQ 전환 검토",
        ],
    },
    5: {
        "label": "Phase 5 — 크래시 (-30%+)",
        "range": (-30, -100),
        "emoji": "💥",
        "sgov_target_ratio": 0.0,
        "sgov_sell_pct": 0,
        "leverage_target": "TQQQ",
        "dca_multiplier": 5.0,
        "description": "시장 붕괴. 전면 공격 모드. 10년 매수 기회.",
        "action_items": [
            "TQQQ 전면 배치 — 승부수",
            "QQQI 원금 20~30% → TQQQ 전환",
            "소수점 DCA 5배 (4만 → 20만원)",
            "NOW, ORCL, NVDA, MSFT 최대 적립",
            "예비 현금(적금 포함) 단계적 투입",
        ],
    },
}

# ── DCA 종목 배분 기본값 ─────────────────────────────────────────────────
# CRM 추가 (2026-05-31: 보유 중, DCA 편입 결정)
# CPNG는 DCA 제외 (손실 포지션, 현상 유지)
_DCA_WEIGHTS_DEFAULT = {
    "NOW": 0.18, "ORCL": 0.18, "NVDA": 0.14,
    "MSFT": 0.14, "GOOGL": 0.10, "UNH": 0.10,
    "CRM": 0.10, "SAP": 0.03, "SPMO": 0.03,
}
_BEAR_DCA_WEIGHTS_DEFAULT = {
    "NOW": 0.23, "ORCL": 0.23, "NVDA": 0.20,
    "MSFT": 0.14, "GOOGL": 0.10, "CRM": 0.07, "UNH": 0.03,
}

DCA_WEIGHTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dca_weights.json")


def load_dca_weights() -> tuple[dict, dict]:
    """
    dca_weights.json 에서 DCA 비중 로드.
    파일 없으면 기본값 반환.
    Returns: (normal_weights, bear_weights)
    """
    if os.path.exists(DCA_WEIGHTS_FILE):
        try:
            with open(DCA_WEIGHTS_FILE, encoding="utf-8") as f:
                data = json.load(f)
            normal = data.get("normal", _DCA_WEIGHTS_DEFAULT)
            bear   = data.get("bear",   _BEAR_DCA_WEIGHTS_DEFAULT)
            # 합계 1.0 정규화
            n_sum = sum(normal.values())
            b_sum = sum(bear.values())
            if n_sum > 0: normal = {k: round(v / n_sum, 4) for k, v in normal.items()}
            if b_sum > 0: bear   = {k: round(v / b_sum, 4) for k, v in bear.items()}
            return normal, bear
        except Exception:
            pass
    return _DCA_WEIGHTS_DEFAULT, _BEAR_DCA_WEIGHTS_DEFAULT


def save_dca_weights(normal: dict, bear: dict):
    """DCA 비중 저장."""
    with open(DCA_WEIGHTS_FILE, "w", encoding="utf-8") as f:
        json.dump({"normal": normal, "bear": bear}, f, indent=2, ensure_ascii=False)


# 런타임에 dca_weights.json 로드
DCA_WEIGHTS, BEAR_DCA_WEIGHTS = load_dca_weights()

# ── 목표 비중 파일 경로 ──────────────────────────────────────────────
TARGET_WEIGHTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "target_weights.json")

# ETF / 실탄 / 레버리지 — 개별 종목 목표비중 분석 제외 티커
_SKIP_TICKERS = {"SGOV", "QQQI", "QLD", "TQQQ", "BIL", "SHV", "SHY",
                 "QQQ", "SPY", "VTI", "EFA", "EEM", "TLT", "IEF", "GLD",
                 "DBC", "DBMF", "UPRO", "TMF"}

_TOTAL_STOCK_BUDGET = 0.44   # 개별주 총 목표 비중 (QQQI·SGOV 제외 포트의 44%)


def load_target_weights(portfolio: dict | None = None) -> dict:
    """
    target_weights.json 로드.
    현재 보유 종목 중 설정 없는 종목은 DCA 비중 기반으로 자동 추론.
    portfolio: fetch_portfolio_value() 반환값 (holdings, prices 포함)
    """
    # 1. 파일에서 명시적 목표 로드
    explicit: dict = {}
    if os.path.exists(TARGET_WEIGHTS_FILE):
        try:
            with open(TARGET_WEIGHTS_FILE, encoding="utf-8") as f:
                raw = json.load(f)
            explicit = {k: float(v) for k, v in raw.items()
                        if not k.startswith("_") and isinstance(v, (int, float))}
        except Exception:
            pass

    if portfolio is None:
        return explicit

    # 2. 현재 보유 종목 추출
    holdings = portfolio.get("holdings", {})
    w_normal, _ = load_dca_weights()
    dca_total    = sum(w_normal.values()) or 1.0

    result = dict(explicit)

    for ticker in holdings:
        if ticker in _SKIP_TICKERS or ticker in result:
            continue

        # DCA 비중 기반 자동 산출
        if ticker in w_normal:
            dca_share = w_normal[ticker] / dca_total
            result[ticker] = round(dca_share * _TOTAL_STOCK_BUDGET, 4)
        else:
            # DCA에도 없는 신규 종목 → 소규모 추적 포지션
            result[ticker] = 0.02

    return result


def save_target_weights(updates: dict):
    """목표 비중 파일 저장 (기존 값 유지 + 업데이트)."""
    existing: dict = {}
    if os.path.exists(TARGET_WEIGHTS_FILE):
        try:
            with open(TARGET_WEIGHTS_FILE, encoding="utf-8") as f:
                raw = json.load(f)
            existing = {k: v for k, v in raw.items()}  # _comment 등 보존
        except Exception:
            pass
    existing.update(updates)
    with open(TARGET_WEIGHTS_FILE, "w", encoding="utf-8") as f:
        json.dump(existing, f, indent=2, ensure_ascii=False)


# ══════════════════════════════════════════════════════════════════════
#  헬퍼
# ══════════════════════════════════════════════════════════════════════

def _safe_float(val, default=0.0):
    try:
        v = float(val)
        return v if not (np.isnan(v) or np.isinf(v)) else default
    except Exception:
        return default


def _holding_details_from_snapshot(snap: dict) -> list[dict]:
    details = []
    for section, key in [("overseas_general", "holdings_usd"),
                          ("overseas_fractional", "holdings")]:
        for h in snap.get(section, {}).get(key, []):
            details.append({
                "ticker": h.get("ticker"),
                "name": h.get("name"),
                "shares": h.get("shares"),
                "value_usd": h.get("value_usd"),
                "return_pct": h.get("return_pct"),
            })

    for h in snap.get("domestic", {}).get("holdings", []):
        value_krw = h.get("value_krw")
        if value_krw is None and h.get("current_price") is not None:
            value_krw = _safe_float(h.get("current_price")) * _safe_float(h.get("shares"))
        details.append({
            "ticker": h.get("ticker"),
            "name": h.get("name"),
            "shares": h.get("shares"),
            "value_krw": round(value_krw) if value_krw is not None else None,
            "return_pct": h.get("return_pct"),
        })
    return details


# ══════════════════════════════════════════════════════════════════════
#  데이터 수집
# ══════════════════════════════════════════════════════════════════════

def fetch_exchange_rate() -> float:
    """USD/KRW 실시간 환율."""
    try:
        hist = yf.Ticker("USDKRW=X").history(period="3d")
        if not hist.empty:
            rate = _safe_float(hist["Close"].iloc[-1])
            if 900 < rate < 2500:
                return round(rate, 1)
    except Exception:
        pass
    logger.warning("환율 조회 실패 — 1,380원 기본값 사용")
    return 1380.0


def fetch_qqq_data() -> dict:
    """QQQ 현재가, 52주 고점, 낙폭, 모멘텀 계산."""
    try:
        hist = yf.Ticker("QQQ").history(period="1y")
        if hist.empty:
            return {}
        current = _safe_float(hist["Close"].iloc[-1])
        high_52w = _safe_float(hist["High"].max())
        low_52w = _safe_float(hist["Low"].min())
        drawdown = (current - high_52w) / high_52w * 100 if high_52w > 0 else 0

        mom_1m = mom_3m = 0.0
        if len(hist) >= 21:
            p1m = _safe_float(hist["Close"].iloc[-21])
            mom_1m = (current - p1m) / p1m * 100 if p1m > 0 else 0
        if len(hist) >= 63:
            p3m = _safe_float(hist["Close"].iloc[-63])
            mom_3m = (current - p3m) / p3m * 100 if p3m > 0 else 0

        range_52w = high_52w - low_52w
        position_52w = (current - low_52w) / range_52w * 100 if range_52w > 0 else 50

        return {
            "current": round(current, 2),
            "high_52w": round(high_52w, 2),
            "low_52w": round(low_52w, 2),
            "drawdown_pct": round(drawdown, 2),
            "position_52w_pct": round(position_52w, 1),
            "mom_1m_pct": round(mom_1m, 2),
            "mom_3m_pct": round(mom_3m, 2),
        }
    except Exception as e:
        logger.warning(f"QQQ 데이터 오류: {e}")
        return {}


def fetch_rsi(ticker_sym: str, period: int = 14) -> float:
    """RSI 계산."""
    try:
        hist = yf.Ticker(ticker_sym).history(period="3mo")
        if len(hist) < period + 1:
            return 50.0
        delta = hist["Close"].diff().dropna()
        gain = delta.clip(lower=0).rolling(period).mean()
        loss = (-delta.clip(upper=0)).rolling(period).mean()
        rs = gain / loss.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs))
        return round(_safe_float(rsi.iloc[-1], 50.0), 1)
    except Exception:
        return 50.0


def fetch_vix() -> float:
    """VIX 현재값."""
    try:
        hist = yf.Ticker("^VIX").history(period="5d")
        return round(_safe_float(hist["Close"].iloc[-1]), 2) if not hist.empty else 20.0
    except Exception:
        return 20.0


def fetch_ma200(ticker_sym: str) -> dict:
    """현재가 vs 200일 MA."""
    try:
        hist = yf.Ticker(ticker_sym).history(period="1y")
        if len(hist) < 50:
            return {"above_ma200": True, "gap_pct": 0.0}
        n = min(200, len(hist))
        ma = _safe_float(hist["Close"].rolling(n).mean().iloc[-1])
        current = _safe_float(hist["Close"].iloc[-1])
        gap = (current - ma) / ma * 100 if ma > 0 else 0
        return {"above_ma200": current > ma, "ma200": round(ma, 2), "current": round(current, 2), "gap_pct": round(gap, 2)}
    except Exception:
        return {"above_ma200": True, "gap_pct": 0.0}


def fetch_portfolio_value() -> dict:
    """
    portfolio_snapshot.json 보유 수량 × yfinance 실시간 가격 → 포트폴리오 총액.
    QLD/TQQQ leverage_state.json 포지션도 포함.
    """
    # --- 보유 수량 집계 ---
    holdings: dict[str, float] = {}
    holdings_detail: list[dict] = []
    try:
        with open(PORTFOLIO_PATH) as f:
            snap = json.load(f)
        holdings_detail = _holding_details_from_snapshot(snap)
        for h in snap.get("overseas_general", {}).get("holdings_usd", []):
            t = h["ticker"]
            holdings[t] = holdings.get(t, 0.0) + float(h.get("shares", 0))
        for h in snap.get("overseas_fractional", {}).get("holdings", []):
            t = h["ticker"]
            holdings[t] = holdings.get(t, 0.0) + float(h.get("shares", 0))
    except Exception as e:
        logger.warning(f"portfolio_snapshot.json 로드 실패: {e}")

    # 레버리지 포지션 추가
    leverage = load_leverage_state()
    for ticker, pos in leverage.items():
        sh = float(pos.get("shares", 0))
        if sh > 0:
            holdings[ticker] = holdings.get(ticker, 0.0) + sh

    if not holdings:
        return {"total_usd": 7940.0, "sgov_usd": 1006.7, "qqqi_usd": 2019.77, "qqqi_shares": 35.2987, "prices": {}, "holdings": {}, "holdings_detail": holdings_detail}

    # --- 실시간 가격 조회 ---
    tickers = list(holdings.keys())
    prices: dict[str, float] = {}

    try:
        data = yf.download(tickers, period="2d", auto_adjust=True, progress=False)
        if not data.empty and "Close" in data.columns:
            close = data["Close"]
            if hasattr(close, "columns"):           # DataFrame (복수 종목)
                for t in tickers:
                    if t in close.columns:
                        s = close[t].dropna()
                        if not s.empty:
                            prices[t] = _safe_float(s.iloc[-1])
            else:                                   # Series (단일 종목)
                s = close.dropna()
                if not s.empty:
                    prices[tickers[0]] = _safe_float(s.iloc[-1])
    except Exception as e:
        logger.warning(f"batch download 실패: {e} — 개별 조회로 대체")
        for t in tickers:
            try:
                h = yf.Ticker(t).history(period="2d")
                if not h.empty:
                    prices[t] = _safe_float(h["Close"].iloc[-1])
            except Exception:
                pass

    # 가격 없는 종목은 스냅샷 가격으로 fallback
    if os.path.exists(PORTFOLIO_PATH):
        try:
            with open(PORTFOLIO_PATH) as f:
                snap = json.load(f)
            for h in snap.get("overseas_general", {}).get("holdings_usd", []):
                t = h["ticker"]
                if t not in prices and "current_price_usd" in h:
                    prices[t] = float(h["current_price_usd"])
        except Exception:
            pass

    total_usd = sum(holdings.get(t, 0) * prices.get(t, 0) for t in tickers)
    sgov_usd = holdings.get("SGOV", SGOV_SHARES_DEFAULT) * prices.get("SGOV", 100.67)
    qqqi_shares = holdings.get("QQQI", QQQI_SHARES_DEFAULT)
    qqqi_price = prices.get("QQQI", 57.22)
    qqqi_usd = qqqi_shares * qqqi_price

    return {
        "total_usd": round(total_usd, 2),
        "sgov_usd": round(sgov_usd, 2),
        "qqqi_usd": round(qqqi_usd, 2),
        "qqqi_shares": round(qqqi_shares, 4),
        "prices": prices,
        "holdings": holdings,
        "holdings_detail": holdings_detail,
    }


def estimate_qqqi_monthly_dividend(qqqi_shares: float, qqqi_usd: float) -> dict:
    """QQQI 월간 배당금 추산 (실제 배당 히스토리 우선, 없으면 연 12% 추산)."""
    try:
        ticker = yf.Ticker("QQQI")
        divs = ticker.dividends
        if not divs.empty:
            recent = divs.iloc[-3:] if len(divs) >= 3 else divs
            avg_div = float(recent.mean())
            if avg_div > 0:
                monthly_usd = qqqi_shares * avg_div
                hist = ticker.history(period="5d")
                price = _safe_float(hist["Close"].iloc[-1]) if not hist.empty else (qqqi_usd / qqqi_shares if qqqi_shares > 0 else 57.22)
                annual_yield = avg_div * 12 / price * 100 if price > 0 else 12.0
                return {
                    "monthly_usd": round(monthly_usd, 2),
                    "annual_yield_pct": round(annual_yield, 1),
                    "per_share": round(avg_div, 4),
                    "note": "최근 3개월 평균 배당 기준",
                }
    except Exception:
        pass

    monthly_usd = qqqi_usd * QQQI_ANNUAL_YIELD / 12
    return {
        "monthly_usd": round(monthly_usd, 2),
        "annual_yield_pct": round(QQQI_ANNUAL_YIELD * 100, 1),
        "per_share": None,
        "note": "추산값 (연 12% 기준)",
    }


# ══════════════════════════════════════════════════════════════════════
#  레버리지 포지션 관리
# ══════════════════════════════════════════════════════════════════════

def load_leverage_state() -> dict:
    """QLD/TQQQ 보유 현황 로드."""
    default = {
        "QLD": {"shares": 0.0, "avg_price_usd": 0.0, "updated": ""},
        "TQQQ": {"shares": 0.0, "avg_price_usd": 0.0, "updated": ""},
    }
    if not os.path.exists(LEVERAGE_FILE):
        return default
    try:
        with open(LEVERAGE_FILE) as f:
            data = json.load(f)
        for k, v in default.items():
            if k not in data:
                data[k] = v
        return data
    except Exception:
        return default


def save_leverage_state(state: dict):
    """QLD/TQQQ 보유 현황 저장."""
    with open(LEVERAGE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def update_leverage_position(ticker: str, shares: float, avg_price: float):
    """CLI --update-leverage 에서 호출: QLD/TQQQ 포지션 업데이트."""
    state = load_leverage_state()
    state[ticker.upper()] = {
        "shares": round(shares, 4),
        "avg_price_usd": round(avg_price, 2),
        "updated": datetime.now().strftime("%Y-%m-%d"),
    }
    save_leverage_state(state)
    print(f"✅ {ticker.upper()} 포지션 업데이트: {shares}주 @ ${avg_price:.2f}")


# ══════════════════════════════════════════════════════════════════════
#  Phase 상태 캐시 (중복 알림 방지)
# ══════════════════════════════════════════════════════════════════════

def load_phase_state() -> dict:
    """이전 Phase 상태 로드."""
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_phase_state(market_type: str, phase_key, drawdown: float):
    """현재 Phase 상태 저장 (다음 실행 비교용)."""
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    state = {
        "last_run": datetime.now().isoformat(),
        "market_type": market_type,
        "phase_key": str(phase_key),
        "drawdown_pct": round(drawdown, 2),
    }
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def has_phase_changed(old_state: dict, market_type: str, phase_key) -> bool:
    """Phase 변화 감지 → True이면 텔레그램 알림 발송."""
    if not old_state:
        return False  # 첫 실행은 베이스라인만 저장, 알림 스킵
    return old_state.get("market_type") != market_type or old_state.get("phase_key") != str(phase_key)


# ══════════════════════════════════════════════════════════════════════
#  전략 로직
# ══════════════════════════════════════════════════════════════════════

def classify_market(qqq_data: dict, rsi: float, vix: float) -> tuple:
    """
    시장 상태 분류.
    Returns: (market_type, phase_key)
      market_type: "bull" | "neutral" | "bear"
      phase_key  : "bull_2" | "bull_1" | 0~5
    """
    drawdown = qqq_data.get("drawdown_pct", 0)
    mom_1m = qqq_data.get("mom_1m_pct", 0)

    if drawdown <= -30:   return "bear", 5
    elif drawdown <= -20: return "bear", 4
    elif drawdown <= -15: return "bear", 3
    elif drawdown <= -10: return "bear", 2
    elif drawdown <= -5:  return "bear", 1

    # 고점 대비 -5% 이내: 상승/중립 판별
    if rsi > RSI_EXTREME_OB and mom_1m > 8 and vix < VIX_LOW:
        return "bull", "bull_2"
    elif rsi > RSI_OVERBOUGHT or mom_1m > 5:
        return "bull", "bull_1"
    else:
        return "neutral", 0


def calculate_sgov_target(market_type: str, phase_key, portfolio_total_usd: float, sgov_current_usd: float) -> dict:
    """현재 시장 상태에 따른 SGOV 목표 비중 및 액션 계산."""
    if market_type == "bull":
        target_ratio = BULL_PHASES[phase_key]["sgov_target_ratio"]
    elif market_type == "neutral":
        target_ratio = TARGET_SGOV_RATIO
    else:
        target_ratio = BEAR_PHASES[phase_key]["sgov_target_ratio"]

    target_usd = portfolio_total_usd * target_ratio
    diff = target_usd - sgov_current_usd

    if diff > 50:
        action = f"SGOV 매수 필요: +${diff:.0f} (목표 ${target_usd:.0f})"
        direction = "buy"
    elif diff < -50:
        action = f"SGOV 매도 필요: ${abs(diff):.0f} → 레버리지/DCA 전환"
        direction = "sell"
    else:
        action = f"SGOV 적정 수준 유지 (현재 ${sgov_current_usd:.0f})"
        direction = "hold"

    return {
        "target_pct": round(target_ratio * 100, 1),
        "target_usd": round(target_usd, 2),
        "current_usd": round(sgov_current_usd, 2),
        "diff_usd": round(diff, 2),
        "action": action,
        "direction": direction,
    }


def calculate_dca(market_type: str, phase_key, exchange_rate: float = 1380.0) -> dict:
    """시장 상태별 DCA 금액 및 종목 배분 (원화 + USD 환산)."""
    # 매번 파일 로드 — 텔레그램 봇으로 변경해도 즉시 반영
    w_normal, w_bear = load_dca_weights()

    if market_type == "bull":
        mult    = BULL_PHASES[phase_key]["dca_multiplier"]
        weights = w_normal
    elif market_type == "neutral":
        mult    = BEAR_PHASES[0]["dca_multiplier"]
        weights = w_normal
    else:
        mult    = BEAR_PHASES[phase_key]["dca_multiplier"]
        weights = w_bear if phase_key >= 2 else w_normal

    total_krw = int(DCA_DAILY_BASE_KRW * mult)
    total_usd = round(total_krw / exchange_rate, 2)
    allocation = {t: int(total_krw * w) for t, w in weights.items()}
    return {
        "total_krw": total_krw,
        "total_usd": total_usd,
        "multiplier": mult,
        "by_ticker": allocation,
        "exchange_rate": exchange_rate,
    }


# ══════════════════════════════════════════════════════════════════════
#  스마트 리밸런싱 — 안전마진 + 종목별 비중 분석
# ══════════════════════════════════════════════════════════════════════

def calculate_position_analysis(portfolio: dict) -> list[dict]:
    """
    종목별 현재 비중 vs 목표 비중 비교.
    - target_weights.json 에서 목표 로드 (없으면 DCA 비중으로 자동 추론)
    - portfolio_snapshot.json 손익 데이터 + 실시간 가격 활용
    - 보유 중인 모든 종목 자동 포함 (신규 종목도 즉시 분석)
    """
    total    = portfolio.get("total_usd", 1)
    prices   = portfolio.get("prices", {})
    holdings = portfolio.get("holdings", {})

    # 동적 목표 비중 로드 (보유 종목 기반 자동 추론 포함)
    target_map = load_target_weights(portfolio)

    # 스냅샷에서 평단가·손익 보조
    pnl_map: dict[str, float] = {}
    avg_map: dict[str, float] = {}
    note_map: dict[str, str]  = {}
    try:
        with open(PORTFOLIO_PATH, encoding="utf-8") as f:
            snap = json.load(f)
        for h in snap.get("overseas_general", {}).get("holdings_usd", []):
            t = h["ticker"]
            pnl_map[t]  = float(h.get("pnl_usd", 0))
            avg_map[t]  = float(h.get("avg_price_usd", 0))
            if h.get("note"):
                note_map[t] = h["note"]
        for h in snap.get("overseas_fractional", {}).get("holdings", []):
            t = h["ticker"]
            pnl_map[t] = pnl_map.get(t, 0) + float(h.get("pnl_usd", 0))
    except Exception:
        pass

    # 분석 대상: 보유 중 + 목표 설정된 모든 종목
    all_tickers = (
        set(target_map.keys())
        | set(holdings.keys())
        | set(pnl_map.keys())
    ) - _SKIP_TICKERS

    explicit_targets = load_target_weights()  # 명시적 목표만 (신규 종목 감지용)
    results = []
    for ticker in sorted(all_tickers):
        price     = prices.get(ticker, 0)
        shares    = holdings.get(ticker, 0)
        val       = shares * price if price > 0 else 0
        target_w  = target_map.get(ticker, 0.0)
        current_w = val / total if total > 0 else 0
        diff_w    = current_w - target_w
        pnl       = pnl_map.get(ticker, 0.0)
        avg_price = avg_map.get(ticker, 0.0)
        note      = note_map.get(ticker, "")

        # 행동 제안
        if target_w == 0 and val > 0:
            action, direction = "목표 없음 — 정리 또는 목표 설정 권장", "sell"
        elif diff_w > 0.04:
            action, direction = f"익절 ${diff_w * total:.0f} 검토", "sell"
        elif diff_w > 0.02:
            action, direction = "DCA 일시 중단 or 소폭 익절", "trim"
        elif diff_w < -0.03:
            action, direction = "DCA 우선 배정", "buy"
        elif diff_w < -0.015:
            action, direction = "DCA 소폭 증가", "add"
        else:
            action, direction = "적정 — 유지", "hold"

        # 신규 종목 태그
        is_new = ticker not in explicit_targets
        tag    = " 🆕" if is_new and val > 0 else ""

        results.append({
            "ticker":      ticker,
            "val":         round(val, 2),
            "current_pct": round(current_w * 100, 1),
            "target_pct":  round(target_w * 100, 1),
            "diff_pct":    round(diff_w * 100, 1),
            "pnl":         round(pnl, 2),
            "avg_price":   round(avg_price, 2),
            "action":      action,
            "direction":   direction,
            "note":        note + tag,
        })

    return sorted(results, key=lambda x: abs(x["diff_pct"]), reverse=True)


def calculate_safety_margin(portfolio: dict, market_type: str, phase_key) -> dict:
    """
    안전마진 점수 (0~100).
    전략을 얼마나 공격적으로 실행할지 판단하는 종합 계수.

    감점: 종목 집중도, 손실 포지션 보유
    가점: 미실현 이익 쿠션, SGOV 충분, 분산도
    """
    total = portfolio.get("total_usd", 1)
    prices = portfolio.get("prices", {})
    holdings = portfolio.get("holdings", {})

    score   = 70.0   # 기본점수
    factors = {}

    # ── 1. 종목 집중도 (HHI) ─────────────────────────────────────────
    stock_ws = []
    for t, sh in holdings.items():
        if t in ("SGOV", "QQQI", "QLD", "TQQQ", "SPMO", "BIL", "SHV"):
            continue
        p = prices.get(t, 0)
        if p > 0:
            stock_ws.append(sh * p / total)

    if stock_ws:
        hhi = sum(w ** 2 for w in stock_ws)
        if hhi > 0.15:
            penalty = min((hhi - 0.15) * 100, 20)
            score  -= penalty
            factors["집중도 과다"] = f"-{penalty:.0f}점  (HHI {hhi:.2f})"
        else:
            bonus = (0.15 - hhi) * 60
            score += min(bonus, 10)
            factors["분산 양호"] = f"+{min(bonus,10):.0f}점  (HHI {hhi:.2f})"

    # ── 2. 손실 포지션 + 3. 미실현 이익 쿠션 ────────────────────────
    snap = {}
    try:
        with open(PORTFOLIO_PATH, encoding="utf-8") as f:
            snap = json.load(f)
    except Exception:
        pass

    overseas = snap.get("overseas_general", {}).get("holdings_usd", [])
    for h in overseas:
        pnl = float(h.get("pnl_usd", 0))
        if pnl < -40:
            penalty = min(abs(pnl) / total * 200, 12)
            score  -= penalty
            factors[f"손실포지션 {h['ticker']}"] = f"-{penalty:.0f}점 (손실 ${abs(pnl):.0f})"

    # ── 3. 미실현 이익 쿠션 ─────────────────────────────────────────
    total_gain = sum(float(h.get("pnl_usd", 0)) for h in overseas if float(h.get("pnl_usd", 0)) > 0)
    gain_ratio = total_gain / total if total > 0 else 0
    if gain_ratio > 0.05:
        bonus = min(gain_ratio * 80, 15)
        score += bonus
        factors["미실현이익 쿠션"] = f"+{bonus:.0f}점 (+${total_gain:.0f}, {gain_ratio*100:.1f}%)"

    # ── 4. SGOV 충분도 ────────────────────────────────────────────────
    if market_type == "bull":
        target_sgov_r = BULL_PHASES[phase_key]["sgov_target_ratio"]
    elif market_type == "neutral":
        target_sgov_r = TARGET_SGOV_RATIO
    else:
        target_sgov_r = BEAR_PHASES[phase_key].get("sgov_target_ratio", TARGET_SGOV_RATIO)

    sgov_r = portfolio.get("sgov_usd", 0) / total
    diff_sgov = sgov_r - target_sgov_r
    if diff_sgov > 0.01:
        bonus = min(diff_sgov * 120, 10)
        score += bonus
        factors["SGOV 충분"] = f"+{bonus:.0f}점 ({sgov_r*100:.1f}% / 목표 {target_sgov_r*100:.0f}%)"
    elif diff_sgov < -0.03:
        penalty = min(abs(diff_sgov) * 80, 10)
        score  -= penalty
        factors["SGOV 부족"] = f"-{penalty:.0f}점 ({sgov_r*100:.1f}% / 목표 {target_sgov_r*100:.0f}%)"

    score = max(20, min(100, score))

    if score >= 80:
        grade, emoji = "전략 100% 실행", "🟢"
    elif score >= 65:
        grade, emoji = "전략 80% 실행 권장", "🟡"
    elif score >= 50:
        grade, emoji = "전략 60% 실행 권장", "🟠"
    else:
        grade, emoji = "방어 우선 — 전략 완화", "🔴"

    return {
        "score":     round(score),
        "grade":     grade,
        "emoji":     emoji,
        "factors":   factors,
        "multiplier": round(max(0.5, score / 100), 2),
    }


def calculate_smart_rebalancing(
    portfolio: dict,
    market_type: str,
    phase_key,
    exchange_rate: float = 1380.0,
) -> dict:
    """
    안전마진 + 비중 분석 기반 스마트 리밸런싱.
    - 포지션 과/부족 진단
    - 안전마진 점수로 DCA 배율 조정
    - 비중 불균형 종목에 DCA 재배분
    """
    safety    = calculate_safety_margin(portfolio, market_type, phase_key)
    positions = calculate_position_analysis(portfolio)
    sgov      = calculate_sgov_target(market_type, phase_key, portfolio["total_usd"], portfolio["sgov_usd"])
    base_dca  = calculate_dca(market_type, phase_key, exchange_rate)

    # 안전마진으로 DCA 금액 조정
    adj_mult      = safety["multiplier"]
    adj_total_krw = int(base_dca["total_krw"] * adj_mult)

    # 비중 불균형 반영하여 종목별 DCA 가중치 재조정
    w_normal, _ = load_dca_weights()
    adj_weights  = dict(w_normal)

    for pos in positions:
        t = pos["ticker"]
        if t not in adj_weights:
            continue
        d = pos["diff_pct"]
        if d > 3:       # 초과 비중 → DCA 감소
            adj_weights[t] *= max(0.2, 1 - d / 25)
        elif d < -2:    # 부족 비중 → DCA 증가
            adj_weights[t] *= min(2.5, 1 + abs(d) / 12)

    w_sum = sum(adj_weights.values())
    if w_sum > 0:
        adj_weights = {k: round(v / w_sum, 4) for k, v in adj_weights.items()}

    adj_dca = {t: int(adj_total_krw * w) for t, w in adj_weights.items()}

    return {
        "safety":         safety,
        "positions":      positions,
        "sgov":           sgov,
        "base_dca_krw":   base_dca["total_krw"],
        "adj_dca_krw":    adj_total_krw,
        "adj_multiplier": adj_mult,
        "adj_weights":    adj_weights,
        "adj_dca":        adj_dca,
        "exchange_rate":  exchange_rate,
    }


def build_smart_report(portfolio: dict, market_type: str, phase_key,
                        exchange_rate: float = 1380.0) -> str:
    """스마트 리밸런싱 전용 텔레그램 출력."""
    result = calculate_smart_rebalancing(portfolio, market_type, phase_key, exchange_rate)
    s  = result["safety"]
    sg = result["sgov"]
    L  = [
        "⚖️ 스마트 리밸런싱 분석",
        "━━━━━━━━━━━━━━━━━━━━━━━",
    ]

    # ── 안전마진 점수 ─────────────────────────────────────────────────
    bar = _bar(s["score"] / 100, 12)
    L += [
        f"  {s['emoji']} 안전마진  {s['score']}점  {bar}",
        f"  → {s['grade']}",
        "",
    ]
    for factor, detail in s["factors"].items():
        L.append(f"    {factor}: {detail}")

    # ── 종목 비중 진단 ────────────────────────────────────────────────
    L += ["", "━━━ 종목별 비중 진단 ━━━━━━━━━━━━━━━━━━━"]
    for p in result["positions"]:
        diff  = p["diff_pct"]
        arrow = "▲" if diff > 0 else ("▼" if diff < 0 else "─")
        bar_c = _bar(p["current_pct"] / 15, 6)
        bar_t = _bar(p["target_pct"] / 15, 6)
        pnl_s = f"  P&L ${p['pnl']:+.0f}" if p["pnl"] != 0 else ""
        L.append(
            f"  {p['ticker']:<6}  현재 {p['current_pct']:>4.1f}% {bar_c}  "
            f"목표 {p['target_pct']:>4.1f}% {bar_t}  "
            f"{arrow}{abs(diff):.1f}%p{pnl_s}"
        )
        if p["direction"] != "hold":
            L.append(f"    → {p['action']}")

    # ── SGOV ────────────────────────────────────────────────────────
    L += [
        "", "━━━ SGOV 실탄 ━━━━━━━━━━━━━━━━━━━━━━",
        f"  현재 ${sg['current_usd']:>7,.0f}  목표 ${sg['target_usd']:>7,.0f}  ({sg['target_pct']}%)",
        f"  → {sg['action']}",
    ]

    # ── 조정된 DCA ───────────────────────────────────────────────────
    L += [
        "", f"━━━ 조정 DCA  {result['adj_dca_krw']:,}원  "
            f"(기본 {result['base_dca_krw']:,}원 × {result['adj_multiplier']:.0%}) ━━━",
    ]
    max_amt = max(result["adj_dca"].values()) if result["adj_dca"] else 1
    for ticker, amt in result["adj_dca"].items():
        bar  = _bar(amt / max_amt, 8)
        usd  = round(amt / exchange_rate, 1)
        orig = int(result["base_dca_krw"] * result["adj_weights"].get(ticker, 0))
        diff_amt = amt - orig
        diff_s = f"  ({diff_amt:+,}원)" if abs(diff_amt) > 100 else ""
        L.append(f"  {ticker:<6}  {bar}  {amt:,}원  ${usd:.1f}{diff_s}")

    return "\n".join(L)


# ══════════════════════════════════════════════════════════════════════
#  시각화 헬퍼
# ══════════════════════════════════════════════════════════════════════

def _bar(ratio: float, width: int = 10, fill: str = "█", empty: str = "░") -> str:
    """비율(0~1) → 채워진 막대."""
    r = max(0.0, min(1.0, ratio))
    n = round(r * width)
    return fill * n + empty * (width - n)


def _phase_meter(market_type: str, phase_key) -> str:
    """Phase 위치 표시기 (레이블 + 이모지 두 줄)."""
    LABELS = ["B2", "B1", "N0", "P1", "P2", "P3", "P4", "P5"]
    EMOJIS = ["🫧", "🐂", "🟢", "🟡", "🟠", "🔴", "🚨", "💥"]
    if market_type == "neutral":
        idx = 2
    elif market_type == "bull":
        idx = 0 if phase_key == "bull_2" else 1
    else:
        idx = int(phase_key) + 2

    label_row = "  ".join(f"[{l}]" if i == idx else f" {l} " for i, l in enumerate(LABELS))
    emoji_row = "   ".join(f"◉{e}" if i == idx else f" {e}" for i, e in enumerate(EMOJIS))
    return label_row + "\n" + emoji_row


def _drawdown_ruler(drawdown_pct: float, width: int = 22) -> str:
    """낙폭 위치를 눈금자로 표시 (-30% ~ 0%)."""
    ratio = max(0.0, min(1.0, (drawdown_pct + 30) / 30))
    pos = round(ratio * width)
    ruler = "─" * pos + "●" + "─" * (width - pos)
    return f"  ◄{ruler}►\n  -30%{'':<{width - 3}}0%"


def _rsi_visual(rsi: float) -> str:
    bar = _bar(rsi / 100, 12)
    if rsi < RSI_OVERSOLD:        label = "과매도 🔥"
    elif rsi < RSI_NEAR_OVERSOLD: label = "약세 ⚠️"
    elif rsi > RSI_EXTREME_OB:    label = "극과매수 🫧"
    elif rsi > RSI_OVERBOUGHT:    label = "과매수 🌡"
    else:                         label = "중립 ✅"
    return f"  RSI  {rsi:5.1f}  {bar}  {label}"


def _vix_visual(vix: float) -> str:
    bar = _bar(min(1.0, vix / 50), 12)
    if vix > VIX_EXTREME:  label = "극단공포 💥"
    elif vix > VIX_HIGH:   label = "공포 🚨"
    elif vix < VIX_LOW:    label = "과낙관 😴"
    else:                  label = "정상 ✅"
    return f"  VIX  {vix:5.1f}  {bar}  {label}"


def _sgov_compare(current: float, target: float) -> list:
    """SGOV 현재/목표 비교 막대 두 줄."""
    scale = max(current, target, 1) * 1.05
    bar_c = _bar(current / scale, 12)
    bar_t = _bar(target / scale, 12)
    arrow = "↑ 매수" if target > current + 50 else ("↓ 매도" if current > target + 50 else "= 유지")
    return [
        f"  현재  ${current:>7,.0f}  {bar_c}",
        f"  목표  ${target:>7,.0f}  {bar_t}  {arrow}",
    ]


def _dca_rows(by_ticker: dict, total_krw: int, exchange_rate: float) -> list:
    """DCA 종목별 배분 막대."""
    if not by_ticker:
        return []
    max_amt = max(by_ticker.values())
    rows = []
    for ticker, amt in by_ticker.items():
        bar = _bar(amt / max_amt if max_amt > 0 else 0, 8)
        pct = round(amt / total_krw * 100) if total_krw > 0 else 0
        usd = round(amt / exchange_rate, 1)
        rows.append(f"  {ticker:<5}  {bar}  {amt:,}원  ${usd:.1f}  ({pct}%)")
    return rows


# ══════════════════════════════════════════════════════════════════════
#  리포트 생성
# ══════════════════════════════════════════════════════════════════════

def build_report(
    qqq_data: dict,
    rsi: float,
    vix: float,
    ma_data: dict,
    portfolio: dict = None,
    exchange_rate: float = 1380.0,
    qqqi_div: dict = None,
    old_phase_state: dict = None,
) -> str:
    """시각화 바벨 전략 리포트 생성."""
    if portfolio is None:
        portfolio = {"total_usd": 7940.0, "sgov_usd": 1006.7, "qqqi_usd": 2019.77,
                     "qqqi_shares": 35.2987, "prices": {}, "holdings": {}}
    if qqqi_div is None:
        qqqi_div = {"monthly_usd": 20.0, "annual_yield_pct": 12.0, "per_share": None, "note": "추산값"}

    market_type, phase_key = classify_market(qqq_data, rsi, vix)
    dca    = calculate_dca(market_type, phase_key, exchange_rate)
    sgov   = calculate_sgov_target(market_type, phase_key, portfolio["total_usd"], portfolio["sgov_usd"])
    p_info = BULL_PHASES[phase_key] if market_type == "bull" else BEAR_PHASES[phase_key]

    now      = datetime.now().strftime("%Y-%m-%d %H:%M KST")
    drawdown = qqq_data.get("drawdown_pct", 0)
    total_krw = int(portfolio["total_usd"] * exchange_rate)

    L = []

    # ── 헤더 ─────────────────────────────────────────────────────────
    L += [
        "🏋️ Intelligence Barbell v2.1",
        f"📅 {now}",
    ]

    # Phase 변화 경보 (최상단)
    if old_phase_state and has_phase_changed(old_phase_state, market_type, phase_key):
        old_t  = old_phase_state.get("market_type", "?")
        old_k  = old_phase_state.get("phase_key", "?")
        old_dd = old_phase_state.get("drawdown_pct", 0)
        L += [
            "",
            "╔══════════════════════════════╗",
            "║  ⚡ PHASE 변화 감지!           ║",
            f"║  {old_t}/{old_k} ({old_dd:+.1f}%)  →  {market_type}/{phase_key} ({drawdown:+.1f}%)  ║",
            "╚══════════════════════════════╝",
        ]

    # ── Phase 미터 ────────────────────────────────────────────────────
    L += [
        "",
        f"📍 Phase  {p_info['emoji']} {p_info['label']}",
        _phase_meter(market_type, phase_key),
        f"  QQQ 고점 대비  {drawdown:+.2f}%   {p_info['description']}",
    ]

    # ── 포트폴리오 요약 ───────────────────────────────────────────────
    sgov_ratio = portfolio["sgov_usd"] / portfolio["total_usd"] if portfolio["total_usd"] > 0 else 0
    qqqi_ratio = portfolio["qqqi_usd"] / portfolio["total_usd"] if portfolio["total_usd"] > 0 else 0

    L += [
        "",
        "━━━ 💼 포트폴리오 ━━━",
        f"  총액  ${portfolio['total_usd']:>8,.2f}   (₩{total_krw:,})",
        f"  환율  {exchange_rate:,.1f}원/USD",
        f"  SGOV  ${portfolio['sgov_usd']:>7,.2f}   {_bar(sgov_ratio, 10)}  {sgov_ratio*100:.1f}%  실탄",
        f"  QQQI  ${portfolio['qqqi_usd']:>7,.2f}   {_bar(min(qqqi_ratio / 0.35, 1), 10)}  {qqqi_ratio*100:.1f}%  배당엔진",
    ]

    # 레버리지 포지션
    leverage   = load_leverage_state()
    lev_prices = portfolio.get("prices", {})
    has_lev    = False
    for ticker, pos in leverage.items():
        sh = pos.get("shares", 0)
        if sh > 0:
            has_lev = True
            avg   = pos.get("avg_price_usd", 0)
            price = lev_prices.get(ticker, avg)
            val   = sh * price
            pnl   = (price - avg) / avg * 100 if avg > 0 else 0
            sign  = "+" if pnl >= 0 else ""
            L.append(f"  {ticker}    ${val:>7,.0f}   {sh}주 @${avg:.2f}  {sign}{pnl:.1f}%")
    if not has_lev:
        L.append("  레버리지  미보유  (Phase 2+ 진입 시 QLD 매수)")

    # ── QQQ 레이더 ────────────────────────────────────────────────────
    pos_52w = qqq_data.get("position_52w_pct", 50)
    mom_1m  = qqq_data.get("mom_1m_pct", 0)
    mom_3m  = qqq_data.get("mom_3m_pct", 0)
    ma_gap  = ma_data.get("gap_pct", 0)
    ma_icon = "✅" if ma_data.get("above_ma200", True) else "❌ MA 이탈!"

    L += [
        "",
        "━━━ 📈 QQQ 레이더 ━━━",
        f"  현재가  ${qqq_data.get('current', 0):>8,.2f}   52주高 ${qqq_data.get('high_52w', 0):,.2f}  低 ${qqq_data.get('low_52w', 0):,.2f}",
        f"  낙폭    {drawdown:>+7.2f}%   52주위치 {_bar(pos_52w / 100, 12)} {pos_52w:.0f}%",
        _drawdown_ruler(drawdown),
        f"  1M {mom_1m:>+6.1f}%  3M {mom_3m:>+6.1f}%",
        _rsi_visual(rsi),
        _vix_visual(vix),
        f"  200MA   {ma_gap:>+6.1f}%  {ma_icon}",
    ]

    # ── SGOV 실탄 ─────────────────────────────────────────────────────
    L += [
        "",
        "━━━ 🛡 SGOV 실탄 ━━━",
    ] + _sgov_compare(sgov["current_usd"], sgov["target_usd"]) + [
        f"  목표 {sgov['target_pct']}%  |  차이 ${sgov['diff_usd']:+,.0f}",
        f"  → {sgov['action']}",
    ]

    # ── QQQI 배당 파이프라인 ──────────────────────────────────────────
    per_s = f"  주당 ${qqqi_div['per_share']:.4f} |" if qqqi_div.get("per_share") else ""
    if market_type == "bull":
        div_act = "배당 50% → SGOV 비축,  50% → DCA"
    elif market_type == "bear" and isinstance(phase_key, int) and phase_key >= 2:
        div_act = "배당 전액 → QLD/TQQQ 재투자"
    else:
        div_act = "배당 전액 → 소수점 DCA 재투자"

    L += [
        "",
        "━━━ 💰 QQQI 배당 ━━━",
        f"  월 ${qqqi_div['monthly_usd']:.2f}{per_s}  연 {qqqi_div['annual_yield_pct']:.1f}%  ({qqqi_div['note']})",
        f"  → {div_act}",
    ]

    # ── 행동 지침 ─────────────────────────────────────────────────────
    L += ["", "━━━ 📋 행동 지침 ━━━"]
    for i, act in enumerate(p_info["action_items"], 1):
        L.append(f"  {i}. {act}")

    # ── DCA 배분 막대 ─────────────────────────────────────────────────
    L += [
        "",
        f"━━━ 💸 DCA  {dca['total_krw']:,}원  (${dca['total_usd']:.2f} @ {exchange_rate:,.0f}원)  [{dca['multiplier']}x] ━━━",
    ] + _dca_rows(dca["by_ticker"], dca["total_krw"], exchange_rate)

    # ── 특수 경고 ─────────────────────────────────────────────────────
    alerts = []
    if market_type == "bull" and phase_key == "bull_2":
        hot = [
            f"{h['ticker']} {h['return_pct']:+.0f}%"
            for h in portfolio.get("holdings_detail", [])
            if h.get("ticker") not in _SKIP_TICKERS
            and isinstance(h.get("return_pct"), (int, float))
            and (h.get("return_pct") or 0) >= 30
        ]
        if hot:
            alerts.append(f"⚡ 과열 익절 검토: {', '.join(hot[:3])} — SGOV 비축 최우선")
    if market_type == "bear" and isinstance(phase_key, int) and phase_key >= 3:
        loss = [
            f"{h['ticker']} {h['return_pct']:+.0f}%"
            for h in portfolio.get("holdings_detail", [])
            if h.get("ticker") not in _SKIP_TICKERS
            and isinstance(h.get("return_pct"), (int, float))
            and (h.get("return_pct") or 0) <= -10
        ]
        if loss:
            alerts.append(f"⚡ 손절 검토: {', '.join(loss[:3])} — 재원 QLD/TQQQ 재배치")
    if market_type == "bull" and sgov["direction"] == "buy":
        alerts.append("💡 QQQI 배당금 → SGOV 우선 비축 (강세장 실탄 적립)")
    if alerts:
        L.append("")
        L += alerts

    return "\n".join(L)


def _simulation_payload(mode: str) -> dict:
    SIM_DATA = {
        "bull2": {"qqq": {"current": 530, "high_52w": 520, "low_52w": 400, "drawdown_pct": -0.5,
                          "position_52w_pct": 95, "mom_1m_pct": 9.0, "mom_3m_pct": 18.0},
                  "rsi": 76.0, "vix": 13.0},
        "bull1": {"qqq": {"current": 500, "high_52w": 515, "low_52w": 400, "drawdown_pct": -2.9,
                          "position_52w_pct": 87, "mom_1m_pct": 5.5, "mom_3m_pct": 12.0},
                  "rsi": 65.0, "vix": 17.0},
        "0":    {"qqq": {"current": 480, "high_52w": 485, "low_52w": 380, "drawdown_pct": -1.0,
                         "position_52w_pct": 96, "mom_1m_pct": 2.0, "mom_3m_pct": 5.0},
                 "rsi": 55.0, "vix": 20.0},
        "1":    {"qqq": {"current": 450, "high_52w": 490, "low_52w": 380, "drawdown_pct": -8.2,
                         "position_52w_pct": 64, "mom_1m_pct": -3.0, "mom_3m_pct": 2.0},
                 "rsi": 42.0, "vix": 24.0},
        "2":    {"qqq": {"current": 420, "high_52w": 490, "low_52w": 380, "drawdown_pct": -14.3,
                         "position_52w_pct": 36, "mom_1m_pct": -8.0, "mom_3m_pct": -5.0},
                 "rsi": 32.0, "vix": 32.0},
        "3":    {"qqq": {"current": 400, "high_52w": 490, "low_52w": 360, "drawdown_pct": -18.4,
                         "position_52w_pct": 31, "mom_1m_pct": -10.0, "mom_3m_pct": -12.0},
                 "rsi": 27.0, "vix": 38.0},
        "4":    {"qqq": {"current": 370, "high_52w": 490, "low_52w": 340, "drawdown_pct": -24.5,
                         "position_52w_pct": 20, "mom_1m_pct": -12.0, "mom_3m_pct": -20.0},
                 "rsi": 22.0, "vix": 45.0},
        "5":    {"qqq": {"current": 330, "high_52w": 490, "low_52w": 300, "drawdown_pct": -32.7,
                         "position_52w_pct": 16, "mom_1m_pct": -18.0, "mom_3m_pct": -28.0},
                 "rsi": 18.0, "vix": 55.0},
    }
    return SIM_DATA.get(mode, SIM_DATA["bull2"])


def build_simulation_report(mode: str = "bull2") -> str:
    d = _simulation_payload(mode)
    ma_sim = {"above_ma200": d["qqq"]["drawdown_pct"] > -15, "gap_pct": -5.0 if d["qqq"]["drawdown_pct"] < -15 else 8.0}
    sim_portfolio = {"total_usd": 7940.0, "sgov_usd": 1006.7, "qqqi_usd": 2019.77, "qqqi_shares": 35.2987, "prices": {}, "holdings": {}}
    sim_div = {"monthly_usd": 20.20, "annual_yield_pct": 12.0, "per_share": 0.5727, "note": "시뮬레이션 추산값"}
    return (
        f"\n{'=' * 50}\n"
        f"[시뮬레이션 모드: {mode}]\n"
        f"{'=' * 50}\n\n"
        + build_report(d["qqq"], d["rsi"], d["vix"], ma_sim, sim_portfolio, 1380.0, sim_div)
    )


# ══════════════════════════════════════════════════════════════════════
#  리밸런싱 계산기
# ══════════════════════════════════════════════════════════════════════

def calculate_rebalancing(
    market_type: str,
    phase_key,
    portfolio: dict,
    exchange_rate: float = 1380.0,
) -> dict:
    """
    현재 포트폴리오 vs Phase 목표 비중 비교 → 구체적 매수/매도 금액 제시.

    반환:
      sgov_action   : SGOV 매수/매도 금액 및 방향
      leverage_action: QLD/TQQQ 현황 및 권고
      dca_weights   : 오늘 DCA 배분 비중
      summary_lines : 텔레그램 출력용 텍스트 리스트
    """
    total = portfolio["total_usd"]
    sgov  = portfolio["sgov_usd"]
    qqqi  = portfolio["qqqi_usd"]

    # SGOV 목표 비중
    if market_type == "bull":
        sgov_target_r = BULL_PHASES[phase_key]["sgov_target_ratio"]
    elif market_type == "neutral":
        sgov_target_r = TARGET_SGOV_RATIO
    else:
        sgov_target_r = BEAR_PHASES[phase_key]["sgov_target_ratio"]

    sgov_target  = total * sgov_target_r
    sgov_diff    = sgov_target - sgov
    sgov_pct_now = sgov / total * 100 if total > 0 else 0

    if sgov_diff > 50:
        sgov_act = f"매수 ${sgov_diff:.0f}  ({int(sgov_diff / 100.67)}주 SGOV)"
        sgov_dir = "buy"
    elif sgov_diff < -50:
        sgov_act = f"매도 ${abs(sgov_diff):.0f}  → DCA/레버리지 전환"
        sgov_dir = "sell"
    else:
        sgov_act = "적정 수준 유지"
        sgov_dir = "hold"

    # 레버리지 현황
    leverage = load_leverage_state()
    lev_lines = []
    prices    = portfolio.get("prices", {})
    for ticker, pos in leverage.items():
        sh = pos.get("shares", 0)
        if sh > 0:
            avg   = pos.get("avg_price_usd", 0)
            price = prices.get(ticker, avg)
            val   = sh * price
            pnl   = (price - avg) / avg * 100 if avg > 0 else 0
            sign  = "+" if pnl >= 0 else ""
            # Bull/중립 복귀 시 레버리지 정리 권고
            if market_type in ("bull", "neutral") and val > 100:
                lev_lines.append(f"  {ticker}  ${val:.0f}  {sign}{pnl:.1f}%  → ⚠️ 복귀 구간, 일부 익절 고려")
            else:
                lev_lines.append(f"  {ticker}  ${val:.0f}  {sign}{pnl:.1f}%  (보유 유지)")

    # DCA 비중 — Phase 2+ 는 BEAR 가중치
    w_normal_r, w_bear_r = load_dca_weights()
    use_bear = market_type == "bear" and isinstance(phase_key, int) and phase_key >= 2
    dca_w    = w_bear_r if use_bear else w_normal_r
    dca_mult = (BULL_PHASES[phase_key]["dca_multiplier"] if market_type == "bull"
                else BEAR_PHASES[phase_key]["dca_multiplier"] if market_type == "bear"
                else BEAR_PHASES[0]["dca_multiplier"])
    daily_krw = int(DCA_DAILY_BASE_KRW * dca_mult)

    # QQQI 비중 (참고용)
    qqqi_pct = qqqi / total * 100 if total > 0 else 0

    lines = [
        "⚖️ 리밸런싱 계산기",
        "━━━━━━━━━━━━━━━━━━━━━━━",
        f"  총 포트폴리오  ${total:,.2f}",
        "",
        "━━━ SGOV 실탄 ━━━━━━━━━━━━━━━━━━━━━",
        f"  현재  ${sgov:>7,.2f}  ({sgov_pct_now:.1f}%)",
        f"  목표  ${sgov_target:>7,.2f}  ({sgov_target_r*100:.1f}%)",
        f"  차이  ${sgov_diff:>+7,.0f}",
        f"  → {sgov_act}",
        "",
        "━━━ QQQI 비중 ━━━━━━━━━━━━━━━━━━━━━",
        f"  현재  ${qqqi:>7,.2f}  ({qqqi_pct:.1f}%)",
        f"  역할: 배당 현금흐름 엔진 (매도 불필요)",
    ]

    if lev_lines:
        lines += ["", "━━━ 레버리지 포지션 ━━━━━━━━━━━━━━━━━"] + lev_lines
    else:
        lines += ["", "  레버리지  미보유  (Phase 2+ 진입 시 QLD 매수)"]

    lines += [
        "",
        f"━━━ DCA 배분  {daily_krw:,}원/일  [{dca_mult}x] ━━━━━━━━",
    ]
    max_w = max(dca_w.values(), default=1.0)
    for ticker, w in dca_w.items():
        amt = int(daily_krw * w)
        bar = _bar(w / max_w, 8)
        lines.append(f"  {ticker:<5}  {bar}  {amt:,}원  ({int(w*100)}%)")

    return {
        "sgov_diff":    round(sgov_diff, 2),
        "sgov_dir":     sgov_dir,
        "sgov_action":  sgov_act,
        "daily_dca_krw": daily_krw,
        "summary_lines": lines,
    }


# ══════════════════════════════════════════════════════════════════════
#  텔레그램
# ══════════════════════════════════════════════════════════════════════

_TG_MAX_CHARS = 4000  # Telegram 4096자 제한 — 여유 96자


def send_telegram(message: str) -> bool:
    if not TELEGRAM_TOKEN:
        logger.warning("TELEGRAM_TOKEN 없음 — 콘솔 출력만 수행")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    if len(message) > _TG_MAX_CHARS:
        message = message[:_TG_MAX_CHARS] + "\n…(이하 생략)"
    try:
        resp = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message}, timeout=10)
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"텔레그램 전송 실패: {e}")
        return False


def send_phase5_emergency(
    drawdown_pct: float, exchange_rate: float, portfolio: dict | None = None
) -> bool:
    """Phase 5 전용 긴급 에스컬레이션 — 포트폴리오 기반 구체적 금액 포함."""
    portfolio = portfolio or {}
    sgov_usd   = portfolio.get("sgov_usd", 0.0)
    qqqi_usd   = portfolio.get("qqqi_usd", 0.0)
    total_usd  = portfolio.get("total_usd", 0.0)

    sgov_krw       = int(sgov_usd * exchange_rate)
    qqqi_20pct_usd = round(qqqi_usd * 0.20, 2)
    qqqi_30pct_usd = round(qqqi_usd * 0.30, 2)
    qqqi_20pct_krw = int(qqqi_20pct_usd * exchange_rate)
    qqqi_30pct_krw = int(qqqi_30pct_usd * exchange_rate)
    dca_krw        = int(DCA_DAILY_BASE_KRW * 5.0)   # 200,000원
    dca_usd        = round(dca_krw / exchange_rate, 1)
    total_krw      = int(total_usd * exchange_rate)

    msg = (
        "💥💥💥 Phase 5 크래시 에스컬레이션 💥💥💥\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"QQQ 고점 대비 {drawdown_pct:+.1f}% — 시장 붕괴 구간 진입\n"
        f"포트폴리오 총액: ${total_usd:,.0f}  (₩{total_krw:,})\n"
        f"환율: {exchange_rate:,.0f}원/USD\n"
        "\n"
        "⚡ 지금 당장 이렇게 하세요:\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        "1. SGOV 전량 → QLD(2x) 또는 TQQQ(3x) 매수\n"
        f"   투입 가능 금액: ${sgov_usd:,.0f}  (₩{sgov_krw:,})\n"
        "2. QQQI 원금 20~30% → QLD 또는 TQQQ 전환\n"
        f"   20% 기준: ${qqqi_20pct_usd:,.0f}  (₩{qqqi_20pct_krw:,})\n"
        f"   30% 기준: ${qqqi_30pct_usd:,.0f}  (₩{qqqi_30pct_krw:,})\n"
        f"3. DCA 5배 즉시 실행: {dca_krw:,}원/일  (${dca_usd:.1f})\n"
        "4. NOW, ORCL, NVDA, MSFT 최대 적립\n"
        "5. 예비 현금(적금 포함) 단계적 투입 준비\n"
        "\n"
        "📱 /order 로 주문서 즉시 생성\n"
        "📊 /phase 로 전체 Phase 리포트 확인\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        "10년에 한 번 오는 매수 기회. 공포에 팔지 말 것."
    )
    return send_telegram(msg)


# ══════════════════════════════════════════════════════════════════════
#  메인 실행
# ══════════════════════════════════════════════════════════════════════

def run(send_alert: bool = False) -> dict | None:
    """바벨 전략 실행."""
    logger.info("Intelligence Barbell v2.1 분석 시작...")

    # 이전 Phase 상태 로드
    old_state = load_phase_state()

    # 시장 데이터 수집
    qqq = fetch_qqq_data()
    if not qqq:
        logger.error("QQQ 데이터 수집 실패")
        return None

    rsi = fetch_rsi("QQQ")
    vix = fetch_vix()
    ma = fetch_ma200("QQQ")

    # 신규: 환율 + 포트폴리오 실시간 + 배당 추산
    exchange_rate = fetch_exchange_rate()
    portfolio = fetch_portfolio_value()
    qqqi_div = estimate_qqqi_monthly_dividend(portfolio["qqqi_shares"], portfolio["qqqi_usd"])

    # Phase 분류
    market_type, phase_key = classify_market(qqq, rsi, vix)

    # Phase 변화 감지
    phase_changed = has_phase_changed(old_state, market_type, phase_key)

    # 리포트 생성 및 출력
    report = build_report(qqq, rsi, vix, ma, portfolio, exchange_rate, qqqi_div, old_state)
    print(report)

    # 텔레그램: Phase 변화 시 또는 강제 발송 시
    if send_alert or phase_changed:
        # Phase 5 크래시 진입: 긴급 알림 3회 반복 발송
        if market_type == "bear" and phase_key == 5 and phase_changed:
            for i in range(3):
                send_phase5_emergency(qqq.get("drawdown_pct", 0), exchange_rate, portfolio)
                if i < 2:
                    time.sleep(3)
            logger.warning("Phase 5 긴급 에스컬레이션 3회 발송 완료")

        sent = send_telegram(report)
        if sent:
            reason = "강제 발송" if send_alert else f"Phase 변화 ({old_state.get('phase_key', '?')} → {phase_key})"
            logger.info(f"텔레그램 알림 발송 완료 [{reason}]")
    else:
        logger.info(f"Phase 변화 없음 ({market_type}/{phase_key}) — 텔레그램 스킵")

    # 현재 Phase 상태 저장
    save_phase_state(market_type, phase_key, qqq.get("drawdown_pct", 0))

    return {
        "market_type": market_type,
        "phase": phase_key,
        "drawdown_pct": qqq.get("drawdown_pct", 0),
        "rsi": rsi,
        "vix": vix,
        "exchange_rate": exchange_rate,
        "portfolio_total_usd": portfolio["total_usd"],
        "sgov_usd": portfolio["sgov_usd"],
        "qqqi_monthly_div": qqqi_div["monthly_usd"],
        "above_ma200": ma.get("above_ma200", True),
        "phase_changed": phase_changed,
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Intelligence Barbell v2.1")
    parser.add_argument("--send", action="store_true", help="텔레그램 알림 강제 발송")
    parser.add_argument("--sim", choices=["bull2", "bull1", "0", "1", "2", "3", "4", "5"],
                        help="시장 상태 시뮬레이션 (오프라인)")
    parser.add_argument("--update-leverage", nargs=3, metavar=("TICKER", "SHARES", "AVG_PRICE"),
                        help="레버리지 포지션 업데이트. 예: --update-leverage QLD 5 75.50")
    args = parser.parse_args()

    if args.update_leverage:
        ticker, shares, avg_price = args.update_leverage
        update_leverage_position(ticker, float(shares), float(avg_price))

    elif args.sim:
        print(build_simulation_report(args.sim))

    else:
        run(send_alert=args.send)
