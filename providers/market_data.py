#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""providers/market_data.py — 바벨 전략의 데이터 수집층.

barbell_strategy.py(2200줄 god-module)에서 잘라낸 데이터/상태 접근 함수 모음.
  - 외부 금융 피드 조회: QQQ/RSI/VIX/F&G/200MA/환율/포트폴리오 평가
  - 상태파일 R/W: 레버리지 포지션, 낙폭 앵커, 직전 정상가 캐시
  - 인-프로세스 히스토리 캐시 (yfinance rate-limit 보호)

설계 불변식(절대 원칙):
  - 이 모듈은 barbell_strategy 의 어떤 것도 import 하지 않는다(순환참조 금지).
    전략/리포트 로직은 barbell_strategy 에 남고, 그쪽이 이 모듈을 재export 한다.
  - 동작 100% 보존 — 라이브 금융 봇이므로 함수 본문/상수/폴백을 변경하지 않는다.
"""

import os
import json
import time
import logging
from datetime import datetime, timezone, timedelta

import numpy as np
import requests

try:
    import yfinance as yf
except ImportError:
    yf = None

import store    # SQLite 통합 저장소 (설정 블롭 권위 사본 + 파일 미러)
import safe_io  # 교차 프로세스 쓰기 락 (상태파일 read-modify-write 직렬화)

# portfolio_snapshot 경로 단일 소스 — portfolio_universe(STOCK_REPORT_PROJECT_DIR env 반영).
from portfolio_universe import PORTFOLIO_SNAPSHOT_PATH as PORTFOLIO_PATH

logger = logging.getLogger(__name__)

# 레버리지 상태파일 경로 — barbell_strategy.py 와 동일 위치(레포 루트) 유지.
# __file__ 은 providers/ 하위이므로 부모 디렉터리(레포 루트)로 한 단계 올라간다.
LEVERAGE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "leverage_state.json"
)

# ── 기본값 (실시간 로드 실패 시 fallback) ───────────────────────────────
SGOV_SHARES_DEFAULT = 10.0
QQQI_SHARES_DEFAULT = 35.2987
SGOV_FALLBACK_PRICE = 100.67   # 직전 정상가 캐시도 없을 때의 최후 상수 (drift 있음 — 캐시 우선)
QQQI_FALLBACK_PRICE = 57.22
QQQI_ANNUAL_YIELD = 0.12      # QQQI 연간 배당수익률 ~12% (추산)

PRICE_STALE_MAX_DAYS = float(os.getenv("BARBELL_PRICE_STALE_DAYS", "4"))   # 최신 종가가 이보다 오래되면 stale (주말+공휴일 여유)


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
    # overseas_general + overseas_fractional → dict keyed by ticker
    merged: dict[str, dict] = {}
    for section, key in [("overseas_general", "holdings_usd"),
                          ("overseas_fractional", "holdings")]:
        for h in snap.get(section, {}).get(key, []):
            ticker = h.get("ticker")
            if ticker in merged:
                e = merged[ticker]
                e["shares"] = (e.get("shares") or 0) + (h.get("shares") or 0)
                e["value_usd"] = (e.get("value_usd") or 0) + (h.get("value_usd") or 0)
            else:
                merged[ticker] = {
                    "ticker": ticker,
                    "name": h.get("name"),
                    "shares": h.get("shares"),
                    "value_usd": h.get("value_usd"),
                    "return_pct": h.get("return_pct"),
                }
    for h in sorted(merged.values(), key=lambda x: x.get("value_usd", 0) or 0, reverse=True):
        details.append(h)

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

ANCHOR_FILE = os.path.expanduser("~/.cache/barbell_anchor.json")
ANCHOR_RESET_RECOVERY = 0.95   # 앵커 대비 -5% 이내 회복 시 롤링 고점으로 복귀

# yfinance 히스토리 인-프로세스 캐시 — fetch_qqq_data/fetch_rsi/fetch_ma200이
# 같은 QQQ 1y 데이터를 각자 다운로드하던 중복 제거 (rate-limit 보호)
_HIST_CACHE: dict[tuple, tuple[float, "object"]] = {}
_HIST_CACHE_TTL_S = 300


def _history_cached(symbol: str, period: str = "1y"):
    """yf.Ticker(symbol).history(period) 5분 캐시. 실패 시 빈 DataFrame."""
    import pandas as pd
    key = (symbol, period)
    hit = _HIST_CACHE.get(key)
    if hit and time.time() - hit[0] < _HIST_CACHE_TTL_S:
        return hit[1]
    try:
        hist = yf.Ticker(symbol).history(period=period)
    except Exception:
        hist = pd.DataFrame()
    if hist is not None and not hist.empty:
        now = time.time()
        # 만료 엔트리 정리 — 인-프로세스 캐시 무한 증가 방지 (상시 봇 프로세스)
        for k in [k for k, v in _HIST_CACHE.items() if now - v[0] >= _HIST_CACHE_TTL_S]:
            _HIST_CACHE.pop(k, None)
        _HIST_CACHE[key] = (now, hist)
    return hist if hist is not None else pd.DataFrame()


def fetch_kospi_stats(since_date: str | None = None, symbol: str = "^KS11") -> dict:
    """KOSPI(기본 ^KS11) 누적수익률(%)·MDD(양수 크기) — since_date~오늘 구간.

    모의 포트폴리오의 '지수 대비 아웃퍼폼 + MDD≤지수' 목표 가시화용.
    실패/데이터부족 시 {"return_pct": None, "mdd": None}.
    """
    import pandas as pd
    hist = _history_cached(symbol, "1y")
    if hist is None or getattr(hist, "empty", True) or "Close" not in getattr(hist, "columns", []):
        return {"return_pct": None, "mdd": None}
    close = hist["Close"].dropna()
    if since_date:
        try:
            cutoff = pd.Timestamp(since_date).date()
            close = close[[d.date() >= cutoff for d in close.index]]
        except Exception:
            pass
    if len(close) < 2:
        return {"return_pct": None, "mdd": None}
    ret = (float(close.iloc[-1]) / float(close.iloc[0]) - 1.0) * 100.0
    peak, mdd = -1e18, 0.0
    for v in close.values:
        v = float(v)
        if v > peak:
            peak = v
        if peak > 0:
            dd = (peak - v) / peak
            if dd > mdd:
                mdd = dd
    return {"return_pct": round(ret, 2), "mdd": round(mdd, 4)}


_LAST_PRICES_FILE = os.path.expanduser("~/.cache/barbell_last_prices.json")


def _load_last_prices() -> dict:
    """직전 정상 조회가격 캐시 — yfinance 실패 시 하드코딩 대신 최근 실값으로 폴백."""
    try:
        with open(_LAST_PRICES_FILE, encoding="utf-8") as f:
            d = json.load(f)
        return {k: float(v) for k, v in d.items() if isinstance(v, (int, float)) and v > 0}
    except Exception:
        return {}


def _save_last_prices(prices: dict) -> None:
    """이번에 성공 조회한 가격을 직전 정상가 캐시에 병합 저장 (best-effort)."""
    try:
        merged = _load_last_prices()
        merged.update({k: float(v) for k, v in prices.items() if v and v > 0})
        safe_io.atomic_write_json(_LAST_PRICES_FILE, merged)
    except Exception as e:
        logger.debug("직전 가격 캐시 저장 실패(무시): %s", e)


def _load_drawdown_anchor() -> float:
    try:
        data = store.load_doc("barbell_anchor", ANCHOR_FILE, {})
        return _safe_float((data or {}).get("anchor_high"))
    except Exception:
        return 0.0


def _update_drawdown_anchor(high_52w: float, current: float) -> float:
    """낙폭 기준 고점 앵커 관리 (Phase 드리프트 방지).

    롤링 52주 고점만 쓰면 장기 하락장에서 고점 자체가 내려와
    시장 회복 없이 낙폭이 0%로 수렴 → Phase가 기계적으로 풀린다.
    → 앵커는 단조 증가시키고, 가격이 앵커 -5% 이내로 실제 회복했을 때만
      롤링 52주 고점으로 리셋한다.
    """
    # 봇(5분 루프)+크론이 매 run()마다 호출 → load-decide-save 를 교차 프로세스 락으로 직렬화
    # (lost update 방지). 앵커 리셋(회복 시 롤링 고점 복귀)은 의도된 설계이므로 단조성은 그대로 둔다.
    try:
        with safe_io.file_write_lock(ANCHOR_FILE):
            anchor = max(high_52w, _load_drawdown_anchor())
            if anchor > 0 and current >= anchor * ANCHOR_RESET_RECOVERY:
                anchor = high_52w
            store.save_doc("barbell_anchor", {
                "anchor_high": round(anchor, 2),
                "updated": datetime.now().isoformat(),
            }, ANCHOR_FILE)
    except Exception:
        # 락/저장 실패해도 앵커 계산값은 반환 (Phase 분류 진행)
        anchor = max(high_52w, _load_drawdown_anchor())
        if anchor > 0 and current >= anchor * ANCHOR_RESET_RECOVERY:
            anchor = high_52w
    return anchor


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


def _last_completed_close(hist, now_utc_date):
    """일봉 프레임에서 **확정 종가** 선택 (순수) — 마지막 봉이 오늘(UTC·진행 중)이면 직전 봉.

    주말/휴장엔 마지막 봉 자체가 확정 종가라 그대로 쓴다.
    """
    if hist is None or getattr(hist, "empty", True) or "Close" not in hist.columns:
        return None
    c = hist["Close"].dropna()
    if len(c) == 0:
        return None
    last_date = c.index[-1].date()
    if last_date >= now_utc_date and len(c) >= 2:      # 오늘 봉 = 아직 형성 중 → 직전 확정
        return float(c.iloc[-2])
    return float(c.iloc[-1])


def fetch_exchange_rate_close() -> float:
    """USD/KRW **최근 확정 종가** 환율 — 주식 모으기·소수점 주문서 기준 (장중 변동 배제).

    실시간(진행 중) 봉을 제외한 직전 영업일 종가라 하루 동안 값이 고정된다.
    실패 시 실시간 환율로 폴백.
    """
    try:
        from datetime import datetime as _dt, timezone as _tz
        hist = yf.Ticker("USDKRW=X").history(period="7d")
        rate = _last_completed_close(hist, _dt.now(_tz.utc).date())
        if rate and 900 < rate < 2500:
            return round(rate, 1)
    except Exception:
        pass
    return fetch_exchange_rate()


def fetch_qqq_data() -> dict:
    """QQQ 현재가, 52주 고점, 낙폭, 모멘텀 계산."""
    try:
        hist = _history_cached("QQQ", "1y")
        if hist.empty:
            return {}
        valid = hist.dropna(subset=["High", "Low", "Close"])
        valid = valid[(valid["High"] > 0) & (valid["Low"] > 0) & (valid["Close"] > 0)]
        if valid.empty:
            logger.warning("QQQ 데이터 오류 — 유효한 OHLC 행 없음")
            return {}
        closes_s = valid["Close"]
        current = _safe_float(closes_s.iloc[-1])
        rt = _realtime_current("QQQ")              # 실시간 스트림 신선시 장중 현재가로 오버레이
        if rt and rt > 0:
            current = round(float(rt), 2)
        # 종가 기준 고저 — 장중 고가(High) 대비 종가 비교는 낙폭을 체계적으로 과대측정
        high_52w = max(_safe_float(closes_s.max()), current)   # 실시간 신고가 반영
        low_52w = _safe_float(closes_s.min())
        if current <= 0 or high_52w <= 0 or low_52w <= 0 or low_52w > high_52w:
            logger.warning("QQQ 데이터 비정상 — current=%s high_52w=%s low_52w=%s", current, high_52w, low_52w)
            return {}
        # 낙폭은 롤링 52주 고점이 아닌 앵커 고점 대비 (장기 베어 Phase 드리프트 방지)
        anchor = _update_drawdown_anchor(high_52w, current)
        drawdown = (current - anchor) / anchor * 100 if anchor > 0 else 0.0

        mom_1m = mom_3m = 0.0
        if len(valid) >= 21:
            p1m = _safe_float(valid["Close"].iloc[-21])
            mom_1m = (current - p1m) / p1m * 100 if p1m > 0 else 0
        if len(valid) >= 63:
            p3m = _safe_float(valid["Close"].iloc[-63])
            mom_3m = (current - p3m) / p3m * 100 if p3m > 0 else 0

        range_52w = high_52w - low_52w
        position_52w = (current - low_52w) / range_52w * 100 if range_52w > 0 else 50

        # 데이터 신선도 — 최신 종가가 너무 오래되면(피드 장애·rate limit) stale 플래그.
        # 묵은 가격으로 낙폭→Phase 를 잘못 분류(유령 Phase 5 / 진짜 낙폭 누락)하는 것을 막는다.
        age_days = 0
        try:
            last_ts = valid.index[-1]
            last_date = last_ts.date() if hasattr(last_ts, "date") else None
            if last_date is not None:
                age_days = (datetime.now().date() - last_date).days
        except Exception:
            age_days = 0
        stale = age_days > PRICE_STALE_MAX_DAYS
        if stale:
            logger.warning("QQQ 가격 stale — 최신 종가 %d일 전 (>%s일). Phase 에스컬레이션 보류 권고",
                           age_days, PRICE_STALE_MAX_DAYS)

        return {
            "current": round(current, 2),
            "high_52w": round(high_52w, 2),
            "low_52w": round(low_52w, 2),
            "anchor_high": round(anchor, 2),
            "drawdown_pct": round(drawdown, 2),
            "position_52w_pct": round(position_52w, 1),
            "mom_1m_pct": round(mom_1m, 2),
            "mom_3m_pct": round(mom_3m, 2),
            "data_age_days": age_days,
            "stale": stale,
        }
    except Exception as e:
        logger.warning(f"QQQ 데이터 오류: {e}")
        return {}


def _realized_vol_annual(symbol: str = "QQQ", window: int = 20) -> float:
    """최근 window 거래일 일간수익률의 연환산 변동성. 실패 시 0.0(=캡 미적용)."""
    try:
        hist = _history_cached(symbol, "1y")
        closes = hist["Close"].dropna()
        if len(closes) < window + 1:
            return 0.0
        rets = closes.pct_change().dropna().iloc[-window:]
        return float(rets.std() * (252 ** 0.5))
    except Exception:
        return 0.0


def fetch_rsi(ticker_sym: str, period: int = 14) -> float:
    """RSI 계산 (1y 공유 캐시 사용 — QQQ는 fetch_qqq_data와 다운로드 공유)."""
    try:
        hist = _history_cached(ticker_sym, "1y")
        if len(hist) < period + 1:
            return 50.0
        delta = hist["Close"].diff().dropna()
        # Wilder 평활 (ewm alpha=1/N) — 단순이동평균(Cutler) 방식은 70/75 관행 임계값과 어긋남
        gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
        loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
        rs = gain / loss.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs))
        return round(_safe_float(rsi.iloc[-1], 50.0), 1)
    except Exception:
        return 50.0


def fetch_vix() -> float:
    """VIX 현재값."""
    try:
        hist = _history_cached("^VIX", "5d")
        return round(_safe_float(hist["Close"].iloc[-1]), 2) if not hist.empty else 20.0
    except Exception:
        return 20.0


def fetch_fear_greed() -> dict:
    """CNN Fear & Greed Index 조회. 실패 시 자체 proxy로 fallback."""
    from datetime import timedelta

    def _proxy_score() -> float:
        try:
            from ml.data_pipeline import get_fg_proxy_score
            return get_fg_proxy_score()
        except Exception:
            return -1.0

    date_str = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    url = f"https://production.dataviz.cnn.io/index/fearandgreed/graphdata/{date_str}"
    try:
        resp = requests.get(url, timeout=10, headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://www.cnn.com/",
            "Accept": "application/json",
        })
        resp.raise_for_status()
        fg = resp.json().get("fear_and_greed", {})
        return {
            "score":       round(float(fg.get("score", 50)), 1),
            "rating":      fg.get("rating", "neutral"),
            "prev_close":  round(float(fg.get("previous_close", 50)), 1),
            "prev_week":   round(float(fg.get("previous_1_week", 50)), 1),
            "prev_month":  round(float(fg.get("previous_1_month", 50)), 1),
            "proxy_score": round(_proxy_score(), 1),
            "cnn_ok":      True,
        }
    except Exception:
        proxy = _proxy_score()
        score = proxy if proxy >= 0 else 50.0
        return {
            "score":       score,
            "rating":      "neutral",
            "prev_close":  score,
            "prev_week":   score,
            "prev_month":  score,
            "proxy_score": round(proxy, 1),
            "cnn_ok":      False,
        }


def fetch_ma200(ticker_sym: str) -> dict:
    """현재가 vs 200일 MA (1y 공유 캐시 사용)."""
    try:
        hist = _history_cached(ticker_sym, "1y")
        if len(hist) < 50:
            return {"above_ma200": True, "gap_pct": 0.0}
        n = min(200, len(hist))
        ma = _safe_float(hist["Close"].rolling(n).mean().iloc[-1])
        current = _safe_float(hist["Close"].iloc[-1])
        gap = (current - ma) / ma * 100 if ma > 0 else 0
        return {"above_ma200": current > ma, "ma200": round(ma, 2), "current": round(current, 2), "gap_pct": round(gap, 2)}
    except Exception:
        return {"above_ma200": True, "gap_pct": 0.0}


def _realtime_spot_overlay(tickers: list) -> dict:
    """실시간 캐시에서 신선한 스팟가만 추출(REALTIME_ENABLED·신선시). 예외 무발 → {ticker: price}.

    가산 오버레이용 — yfinance/스냅샷 폴백을 대체하지 않고, 신선한 종목만 최신가로 갱신.
    """
    out: dict = {}
    try:
        from providers import realtime_quotes
        if not realtime_quotes.enabled():
            return out
        stale = int(os.getenv("REALTIME_SPOT_STALE_S", "60"))
        for t in tickers:
            p = realtime_quotes.get_price(str(t).split(".")[0], max_age_s=stale)
            if p and p > 0:
                out[t] = float(p)
    except Exception:
        pass
    return out


def _realtime_current(symbol: str):
    """실시간 캐시 현재가(스트림 가동·신선시). 비활성/없음/stale → None. 예외 무발."""
    try:
        from providers import realtime_quotes
        if realtime_quotes.enabled():
            return realtime_quotes.get_price(symbol)
    except Exception:
        pass
    return None


def freshness_note(fetched_ts, *, now: float | None = None) -> str:
    """대시보드/결정 명령용 신선도 한 줄. fetched_ts = 시장데이터 조회 epoch.

    실시간 스트림 가동 중이면 'ON(N초)' 표기, 아니면 yfinance. 데이터가 언제 기준인지 일관 노출.
    """
    now = time.time() if now is None else now
    if fetched_ts:
        try:
            kst = datetime.fromtimestamp(float(fetched_ts), tz=timezone(timedelta(hours=9)))
            when = kst.strftime("%H:%M:%S")
            age = max(0.0, now - float(fetched_ts))
            age_str = f"{int(age)}초 전" if age < 90 else f"{int(age // 60)}분 전"
        except (ValueError, TypeError, OSError):
            when, age_str = "?", "?"
    else:
        when, age_str = "?", "?"
    src = "yfinance"
    try:
        import providers.realtime_quotes as _rq
        if _rq.enabled():
            hb = _rq.heartbeat_age()
            src = f"실시간 ON({int(hb)}초)" if (hb is not None and hb <= _rq.HEARTBEAT_STALE_S) else "실시간 대기"
    except Exception:
        pass
    return f"🕒 {when} KST ({age_str}) · 시세 {src}"


def fetch_portfolio_value() -> dict:
    """
    portfolio_snapshot.json 보유 수량 × 실시간 가격(실시간 캐시 우선·yfinance 폴백) → 포트폴리오 총액.
    QLD/TQQQ leverage_state.json 포지션도 포함.
    """
    # --- 보유 수량 집계 ---
    holdings: dict[str, float] = {}
    holdings_detail: list[dict] = []
    cost_usd_by_ticker: dict[str, float] = {}
    domestic_cost_krw = 0.0
    domestic_value_krw = 0.0
    domestic_pnl_krw = 0.0
    try:
        with open(PORTFOLIO_PATH) as f:
            snap = json.load(f)
        holdings_detail = _holding_details_from_snapshot(snap)
        domestic_summary = snap.get("domestic", {}).get("summary", {})
        domestic_cost_krw = _safe_float(domestic_summary.get("total_cost_krw"))
        domestic_value_krw = _safe_float(domestic_summary.get("total_value_krw"))
        domestic_pnl_krw = _safe_float(domestic_summary.get("total_pnl_krw"), domestic_value_krw - domestic_cost_krw)
        for section, key in [("overseas_general", "holdings_usd"),
                             ("overseas_fractional", "holdings")]:
            for h in snap.get(section, {}).get(key, []):
                if not isinstance(h, dict) or "ticker" not in h:
                    continue   # 손상/수기편집 스냅샷 방어
                t = h["ticker"]
                shares = float(h.get("shares", 0) or 0)
                holdings[t] = holdings.get(t, 0.0) + shares
                cost = _safe_float(h.get("cost_usd"))
                if cost <= 0 and _safe_float(h.get("avg_price_usd")) > 0:
                    cost = shares * _safe_float(h.get("avg_price_usd"))
                if cost > 0:
                    cost_usd_by_ticker[t] = cost_usd_by_ticker.get(t, 0.0) + cost
    except Exception as e:
        logger.warning(f"portfolio_snapshot.json 로드 실패: {e}")

    # 레버리지 포지션 추가
    leverage = load_leverage_state()
    for ticker, pos in leverage.items():
        sh = float(pos.get("shares", 0))
        if sh > 0:
            holdings[ticker] = holdings.get(ticker, 0.0) + sh
            avg = _safe_float(pos.get("avg_price_usd"))
            if avg > 0:
                cost_usd_by_ticker[ticker] = cost_usd_by_ticker.get(ticker, 0.0) + sh * avg

    if not holdings:
        # 스냅샷이 비었을 때의 최후 폴백 — 값이 실제 포트폴리오와 다르므로 'data_missing' 으로
        # 명시 플래그(리포트가 추정치임을 표시·신뢰하지 않도록). 키는 유지해 다운스트림 KeyError 방지.
        logger.error("보유 수량 데이터 없음 — 폴백 추정치 사용(실제와 불일치). portfolio_snapshot.json 확인 필요")
        return {"total_usd": 7940.0, "sgov_usd": 1006.7, "qqqi_usd": 2019.77, "qqqi_shares": 35.2987,
                "prices": {}, "holdings": {}, "holdings_detail": holdings_detail, "data_missing": True}

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
            for section, key in [("overseas_general", "holdings_usd"),
                                 ("overseas_fractional", "holdings")]:
                for h in snap.get(section, {}).get(key, []):
                    t = h["ticker"]
                    if t not in prices and "current_price_usd" in h:
                        prices[t] = float(h["current_price_usd"])
        except Exception:
            pass

    # 그래도 가격 없는 종목 → 직전 정상가 캐시 → 평단가 순 fallback (가짜 하드코딩 대신 최근 실값 우선)
    last_prices = _load_last_prices()
    for t in tickers:
        if prices.get(t, 0) > 0:
            continue
        if last_prices.get(t, 0) > 0:
            prices[t] = float(last_prices[t])
            logger.warning("%s 가격 조회 실패 — 직전 정상가 $%.2f 로 대체", t, prices[t])
            continue
        shares = float(holdings.get(t, 0) or 0)
        avg = cost_usd_by_ticker.get(t, 0.0) / shares if shares > 0 else 0.0
        if avg > 0:
            prices[t] = round(avg, 2)
            logger.warning("%s 가격 조회 실패 — 평단가 $%.2f 로 대체 평가", t, avg)
        else:
            logger.warning("%s 가격 조회 실패 + 평단가 없음 — $0 평가 (총액 과소측정 가능)", t)

    # 실시간 캐시 오버레이 — 활성·신선시 yfinance/스냅샷보다 최신 스팟으로 갱신(가산·폴백 보존)
    for t, rt in _realtime_spot_overlay(tickers).items():
        prices[t] = rt

    # 이번에 성공 조회한 가격을 직전 정상가 캐시에 저장 (다음 실패 시 fallback 소스)
    _save_last_prices({t: prices[t] for t in tickers if prices.get(t, 0) > 0})

    # float 캐스팅 — 손상된 스냅샷(문자열 shares 등)에서도 산술 오류/오평가 방지
    total_usd = sum(float(holdings.get(t, 0) or 0) * float(prices.get(t, 0) or 0) for t in tickers)
    cost_usd = sum(cost_usd_by_ticker.values())
    pnl_usd = total_usd - cost_usd if cost_usd > 0 else 0.0
    return_pct = pnl_usd / cost_usd * 100 if cost_usd > 0 else 0.0
    # SGOV/QQQI 평가 — 조회 실패 시 직전 정상가 → 최후의 하드코딩 상수
    sgov_px = prices.get("SGOV") or last_prices.get("SGOV") or SGOV_FALLBACK_PRICE
    if "SGOV" in holdings and not (prices.get("SGOV", 0) > 0):
        logger.warning("SGOV 가격 조회 실패 — 대체가 $%.2f 사용", sgov_px)
    # 티커가 holdings 에 없으면(전량 청산) 0 — 하드코딩 기본수량(SGOV_SHARES_DEFAULT 등)을 쓰면
    # 청산 후에도 유령 평가액이 생겨 Phase 4/5 청산 국면서 '없는 SGOV 매도' 반복권고가 난다(감사 확정).
    # 하드코딩 기본은 위 data_missing(빈 스냅샷) 폴백에만 사용.
    sgov_usd = float(holdings.get("SGOV", 0) or 0) * float(sgov_px)
    qqqi_shares = float(holdings.get("QQQI", 0) or 0)
    qqqi_px = prices.get("QQQI") or last_prices.get("QQQI") or QQQI_FALLBACK_PRICE
    if "QQQI" in holdings and not (prices.get("QQQI", 0) > 0):
        logger.warning("QQQI 가격 조회 실패 — 대체가 $%.2f 사용", qqqi_px)
    qqqi_usd = qqqi_shares * float(qqqi_px)

    return {
        "total_usd": round(total_usd, 2),
        "cost_usd": round(cost_usd, 2),
        "pnl_usd": round(pnl_usd, 2),
        "return_pct": round(return_pct, 2),
        "domestic_cost_krw": round(domestic_cost_krw),
        "domestic_value_krw": round(domestic_value_krw),
        "domestic_pnl_krw": round(domestic_pnl_krw),
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
    data = store.load_doc("leverage_state", LEVERAGE_FILE, None)
    if not isinstance(data, dict):
        return default
    for k, v in default.items():
        if k not in data:
            data[k] = v
    return data


def save_leverage_state(state: dict):
    """QLD/TQQQ 보유 현황 저장 (store 권위 + 파일 미러)."""
    store.save_doc("leverage_state", state, LEVERAGE_FILE)


def update_leverage_position(ticker: str, shares: float, avg_price: float):
    """CLI --update-leverage 에서 호출: QLD/TQQQ 포지션 업데이트.

    read-modify-write 를 교차 프로세스 락으로 직렬화 — 동시 갱신 시 lost update 방지.
    """
    with safe_io.file_write_lock(LEVERAGE_FILE):
        state = load_leverage_state()
        state[ticker.upper()] = {
            "shares": round(shares, 4),
            "avg_price_usd": round(avg_price, 2),
            "updated": datetime.now().strftime("%Y-%m-%d"),
        }
        save_leverage_state(state)
    print(f"✅ {ticker.upper()} 포지션 업데이트: {shares}주 @ ${avg_price:.2f}")
