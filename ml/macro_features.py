"""ml/macro_features.py — 거시경제·시장 체제 피처 빌더

yfinance 기반 시장 파생 지표 (FRED 없이 순수 가격 데이터):

피처 그룹:
  금리·수익률곡선: 10Y, 5Y, 30Y, 3M T-bill, 기울기(2개)
  신용·스프레드  : HYG/IEF (하이일드), LQD/IEF (투자등급), TLT/SHY (안전자산)
  변동성 체제    : VIX 수준, VIX 백분위, VIX 텀스트럭처(VIX3M/VIX)
  안전자산 수요  : 금(GLD), 달러(UUP), 실질금리 프록시(TIP/IEF)
  원자재·경기    : 원유(USO), 구리(CPER), 광범위 원자재(PDBC)
  글로벌 리스크  : 이머징(EEM) vs 미국(SPY), ACWI 모멘텀
  리스크온/오프  : 복합 합성 지표 (0=리스크오프, 1=리스크온)

공개 API:
  build_macro_features(days)  → pd.DataFrame (날짜 인덱스)
  get_macro_today()           → dict (오늘 값, 캐시 1h)
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ── 매크로 티커 정의 ──────────────────────────────────────────────────────────

_TICKERS = {
    # 금리
    "^TNX": "tnx",      # 10Y Treasury yield
    "^FVX": "fvx",      # 5Y Treasury yield
    "^TYX": "tyx",      # 30Y Treasury yield
    "^IRX": "irx",      # 13-week T-bill yield

    # 채권 ETF (스프레드 계산용)
    "HYG":  "hyg",      # 하이일드 회사채
    "IEF":  "ief",      # 7-10Y 국채
    "TLT":  "tlt",      # 20Y+ 국채
    "LQD":  "lqd",      # 투자등급 회사채
    "SHY":  "shy",      # 1-3Y 국채
    "TIP":  "tip",      # 물가연동채 (TIPS)

    # 변동성
    "^VIX": "vix",      # 단기 변동성
    "VIXM": "vixm",     # VIX 중기 (VIX3M 프록시, 선물 기반 ETF)

    # 안전자산
    "GLD":  "gld",      # 금
    "UUP":  "uup",      # 달러 인덱스 ETF

    # 원자재
    "USO":  "uso",      # WTI 원유
    "CPER": "cper",     # 구리 (경기 선행)

    # 글로벌
    "EEM":  "eem",      # 이머징마켓
    "ACWI": "acwi",     # 전세계 주식

    # 기준
    "SPY":  "spy",      # S&P500
    "QQQ":  "qqq",      # NASDAQ100
}

# ── 내부 캐시 ─────────────────────────────────────────────────────────────────

_CACHE_DIR = Path(os.path.expanduser("~/reports/ml-cache"))


def _cache_path(key: str) -> Path:
    import hashlib
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    h = hashlib.md5(key.encode()).hexdigest()[:8]
    return _CACHE_DIR / f"macro_{key[:30]}_{h}.pkl"


def _load(key: str, ttl_hours: float) -> pd.DataFrame | None:
    path = _cache_path(key)
    if not path.exists():
        return None
    age = datetime.now() - datetime.fromtimestamp(path.stat().st_mtime)
    if age > timedelta(hours=ttl_hours):
        return None
    try:
        import pickle
        return pickle.loads(path.read_bytes())
    except Exception:
        return None


def _save(key: str, df: pd.DataFrame) -> None:
    try:
        import pickle
        _cache_path(key).write_bytes(pickle.dumps(df))
    except Exception as e:
        logger.warning("매크로 캐시 저장 실패: %s", e)


def _rank_normalize(s: pd.Series, window: int = 252) -> pd.Series:
    """롤링 백분위 정규화 (0~100)."""
    return s.rolling(window, min_periods=window // 4).rank(pct=True) * 100


def _safe_close(prices: dict, ticker: str) -> pd.Series | None:
    df = prices.get(ticker)
    if df is None:
        return None
    col = "Close" if "Close" in df.columns else (df.columns[0] if len(df.columns) else None)
    return df[col] if col else None


# ── 피처 빌더 ─────────────────────────────────────────────────────────────────

def build_macro_features(days: int = 1260) -> pd.DataFrame:
    """거시경제 피처 DataFrame 반환 (일봉, 날짜 인덱스).

    캐시: 6시간. 네트워크 실패 시 빈 DataFrame 반환.
    """
    cache_key = f"macro_{days}d"
    cached = _load(cache_key, ttl_hours=6)
    if cached is not None:
        return cached

    try:
        import yfinance as yf
        tickers = list(_TICKERS.keys())
        raw = yf.download(tickers, period=f"{days // 252 + 2}y",
                          auto_adjust=True, progress=False, threads=True)

        prices: dict[str, pd.DataFrame] = {}
        if isinstance(raw.columns, pd.MultiIndex):
            for tk in tickers:
                try:
                    df = raw.xs(tk, axis=1, level=1).dropna(how="all")
                    df.index = pd.to_datetime(df.index).tz_localize(None)
                    prices[tk] = df
                except Exception:
                    pass
        else:
            # 단일 종목 fallback
            raw.index = pd.to_datetime(raw.index).tz_localize(None)
            prices[tickers[0]] = raw

        result = _compute_macro(prices, days)
        _save(cache_key, result)
        return result

    except Exception as e:
        logger.warning("매크로 피처 빌드 실패: %s", e)
        return pd.DataFrame()


def _compute_macro(prices: dict, days: int) -> pd.DataFrame:
    """가격 데이터에서 매크로 피처 계산."""

    # 공통 날짜 인덱스: QQQ or SPY 기준
    base = _safe_close(prices, "QQQ") or _safe_close(prices, "SPY")
    if base is None:
        return pd.DataFrame()
    idx = base.dropna().index[-days:]

    feat = pd.DataFrame(index=idx)

    # ── 금리·수익률곡선 ───────────────────────────────────────────────────────
    tnx = _safe_close(prices, "^TNX")
    fvx = _safe_close(prices, "^FVX")
    tyx = _safe_close(prices, "^TYX")
    irx = _safe_close(prices, "^IRX")

    if tnx is not None:
        tnx_r = tnx.reindex(idx).ffill()
        feat["tnx_10y"]       = tnx_r                              # 10Y 금리 수준
        feat["tnx_pct"]       = _rank_normalize(tnx_r)             # 10Y 금리 백분위
        feat["tnx_mom_20d"]   = tnx_r.pct_change(20)               # 10Y 금리 20일 변화율
    if irx is not None and tnx is not None:
        irx_r = irx.reindex(idx).ffill()
        feat["yield_curve_10_3m"] = tnx_r - irx_r                 # 10Y - 3M (경기 선행)
        feat["yield_curve_pct"]   = _rank_normalize(tnx_r - irx_r) # 백분위
    if tyx is not None and tnx is not None:
        tyx_r = tyx.reindex(idx).ffill()
        feat["yield_curve_30_10"] = tyx_r - tnx_r                 # 30Y - 10Y (장기 기울기)
    if fvx is not None and irx is not None:
        fvx_r = fvx.reindex(idx).ffill()
        feat["yield_curve_5_3m"] = fvx_r - irx_r                  # 5Y - 3M (중기 기울기)

    # ── 신용 스프레드 ─────────────────────────────────────────────────────────
    hyg = _safe_close(prices, "HYG")
    ief = _safe_close(prices, "IEF")
    lqd = _safe_close(prices, "LQD")
    tlt = _safe_close(prices, "TLT")
    shy = _safe_close(prices, "SHY")

    if hyg is not None and ief is not None:
        hyg_r  = hyg.reindex(idx).ffill()
        ief_r  = ief.reindex(idx).ffill()
        ratio  = (hyg_r / ief_r.replace(0, np.nan))
        feat["credit_spread_hy"]  = _rank_normalize(ratio)         # HY 스프레드 (높을수록 낙관)
        feat["credit_mom_20d"]    = ratio.pct_change(20)
    if lqd is not None and ief is not None:
        lqd_r  = lqd.reindex(idx).ffill()
        ratio2 = (lqd_r / ief_r.replace(0, np.nan))
        feat["credit_spread_ig"]  = _rank_normalize(ratio2)        # IG 스프레드
    if tlt is not None and shy is not None:
        tlt_r  = tlt.reindex(idx).ffill()
        shy_r  = shy.reindex(idx).ffill()
        feat["safe_haven_ratio"]  = _rank_normalize(
            tlt_r.pct_change(20) - shy_r.pct_change(20)
        )                                                           # 장기채 선호도

    # ── 변동성 체제 ───────────────────────────────────────────────────────────
    vix  = _safe_close(prices, "^VIX")
    vixm = _safe_close(prices, "VIXM")

    if vix is not None:
        vix_r = vix.reindex(idx).ffill()
        feat["vix_level"]      = vix_r
        feat["vix_pct"]        = _rank_normalize(-vix_r)           # VIX 역백분위 (낮을수록 낙관)
        feat["vix_mom_5d"]     = vix_r.pct_change(5)               # VIX 5일 변화율
        feat["vix_above_30"]   = (vix_r > 30).astype(float)        # 고변동성 체제
        feat["vix_above_20"]   = (vix_r > 20).astype(float)
    if vix is not None and vixm is not None:
        vixm_r = vixm.reindex(idx).ffill()
        # VIX 텀스트럭처: 중기/단기 > 1 = 콘탱고(정상), < 1 = 백워데이션(패닉)
        vix_vs_vixm = vixm_r.pct_change() / vix_r.pct_change().replace(0, np.nan)
        feat["vix_term_contango"] = _rank_normalize(vixm_r / vix_r.replace(0, np.nan))

    # ── 안전자산 수요 ─────────────────────────────────────────────────────────
    gld = _safe_close(prices, "GLD")
    uup = _safe_close(prices, "UUP")
    tip = _safe_close(prices, "TIP")

    if gld is not None:
        gld_r = gld.reindex(idx).ffill()
        feat["gold_mom_20d"]  = gld_r.pct_change(20)               # 금 20일 모멘텀
        feat["gold_mom_60d"]  = gld_r.pct_change(60)               # 금 60일 모멘텀
        feat["gold_pct"]      = _rank_normalize(gld_r.pct_change(60))
    if uup is not None:
        uup_r = uup.reindex(idx).ffill()
        feat["dollar_mom_20d"] = uup_r.pct_change(20)              # 달러 모멘텀
        feat["dollar_pct"]     = _rank_normalize(uup_r.pct_change(60))
        feat["dollar_strength"] = _rank_normalize(uup_r)           # 달러 절대 강도
    if tip is not None and ief is not None:
        tip_r = tip.reindex(idx).ffill()
        real_rate_proxy = tip_r.pct_change(20) - ief_r.pct_change(20)
        feat["real_rate_proxy"] = _rank_normalize(real_rate_proxy)  # 실질금리 방향

    # ── 원자재·경기 ───────────────────────────────────────────────────────────
    uso  = _safe_close(prices, "USO")
    cper = _safe_close(prices, "CPER")

    if uso is not None:
        uso_r = uso.reindex(idx).ffill()
        feat["oil_mom_20d"] = uso_r.pct_change(20)                 # 원유 모멘텀
        feat["oil_pct"]     = _rank_normalize(uso_r.pct_change(60))
    if cper is not None:
        cper_r = cper.reindex(idx).ffill()
        feat["copper_mom_20d"] = cper_r.pct_change(20)             # 구리 모멘텀 (경기 선행)
        feat["copper_pct"]     = _rank_normalize(cper_r.pct_change(60))

    # ── 글로벌 리스크 ─────────────────────────────────────────────────────────
    eem  = _safe_close(prices, "EEM")
    acwi = _safe_close(prices, "ACWI")
    spy  = _safe_close(prices, "SPY")

    if eem is not None and spy is not None:
        eem_r = eem.reindex(idx).ffill()
        spy_r = spy.reindex(idx).ffill()
        em_vs_us = eem_r.pct_change(60) - spy_r.pct_change(60)
        feat["em_vs_us_60d"] = em_vs_us                            # EM 상대 강도
        feat["em_vs_us_pct"] = _rank_normalize(em_vs_us)
    if acwi is not None:
        acwi_r = acwi.reindex(idx).ffill()
        feat["acwi_mom_20d"] = acwi_r.pct_change(20)              # 글로벌 모멘텀
        feat["acwi_pct"]     = _rank_normalize(acwi_r.pct_change(60))

    # ── 리스크온/오프 합성 지표 ───────────────────────────────────────────────
    # 여러 신호를 평균해 0(완전 리스크오프)~100(완전 리스크온) 범위로 통합
    risk_components: list[pd.Series] = []
    if "credit_spread_hy"  in feat: risk_components.append(feat["credit_spread_hy"])
    if "vix_pct"           in feat: risk_components.append(feat["vix_pct"])
    if "yield_curve_pct"   in feat: risk_components.append(feat["yield_curve_pct"])
    if "gold_pct"          in feat: risk_components.append(100 - feat["gold_pct"])  # 금↑ = 리스크오프
    if "dollar_strength"   in feat: risk_components.append(100 - feat["dollar_strength"])  # 달러↑ = 리스크오프
    if "acwi_pct"          in feat: risk_components.append(feat["acwi_pct"])
    if risk_components:
        feat["risk_on_composite"] = pd.concat(risk_components, axis=1).mean(axis=1)

    # ── 최종 정리 ─────────────────────────────────────────────────────────────
    feat = feat.ffill(limit=5)   # 공휴일 등으로 빠진 날 최대 5일 앞으로 채우기
    return feat


# ── 오늘 값 빠른 조회 ────────────────────────────────────────────────────────

def get_macro_today() -> dict:
    """오늘 매크로 피처값 딕셔너리 반환 (캐시 1시간).

    실패 시 빈 딕셔너리 반환 (호출자가 graceful fallback 처리).
    """
    cache_key = "macro_today"
    cached = _load(cache_key, ttl_hours=1)
    if cached is not None and not cached.empty:
        row = cached.iloc[-1]
        return row.dropna().to_dict()

    try:
        df = build_macro_features(days=300)
        if df.empty:
            return {}
        _save(cache_key, df.tail(1))
        return df.iloc[-1].dropna().to_dict()
    except Exception as e:
        logger.warning("get_macro_today 실패: %s", e)
        return {}
