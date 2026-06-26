#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""providers/earnings_data.py — 어닝·컨센서스·밸류에이션 데이터층 (Phase 1 / §G).

데이터 실현성(2026 검증):
  US  = yfinance 단독 무료로 전체 확보 — earnings_dates(과거 서프라이즈), earnings_estimate
        (포워드 컨센서스 + 애널리스트수), eps_revisions(★리비전 모멘텀 — 문서화된 실제 팩터),
        analyst_price_targets, .info 밸류에이션(PER/PBR/PSR/ROE/EPS/배당), dividends(배당성장 CAGR).
  KR(.KS/.KQ) = **열화모드** — yfinance .info 밸류에이션·dividends 만 신뢰. 포워드 컨센서스/리비전은
        무료 API 부재(추후 FnGuide 스크레이프 보강 예정). 없으면 None — 호출측은 밸류에이션만 표시.

원칙: 전부 결측 graceful(None) + 파일 캐시(slow-changing → 12h). 무네트워크 테스트는 _ticker 모킹.
yfinance 의 future earnings_dates 반환은 알려진 불안정 → 모두 try/except 로 감쌈.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_CACHE_DIR = Path(os.path.expanduser("~/reports/ml-cache"))
_CACHE_TTL_H = 12.0


# ── 캐시 (JSON, dict/list 페이로드) ─────────────────────────────────────────────

def _cache_path(key: str) -> Path:
    safe = "".join(c if c.isalnum() else "_" for c in key)[:60]
    return _CACHE_DIR / f"earnings_{safe}.json"


def _cache_get(key: str, ttl_h: float = _CACHE_TTL_H):
    try:
        p = _cache_path(key)
        if not p.exists():
            return None
        if (time.time() - p.stat().st_mtime) > ttl_h * 3600:
            return None
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _cache_put(key: str, obj) -> None:
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        try:
            from ml._safe_cache import harden_cache_dir
            harden_cache_dir(_CACHE_DIR)
        except Exception:
            pass
        p = _cache_path(key)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)
    except Exception as e:
        logger.debug("earnings 캐시 저장 실패 %s: %s", key, e)


# ── 유틸 ────────────────────────────────────────────────────────────────────────

def is_kr(ticker: str) -> bool:
    t = (ticker or "").upper()
    return t.endswith(".KS") or t.endswith(".KQ")


def _ticker(symbol: str):
    """yfinance Ticker (테스트는 이 함수를 monkeypatch)."""
    import yfinance as yf
    return yf.Ticker(symbol)


def _f(v):
    """None/NaN/비수치 → None, 그 외 float."""
    try:
        if v is None:
            return None
        f = float(v)
        if f != f or f in (float("inf"), float("-inf")):   # NaN/inf
            return None
        return f
    except (TypeError, ValueError):
        return None


# ── 밸류에이션·품질 (G1, 결정적) ────────────────────────────────────────────────

def _dividend_cagr(divs, years: int):
    """배당성장 CAGR — 마지막 배당일 기준 TTM vs years년 전 TTM(결정적·now 비의존)."""
    try:
        import pandas as pd  # noqa: F401
        if divs is None or len(divs) < 2:
            return None
        last = divs.index.max()

        def _ttm(end):
            start = end - __import__("pandas").DateOffset(years=1)
            return float(divs[(divs.index > start) & (divs.index <= end)].sum())

        now_ttm = _ttm(last)
        past_ttm = _ttm(last - __import__("pandas").DateOffset(years=years))
        if past_ttm <= 0 or now_ttm <= 0:
            return None
        return round((now_ttm / past_ttm) ** (1.0 / years) - 1.0, 4)
    except Exception:
        return None


def valuation_metrics(ticker: str, *, _t=None) -> dict:
    """PER·forwardPE·PBR·PSR·ROE·EPS(ttm/fwd)·배당률·payout·배당성장(1y/3y). 결측 None.

    US/KR 공통(yfinance .info + dividends). KR 은 forward 계열이 대체로 None(열화모드).
    """
    out = {k: None for k in ("per", "forward_pe", "pbr", "psr", "roe", "eps_ttm", "eps_fwd",
                             "div_yield", "div_yield_5y_avg", "payout", "div_growth_1y", "div_growth_3y")}
    out["market_type"] = "kr" if is_kr(ticker) else "us"
    try:
        t = _t or _ticker(ticker)
        info = {}
        try:
            info = t.info or {}
        except Exception as e:
            logger.debug("info 조회 실패 %s: %s", ticker, e)
            info = {}
        out["per"] = _f(info.get("trailingPE"))
        out["forward_pe"] = _f(info.get("forwardPE"))
        out["pbr"] = _f(info.get("priceToBook"))
        out["psr"] = _f(info.get("priceToSalesTrailing12Months"))
        out["roe"] = _f(info.get("returnOnEquity"))
        out["eps_ttm"] = _f(info.get("trailingEps"))
        out["eps_fwd"] = _f(info.get("forwardEps"))
        dy = _f(info.get("dividendYield"))
        # yfinance 는 버전에 따라 0.024 또는 2.4(%) 로 줌 — 1 초과면 %로 보고 환산
        if dy is not None and dy > 1.0:
            dy = dy / 100.0
        out["div_yield"] = dy
        out["div_yield_5y_avg"] = _f(info.get("fiveYearAvgDividendYield"))
        out["payout"] = _f(info.get("payoutRatio"))
        try:
            divs = t.dividends
            out["div_growth_1y"] = _dividend_cagr(divs, 1)
            out["div_growth_3y"] = _dividend_cagr(divs, 3)
        except Exception as e:
            logger.debug("배당 이력 실패 %s: %s", ticker, e)
    except Exception as e:
        logger.warning("밸류에이션 조회 실패 %s: %s", ticker, e)
    return out


# ── 과거 서프라이즈 (G2) ────────────────────────────────────────────────────────

def earnings_history(ticker: str, *, limit: int = 12, _t=None) -> list[dict]:
    """과거 실적 서프라이즈 [{date, eps_est, eps_actual, surprise_pct}] (최신순). 실패 시 []."""
    try:
        t = _t or _ticker(ticker)
        df = t.get_earnings_dates(limit=limit) if hasattr(t, "get_earnings_dates") else t.earnings_dates
        if df is None or len(df) == 0:
            return []
        rows = []
        for idx, r in df.iterrows():
            actual = _f(r.get("Reported EPS"))
            if actual is None:
                continue   # 미래/미보고 분기 제외(과거 실적만)
            est = _f(r.get("EPS Estimate"))
            surp = _f(r.get("Surprise(%)"))
            if surp is None and est not in (None, 0) and actual is not None:
                surp = round((actual - est) / abs(est) * 100.0, 2)
            try:
                dstr = idx.strftime("%Y-%m-%d")
            except Exception:
                dstr = str(idx)[:10]
            rows.append({"date": dstr, "eps_est": est, "eps_actual": actual, "surprise_pct": surp})
        return rows
    except Exception as e:
        logger.debug("어닝 이력 실패 %s: %s", ticker, e)
        return []


# ── 컨센서스·리비전 (G3 피처 소스, US 중심) ─────────────────────────────────────

def consensus(ticker: str, *, _t=None) -> dict:
    """포워드 컨센서스 + ★리비전 모멘텀 + 목표가. KR 은 대체로 None(열화모드)."""
    out = {k: None for k in ("eps_fwd_avg", "n_analysts", "rev_fwd_avg", "eps_rev_up_30d",
                             "eps_rev_down_30d", "revision_momentum", "target_mean", "target_upside_pct")}
    try:
        t = _t or _ticker(ticker)
        # 포워드 EPS 컨센서스(다음 분기 '+1q')
        try:
            ee = t.earnings_estimate
            if ee is not None and len(ee) and "+1q" in ee.index:
                out["eps_fwd_avg"] = _f(ee.loc["+1q"].get("avg"))
                out["n_analysts"] = _f(ee.loc["+1q"].get("numberOfAnalysts"))
        except Exception:
            pass
        try:
            re_ = t.revenue_estimate
            if re_ is not None and len(re_) and "+1q" in re_.index:
                out["rev_fwd_avg"] = _f(re_.loc["+1q"].get("avg"))
        except Exception:
            pass
        # ★리비전 모멘텀: 최근 30일 상향 − 하향 (다음 분기)
        try:
            rev = t.eps_revisions
            if rev is not None and len(rev) and "+1q" in rev.index:
                up = _f(rev.loc["+1q"].get("upLast30days")) or 0.0
                dn = _f(rev.loc["+1q"].get("downLast30days")) or 0.0
                out["eps_rev_up_30d"] = up
                out["eps_rev_down_30d"] = dn
                tot = up + dn
                out["revision_momentum"] = round((up - dn) / tot, 3) if tot > 0 else None
        except Exception:
            pass
        # 목표가
        try:
            apt = t.analyst_price_targets
            if isinstance(apt, dict):
                mean = _f(apt.get("mean"))
                cur = _f(apt.get("current"))
                out["target_mean"] = mean
                if mean and cur and cur > 0:
                    out["target_upside_pct"] = round((mean / cur - 1.0) * 100.0, 1)
        except Exception:
            pass
    except Exception as e:
        logger.debug("컨센서스 실패 %s: %s", ticker, e)
    return out


def next_earnings(ticker: str, *, _t=None, today: str | None = None) -> dict:
    """다음 실적일 + 잔여일수. {date, days_until} (없으면 {None, None}).

    today='YYYY-MM-DD' 주입 시 그 기준(테스트 결정성). 미주입 시 실제 오늘.
    """
    out = {"date": None, "days_until": None}
    try:
        from datetime import date as _date
        ref = _date.fromisoformat(today) if today else _date.today()
        t = _t or _ticker(ticker)
        cand = None
        try:
            cal = t.calendar
            if isinstance(cal, dict):
                ev = cal.get("Earnings Date")
                if isinstance(ev, (list, tuple)) and ev:
                    cand = ev[0]
                elif ev:
                    cand = ev
        except Exception:
            pass
        if cand is None:
            try:
                df = t.earnings_dates
                if df is not None and len(df):
                    future = [i for i in df.index if hasattr(i, "date") and i.date() >= ref]
                    if future:
                        cand = min(future)
            except Exception:
                pass
        if cand is not None:
            d = cand.date() if hasattr(cand, "date") else _date.fromisoformat(str(cand)[:10])
            out["date"] = d.isoformat()
            out["days_until"] = (d - ref).days
    except Exception as e:
        logger.debug("다음 실적일 실패 %s: %s", ticker, e)
    return out


# ── 통합 요약 (리포트·커맨드·스냅샷 공용) ───────────────────────────────────────

def summary(ticker: str, *, force: bool = False, today: str | None = None) -> dict:
    """밸류에이션 + 다음실적 + 직전 서프라이즈 + 컨센서스 요약 (12h 캐시; force=True 우회).

    스냅샷 크론은 force=True(신선), 리포트/커맨드는 캐시 사용.
    """
    key = f"summary_{ticker}"
    if not force:
        c = _cache_get(key)
        if c is not None:
            return c
    t = None
    try:
        t = _ticker(ticker)
    except Exception:
        t = None
    val = valuation_metrics(ticker, _t=t)
    hist = earnings_history(ticker, limit=8, _t=t)
    cons = consensus(ticker, _t=t)
    nxt = next_earnings(ticker, _t=t, today=today)
    last = hist[0] if hist else None
    out = {
        "ticker": ticker,
        "market_type": val.get("market_type"),
        "valuation": val,
        "next_earnings": nxt,
        "last_surprise": last,
        "consensus": cons,
        "degraded": bool(is_kr(ticker) and cons.get("eps_fwd_avg") is None),
    }
    _cache_put(key, out)
    return out
