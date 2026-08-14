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
import os
import sys
import time
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

import pandas as pd
import kis_mock
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
# 주문가능금액(frcr_use_psbl_amt)의 일부만 매수에 사용 — 수수료(KIS 해외 ~0.25%)·통합증거금
# USD 환산 haircut·지정가 틱업 여유. 1% 슬리피지만으론 "주문가능금액 부족" 거부가 남아 별도 버퍼.
CASH_BUFFER = _float_env("US_MOCK_CASH_BUFFER", 0.95)
# cash_usd 가 KIS 실측(frcr_use_psbl_amt)이 아니라 nav-pos_value 역산 추정치(kis_mock.get_balance
# 의 cash_derived=True)일 때 — 포지션 평가액이 NAV 근접/초과 시 부정확해지기 쉬워 더 보수적으로
# (2026-07-24: 실계좌 확인 결과 cash_derived=True·frcr_use_psbl_amt=0 상태에서도 매수 재시도 반복
# → 주문가능금액 부족 실패의 주 원인이었음)
CASH_BUFFER_DERIVED = _float_env("US_MOCK_CASH_BUFFER_DERIVED", 0.5)
# 이 밑으로 남은 현금이면 매수 자체를 시도 안 함(어차피 실패할 소액 주문 API 낭비 방지)
MIN_CASH_USD = _float_env("US_MOCK_MIN_CASH_USD", 500.0)
REBAL_BAND = _float_env("US_MOCK_REBAL_BAND", 0.25)   # 무거래 밴드(목표比 ±25% 벗어날 때만 조정·회전율↓)
EXIT_BUFFER = _int_env("US_MOCK_EXIT_BUFFER", 2)      # 히스테리시스(top-N+2 안이면 보유 유지·경계 flip 방지)
# ★분할매수·분할매도 — 회당 목표 1/N (N회 평균 진입/청산·분산 축소·bps 비용 불변). 기본 3·1=일괄.
TRANCHES = _int_env("US_MOCK_TRANCHES", 3)
QUOTE_STALE_S = _int_env("REALTIME_QUOTE_STALE_S", 10)
LEV_ETF_MAX_POS = _int_env("US_MOCK_LEVERAGE_MAX_POS", 2)
LEV_ETF_MAX_WEIGHT = _float_env("US_MOCK_LEVERAGE_MAX_WEIGHT", 0.35)

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
                symbol: str = "QLD") -> tuple[float, list[dict]]:
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
    if delta > 0:
        return frac, [{"symbol": symbol, "side": "buy", "qty": delta,
                       "reason": f"Tier3 구조레버 슬리브 ×{reco_lev:.2f}", "sleeve": True}]
    if delta < 0:
        return frac, [{"symbol": symbol, "side": "sell", "qty": -delta,
                       "reason": "슬리브 리밸런스", "sleeve": True}]
    return frac, []


def _rt_best(sym: str, side: str):
    """실시간 우호가(매수=ask·매도=bid) — 활성·신선시. 없으면 None(정적 슬리피지/신호가 폴백)."""
    try:
        from providers import realtime_quotes
        if realtime_quotes.enabled():
            return realtime_quotes.best(sym, side, max_age_s=QUOTE_STALE_S)
    except Exception:
        pass
    return None


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
            try:
                from providers.market_data import _history_cached
                h = _history_cached(tk, period="1y")
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

    # US ranker(가치모델) best-effort → 횡단면 정규화 주입
    try:
        from ml import ranker
        raw = ranker.scores_by_ticker([s["ticker"] for s in out]) if hasattr(ranker, "scores_by_ticker") else {}
        vals = [raw[s["ticker"]] for s in out if s["ticker"] in raw]
        if vals:
            lo, hi = min(vals), max(vals)
            rng = (hi - lo) or 1.0
            for s in out:
                if s["ticker"] in raw:
                    s["features"]["ranker"] = round((raw[s["ticker"]] - lo) / rng, 4)
    except Exception as e:
        logger.info("US ranker 주입 생략(폴백: 규칙 가중만): %s", e)

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
    cash_buffer<1 이면 주문가능금액의 그 비율만 매수에 사용(수수료·통합증거금 FX·틱업 여유).
    rebal_band>0: 보유종목 조정을 |현재가치−목표가치|/목표가치 > band 일 때만(잔챙이 skip·회전율↓).
    exit_buffer>0: 보유종목이 top-(N+buffer) 안이면 유지(경계 flip-flop 방지·회전율↓).
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
    for s in buys:
        sym, price = s["ticker"], s["price"]
        per = target_values.get(sym, 0.0)
        if per <= 0 or price <= 0:
            continue
        cur = int(positions.get(sym, {}).get("shares", 0) or 0)
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

    cash_derived = False
    if not dry:
        bal = kis_mock.get_balance()
        if not bal["ok"]:
            logger.error("KIS 모의 잔고 조회 실패 — 주문 보류")
            return 1
        positions, cash, nav = bal["positions"], bal["cash_usd"], bal["nav"]
        cash_derived = bool(bal.get("cash_derived"))
        logger.info("잔고: cash=$%s(%s) nav=$%s pos_value=$%s", f"{cash or 0:,.0f}",
                    "역산추정" if cash_derived else "실측", f"{nav or 0:,.0f}",
                    f"{bal.get('pos_value') or 0:,.0f}")
    else:
        positions, cash, nav = {}, SEED_USD, SEED_USD    # dry-run: 시드 가정 미리보기
    if nav is None:
        nav = (bal["pos_value"] if not dry else 0) or SEED_USD

    signals = compute_us_signals()
    if not signals:
        logger.warning("US 신호 0건 — 종료")
        return 0
    leverage_symbols = set(leverage_universe())

    # ★Tier3 구조레버 슬리브 (모의 한정) — 게이트 GO·신선 시 NAV×(reco−1) 을 2x ETF 로.
    # 슬리브 종목은 선택 로직 대상에서 제외(positions 분리 — '타깃이탈' 오청산 방지).
    sleeve_frac, sleeve_orders = 0.0, []
    positions_stock = {k: v for k, v in positions.items() if k != LEV_SLEEVE_SYMBOL}
    sleeve_mode = leverage_sleeve_mode()
    if sleeve_mode != "off":
        reco = load_lev_shadow()
        lev_px = _rt_best(LEV_SLEEVE_SYMBOL, "buy") or (kis_mock.get_price(LEV_SLEEVE_SYMBOL) if not dry else None)
        shadow_frac, shadow_orders = sleeve_plan(reco, nav, positions, lev_px,
                                                 band=REBAL_BAND, symbol=LEV_SLEEVE_SYMBOL)
        if sleeve_mode == "paper":
            sleeve_frac, sleeve_orders = shadow_frac, shadow_orders
        logger.info("슬리브[%s]: reco=%s → 비중 %.0f%% · 주문 %d건",
                    sleeve_mode, reco, shadow_frac * 100, len(shadow_orders))

    budget = nav * INVEST * (1.0 - sleeve_frac)
    trade_signals = [
        s for s in signals
        if not (sleeve_mode == "paper" and s.get("ticker") == LEV_SLEEVE_SYMBOL)
    ]
    effective_cash_buffer = CASH_BUFFER_DERIVED if cash_derived else CASH_BUFFER
    target_multipliers = {
        s["ticker"]: float(s.get("momentum_multiplier", 1.0) or 1.0)
        for s in trade_signals if s.get("ticker")
    }
    plan = plan_rebalance(trade_signals, positions_stock, budget, MAX_POS, cash_usd=cash,
                          slippage=SLIPPAGE, quote_fn=_rt_best, cash_buffer=effective_cash_buffer,
                          rebal_band=REBAL_BAND, exit_buffer=EXIT_BUFFER,
                          leverage_symbols=leverage_symbols,
                          leverage_max_positions=LEV_ETF_MAX_POS,
                          leverage_budget_frac=LEV_ETF_MAX_WEIGHT,
                          min_cash_usd=MIN_CASH_USD, target_multipliers=target_multipliers)
    # ★분할매수/매도: 종목 주문을 회당 목표의 1/N 로 상한 (슬리브는 별도 — 제외)
    if TRANCHES > 1 and plan:
        from lib.tranche import plan_tranches
        _px = {s["ticker"]: s.get("price") for s in signals if s.get("ticker")}
        for sym, p in positions_stock.items():
            _px.setdefault(sym, p.get("cur_price"))
        plan = plan_tranches(plan, budget / max(MAX_POS, 1), lambda s: _px.get(s), TRANCHES,
                             id_key="symbol")
    # 실행 순서: 매도(현금 확보) → 슬리브 → 매수
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
    today = datetime.now(KST).strftime("%Y-%m-%d")

    llm_payload = llm_exec.build_order_review_payload(
        market="US", nav=nav, cash=cash, budget=budget, max_positions=MAX_POS,
        orders=plan, positions=positions, signals=signals)
    llm_reviews, llm_status = llm_exec.run_order_review(llm_payload)
    logged = llm_exec.log_shadow_reviews(
        llm_shadow_ledger, market="US", date=today, plan=plan, reviews=llm_reviews,
        signals_by=sig_by, applied_mode=llm_exec.mode())
    plan, llm_applied = llm_exec.apply_reviews(plan, llm_reviews)
    logger.info("LLM order review: %s · shadow %d건 · applied %d건",
                llm_status, logged, len(llm_applied))
    for item in llm_applied:
        logger.warning("LLM guarded_apply %s %s %s주 → %s",
                       item.get("side"), item.get("symbol"), item.get("qty"), item.get("llm_applied"))

    from ml.adaptive import costs
    results = []
    day_cost = day_notional = 0.0
    for o in plan:
        cur = int(positions.get(o["symbol"], {}).get("shares", 0) or 0)
        kind = "레버슬리브" if o.get("sleeve") else _classify_kind(o["side"], o["qty"], cur)
        s = sig_by.get(o["symbol"], {}) if not o.get("sleeve") else {
            "price": None, "rationale": {"one_line_reason": o.get("reason", "")}}
        px = _rt_best(o["symbol"], o["side"]) or s.get("price") or kis_mock.get_price(o["symbol"]) or 0
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
    _record_snapshot(nav, cash, positions)
    _notify(nav, results)
    logger.info("=== 완료: 집행 %d건 ===", sum(1 for r in results if r.get("ok")))
    return 0


def _log_decision(ledger, sig, sym, kind, order_side, qty, ok, today):
    try:
        ledger.log_decision({
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
        })
        if kind in ("편입", "퇴출"):
            icon = "📥" if kind == "편입" else "📤"
            rr = (sig.get("rationale") or {}).get("one_line_reason", "")
            ledger.append_journal(today, f"- {today} {icon} {kind} {sym} — {rr} (정책 {sig.get('policy_score','')})")
    except Exception as e:
        logger.warning("결정 원장 기록 실패 %s: %s", sym, e)


def _record_snapshot(nav, cash, positions):
    try:
        import store
        store.append("us_mock_history", {
            "date": datetime.now(KST).strftime("%Y-%m-%d %H:%M"), "kind": "snapshot",
            "nav": nav, "cash": cash,
            "positions": len([p for p in positions.values() if int(p.get("shares", 0) or 0) > 0])})
    except Exception as e:
        logger.warning("US 모의 스냅샷 기록 실패: %s", e)


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


def _notify(nav, results):
    _ICON = {"편입": "📥", "증액": "➕", "퇴출": "📤", "감액": "➖", "레버슬리브": "🏗️"}
    lines = ["🧪 [모의] 미국 페이퍼트레이딩 (KIS 해외)", "━━━━━━━━━━━━━━"]
    if nav is not None:
        lines.append(f"  NAV  ${nav:,.0f}")
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
    lines.append("  ⚠️ 모의투자 — 실거래 아님")
    try:
        import notify
        notify.send_telegram("\n".join(lines), token=os.getenv("STOCK_BOT_TOKEN"),
                             chat_id=os.getenv("STOCK_BOT_CHAT_ID"), timeout=15)
    except Exception as e:
        logger.warning("텔레그램 발송 실패: %s", e)


if __name__ == "__main__":
    sys.exit(main())
