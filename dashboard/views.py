"""dashboard/views.py — 모듈별 표시 데이터 (기존 provider 래퍼·graceful).

전부 try/except 로 감싸 한 모듈 실패가 화면을 깨지 않게 한다. 네트워크 호출이라
app.py 에서 st.cache_data 로 감싼다. provider 는 함수 내부 import(테스트서 monkeypatch 용).
"""
from __future__ import annotations

import os
import re


def _strip_html(s: str) -> str:
    """텔레그램용 HTML 태그 제거 (st.code 모노스페이스 렌더용)."""
    return re.sub(r"<[^>]+>", "", s or "")


def valuation(ticker: str) -> dict:
    """상대가치 + 컨센서스 + 실적 서프라이즈 이력."""
    from providers import earnings_data
    out: dict = {"ticker": ticker}
    for key, fn in (("metrics", lambda: earnings_data.valuation_metrics(ticker)),
                    ("consensus", lambda: earnings_data.consensus(ticker)),
                    ("history", lambda: earnings_data.earnings_history(ticker, limit=8))):
        try:
            out[key] = fn()
        except Exception as e:
            out[key + "_error"] = str(e)
    return out


def financials(ticker: str) -> dict:
    """SEC EDGAR 펀더멘털 추세 (매출YoY·순마진·부채)."""
    from providers import edgar
    try:
        return {"trends": edgar.fundamental_trends(ticker)}
    except Exception as e:
        return {"error": str(e)}


def risk_report_text(weights: dict) -> str:
    """포트폴리오 리스크 리포트 (format_risk_report → HTML 태그 제거 평문)."""
    from ml import risk_model
    if not weights:
        return "보유 데이터 없음 — portfolio_snapshot 확인 필요"
    try:
        summ = risk_model.portfolio_risk_summary(weights)
        return _strip_html(risk_model.format_risk_report(summ))
    except Exception as e:
        return f"리스크 분석 실패: {e}"


def risk_summary(weights: dict) -> dict:
    """구조화 리스크 요약 (위험기여·팩터β·레버리지 — 차트용). graceful."""
    from ml import risk_model
    if not weights:
        return {"error": "보유 데이터 없음 — portfolio_snapshot 확인 필요"}
    try:
        return risk_model.portfolio_risk_summary(weights)
    except Exception as e:
        return {"error": str(e)}


def institutional(ticker: str) -> dict:
    """선택 종목 13F 지분 + 매집 강도(가능 시)."""
    from reports import institutional_flow
    out: dict = {"ticker": ticker}
    try:
        out["inst13f"] = institutional_flow.fetch_13f(ticker)
    except Exception as e:
        out["error_13f"] = str(e)
    try:
        ranked = institutional_flow.rank_accumulation([ticker], enrich_top=1)
        out["accum"] = ranked[0] if ranked else None
    except Exception as e:
        out["error_accum"] = str(e)
    return out


def news_digest(ticker: str, hours: int = 72, limit: int = 10) -> str:
    """최근 뉴스 다이제스트 (티커 관련 우선, 없으면 전체)."""
    from reports import source_collector
    try:
        ev = source_collector.load_recent_events(hours=hours)
        if ticker:
            rel = [e for e in ev
                   if ticker in (e.get("symbols") or e.get("tickers") or [])]
            ev = rel or ev
        return source_collector.build_digest(ev, limit=limit)
    except Exception as e:
        return f"뉴스 로드 실패: {e}"


def earnings_calendar(ticker: str) -> dict:
    """실적 서프라이즈 이력 (종목별)."""
    from providers import earnings_data
    try:
        return {"history": earnings_data.earnings_history(ticker, limit=6)}
    except Exception as e:
        return {"error": str(e)}


def intrinsic_value(ticker: str) -> dict:
    """DDM·RIM 내재가치 밴드 (QT2)."""
    from providers import intrinsic
    try:
        return intrinsic.intrinsic(ticker)
    except Exception as e:
        return {"error": str(e)}


def econ_events(days: int = 14) -> list[dict]:
    """경제 일정 (saveticker /calendar/events, QT2)."""
    from providers import econ_calendar
    try:
        return econ_calendar.upcoming_events(days=days)
    except Exception:
        return []


def insider_trades(ticker: str) -> dict:
    """내부자거래 (SEC Form 4, 美·키불요) — QT2b."""
    from providers import insider
    try:
        return insider.recent_insider(ticker)
    except Exception as e:
        return {"error": str(e), "transactions": []}


def disclosures(ticker: str) -> dict:
    """공시 — 美: SEC filings · 韓(.KS): DART. 키 없으면 graceful. QT2b."""
    from providers import dart, insider
    if dart.stock_code(ticker):
        d = dart.recent_disclosures(ticker)
        return {"market": "KR", **d}
    f = insider.recent_filings(ticker)
    return {"market": "US", "list": f.get("filings", []), "error": f.get("error")}


def screener(top_n: int = 20) -> dict:
    """NASDAQ100 LightGBM 랭킹 스크리너 (무엣지·정보용). QT3."""
    from ml.ranker import load_ranker, rank_today
    try:
        df = rank_today(mode="nasdaq100", top_n=top_n)
        res = load_ranker()
        rows = df.to_dict("records") if (df is not None and not df.empty) else []
        meta = {}
        if res is not None:
            meta = {"ic": getattr(res, "oos_ic", None), "icir": getattr(res, "oos_icir", None),
                    "top_decile": getattr(res, "oos_top_decile_ret", None),
                    "train_end": getattr(res, "train_end_date", None)}
        return {"rows": rows, "meta": meta}
    except Exception as e:
        return {"error": str(e), "rows": [], "meta": {}}


def backtest_summary() -> dict:
    """ML 전략 백테스트 (QQQ 3년 실데이터) + 채택 판정. QT3."""
    from ml.data_pipeline import build_real_sweetspot_data
    from ml.reporting import _ml_adoption_verdict
    from ml.sweet_spot import optimize_sweet_spot

    def _m(x):
        return {"cagr": getattr(x, "cagr", None), "sharpe": getattr(x, "sharpe", None),
                "mdd": getattr(x, "max_drawdown", None)}

    try:
        data = build_real_sweetspot_data("QQQ", days=756)
        r = optimize_sweet_spot(data)
        verdict, reasons = _ml_adoption_verdict(r.ml_result, r.qqq_result)
        return {"ml": _m(r.ml_result), "overlay": _m(r.overlay_result), "qqq": _m(r.qqq_result),
                "verdict": verdict, "reasons": list(reasons or []),
                "equity": getattr(r, "equity", None), "wf": getattr(r, "wf_summary", {})}
    except Exception as e:
        return {"error": str(e)}


def learning_evolution(surface: str = "kr_mock") -> dict:
    """모의 자기개선 진화 — 주간 학습 이력 + 라이브 스냅샷 verdict. read-only·graceful."""
    from ml.adaptive import Ledger, evolution
    try:
        rows = Ledger(surface).training_set()
    except Exception:
        rows = []
    try:
        return evolution.evolution_summary(surface, rows)
    except Exception as e:
        return {"error": str(e), "snapshot": {}, "verdict": {}, "series": [], "adoptions": [], "n_runs": 0}


def realtime_quote(ticker: str) -> dict | None:
    """실시간 시세+호가 (KIS — 캐시 seam 우선·REST 온디맨드 폴백). read-only·graceful None.

    반환 {price, bids, asks, ts, source, market}. REALTIME_ENABLED off/미보유/장애 시 None →
    호출부 yfinance 폴백. 주문 경로 없음(kis_quote 는 read-only).
    """
    t = (ticker or "").strip()
    if not t:
        return None
    tu = t.upper()
    if tu.endswith(".KS") or tu.endswith(".KQ"):
        sym, market = t[:-3], "KR"
    else:
        sym, market = tu, "US"
    price = None
    try:                                    # 1) 캐시 seam (워치리스트 종목 = 즉시)
        from providers import market_data
        price = market_data._realtime_current(t)
    except Exception:
        price = None
    snap = None
    try:                                    # 2) REST 온디맨드 (임의 티커·호가 포함)
        from providers import kis_quote
        snap = kis_quote.get_snapshot(sym, market=market)
    except Exception:
        snap = None
    if snap and snap.get("price"):
        return {"price": price or snap.get("price"), "bids": snap.get("bids") or [],
                "asks": snap.get("asks") or [], "ts": snap.get("ts"),
                "source": snap.get("source", "kis_rest"), "market": market}
    if price and price > 0:
        return {"price": price, "bids": [], "asks": [], "ts": None, "source": "kis_ws", "market": market}
    return None


_HEATMAP_SNAP = os.path.expanduser("~/reports/ml-cache/sp500_heatmap.json")


def sp500_heatmap() -> list[dict]:
    """S&P500 시장 맵 rows — 크론 JSON 스냅샷(<90분) 우선(즉시) → 없으면 라이브 후 스냅샷 기록(self-heal).

    콜드로드 ~60초(503 배치)를 스냅샷 파일읽기로 즉시화. crons/sp500_heatmap_snapshot.py 가 20분마다 갱신.
    """
    import json
    import time
    try:
        if time.time() - os.stat(_HEATMAP_SNAP).st_mtime < 5400:      # 90분 이내 신선
            with open(_HEATMAP_SNAP, encoding="utf-8") as f:
                rows = json.load(f)
            if rows:
                return rows
    except Exception:
        pass
    rows = _sp500_heatmap_live()
    if rows:
        try:
            from safe_io import atomic_write_json
            atomic_write_json(_HEATMAP_SNAP, rows)
        except Exception:
            pass
    return rows


def _sp500_heatmap_live() -> list[dict]:
    """S&P500 시장 맵 라이브 조립 — [{ticker,name,sector_kr,market_cap,pct}]. 표시·graceful.

    섹터·시총 = 정적 시드(sp500_seed·sp500_meta), 당일 등락% = 라이브 배치(yf.download 2일 종가).
    결측(시총·pct 없음) 스킵. 네트워크/모듈 실패 시 빈 리스트. (크론·스냅샷 미스 시 폴백)
    """
    try:
        import sp500_meta
        import sp500_seed
    except Exception:
        return []
    tickers = list(sp500_seed.SP500)
    sec_map = getattr(sp500_meta, "SECTOR", {})
    cap_map = getattr(sp500_meta, "MARKET_CAP", {})
    kr_map = getattr(sp500_meta, "SECTOR_KR", {})
    pct: dict[str, float] = {}
    try:
        import warnings
        warnings.filterwarnings("ignore")
        import yfinance as yf
        df = yf.download(tickers, period="2d", progress=False, group_by="ticker", threads=True)
        for t in tickers:
            try:
                c = df[t]["Close"].dropna()
                if len(c) >= 2 and c.iloc[-2]:
                    pct[t] = round((c.iloc[-1] / c.iloc[-2] - 1) * 100, 2)
            except Exception:
                pass
    except Exception:
        return []
    rows: list[dict] = []
    for t in tickers:
        cap = cap_map.get(t) or 0
        p = pct.get(t)
        if cap <= 0 or p is None:
            continue
        rows.append({
            "ticker": t, "name": sp500_seed.SP500.get(t) or t,
            "sector_kr": kr_map.get(sec_map.get(t) or "") or "기타",
            "market_cap": float(cap), "pct": p})
    return rows


def market_indicators() -> dict:
    """홈 시장 지표 — 공포·탐욕지수 + S&P500·나스닥 일/주봉 RSI. 표시·graceful.

    반환 {fear_greed:{score,rating,prev_week,prev_month}|None,
          indices:[{ticker,name,price,chg,rsi_d,rsi_w}]}. 네트워크 실패는 None/빈으로 흡수.
    """
    from dashboard import data
    out: dict = {"fear_greed": None, "indices": []}
    try:
        from providers import market_data
        fg = market_data.fetch_fear_greed()
        if isinstance(fg, dict) and fg.get("score") is not None:
            out["fear_greed"] = {"score": float(fg["score"]), "rating": fg.get("rating"),
                                 "prev_week": fg.get("prev_week"), "prev_month": fg.get("prev_month")}
    except Exception:
        pass
    specs = [("^GSPC", "S&P 500"), ("^IXIC", "나스닥")]
    try:
        import warnings
        warnings.filterwarnings("ignore")
        import yfinance as yf
        tks = [s[0] for s in specs]
        hd = yf.download(tks, period="4mo", progress=False, group_by="ticker", threads=True)
        hw = yf.download(tks, period="2y", interval="1wk", progress=False, group_by="ticker", threads=True)
    except Exception:
        return out
    for tk, name in specs:
        row = {"ticker": tk, "name": name, "price": None, "chg": None, "rsi_d": None, "rsi_w": None}
        try:
            cd = hd[tk]["Close"].dropna()
            rd = data.rsi(cd)
            row["rsi_d"] = round(rd, 1) if rd is not None else None
            if len(cd) >= 2 and cd.iloc[-2]:
                row["price"] = float(cd.iloc[-1])
                row["chg"] = round((cd.iloc[-1] / cd.iloc[-2] - 1) * 100, 2)
        except Exception:
            pass
        try:
            rw = data.rsi(hw[tk]["Close"].dropna())
            row["rsi_w"] = round(rw, 1) if rw is not None else None
        except Exception:
            pass
        out["indices"].append(row)
    return out
