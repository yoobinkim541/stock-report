"""providers/etf_data.py — ETF 전용 데이터층 (yfinance funds_data · graceful · 12h 캐시).

ETF 는 개별주와 다른 정보가 본질이다: PER/ROE/재무제표가 아니라 **프로필(운용사·AUM·
NAV·상장일)·보유 비중 Top10·운용보수·괴리율·배당**. 대시보드 종목분석이 ETF 를
감지하면 주식 섹션 대신 이 층의 데이터를 렌더한다 (표시 전용·주문 0).

원천: yfinance `Ticker.funds_data`(top_holdings·fund_overview·description·fund_operations)
+ `info`(totalAssets·navPrice·expenseRatio·sharesOutstanding) + `dividends`(12개월).
전 필드 결측 graceful — 네트워크/필드 실패는 None 으로 흡수(화면은 '—').
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

CACHE_DIR = Path(os.path.expanduser("~/reports/ml-cache"))
CACHE_TTL_S = 12 * 3600

# 네트워크 불가/info 실패 시 ETF 감지 폴백 (보유·주요 ETF — 감지 실패로 주식 뷰가 뜨는 것 방지)
_KNOWN_ETFS = {
    "QQQI", "SGOV", "SPMO", "QQQ", "SPY", "VOO", "IVV", "DIA", "IWM", "VTI", "RSP",
    "QLD", "TQQQ", "SOXL", "UPRO", "SQQQ", "SMH", "SOXX", "IGV", "SCHD", "JEPI", "JEPQ",
    "QYLD", "GLD", "TLT", "IEF", "SHY", "HYG", "LQD", "BIL", "XLK", "XLF", "XLV", "XLE",
    "XLI", "XLY", "XLP", "XLU", "XLB", "XLC", "XLRE", "EFA", "EEM", "ARKK", "MOAT",
}


def _cache_path(ticker: str) -> Path:
    return CACHE_DIR / f"etf_{ticker.upper().replace('.', '_')}.json"


def _load_cache(ticker: str):
    p = _cache_path(ticker)
    try:
        if p.exists() and time.time() - p.stat().st_mtime < CACHE_TTL_S:
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        pass
    return None


def _save_cache(ticker: str, data: dict) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = _cache_path(ticker).with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, _cache_path(ticker))
    except Exception:
        pass


# ── 순수 헬퍼 (테스트 가능) ──────────────────────────────────────────────────

def premium_pct(price, nav):
    """괴리율 % = (시장가 − NAV) / NAV. 결측/0 → None."""
    try:
        price, nav = float(price), float(nav)
        if nav <= 0 or price <= 0:
            return None
        return round((price - nav) / nav * 100, 2)
    except (TypeError, ValueError):
        return None


def dividend_stats(amounts_by_date: list[tuple], price, now=None) -> dict:
    """최근 12개월 배당 통계 — [(date, amount)] → {count, per_share, yield_pct, freq_label}.

    freq: 11회+ 매월 · 3~5회 분기 · 1~2회 연/반기 (표시용 근사).
    """
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=366)
    recent = []
    for d, amt in amounts_by_date or []:
        try:
            ts = d if isinstance(d, datetime) else datetime.fromisoformat(str(d)[:19])
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts >= cutoff and float(amt) > 0:
                recent.append(float(amt))
        except (TypeError, ValueError):
            continue
    n = len(recent)
    per_share = round(sum(recent), 4) if recent else 0.0
    yld = None
    try:
        if per_share > 0 and price and float(price) > 0:
            yld = round(per_share / float(price) * 100, 2)
    except (TypeError, ValueError):
        pass
    freq = ("매월" if n >= 11 else "분기" if 3 <= n <= 5 else
            "반기/연" if 1 <= n <= 2 else "—") if n else "—"
    return {"count_12m": n, "per_share_12m": per_share, "yield_pct": yld, "freq_label": freq}


def parse_top_holdings(df, limit: int = 10) -> list[dict]:
    """funds_data.top_holdings DataFrame → [{symbol, name, pct}] (pct=%, 내림차순)."""
    out = []
    try:
        if df is None or getattr(df, "empty", True):
            return []
        for sym, row in df.head(limit).iterrows():
            pct = row.get("Holding Percent")
            try:
                pct = round(float(pct) * 100, 2) if pct is not None else None
            except (TypeError, ValueError):
                pct = None
            out.append({"symbol": str(sym), "name": str(row.get("Name") or sym), "pct": pct})
    except Exception:
        return out
    return out


def is_etf(ticker: str, quote_type: str | None = None) -> bool:
    """ETF 여부 — quote_type 인자(감지 재사용) > 알려진 ETF 목록 > False.

    네트워크 판정은 etf_summary 가 수행(여긴 순수) — 오프라인/실패 시 목록 폴백.
    """
    if quote_type:
        return str(quote_type).upper() in ("ETF", "MUTUALFUND")
    return str(ticker).upper().split(".")[0] in _KNOWN_ETFS


def etf_summary(ticker: str) -> dict:
    """ETF 요약 — 프로필·Top10 보유·보수·괴리율·배당. 비ETF 는 {"is_etf": False}.

    12h 디스크 캐시(대시보드 st.cache_data 와 이중 — 재시작/봇 재사용 대비).
    """
    tk = str(ticker).upper()
    cached = _load_cache(tk)
    if cached is not None:
        return cached

    out: dict = {"ticker": tk, "is_etf": is_etf(tk)}
    try:
        import yfinance as yf
        t = yf.Ticker(tk)
        info = {}
        try:
            info = t.get_info() or {}
        except Exception:
            info = {}
        qt = info.get("quoteType")
        if qt:
            out["is_etf"] = is_etf(tk, quote_type=qt)
        if not out["is_etf"]:
            _save_cache(tk, out)
            return out

        price = info.get("regularMarketPrice") or info.get("previousClose")
        nav = info.get("navPrice")
        out.update({
            "name": info.get("longName") or info.get("shortName"),
            "family": info.get("fundFamily"),
            "category": info.get("category"),
            "total_assets": info.get("totalAssets"),
            "nav": nav,
            "price": price,
            "premium_pct": premium_pct(price, nav),
            "expense_ratio": (info.get("annualReportExpenseRatio")
                              or info.get("netExpenseRatio")),
            "shares_outstanding": info.get("sharesOutstanding"),
            "inception": None,
        })
        fit = info.get("fundInceptionDate")
        if fit:
            try:
                out["inception"] = datetime.fromtimestamp(int(fit), tz=timezone.utc).strftime("%Y-%m-%d")
            except (TypeError, ValueError, OSError):
                pass

        # funds_data — 설명·Top10·운용 지표 (개별 실패 무시)
        try:
            fd = t.funds_data
            try:
                out["description"] = (fd.description or "")[:600]
            except Exception:
                pass
            try:
                out["top_holdings"] = parse_top_holdings(fd.top_holdings)
            except Exception:
                out["top_holdings"] = []
            try:
                ov = fd.fund_overview or {}
                out["category"] = out.get("category") or ov.get("categoryName")
                out["family"] = out.get("family") or ov.get("family")
            except Exception:
                pass
            if out.get("expense_ratio") is None:
                try:
                    fo = fd.fund_operations
                    if fo is not None and not getattr(fo, "empty", True):
                        row = [i for i in fo.index if "Expense Ratio" in str(i)]
                        if row:
                            out["expense_ratio"] = float(fo.loc[row[0]].iloc[0])
                except Exception:
                    pass
            try:
                sw = fd.sector_weightings or {}
                out["sector_weights"] = {k: round(float(v) * 100, 2) for k, v in sw.items() if v}
            except Exception:
                pass
        except Exception:
            out.setdefault("top_holdings", [])

        # 배당 12개월
        try:
            div = t.dividends
            pairs = [(str(idx), float(v)) for idx, v in div.items()] if div is not None else []
            out["dividends"] = dividend_stats(pairs, price)
        except Exception:
            out["dividends"] = dividend_stats([], price)
    except Exception as e:
        logger.info("etf_summary(%s) 네트워크 실패 — 부분 데이터: %s", tk, e)

    _save_cache(tk, out)
    return out
