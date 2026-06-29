"""dashboard/views.py — 모듈별 표시 데이터 (기존 provider 래퍼·graceful).

전부 try/except 로 감싸 한 모듈 실패가 화면을 깨지 않게 한다. 네트워크 호출이라
app.py 에서 st.cache_data 로 감싼다. provider 는 함수 내부 import(테스트서 monkeypatch 용).
"""
from __future__ import annotations

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
