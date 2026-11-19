#!/usr/bin/env python3
"""us_mock_track.py — 미국주식 자동 페이퍼트레이딩 루프 (KIS 해외 모의투자). kiwoom_mock_track 해외판.

흐름:
  1) US 유니버스 선택신호 (us_policy + ranker best-effort → policy_score)
  2) 모의계좌 잔고 (kis_mock.get_balance — 모의 도메인 하드락)
  3) 목표 바스켓(상위 N 균등) vs 보유 → 정수주 리밸런스(plan_rebalance)
  4) 모의 지정가 주문 (kis_mock.place_order)
  5) 결정+판단근거 불변원장(Ledger "us_mock") + NAV 기록 + 텔레그램

안전:
  - KOREA_MOCK_ENABLED=true 아니면 아무것도 안 함(dry-run 제외). 주문은 전부 모의계좌(kis_mock).
  - 실거래 경로 없음. 잔고 실패 시 매수 보류. flock 중복집행 방지(크론).
  - 정수주만(해외) — 분수 floor, 비중 드리프트 원장 기록.

★정직: 6티어가 US 선택 무엣지 입증 → 이 루프는 *정직 측정 + OOS 안전개선*용. 알파 보장 아님.

크론 (평일 15:00 UTC = 미 개장 후·연중안전: 여름 11:00 ET·겨울 10:00 ET → 당일 체결):
    0 15 * * 1-5 cd /home/ubuntu/projects/stock-report && uv run python crons/us_mock_track.py

env: US_MOCK_UNIVERSE(쉼표 티커, 기본 내장)·US_MOCK_MAX_POS(5)·US_MOCK_INVEST(0.9)·KOREA_MOCK_SEED(100000 USD)
     US_MOCK_INCLUDE_LEVERAGE(true)·US_MOCK_LEVERAGE_UNIVERSE(QLD,TQQQ,SQQQ,SOXL,SSO,SOXS)
     US_MOCK_INCLUDE_SINGLE_LEVERAGE(true)·US_MOCK_SINGLE_LEVERAGE_UNIVERSE(NVDL,TSLL,AAPU,...)
     US_MOCK_LEVERAGE_MAX_POS(2)·US_MOCK_LEVERAGE_MAX_WEIGHT(0.35)
     US_MOCK_LEV_SLEEVE_MODE(shadow|paper|off, 기본 shadow) — paper 에서만 구조레버 mock 주문 반영
"""
from __future__ import annotations

import logging
import math
import os
import sys
import time
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

import pandas as pd
import kis_mock
from lib import mock_generations
from lib import mock_llm_execution as llm_exec
from lib import trade_events
from ml.mock_momentum_overlay import (build_momentum_features,
                                       inactive_momentum_overlay,
                                       score_momentum_overlay)
from providers.market_data import load_ohlc_close_series

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)
KST = timezone(timedelta(hours=9))

# 기본 US 선택 유니버스 (시장 유니버스 — 보유종목 아님). ticker-ok
_DEFAULT_UNIVERSE = ["MSFT", "NVDA", "GOOGL", "AAPL", "AMZN", "META", "AVGO", "ORCL",   # ticker-ok
                     "AMD", "ADBE", "CRM", "NFLX", "QCOM", "TXN", "INTC", "CSCO",   # ticker-ok
                     "PEP", "COST", "AMAT", "MU"]   # ticker-ok (시장 유니버스 — 보유종목 아님)

LEVERAGE_ETF_META = {
    "QLD":  {"leverage": 2.0, "underlying": "QQQ",  "inverse": False, "label": "2x 나스닥100"},
    "TQQQ": {"leverage": 3.0, "underlying": "QQQ",  "inverse": False, "label": "3x 나스닥100"},
    "SQQQ": {"leverage": 3.0, "underlying": "QQQ",  "inverse": True,  "label": "3x 인버스 나스닥100"},
    "SOXL": {"leverage": 3.0, "underlying": "SOXX", "inverse": False, "label": "3x 반도체"},
    "SSO":  {"leverage": 2.0, "underlying": "SPY",  "inverse": False, "label": "2x S&P500"},
    "SOXS": {"leverage": 3.0, "underlying": "SOXX", "inverse": True,  "label": "3x 인버스 반도체"},
    # Single-stock leveraged ETFs. Availability/listing venues can change; override via env if needed.
    "NVDL": {"leverage": 2.0, "underlying": "NVDA",  "inverse": False, "label": "2x 엔비디아"},
    "NVD":  {"leverage": 2.0, "underlying": "NVDA",  "inverse": True,  "label": "2x 인버스 엔비디아"},
    "TSLL": {"leverage": 2.0, "underlying": "TSLA",  "inverse": False, "label": "2x 테슬라"},
    "AAPU": {"leverage": 2.0, "underlying": "AAPL",  "inverse": False, "label": "2x 애플"},
    "AMZU": {"leverage": 2.0, "underlying": "AMZN",  "inverse": False, "label": "2x 아마존"},
    "GGLL": {"leverage": 2.0, "underlying": "GOOGL", "inverse": False, "label": "2x 알파벳"},
    "MSFU": {"leverage": 2.0, "underlying": "MSFT",  "inverse": False, "label": "2x 마이크로소프트"},
    "METU": {"leverage": 2.0, "underlying": "META",  "inverse": False, "label": "2x 메타"},
    "CONL": {"leverage": 2.0, "underlying": "COIN",  "inverse": False, "label": "2x 코인베이스"},
    "PLTU": {"leverage": 2.0, "underlying": "PLTR",  "inverse": False, "label": "2x 팔란티어"},
    "MSTU": {"leverage": 2.0, "underlying": "MSTR",  "inverse": False, "label": "2x 마이크로스트래티지"},
}
_DEFAULT_LEVERAGE_UNIVERSE = ["QLD", "TQQQ", "SQQQ", "SOXL", "SSO", "SOXS"]
_DEFAULT_SINGLE_LEVERAGE_UNIVERSE = [
    "NVDL", "NVD", "TSLL", "AAPU", "AMZU", "GGLL", "MSFU", "METU", "CONL", "PLTU", "MSTU",
]


def _int_env(n, d):
    try:
        return int(os.getenv(n, str(d)))
    except ValueError:
        return d


def _float_env(n, d):
    try:
        return float(os.getenv(n, str(d)))
    except ValueError:
        return d


def _bool_env(n: str, d: bool = False) -> bool:
    raw = os.getenv(n)
    if raw is None:
        return d
    return str(raw).lower() in ("1", "true", "yes", "on")


def _csv_tickers(raw: str) -> list[str]:
    return [t.strip().upper() for t in raw.split(",") if t.strip()]


def _dedupe(seq: list[str]) -> list[str]:
    return list(dict.fromkeys(seq))


def leverage_universe() -> list[str]:
    raw = os.getenv("US_MOCK_LEVERAGE_UNIVERSE", "")
    out = _csv_tickers(raw) or list(_DEFAULT_LEVERAGE_UNIVERSE)
    if _bool_env("US_MOCK_INCLUDE_SINGLE_LEVERAGE", True):
        single_raw = os.getenv("US_MOCK_SINGLE_LEVERAGE_UNIVERSE", "")
        out += _csv_tickers(single_raw) or list(_DEFAULT_SINGLE_LEVERAGE_UNIVERSE)
    return _dedupe(out)


def is_leverage_etf(ticker: str) -> bool:
    return str(ticker or "").upper() in LEVERAGE_ETF_META


def _active_leverage_meta(ticker: str, active_symbols: set[str] | None = None) -> dict | None:
    tk = str(ticker or "").upper()
    meta = LEVERAGE_ETF_META.get(tk)
    if meta:
        return meta
    if active_symbols and tk in active_symbols:
        return {"leverage": None, "underlying": tk, "inverse": False, "label": "레버리지 ETF"}
    return None


def _universe() -> list[str]:
    raw = os.getenv("US_MOCK_UNIVERSE", "")
    base = _csv_tickers(raw) or list(_DEFAULT_UNIVERSE)
    if _bool_env("US_MOCK_INCLUDE_LEVERAGE", True):
        base += leverage_universe()
    return _dedupe(base)


MAX_POS = _int_env("US_MOCK_MAX_POS", 5)
INVEST = _float_env("US_MOCK_INVEST", 0.9)
SEED_USD = _float_env("KOREA_MOCK_SEED", 100_000)
SLIPPAGE = _float_env("US_MOCK_SLIPPAGE", 0.01)
# USD 주문가능금액의 일부만 매수에 사용 — 수수료(KIS 해외 ~0.25%)·지정가 여유(LIMIT_BUFFER).
# 1% 슬리피지만으론 "주문가능금액 부족" 거부가 남아 별도 버퍼.
CASH_BUFFER = _float_env("US_MOCK_CASH_BUFFER", 0.95)
# 실시간 우호가가 없을 때 지정가 = 현재가 × (1 ± 이 값). 현재가 그대로면 체결이 안 되고 당일
# 만료되는 매수가 남는다(지정가만 지원). 체결은 시장가 근처 — 이 값은 상한일 뿐 비용이 아니다.
LIMIT_BUFFER = _float_env("US_MOCK_LIMIT_BUFFER", 0.005)
# 이 밑으로 남은 현금이면 매수 자체를 시도 안 함(어차피 실패할 소액 주문 API 낭비 방지)
MIN_CASH_USD = _float_env("US_MOCK_MIN_CASH_USD", 500.0)
REBAL_BAND = _float_env("US_MOCK_REBAL_BAND", 0.25)   # 무거래 밴드(목표比 ±25% 벗어날 때만 조정·회전율↓)
EXIT_BUFFER = _int_env("US_MOCK_EXIT_BUFFER", 2)      # 히스테리시스(top-N+2 안이면 보유 유지·경계 flip 방지)
# ★분할매수·분할매도 — 회당 목표 1/N (N회 평균 진입/청산·분산 축소·bps 비용 불변). 기본 3·1=일괄.
TRANCHES = _int_env("US_MOCK_TRANCHES", 3)
QUOTE_STALE_S = _int_env("REALTIME_QUOTE_STALE_S", 10)
LEV_ETF_MAX_POS = _int_env("US_MOCK_LEVERAGE_MAX_POS", 2)
LEV_ETF_MAX_WEIGHT = _float_env("US_MOCK_LEVERAGE_MAX_WEIGHT", 0.35)
# 잔고 조회가 일시 장애(KIS '모의투자 서비스가 지연되고 있습니다'·NAV 미확인·응답 없음)면 이 간격으로
# 다시 조회한다. 하루 1회 실행이라 한 번 실패하면 그날 리밸런스가 통째로 빠졌다(2026-11-19 00:00 KST
# 실패 — 당시 NAV 를 주던 체결기준현재잔고만 ≥1시간 '지연'. 지금은 잔고+매수가능금액으로 NAV 를 만들어
# 그 TR 을 쓰지 않지만 두 TR 도 같은 장애를 낼 수 있다). 기본 10분×6회 = 최대 1시간(겨울 10~11시 ET, 장중).
BALANCE_RETRIES = _int_env("US_MOCK_BALANCE_RETRIES", 6)
BALANCE_RETRY_S = _float_env("US_MOCK_BALANCE_RETRY_S", 600.0)
_TRANSIENT_BROKER_SIGNS = ("지연", "잠시후", "잠시 후")


def _transient_balance_problem(problem: str | None) -> bool:
    """다시 조회하면 풀릴 수 있는 잔고 이상. 계좌 미신청 같은 브로커 거부는 재시도하지 않는다."""
    if not problem:
        return False
    if problem in ("nav_missing", "balance_failed"):
        return True
    return problem.startswith("broker_rejected") and any(s in problem for s in _TRANSIENT_BROKER_SIGNS)


def _fetch_balance(retries: int | None = None, wait_s: float | None = None) -> tuple[dict, str | None]:
    """kis_mock.get_balance + 일시 장애 재시도. 반환 (잔고, balance_problem 사유 또는 None)."""
    retries = BALANCE_RETRIES if retries is None else max(0, int(retries))
    wait_s = BALANCE_RETRY_S if wait_s is None else float(wait_s)
    for attempt in range(retries + 1):
        bal = kis_mock.get_balance()
        problem = mock_generations.balance_problem(bal)
        if not _transient_balance_problem(problem) or attempt >= retries:
            return bal, problem
        logger.warning("KIS 모의 잔고 일시 이상(%s) — %.0f초 뒤 재조회 (%d/%d)",
                       problem, wait_s, attempt + 1, retries)
        time.sleep(wait_s)
    return bal, problem


# ★Tier3 구조적 레버리지 슬리브 (모의 한정 라이브 검증) — 게이트 GO shadow 가 신선할 때만
# NAV 의 (reco_lev − 1) 비율을 2x ETF 로 보유 → 유효 레버리지 ≈ reco_lev.
# 기본은 shadow: 추천·진단만 남기고 주문/예산에는 반영하지 않는다. paper 모드에서만 mock 주문.
LEV_SLEEVE_SYMBOL = os.getenv("US_MOCK_LEV_SYMBOL", "QLD")   # 2x NASDAQ100 (kis 해외 모의 주문가능)
LEV_SHADOW_PATH = os.path.expanduser("~/reports/ml-cache/structural_leverage_shadow.json")
LEV_SHADOW_MAX_AGE_D = 21          # 주간 게이트 2회 이상 누락 시 stale → 슬리브 청산 방향
LEV_SLEEVE_MAX_FRAC = 0.5          # (reco−1) 상한 — reco 클램프(1.5)와 정합


def leverage_sleeve_mode() -> str:
    """Runtime structural leverage sleeve mode.

    Legacy US_MOCK_LEV_SLEEVE=true maps to paper so existing deployments keep
    their explicit opt-in behavior.
    """
    raw = os.getenv("US_MOCK_LEV_SLEEVE_MODE")
    if raw is None and _bool_env("US_MOCK_LEV_SLEEVE", False):
        return "paper"
    mode = str(raw or "shadow").strip().lower()
    return mode if mode in {"off", "shadow", "paper"} else "shadow"


def leverage_sleeve_paper_enabled() -> bool:
    return leverage_sleeve_mode() == "paper"


LEV_SLEEVE_ENABLED = leverage_sleeve_paper_enabled()
MOMENTUM_OVERLAY_ENABLED = _bool_env("US_MOCK_MOMENTUM_OVERLAY_ENABLED", False)
MOMENTUM_BENCHMARK = "QQQ"
MOMENTUM_FRESH_DAYS = _int_env("US_MOCK_MOMENTUM_FRESH_DAYS", 5)


def load_lev_shadow(path: str | None = None) -> float | None:
    """Tier3 shadow → 신선(GO·<21일)하면 reco_lev, 아니면 None. read-only·graceful.

    leverage_structural_eval 이 ADAPTIVE_LEVERAGE_ENABLED 시 기록하는 표시용 shadow 를
    모의 슬리브 집행 게이트로 재사용 — 게이트 NO-GO/stale 이면 슬리브 목표 0(청산 방향).
    """
    try:
        import json
        with open(path or LEV_SHADOW_PATH, encoding="utf-8") as f:
            d = json.load(f)
        if d.get("verdict") != "GO":
            return None
        at = str((d.get("_meta") or {}).get("at", ""))[:10]
        if (datetime.now() - datetime.strptime(at, "%Y-%m-%d")).days > LEV_SHADOW_MAX_AGE_D:
            return None
        reco = float(d.get("reco_lev") or 0)
        return reco if reco > 1.0 else None
    except Exception:
        return None


def sleeve_plan(reco_lev: float | None, nav: float, positions: dict, price: float | None,
                *, band: float = 0.25, max_frac: float = LEV_SLEEVE_MAX_FRAC,
                symbol: str = "QLD", buy_cash: float | None = None) -> tuple[float, list[dict]]:
    """유효레버리지 목표 → 2x ETF 슬리브 (비중, 정수주 주문계획). 순수.

    reco=None(게이트 미통과/stale) → 목표 0 = 보유분 전량 청산(안전 방향).
    가격 불명이면 주문 보류(비중만 반환 — 예산 축소는 유지해 과투자 방지).
    """
    cur = int((positions.get(symbol) or {}).get("shares", 0) or 0)
    frac = round(min(max(0.0, (reco_lev or 1.0) - 1.0), max_frac), 4)
    if frac <= 0.0:
        if cur > 0:
            return 0.0, [{"symbol": symbol, "side": "sell", "qty": cur,
                          "reason": "슬리브 청산(게이트 미통과/stale)", "sleeve": True}]
        return 0.0, []
    if not price or price <= 0:
        return frac, []                                    # 가격 불명 — 이번 회차 보류
    tgt_val = nav * frac
    if cur > 0 and abs(cur * price - tgt_val) <= band * tgt_val:
        return frac, []                                    # 무거래 밴드
    tgt = int(tgt_val // price)
    delta = tgt - cur
    if delta > 0 and buy_cash is not None:
        delta = min(delta, int(max(0.0, buy_cash) // price))
    if delta > 0:
        return frac, [{"symbol": symbol, "side": "buy", "qty": delta,
                       "reason": f"Tier3 구조레버 슬리브 ×{reco_lev:.2f}", "sleeve": True}]
    if delta < 0:
        return frac, [{"symbol": symbol, "side": "sell", "qty": -delta,
                       "reason": "슬리브 리밸런스", "sleeve": True}]
    return frac, []


def stock_book_positions(positions: dict, sleeve_mode: str, sleeve_symbol: str) -> dict:
    """주식 북 보유. paper 에서만 슬리브 종목을 뺀다.

    off/shadow 는 그 종목의 소유자가 랭킹 북이다. 빼면 보유가 신규로 세어져 한도가 과소계상된다.
    """
    if sleeve_mode == "paper":
        return {k: v for k, v in positions.items() if k != sleeve_symbol}
    return dict(positions)


def _rt_best(sym: str, side: str):
    """실시간 우호가(매수=ask·매도=bid) — 활성·신선시. 없으면 None(정적 슬리피지/신호가 폴백)."""
    try:
        from providers import realtime_quotes
        if realtime_quotes.enabled():
            return realtime_quotes.best(sym, side, max_age_s=QUOTE_STALE_S)
    except Exception:
        pass
    return None


def _limit_price(sym: str, side: str, fallback: float | None = None) -> float:
    """주문 지정가. 실시간 우호가(ask/bid)면 그대로, 아니면 KIS 현재가(없으면 신호가)에
    LIMIT_BUFFER 를 얹는다(매수 위·매도 아래, 센트 단위로 체결 쪽 올림/내림). 가격 없으면 0."""
    best = _rt_best(sym, side)
    if best:
        return float(best)
    last = kis_mock.get_price(sym) or fallback or 0
    if not last:
        return 0.0
    if side == "buy":
        return math.ceil(float(last) * (1.0 + LIMIT_BUFFER) * 100) / 100
    return max(0.01, math.floor(float(last) * (1.0 - LIMIT_BUFFER) * 100) / 100)


def _overlay_regime() -> tuple[str | None, float]:
    """모멘텀 오버레이용 레짐 라벨. risk_on/neutral 외에는 비활성."""
    try:
        from ml.adaptive.regime import current_regime
        regime, confidence = current_regime()
        regime = str(regime or "").lower()
        confidence = float(confidence or 0.0)
        if confidence < 0.5:
            return None, confidence
        if regime in {"risk_on", "neutral"}:
            return regime, confidence
        return None, confidence
    except Exception:
        return None, 0.0


def _overlay_fresh(close, benchmark_close, *, max_age_days: int = MOMENTUM_FRESH_DAYS) -> bool:
    """일봉 히스토리 최신성 가드. 주말은 허용하고 장기 지연만 차단."""
    try:
        c = getattr(close, "index", None)
        b = getattr(benchmark_close, "index", None)
        last_c = pd.Timestamp(c[-1]).normalize() if c is not None and len(c) else None
        last_b = pd.Timestamp(b[-1]).normalize() if b is not None and len(b) else None
        if last_c is None or last_b is None:
            return False
        today = pd.Timestamp(datetime.now(KST)).normalize()
        return max((today - last_c).days, (today - last_b).days) <= max_age_days
    except Exception:
        return False


# ── 선택 신호 (런타임·네트워크) ────────────────────────────────────────────────

def compute_us_signals(universe: list[str] | None = None) -> list[dict]:
    """유니버스별 us_policy 점수 + 현재가 + 판단근거. ranker 는 best-effort(있으면 횡단정규화 주입)."""
    from ml import us_policy
    params = us_policy.load_params()
    universe = universe or _universe()
    active_leverage_symbols = set(leverage_universe())
    overlay_enabled = _bool_env("US_MOCK_MOMENTUM_OVERLAY_ENABLED", False)
    overlay_regime, overlay_conf = _overlay_regime()
    benchmark_close = None
    overlay_reason_codes: list[str] = []
    overlay_ready = overlay_enabled and overlay_regime is not None
    if overlay_ready:
        try:
            benchmark_close = load_ohlc_close_series(MOMENTUM_BENCHMARK, periods=("2y", "1y"))
        except Exception:
            benchmark_close = None
    if not overlay_enabled:
        overlay_reason_codes = ["flag:off"]
    elif overlay_regime is None:
        overlay_reason_codes = ["gate:regime:missing"]
    elif benchmark_close is None:
        overlay_reason_codes = ["gate:benchmark"]
    elif len(benchmark_close.dropna()) < 253:
        overlay_reason_codes = ["gate:history"]
        overlay_ready = False
    out = []
    for tk in universe:
        try:
            lev_meta = _active_leverage_meta(tk, active_leverage_symbols)
            earnings = {} if lev_meta else _safe_earnings(tk)
            sig = _safe_signals(tk)
            fund = {} if lev_meta else _safe_fund(tk)
            feats = us_policy.extract_features(fund, earnings, sig)
            # ★가격 축(mom12·hi52·lowvol) + PEAD 축 — 기본 가중 0(수집 전용) → 원장 축적 후
            # 학습 게이트(us_mock_learn) 채택. 실패 시 미기록 → score() 재정규화 (graceful)
            # mom12 는 253행이 필요해 2년치를 쓴다(1y 는 ~250행이라 mom12 가 빠졌다 — 2026-11-19).
            try:
                from providers.market_data import _history_cached
                h = _history_cached(tk, period="2y")
                closes = h["Close"] if (h is not None and "Close" in getattr(h, "columns", [])) else None
                if closes is not None:
                    feats.update(us_policy.price_axes(closes))
                from providers import earnings_data
                pa = us_policy.pead_axis(earnings_data.earnings_history(tk, limit=4), closes)
                if pa is not None:
                    feats["pead"] = pa
            except Exception:
                pass
            # ★LLM 뉴스 구조화 축 — news_llm_snapshot 라벨 집계 (없으면 미기록 → 재정규화)
            try:
                from providers import news_labels
                na = news_labels.news_axis(tk)
                if na is not None:
                    feats["news"] = na
            except Exception:
                pass
            price = float((sig.get("price_info") or {}).get("current_price") or 0) or (kis_mock.get_price(tk) or 0)
            reason = f"value {feats['value']:.2f}·quality {feats['quality']:.2f}·mom {feats['mom']:.2f}"
            if lev_meta:
                axes = []
                if feats.get("mom12") is not None:
                    axes.append(f"mom12 {feats['mom12']:.2f}")
                if feats.get("hi52") is not None:
                    axes.append(f"hi52 {feats['hi52']:.2f}")
                axes_s = "·" + "·".join(axes) if axes else ""
                inv = "인버스 " if lev_meta.get("inverse") else ""
                reason = (f"{lev_meta['label']}({inv}{lev_meta['underlying']}) · "
                          f"mom {feats['mom']:.2f}{axes_s}")
            out.append({
                "ticker": tk, "price": price, "features": feats,
                "rationale": {
                    "one_line_reason": reason,
                    "per": earnings.get("per"), "pbr": earnings.get("pbr"), "roe": earnings.get("roe"),
                },
                "asset_class": "leveraged_etf" if lev_meta else "stock",
                "leverage": (lev_meta or {}).get("leverage"),
                "inverse": bool((lev_meta or {}).get("inverse")),
            })
            overlay_features = None
            overlay_fresh = False
            overlay_reasons = list(overlay_reason_codes)
            if overlay_ready:
                close = None
                try:
                    from providers.market_data import _history_cached
                    h = _history_cached(tk, period="2y")
                    close = h["Close"] if (h is not None and "Close" in getattr(h, "columns", [])) else None
                except Exception:
                    close = None
                if close is not None:
                    overlay_fresh = _overlay_fresh(close, benchmark_close)
                    overlay_features = build_momentum_features(close, benchmark_close)
                else:
                    overlay_reasons = ["gate:history"]
            s = out[-1]
            s["overlay_features"] = overlay_features
            s["overlay_fresh"] = overlay_fresh
            s["overlay_reason_codes"] = overlay_reasons
        except Exception as e:
            logger.warning("US 신호 실패 %s: %s", tk, e)

    # US ranker(가치모델) best-effort → 횡단면 정규화 주입.
    # ⚠️ 예전엔 `hasattr(ranker,"scores_by_ticker") else {}` 로 감싸져 있어, 그 함수가
    # 없던 기간 내내 **최대 가중치 축(w_ranker=0.40)이 예외·로그 없이 조용히 미주입**
    # 상태였다(감사 2026-08-21: 섀도 37종목 전부 ranker 부재 → 상위 4개 동점).
    # 이제는 없으면 즉시 시끄럽게 경고한다 — 조용한 실패 금지.
    try:
        from ml import ranker
        if not hasattr(ranker, "scores_by_ticker"):
            raise AttributeError("ml.ranker.scores_by_ticker 부재 — 랭커 축 미주입")
        raw = ranker.scores_by_ticker([s["ticker"] for s in out])
        vals = [raw[s["ticker"]] for s in out if s["ticker"] in raw]
        if vals:
            lo, hi = min(vals), max(vals)
            rng = (hi - lo) or 1.0
            for s in out:
                if s["ticker"] in raw:
                    s["features"]["ranker"] = round((raw[s["ticker"]] - lo) / rng, 4)
        cov = sum(1 for s in out if s["features"].get("ranker") is not None)
        if cov == 0:
            logger.warning("US ranker 커버리지 0/%d — 최대 가중치 축(0.40) 미반영 상태", len(out))
        else:
            logger.info("US ranker 주입: %d/%d 종목", cov, len(out))
    except Exception as e:
        logger.warning("US ranker 주입 실패(폴백: 규칙 가중만 — 가중치 0.40 손실): %s", e)

    for s in out:
        base_score = round(us_policy.score(s["features"], params), 6)
        s["base_score"] = base_score
        overlay_features = s.pop("overlay_features", None)
        fresh = bool(s.pop("overlay_fresh", False))
        overlay_reasons = s.pop("overlay_reason_codes", overlay_reason_codes)
        if overlay_features is not None:
            overlay = score_momentum_overlay(
                base_score, overlay_features, market="us",
                regime=overlay_regime, freshness_ok=fresh,
            )
            overlay["overlay_features"] = overlay_features
        else:
            overlay = inactive_momentum_overlay(
                base_score, market="us", regime=overlay_regime,
                reason_codes=overlay_reasons,
            )
        s["selection_score"] = overlay["selection_score"]
        s["momentum_score"] = overlay["momentum_score"]
        s["momentum_tilt"] = overlay["momentum_tilt"]
        s["momentum_multiplier"] = overlay["momentum_multiplier"]
        s["momentum_state"] = overlay["momentum_state"]
        s["overlay_active"] = overlay["overlay_active"]
        s["regime"] = overlay.get("regime")
        s["market"] = overlay.get("market")
        s["reason_codes"] = overlay.get("reason_codes")
        s["momentum_reason_codes"] = overlay.get("momentum_reason_codes")
        s["overlay_weight"] = overlay.get("overlay_weight")
        if overlay.get("overlay_features") is not None:
            s["overlay_features"] = overlay["overlay_features"]
        s["policy_score"] = base_score
    return out


def _safe_earnings(tk: str) -> dict:
    try:
        from providers import earnings_data
        return earnings_data.valuation_metrics(tk) or {}
    except Exception:
        return {}


def _safe_signals(tk: str) -> dict:
    try:
        from reports.daily_signals import detect_signals
        return detect_signals(tk) or {}
    except Exception:
        return {}


def _safe_fund(tk: str) -> dict:
    try:
        from reports.fundamental_score import score_ticker
        return score_ticker(tk) or {}
    except Exception:
        return {}


# ── 리밸런스 (순수 함수 — 테스트 핵심) ────────────────────────────────────────

def _select_targets(ranked: list[dict], max_positions: int,
                    leverage_symbols: set[str] | None = None,
                    leverage_max_positions: int | None = None) -> list[dict]:
    """상위 랭크에서 목표 바스켓 선택. 레버리지 ETF 수 캡은 모의 계좌 쏠림 방지용."""
    if not leverage_symbols or leverage_max_positions is None:
        return ranked[:max_positions]
    targets: list[dict] = []
    lev_n = 0
    lev_cap = max(0, int(leverage_max_positions))
    for s in ranked:
        if len(targets) >= max_positions:
            break
        sym = str(s.get("ticker") or "").upper()
        is_lev = sym in leverage_symbols
        if is_lev and lev_n >= lev_cap:
            continue
        targets.append(s)
        if is_lev:
            lev_n += 1
    return targets


def _target_values(buys: list[dict], budget_usd: float,
                   leverage_symbols: set[str] | None = None,
                   leverage_budget_frac: float | None = None,
                   target_multipliers: dict[str, float] | None = None) -> dict[str, float]:
    if not buys or budget_usd <= 0:
        return {}
    base = budget_usd / len(buys)
    out = {s["ticker"]: base for s in buys}
    if leverage_symbols and leverage_budget_frac is not None:
        lev = [s["ticker"] for s in buys if s["ticker"] in leverage_symbols]
        stock = [s["ticker"] for s in buys if s["ticker"] not in leverage_symbols]
        if lev:
            lev_cap = max(0.0, min(1.0, float(leverage_budget_frac))) * budget_usd
            desired = base * len(lev)
            if desired > lev_cap:
                lev_per = lev_cap / len(lev) if lev else 0.0
                stock_per = (budget_usd - lev_per * len(lev)) / len(stock) if stock else 0.0
                for sym in lev:
                    out[sym] = lev_per
                for sym in stock:
                    out[sym] = stock_per
    if target_multipliers:
        out = {sym: max(0.0, float(val) * max(0.0, float(target_multipliers.get(sym, 1.0))))
               for sym, val in out.items()}
        total = sum(out.values())
        if budget_usd > 0 and total > budget_usd:
            scale = budget_usd / total
            out = {sym: val * scale for sym, val in out.items()}
    return out


def plan_rebalance(signals: list[dict], positions: dict, budget_usd: float,
                   max_positions: int, cash_usd: float | None = None,
                   slippage: float = 0.0, quote_fn=None,
                   cash_buffer: float = 1.0,
                   rebal_band: float = 0.0, exit_buffer: int = 0,
                   leverage_symbols: set[str] | None = None,
                   leverage_max_positions: int | None = None,
                   leverage_budget_frac: float | None = None,
                   min_cash_usd: float = 0.0,
                   target_multipliers: dict[str, float] | None = None) -> list[dict]:
    """목표 바스켓(policy_score 상위 N 균등) vs 보유 → 정수주 지정가 주문계획.

    반환: [{symbol, side('buy'|'sell'), qty, reason}]. 매도 먼저(현금확보)·예산0/음수면 매수생략·현금 러닝캡.
    cash_buffer<1 이면 주문가능금액의 그 비율만 매수에 사용(수수료·지정가 여유).
    rebal_band>0: 보유종목 조정을 |현재가치−목표가치|/목표가치 > band 일 때만(잔챙이 skip·회전율↓).
    exit_buffer>0: 보유종목이 top-(N+buffer) 안이면 유지(경계 flip-flop 방지·회전율↓).
    신규 편입은 주문 전 보유(양수 수량, 청산 주문 포함)가 남긴 빈 슬롯만 채운다.
    계획된 매도는 체결로 보지 않아 슬롯을 비우지 않고, 그 대금은 가용현금에 더하지 않는다.
    레버리지 캡도 같은 주문 전 보유를 센다. 구조 슬리브는 이 함수 밖이다.
    min_cash_usd: 매수 가용현금(cash_usd*cash_buffer)이 이 밑이면 매수 자체를 생성 안 함
    (2026-07-24: 회당 목표주수>0인데 실제 KIS 주문가능금액은 이미 0인 경우가 있어 — 어차피
    거부될 소액 매수 시도로 API 콜만 낭비하는 걸 방지).
    """
    orders: list[dict] = []
    ranked = sorted([s for s in signals if s.get("price", 0) > 0],
                    key=lambda s: -(
                        s.get("selection_score")
                        if s.get("selection_score") is not None
                        else (s.get("policy_score") or 0)
                    ))
    buys = _select_targets(ranked, max_positions, leverage_symbols, leverage_max_positions)
    # 히스테리시스: 매도는 top-(N+buffer) 밖 종목만 (경계 flip-flop 방지)
    keep_targets = _select_targets(
        ranked,
        max_positions + max(0, exit_buffer),
        leverage_symbols,
        leverage_max_positions,
    )
    keep = {s["ticker"] for s in keep_targets}

    for sym, p in positions.items():
        sh = int(p.get("shares", 0) or 0)
        if sh > 0 and sym not in keep:
            orders.append({"symbol": sym, "side": "sell", "qty": sh, "reason": "타깃이탈"})

    target_values = _target_values(
        buys, budget_usd, leverage_symbols, leverage_budget_frac, target_multipliers=target_multipliers
    )
    # cash_usd=0 은 "현금 없음"(캡=0)이지 "정보 없음"(캡 미적용)이 아니다 — is not None 만 검사
    # (2026-07-24: 예전엔 cash_usd>0 도 요구해서 실계좌 현금 정확히 0일 때 캡이 통째로 빠져
    # 예산 기준 풀사이즈 매수가 그대로 나가던 게 '주문가능금액 부족' 실패의 근본 원인이었음)
    remaining = (cash_usd * cash_buffer) if cash_usd is not None else None
    if remaining is not None and remaining < min_cash_usd:
        remaining = 0.0   # 매수 후보는 훑되 전부 tgt<=cur 로 클램프 — 신규매수 생성 안 함
    held_n = sum(1 for p in positions.values() if int(p.get("shares", 0) or 0) > 0)
    open_slots = max(0, int(max_positions) - held_n)
    lev_syms = {str(s).upper() for s in leverage_symbols} if leverage_symbols else set()
    lev_open = None
    if lev_syms and leverage_max_positions is not None:
        held_lev = sum(
            1 for sym, p in positions.items()
            if int(p.get("shares", 0) or 0) > 0 and str(sym).upper() in lev_syms
        )
        lev_open = max(0, int(leverage_max_positions) - held_lev)
    for s in buys:
        sym, price = s["ticker"], s["price"]
        per = target_values.get(sym, 0.0)
        if per <= 0 or price <= 0:
            continue
        cur = int(positions.get(sym, {}).get("shares", 0) or 0)
        if cur <= 0 and open_slots <= 0:
            logger.info(
                "포지션 한도 — 신규편입 보류: %s (주문 전 보유 %d / 한도 %d, 미체결 매도는 슬롯을 비우지 않음)",
                sym, held_n, max_positions)
            continue
        if cur <= 0 and str(sym).upper() in lev_syms and lev_open is not None and lev_open <= 0:
            logger.info(
                "레버리지 한도 — 신규편입 보류: %s (주문 전 보유 레버가 한도 점유, 매도 미체결로 간주)",
                sym)
            continue
        eff = price * (1.0 + max(0.0, slippage))
        if quote_fn:                                       # 라이브 호가(ask) 있으면 실제 체결가로 사이징
            try:
                q = quote_fn(sym, "buy")
            except Exception:
                q = None
            if q and q > 0:
                eff = q
        # 무거래 밴드: 이미 보유 중이고 목표 대비 band 이내면 조정 skip (신규 진입은 항상 매수)
        if rebal_band > 0 and cur > 0 and abs(cur * eff - per) <= rebal_band * per:
            continue
        tgt = int(per // eff)                              # 정수주 floor
        if remaining is not None:
            tgt = min(tgt, cur + int(remaining // eff))
        tgt = max(0, tgt)
        delta = tgt - cur
        if delta > 0:
            orders.append({"symbol": sym, "side": "buy", "qty": delta, "reason": "신규/추가"})
            if cur <= 0:
                open_slots -= 1
                if str(sym).upper() in lev_syms and lev_open is not None:
                    lev_open -= 1
            if remaining is not None:
                remaining -= delta * eff
        elif delta < 0:
            orders.append({"symbol": sym, "side": "sell", "qty": -delta, "reason": "비중축소"})
    return orders


def _classify_kind(side: str, qty: int, cur_shares: int) -> str:
    if side == "buy":
        return "편입" if cur_shares <= 0 else "증액"
    return "퇴출" if qty >= cur_shares else "감액"


# ── 진입점 ────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    dry = "--dry-run" in argv
    logger.info("=== us_mock_track 시작 [%s]%s ===",
                datetime.now(KST).strftime("%Y-%m-%d %H:%M"), " [DRY-RUN]" if dry else "")
    if not dry and not kis_mock.is_enabled():
        logger.info("KOREA_MOCK_ENABLED 아님 — US 모의 페이퍼트레이딩 생략")
        return 0

    if not dry:
        bal, balance_issue = _fetch_balance()
        # 잔고를 모르면(조회 실패·브로커 거부·NAV 미확인) 빈 보유로 오인하거나 시드/보유평가액
        # 예산으로 매수하면 안 된다 — snapshot 대신 실패 이벤트만 남기고 주문 보류.
        if balance_issue:
            logger.error("KIS 모의 잔고 조회 실패(%s) — 주문 보류", balance_issue)
            _record_snapshot(bal, balance_issue)
            return 1
        positions, cash, nav = bal["positions"], bal["cash_usd"], float(bal["nav"])
        logger.info("잔고: cash=$%s nav=$%s pos_value=$%s", f"{cash or 0:,.0f}", f"{nav:,.0f}",
                    f"{bal.get('pos_value') or 0:,.0f}")
    else:
        positions, cash, nav = {}, SEED_USD, SEED_USD    # dry-run: 시드 가정 미리보기

    signals = compute_us_signals()
    sweet_orders = None
    sweet_basis = ""
    sweet_weights: dict[str, float] = {}
    max_positions = MAX_POS
    if os.getenv("MOCK_SWEET_SPOT", "1") != "0":
        from ml.data_pipeline import fetch_prices
        from ml.mock_sweet_spot import etf_orders, us_n_basis, us_n_state
        loaded = fetch_prices(["QQQ", "QLD", "SGOV", "GLD", "^VIX", "^VIX3M"], days=400)
        closes = {t: df["Close"] for t, df in loaded.items()
                  if df is not None and not getattr(df, "empty", True) and "Close" in df.columns}
        state = us_n_state(closes)
        weights = state.get("weights", {})
        prices = {t: float(s.dropna().iloc[-1]) for t, s in closes.items() if t in weights and len(s.dropna())}
        if weights:
            buy_cash = (cash * CASH_BUFFER) if cash is not None else None   # 주식 경로와 같은 현금 상한
            sweet_orders = etf_orders(weights, positions, nav * INVEST, prices, band=REBAL_BAND,
                                      cash=buy_cash, min_cash=MIN_CASH_USD)
            sweet_basis = us_n_basis(state)
            sweet_weights = weights
            logger.info("%s (매수현금 상한 %s)", sweet_basis,
                        f"${buy_cash:,.0f}" if buy_cash is not None else "없음")
    if sweet_orders is not None:
        plan = sweet_orders
        budget = nav * INVEST
        max_positions = sum(1 for w in sweet_weights.values() if w > 0)
        logger.info("리밸런스 계획 %d건 (스위트스팟 ETF · US_MOCK_MAX_POS 미적용)", len(plan))
    elif not signals:
        logger.warning("US 신호 0건 — 종료")
        return 0
    else:
        leverage_symbols = set(leverage_universe())
        # paper 만 슬리브가 종목을 소유한다(신호·보유 모두 주식 북에서 제외). off/shadow 에서
        # 보유만 빼면 같은 종목을 신규로 착각해 max_positions 가 과소계상된다.
        sleeve_frac, sleeve_orders = 0.0, []
        stock_cash = None
        if cash is not None:
            stock_cash = max(0.0, cash) * min(1.0, max(0.0, CASH_BUFFER))
            if stock_cash < MIN_CASH_USD:
                stock_cash = 0.0
        sleeve_mode = leverage_sleeve_mode()
        positions_stock = stock_book_positions(positions, sleeve_mode, LEV_SLEEVE_SYMBOL)
        if sleeve_mode != "off":
            reco = load_lev_shadow()
            lev_px = _rt_best(LEV_SLEEVE_SYMBOL, "buy") or (kis_mock.get_price(LEV_SLEEVE_SYMBOL) if not dry else None)
            shadow_frac, shadow_orders = sleeve_plan(reco, nav, positions, lev_px,
                                                     band=REBAL_BAND, symbol=LEV_SLEEVE_SYMBOL,
                                                     buy_cash=stock_cash if sleeve_mode == "paper" else None)
            if sleeve_mode == "paper":
                sleeve_frac, sleeve_orders = shadow_frac, shadow_orders
                if stock_cash is not None and sleeve_orders and sleeve_orders[0]["side"] == "buy":
                    stock_cash -= sleeve_orders[0]["qty"] * lev_px
            logger.info("슬리브[%s]: reco=%s → 비중 %.0f%% · 주문 %d건",
                        sleeve_mode, reco, shadow_frac * 100, len(shadow_orders))
        budget = nav * INVEST * (1.0 - sleeve_frac)
        trade_signals = [
            s for s in signals
            if not (sleeve_mode == "paper" and s.get("ticker") == LEV_SLEEVE_SYMBOL)
        ]
        target_multipliers = {
            s["ticker"]: float(s.get("momentum_multiplier", 1.0) or 1.0)
            for s in trade_signals if s.get("ticker")
        }
        plan = plan_rebalance(trade_signals, positions_stock, budget, MAX_POS, cash_usd=stock_cash,
                              slippage=SLIPPAGE, quote_fn=_rt_best,
                              rebal_band=REBAL_BAND, exit_buffer=EXIT_BUFFER,
                              leverage_symbols=leverage_symbols,
                              leverage_max_positions=LEV_ETF_MAX_POS,
                              leverage_budget_frac=LEV_ETF_MAX_WEIGHT,
                              min_cash_usd=MIN_CASH_USD, target_multipliers=target_multipliers)
        if TRANCHES > 1 and plan:
            from lib.tranche import plan_tranches
            _px = {s["ticker"]: s.get("price") for s in signals if s.get("ticker")}
            for sym, p in positions_stock.items():
                _px.setdefault(sym, p.get("cur_price"))
            plan = plan_tranches(plan, budget / max(MAX_POS, 1), lambda s: _px.get(s), TRANCHES,
                                 id_key="symbol")
        plan = ([o for o in plan if o["side"] == "sell"] + sleeve_orders
                + [o for o in plan if o["side"] == "buy"])
        logger.info("리밸런스 계획 %d건 (예산 $%.0f·목표 %d종목·레버ETF 최대 %d개/%.0f%%·슬리브 %.0f%%·%d분할)",
                    len(plan), budget, MAX_POS, LEV_ETF_MAX_POS, LEV_ETF_MAX_WEIGHT * 100,
                    sleeve_frac * 100, TRANCHES)

    if dry:
        for o in plan:
            logger.info("  [DRY] %s %s %s주 · %s", o["side"], o["symbol"], o["qty"], o.get("reason"))
        if not plan:
            logger.info("  [DRY] 주문 없음")
        return 0

    from ml.adaptive import Ledger
    ledger = Ledger("us_mock")
    llm_shadow_ledger = Ledger("us_mock_llm_shadow")
    sig_by = {s["ticker"]: s for s in signals}
    if sweet_orders is not None:
        # 스위트스팟 ETF(SGOV·GLD 등)는 종목 신호에 없거나 다른 근거(주식 정책)를 달고 있다 —
        # 실제 선택 근거로 덮어 원장·알림·LLM 검토가 같은 근거를 보게 한다.
        for sym, w in sweet_weights.items():
            row = dict(sig_by.get(sym) or {"ticker": sym})
            row["selection_basis"] = f"{sweet_basis} · {sym} 목표 {w:.0%}"
            row["rationale"] = {**(row.get("rationale") or {}), "one_line_reason": row["selection_basis"]}
            sig_by[sym] = row
        for o in plan:
            if o["symbol"] not in sweet_weights:
                row = dict(sig_by.get(o["symbol"]) or {"ticker": o["symbol"]})
                row["selection_basis"] = f"{sweet_basis} · {o['symbol']} 바구니 밖 → 전량 매도"
                row["rationale"] = {**(row.get("rationale") or {}), "one_line_reason": row["selection_basis"]}
                sig_by[o["symbol"]] = row
    today = datetime.now(KST).strftime("%Y-%m-%d")

    # ★랭킹 섀도 — KR 미러(kiwoom_mock_track). 주문된 상위 N 만 원장에 남기면 점수 분산이
    # 잘려 IC 가 구조적으로 감쇠(구간 제한)하므로, 점수화된 전 후보를 별도 표면에 적재해
    # 선택편향 없는 전 구간 IC 를 측정한다. 주문·회전율·비용 무영향.
    from lib import rank_shadow
    _shadow_n = rank_shadow.log_ranked_candidates(
        Ledger("us_mock_shadow"), signals, today=today, market="US")
    logger.info("랭킹 섀도 적재: %d건 (유니버스 %d)", _shadow_n, len(signals))

    llm_payload = llm_exec.build_order_review_payload(
        market="US", nav=nav, cash=cash, budget=budget, max_positions=max_positions,
        orders=plan, positions=positions, signals=list(sig_by.values()))
    llm_reviews, llm_status = llm_exec.run_order_review(llm_payload)
    logged = llm_exec.log_shadow_reviews(
        llm_shadow_ledger, market="US", date=today, plan=plan, reviews=llm_reviews,
        signals_by=sig_by, applied_mode=llm_exec.mode())
    llm_mode = llm_exec.mode()
    plan, llm_applied = llm_exec.apply_reviews(plan, llm_reviews, apply_mode=llm_mode,
                                               review_status=llm_status)
    logger.info("LLM order review: %s · shadow %d건 · applied %d건",
                llm_status, logged, len(llm_applied))
    for item in llm_applied:
        logger.warning("LLM guarded_apply %s %s %s주 → %s",
                       item.get("side"), item.get("symbol"), item.get("qty"), item.get("llm_applied"))
    llm_record = llm_exec.review_execution_record(apply_mode=llm_mode, review_status=llm_status,
                                                  applied=llm_applied)
    if llm_record:
        try:
            import store
            store.append("us_mock_history", {"date": datetime.now(KST).strftime("%Y-%m-%d %H:%M"),
                                             **llm_record})
        except Exception as e:
            logger.warning("LLM 주문검토 기록 실패: %s", e)

    from ml.adaptive import costs
    results = []
    day_cost = day_notional = 0.0
    for o in plan:
        cur = int(positions.get(o["symbol"], {}).get("shares", 0) or 0)
        kind = "레버슬리브" if o.get("sleeve") else _classify_kind(o["side"], o["qty"], cur)
        s = sig_by.get(o["symbol"], {}) if not o.get("sleeve") else {
            "price": None, "rationale": {"one_line_reason": o.get("reason", "")}}
        px = _limit_price(o["symbol"], o["side"], s.get("price"))
        r = kis_mock.place_order(o["symbol"], o["qty"], o["side"], price=px)
        results.append({**o, "kind": kind, **r})
        if r.get("ok"):                          # 체결분 거래비용 적립 (수수료+스프레드 — 정직 계기)
            notion = abs(o["qty"]) * float(px or 0)
            day_notional += notion
            day_cost += costs.order_cost(notion, o["side"], "US")
            trade_events.record_trade(
                ticker=o["symbol"],
                side=o["side"],
                qty=o["qty"],
                price=px,
                avg_price=positions.get(o["symbol"], {}).get("avg_price"),
                account="us_mock",
                source="kis_mock",
                market="US",
                currency="USD",
                broker_order_id=r.get("ord_no"),
                confirmed=True,
                note=o.get("reason", ""),
            )
        _log_decision(ledger, s, o["symbol"], kind, o["side"], o["qty"], r.get("ok"), today)
        logger.info("%s(%s) %s %s주 → %s %s", o["side"], kind, o["symbol"], o["qty"],
                    "OK" if r.get("ok") else "FAIL", r.get("msg", ""))
        time.sleep(0.4)   # KIS 레이트리밋
    if day_notional > 0:                          # 당일 거래비용 1건 (리포트 누적·회전율용)
        import store
        store.append("us_mock_history", {"date": datetime.now(KST).strftime("%Y-%m-%d %H:%M"),
                                         "kind": "cost", "cost": round(day_cost, 2),
                                         "notional": round(day_notional, 2)})
    _record_snapshot(bal, None)
    _notify(nav, results, llm_note=llm_exec.fallback_note(llm_record), basis=sweet_basis or None)
    logger.info("=== 완료: 집행 %d건 ===", sum(1 for r in results if r.get("ok")))
    return 0


def _log_decision(ledger, sig, sym, kind, order_side, qty, ok, today):
    from ml.recommendation_surfaces import decision_metadata
    try:
        ledger.log_decision({
            **decision_metadata(sig.get("recommendation_v2_shadow", {}), selection_status="selected"),
            "date": today, "ticker": sym, "side": kind, "order_side": order_side, "qty": qty,
            "price": sig.get("price"), "base_score": sig.get("base_score"),
            "policy_score": sig.get("policy_score"),
            "selection_score": sig.get("selection_score"),
            "momentum_score": sig.get("momentum_score"),
            "momentum_tilt": sig.get("momentum_tilt"),
            "momentum_multiplier": sig.get("momentum_multiplier"),
            "momentum_state": sig.get("momentum_state"),
            "overlay_active": sig.get("overlay_active"),
            "regime": sig.get("regime"),
            "market": sig.get("market"),
            "reason_codes": sig.get("reason_codes"),
            "rationale": sig.get("rationale"), "features": sig.get("features"), "ok": ok,
            "selection_basis": sig.get("selection_basis"),
        })
        if kind in ("편입", "퇴출"):
            icon = "📥" if kind == "편입" else "📤"
            rr = sig.get("selection_basis") or (sig.get("rationale") or {}).get("one_line_reason", "")
            ledger.append_journal(today, f"- {today} {icon} {kind} {sym} — {rr} (정책 {sig.get('policy_score','')})")
    except Exception as e:
        logger.warning("결정 원장 기록 실패 %s: %s", sym, e)


def _record_snapshot(bal: dict, problem: str | None, *, at: str | None = None) -> dict | None:
    """잔고 응답 → NAV snapshot. 쓸 수 없는 잔고는 snapshot 대신 실패 이벤트(mock_generations)."""
    try:
        res = mock_generations.record_snapshot(
            "us_mock_history", nav=(bal or {}).get("nav"), cash=(bal or {}).get("cash_usd"),
            positions=(bal or {}).get("positions") or {}, at=at, seed=SEED_USD, problem=problem)
        if not res["recorded"]:
            logger.warning("US 모의 스냅샷 미기록 — 잔고 이상(%s)", res["reason"])
        elif res["reset"]:
            logger.warning("US 모의계좌 리셋 감지 — 세대 %s 마감(마감 NAV $%s)",
                           res["reset"]["generation"], f"{res['reset']['final_nav']:,.2f}")
        return res
    except Exception as e:
        logger.warning("US 모의 스냅샷 기록 실패: %s", e)
        return None


# 개별 주문이 아니라 '전 주문이 동일하게 막히는' 계좌/시장 레벨 상황 신호 — KR
# (kiwoom_mock_track.py _order_blocker) 와 동일 패턴, KIS 해외 모의 메시지에 맞춤.
_ACCOUNT_SIGNS = ("계좌# 미설정", "토큰 없음")
_MARKET_SIGNS = ("장운영시간", "장 운영시간", "휴장", "거래정지")


def _order_blocker(msg) -> str | None:
    """주문 실패 사유가 '전 주문 공통 차단'인지 분류 — 개별 주문 문제(수량·가격 등)와 구분.

    'account' = 계좌 미설정/토큰 없음 → 설정 확인 필요 · 'market' = 장 운영시간 아님 →
    장중 재시도 · None = 개별.
    """
    m = str(msg or "")
    if any(s in m for s in _ACCOUNT_SIGNS):
        return "account"
    if any(s in m for s in _MARKET_SIGNS):
        return "market"
    return None


def _notify(nav, results, llm_note: str | None = None, basis: str | None = None):
    _ICON = {"편입": "📥", "증액": "➕", "퇴출": "📤", "감액": "➖", "레버슬리브": "🏗️"}
    lines = ["🧪 [모의] 미국 페이퍼트레이딩 (KIS 해외)", "━━━━━━━━━━━━━━"]
    if nav is not None:
        lines.append(f"  NAV  ${nav:,.0f}")
    if basis:
        lines.append(f"  근거: {basis}")
    if not results:
        lines.append("  주문 없음 (목표 = 보유)")

    # 계좌/시장 레벨 차단이면 개별 실패 도배 대신 명확 안내 1건
    blocker = next((_order_blocker(r.get("msg")) for r in results
                    if not r.get("ok") and _order_blocker(r.get("msg"))), None)
    if blocker == "account":
        emsg = next(r.get("msg") for r in results if _order_blocker(r.get("msg")) == "account")
        lines += ["  ⚠️ KIS 해외 모의계좌 문제 — 주문 중단", f"     ↳ {emsg}",
                  "  👉 계좌#·토큰 설정 확인 필요"]
    elif blocker == "market":
        lines += ["  ⚠️ 장 운영시간 아님 — 주문 보류 (다음 개장 시 자동 재시도)"]
    else:
        for r in results:
            mark = "✅" if r.get("ok") else "❌"
            lines.append(f"  {mark} {_ICON.get(r.get('kind'), '')}{r.get('kind')} {r['symbol']} {r['qty']}주")
            if not r.get("ok") and r.get("msg"):
                lines.append(f"     ↳ {r['msg']}")
        lines.append(f"  집행 {sum(1 for r in results if r.get('ok'))} · 실패 {sum(1 for r in results if not r.get('ok'))}")
    if llm_note:
        lines.append(f"  {llm_note}")
    lines.append("  ⚠️ 모의투자 — 실거래 아님")
    try:
        import notify
        notify.send_telegram("\n".join(lines), token=os.getenv("STOCK_BOT_TOKEN"),
                             chat_id=os.getenv("STOCK_BOT_CHAT_ID"), timeout=15)
    except Exception as e:
        logger.warning("텔레그램 발송 실패: %s", e)


if __name__ == "__main__":
    sys.exit(main())
