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

_KR_ETF_META = {
    "069500": {
        "name": "KODEX 200",
        "family": "삼성자산운용",
        "category": "국내 주식형",
        "benchmark": "KOSPI 200",
        "listing_date": "2002-10-14",
        "description": "KOSPI 200 지수를 추종하는 국내 대표 시장 ETF",
    },
    "102110": {"name": "TIGER 200", "family": "미래에셋자산운용", "category": "국내 주식형", "benchmark": "KOSPI 200"},
    "278530": {"name": "KODEX 200TR", "family": "삼성자산운용", "category": "국내 주식형", "benchmark": "KOSPI 200 TR"},
    "122630": {"name": "KODEX 레버리지", "family": "삼성자산운용", "category": "국내 레버리지", "benchmark": "KOSPI 200"},
    "252670": {"name": "KODEX 200선물인버스2X", "family": "삼성자산운용", "category": "국내 인버스", "benchmark": "KOSPI 200 선물"},
    "233740": {"name": "KODEX 코스닥150레버리지", "family": "삼성자산운용", "category": "국내 레버리지", "benchmark": "KOSDAQ 150"},
    "229200": {"name": "KODEX 코스닥150", "family": "삼성자산운용", "category": "국내 주식형", "benchmark": "KOSDAQ 150"},
    "305720": {"name": "KODEX 2차전지산업", "family": "삼성자산운용", "category": "국내 테마형", "benchmark": "FnGuide 2차전지산업"},
    "360750": {"name": "TIGER 미국S&P500", "family": "미래에셋자산운용", "category": "해외 주식형", "benchmark": "S&P 500"},
    "133690": {"name": "TIGER 미국나스닥100", "family": "미래에셋자산운용", "category": "해외 주식형", "benchmark": "NASDAQ 100"},
    "379800": {"name": "KODEX 미국S&P500TR", "family": "삼성자산운용", "category": "해외 주식형", "benchmark": "S&P 500 TR"},
}


def _cache_path(ticker: str) -> Path:
    return CACHE_DIR / f"etf_{ticker.upper().replace('.', '_')}.json"


def kr_code(ticker: str) -> str | None:
    """A069500·069500·069500.KS → 069500. 아니면 None."""
    s = str(ticker or "").strip().upper()
    if s.startswith("A") and len(s) == 7 and s[1:].isdigit():
        return s[1:]
    base = s.split(".")[0]
    return base if len(base) == 6 and base.isdigit() else None


def normalize_ticker(ticker: str) -> str:
    code = kr_code(ticker)
    return f"{code}.KS" if code else str(ticker or "").upper()


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

def norm_expense_ratio(v, *, percent_units: bool = False):
    """총보수(TER) → 분수 정규화. yfinance netExpenseRatio 는 **퍼센트 단위**(0.68=0.68% —
    2026-07 라이브 실증·QQQI 가 68%로 표시되던 버그 원인). 실TER 상한 ~5% 를 방어선으로
    퍼센트 단위 유입을 교정. 무효(<=0) None."""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    if percent_units or v > 0.05:
        return v / 100.0
    return v


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


def _col(row, *names):
    for name in names:
        if name in row and row.get(name) is not None:
            return row.get(name)
    return None


def _num(v):
    try:
        if isinstance(v, str):
            v = v.replace(",", "").replace("%", "").strip()
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def parse_kr_top_holdings(df, limit: int = 10) -> list[dict]:
    """pykrx PDF DataFrame → [{symbol, name, pct, shares, amount}] graceful parser."""
    out = []
    try:
        if df is None or getattr(df, "empty", True):
            return []
        rows = df.reset_index().to_dict("records")
        total_amount = sum(_num(_col(r, "평가금액", "금액", "amount")) or 0 for r in rows)
        for r in rows[:limit]:
            raw_symbol = _col(r, "티커", "종목코드", "코드", "index", "Ticker", "Symbol")
            symbol = str(raw_symbol).zfill(6) if raw_symbol is not None and str(raw_symbol).isdigit() else str(raw_symbol or "")
            name = str(_col(r, "종목명", "종목", "Name", "name") or symbol or "현금")
            pct = _num(_col(r, "비중", "비율", "편입비중", "weight", "Weight"))
            if pct is not None and 0 < pct <= 1:
                pct = pct * 100
            amount = _num(_col(r, "평가금액", "금액", "amount"))
            if pct is None and amount and total_amount:
                pct = amount / total_amount * 100
            shares = _num(_col(r, "수량", "계약수", "shares"))
            out.append({
                "symbol": symbol,
                "name": name,
                "pct": round(pct, 2) if pct is not None else None,
                "shares": shares,
                "amount": amount,
            })
    except Exception:
        return out
    return out


def apply_kr_etf_metric_table(out: dict, df) -> dict:
    """pykrx ETF 괴리율/추적오차류 테이블을 out에 병합. 테스트 가능한 순수 helper."""
    code = str(out.get("stock_code") or kr_code(out.get("ticker")) or "")
    try:
        if df is None or getattr(df, "empty", True) or not code:
            return out
        for r in df.reset_index().to_dict("records"):
            raw_symbol = _col(r, "티커", "종목코드", "코드", "index", "Ticker", "Symbol")
            symbol = str(raw_symbol).zfill(6) if raw_symbol is not None and str(raw_symbol).isdigit() else str(raw_symbol or "")
            if symbol != code:
                continue
            nav = _num(_col(r, "NAV", "순자산가치", "기준가"))
            price = _num(_col(r, "종가", "현재가", "Close", "price"))
            premium = _num(_col(r, "괴리율", "괴리율(%)", "deviation", "premium_pct"))
            tracking = _num(_col(r, "추적오차", "추적오차율", "추적오차율(%)", "tracking_error", "tracking_error_pct"))
            aum = _num(_col(r, "순자산", "순자산총액", "AUM", "total_assets"))
            if nav is not None:
                out["nav"] = nav
            if price is not None:
                out["price"] = price
            if premium is not None:
                out["premium_pct"] = round(premium, 2)
            elif out.get("price") and out.get("nav"):
                out["premium_pct"] = premium_pct(out.get("price"), out.get("nav"))
            if tracking is not None:
                out["tracking_error_pct"] = round(tracking, 2)
            if aum is not None:
                out["total_assets"] = aum
            out["metrics_source"] = "pykrx ETF"
            return out
    except Exception:
        return out
    return out


def is_etf(ticker: str, quote_type: str | None = None) -> bool:
    """ETF 여부 — quote_type 인자(감지 재사용) > 알려진 ETF 목록 > False.

    네트워크 판정은 etf_summary 가 수행(여긴 순수) — 오프라인/실패 시 목록 폴백.
    """
    if quote_type:
        return str(quote_type).upper() in ("ETF", "MUTUALFUND")
    code = kr_code(ticker)
    if code:
        return code in _KR_ETF_META
    return str(ticker).upper().split(".")[0] in _KNOWN_ETFS


def _kr_etf_base(ticker: str) -> dict:
    code = kr_code(ticker) or ""
    meta = _KR_ETF_META.get(code, {})
    return {
        "ticker": f"{code}.KS" if code else str(ticker or "").upper(),
        "stock_code": code,
        "is_etf": bool(code and code in _KR_ETF_META),
        "market_type": "kr",
        "currency": "KRW",
        "source": "KR ETF fallback",
        "name": meta.get("name"),
        "family": meta.get("family"),
        "category": meta.get("category"),
        "benchmark": meta.get("benchmark"),
        "inception": meta.get("listing_date"),
        "description": meta.get("description"),
        "total_assets": None,
        "nav": None,
        "price": None,
        "premium_pct": None,
        "tracking_error_pct": None,
        "expense_ratio": None,
        "top_holdings": [],
        "dividends": dividend_stats([], None),
    }


def _latest_kr_market_row(code: str) -> dict:
    try:
        from providers import kr_market_data as km
        snap = km.marcap_asof(datetime.now().strftime("%Y-%m-%d"), market="")
        if snap is None or len(snap) == 0:
            return {}
        sub = snap[snap["Code"].map(km.norm_code) == km.norm_code(code)]
        if len(sub):
            return sub.iloc[0].to_dict()
    except Exception:
        return {}
    return {}


def _kr_yfinance_overlay(out: dict) -> None:
    try:
        import yfinance as yf
        t = yf.Ticker(out["ticker"])
        info = {}
        try:
            info = t.get_info() or {}
        except Exception:
            info = {}
        price = info.get("regularMarketPrice") or info.get("previousClose")
        nav = info.get("navPrice")
        out["price"] = price or out.get("price")
        out["nav"] = nav or out.get("nav")
        pm = premium_pct(out.get("price"), out.get("nav"))
        if pm is not None:
            out["premium_pct"] = pm
        out["name"] = out.get("name") or info.get("longName") or info.get("shortName")
        out["family"] = out.get("family") or info.get("fundFamily")
        out["category"] = out.get("category") or info.get("category")
        out["total_assets"] = out.get("total_assets") or info.get("totalAssets")
        if out.get("expense_ratio") is None:
            out["expense_ratio"] = (norm_expense_ratio(info.get("annualReportExpenseRatio"))
                                    or norm_expense_ratio(info.get("netExpenseRatio"),
                                                          percent_units=True))
        try:
            div = t.dividends
            pairs = [(str(idx), float(v)) for idx, v in div.items()] if div is not None else []
            out["dividends"] = dividend_stats(pairs, out.get("price"))
        except Exception:
            pass
    except Exception:
        pass


def _kr_pykrx_overlay(out: dict) -> None:
    code = out.get("stock_code")
    if not code:
        return
    try:
        from pykrx import stock
    except Exception:
        return
    today = datetime.now().strftime("%Y%m%d")
    try:
        out["name"] = out.get("name") or stock.get_etf_ticker_name(code)
    except Exception:
        pass
    try:
        pdf = None
        for args in ((code, today), (code,), (today, code)):
            try:
                pdf = stock.get_etf_portfolio_deposit_file(*args)
                if pdf is not None:
                    break
            except Exception:
                continue
        parsed = parse_kr_top_holdings(pdf)
        if parsed:
            out["top_holdings"] = parsed
            out["top_holdings_source"] = "pykrx PDF"
    except Exception:
        pass
    for fn_name in ("get_etf_price_deviation", "get_etf_tracking_error"):
        try:
            fn = getattr(stock, fn_name)
        except Exception:
            continue
        for args in ((today,), (today, code), (code, today)):
            try:
                df = fn(*args)
                before = dict(out)
                apply_kr_etf_metric_table(out, df)
                if out != before:
                    break
            except Exception:
                continue


def kr_etf_summary(ticker: str) -> dict:
    out = _kr_etf_base(ticker)
    if not out["is_etf"]:
        return out
    row = _latest_kr_market_row(out["stock_code"])
    if row:
        out["price"] = row.get("Close") or out.get("price")
        out["market_cap"] = row.get("Marcap")
        out["shares_outstanding"] = row.get("Stocks")
        out["asof"] = str(row.get("Date"))[:10] if row.get("Date") is not None else None
    _kr_yfinance_overlay(out)
    _kr_pykrx_overlay(out)
    return out


def etf_summary(ticker: str) -> dict:
    """ETF 요약 — 프로필·Top10 보유·보수·괴리율·배당. 비ETF 는 {"is_etf": False}.

    12h 디스크 캐시(대시보드 st.cache_data 와 이중 — 재시작/봇 재사용 대비).
    """
    tk = normalize_ticker(ticker)
    cached = _load_cache(tk)
    if cached is not None:
        # 구 캐시의 퍼센트 단위 TER(QQQI 68% 표시 버그) self-heal — 정규화는 멱등
        if cached.get("expense_ratio") is not None:
            cached["expense_ratio"] = norm_expense_ratio(cached["expense_ratio"])
        return cached

    out: dict = {"ticker": tk, "is_etf": is_etf(tk)}
    if kr_code(tk):
        out = kr_etf_summary(tk)
        _save_cache(tk, out)
        return out
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
            "expense_ratio": (norm_expense_ratio(info.get("annualReportExpenseRatio"))
                              or norm_expense_ratio(info.get("netExpenseRatio"),
                                                    percent_units=True)),
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
                            out["expense_ratio"] = norm_expense_ratio(fo.loc[row[0]].iloc[0])
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
