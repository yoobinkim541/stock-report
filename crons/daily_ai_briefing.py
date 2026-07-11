#!/usr/bin/env python3
"""daily_ai_briefing.py — 🌅 포트폴리오 AI 모닝 브리핑 + 보유종목 AI 분석 프리페치 (opt-in).

① 보유 종목(portfolio_universe 단일 소스) 각각의 AI 분석을 미리 생성해 24h 디스크
   캐시를 프라임 — 대시보드 '🤖 분석 생성' 버튼이 즉시(cached) 응답.
② 보유 전체 지표+뉴스를 하나의 DATA 로 묶어 포트폴리오 브리핑 생성 →
   ~/reports/ml-cache/llm_briefing.json (홈 카드) + 텔레그램 발송.

원칙: LLM = 해설 생성기(DATA 한정·처방 금지어 필터·균형 강제 — providers/llm_analysis).
표시 전용 — 시스템 판단(신호·배분) 미반영. 정직 라벨 필수.
안전: DASH_AI_BRIEFING_ENABLED=true 여야 동작(기본 off). 실패는 종목 단위 격리.
비용: 보유 N 종목 프리페치(캐시 신선하면 스킵) + 브리핑 1콜 — 평일 1회.
크론 (평일 22:45 UTC = 07:45 KST — 08:00 아침 리포트 직전):
    45 22 * * 1-5 cd <repo> && uv run python crons/daily_ai_briefing.py
"""
from __future__ import annotations

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

ENABLED = os.getenv("DASH_AI_BRIEFING_ENABLED", "0").lower() in ("1", "true", "yes")
PREFETCH_MAX = int(os.getenv("DASH_AI_BRIEFING_PREFETCH_MAX", "12"))


def _r(v, nd=2):
    try:
        return round(float(v), nd)
    except (TypeError, ValueError):
        return None


def ticker_facts(ticker: str) -> dict:
    """종목 DATA — 디스크 캐시된 provider 만 사용 (결측 생략·graceful·크론용 경량판)."""
    f: dict = {}
    try:
        from dashboard import data as ddata
        from providers.market_data import _history_cached
        hist = _history_cached(ticker, period="2y")
        prof = ddata.series_profile(hist) or {}
        f["기술"] = {"1개월수익률%": _r(prof.get("r1m"), 1), "1년%": _r(prof.get("r1y"), 1),
                    "52주위치(0~1)": _r(prof.get("pos52")),
                    "연변동성%": _r(prof.get("vol_ann"), 1),
                    "200일선이격%": _r(prof.get("ma200_gap"), 1)}
        cl = hist["Close"].dropna()
        if len(cl):
            f["현재가"] = _r(cl.iloc[-1])
    except Exception:
        pass
    try:
        from providers import earnings_data
        v = (earnings_data.summary(ticker) or {}).get("valuation") or {}
        val = {k: _r(v.get(k)) for k in ("per", "pbr", "roe", "eps_ttm") if v.get(k) is not None}
        if val:
            f["밸류에이션"] = val
    except Exception:
        pass
    try:
        from providers import earnings_data
        rows = (earnings_data.quarterly_fundamentals(ticker).get("quarterly") or [])[-4:]
        if rows:
            from dashboard.charts import fmt_big
            f["분기펀더멘털"] = [{"분기": r.get("date"), "매출": fmt_big(r.get("revenue")),
                              "순이익": fmt_big(r.get("net_income"))} for r in rows]
    except Exception:
        pass
    try:
        f["최근뉴스"] = news_items(ticker)
    except Exception:
        pass
    return {k: v for k, v in f.items() if v}


def news_items(ticker: str, n: int = 4) -> list[dict]:
    """최근 뉴스 라벨 — 새니타이즈(공백 접기·80자) (인젝션 방어 1선·순수 가공)."""
    import re as _re

    from dashboard import views
    out = []
    for ev in (views.chart_news_events(ticker) or [])[-n:]:
        t = _re.sub(r"\s+", " ", str(ev.get("title") or "")).strip()[:80]
        if t:
            out.append({"일자": ev.get("date"), "유형": ev.get("event_type") or "",
                        "방향(-1~1)": ev.get("direction"), "제목": t})
    return out


def portfolio_facts(tickers: list[str], per_ticker: dict[str, dict]) -> dict:
    """포트폴리오 DATA — 종목별 요점 + 포트 구성/Phase (순수 조립)."""
    facts: dict = {"보유종목수": len(tickers)}
    try:
        import json as _j
        st_path = os.path.expanduser("~/.cache/barbell_state.json")
        with open(st_path, encoding="utf-8") as fp:
            st = _j.load(fp)
        if st.get("phase") is not None:
            facts["시장Phase"] = st.get("phase")
    except Exception:
        pass
    rows = {}
    for t in tickers:
        tf = per_ticker.get(t) or {}
        row = {}
        tech = tf.get("기술") or {}
        if tech.get("1개월수익률%") is not None:
            row["1개월%"] = tech["1개월수익률%"]
        if tech.get("1년%") is not None:
            row["1년%"] = tech["1년%"]
        if (tf.get("밸류에이션") or {}).get("per") is not None:
            row["PER"] = tf["밸류에이션"]["per"]
        news = tf.get("최근뉴스") or []
        if news:
            row["뉴스"] = news[-1]["제목"]
        if row:
            rows[t] = row
    facts["종목별"] = rows
    return facts


def build_message(brief: dict) -> str:
    """텔레그램 본문 (순수) — 4000자 이내·정직 라벨."""
    lines = ["🌅 AI 포트폴리오 브리핑", "", f"『{brief.get('summary', '')}』", ""]
    hl = brief.get("highlights") or []
    if hl:
        lines.append("📌 오늘 주목")
        lines += [f"• {h}" for h in hl]
        lines.append("")
    rk = brief.get("risks") or []
    if rk:
        lines.append("⚠️ 리스크")
        lines += [f"• {r}" for r in rk]
        lines.append("")
    cp = brief.get("checkpoints") or []
    if cp:
        lines.append("✅ 체크포인트")
        lines += [f"• {c}" for c in cp]
        lines.append("")
    lines.append("— LLM 해설(계산된 지표·뉴스 DATA 한정) · 검증 안 된 참고용 · "
                 "매매신호 아님 · 시스템 판단 미반영")
    return "\n".join(lines)[:4000]


def main() -> int:
    logger.info("=== daily_ai_briefing ===")
    if not ENABLED:
        logger.info("DASH_AI_BRIEFING_ENABLED=false — 스킵 (opt-in)")
        return 0
    import ticker_names
    from portfolio_universe import load_portfolio_tickers
    from providers import llm_analysis

    tickers = [t for t in load_portfolio_tickers() if t][:PREFETCH_MAX]
    logger.info("보유 %d종목 프리페치 시작", len(tickers))
    per_ticker: dict[str, dict] = {}
    primed = fresh = failed = 0
    for t in tickers:
        try:
            facts = ticker_facts(t)
            per_ticker[t] = facts
            name = ticker_names.display_name(t, allow_net=False) or t
            out, status = llm_analysis.analyze(t, name, facts)   # 캐시 신선하면 cached
            if status == "ok":
                primed += 1
            elif status == "cached":
                fresh += 1
            else:
                failed += 1
                logger.warning("프리페치 실패 %s: %s", t, status)
        except Exception as exc:                  # 종목 단위 격리
            failed += 1
            logger.warning("프리페치 예외 %s: %s", t, exc)
    logger.info("프리페치 — 신규 %d · 캐시 %d · 실패 %d", primed, fresh, failed)

    brief, status = llm_analysis.portfolio_brief(portfolio_facts(tickers, per_ticker))
    logger.info("브리핑 상태: %s", status)
    if not brief:
        return 0                                  # graceful — 아침 리포트는 독립 진행
    if status == "ok":                            # 신규 생성일 때만 발송 (cached 재발송 방지)
        try:
            from notify import send_telegram
            send_telegram(build_message(brief))
            logger.info("텔레그램 발송 완료")
        except Exception as exc:
            logger.warning("텔레그램 발송 실패: %s", exc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
