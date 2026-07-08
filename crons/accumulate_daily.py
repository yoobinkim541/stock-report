#!/usr/bin/env python3
"""accumulate_daily.py — 주식 모으기 자동 기록 (미 정규장 마감 직후).

등록된 플랜(lib/accumulation — 대시보드 적립 폼에서 등록)을 그날 **종가**와
**확정 종가 환율**로 소수점 계좌에 매수 기록한다. **기록 전용 — 실계좌 주문 0**
(실제 매수는 키움 주식모으기/수동 — 이 크론은 포트폴리오 반영 자동화).

멱등: 플랜 last_run(세션일)로 재실행 안전 · 휴장은 세션일 불일치로 자연 스킵.
크론 (평일 21:10 UTC = 미 마감 06:10 KST 직후): deploy/crontab.stock-report
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from lib import accumulation


def _get_close(ticker: str):
    """오늘 미 세션 종가 — 마지막 일봉 날짜가 오늘(ET)이 아니면 휴장 취급 None."""
    import yfinance as yf
    hist = yf.Ticker(ticker).history(period="5d")
    if hist is None or hist.empty:
        return None
    last_ts = hist.index[-1]
    today_et = datetime.now(ZoneInfo("America/New_York")).date()
    if last_ts.date() != today_et:
        return None
    return float(hist["Close"].iloc[-1]), last_ts.date()


def _record_market_temp() -> None:
    """🌡️ 시장 온도계 일별 스냅샷 — 홈 스파크라인 이력 (graceful·모으기와 독립)."""
    try:
        from datetime import date

        import store
        from dashboard.data import market_temperature
        from providers.market_data import fetch_fear_greed, fetch_qqq_data
        from providers.market_valuation import sp500_valuation
        import yfinance as yf

        today = date.today().isoformat()
        if any(r.get("date") == today for r in store.all("market_temp_history")):
            return                                     # 멱등 — 하루 1회
        fg = (fetch_fear_greed() or {}).get("score")
        v = sp500_valuation() or {}
        w = yf.Ticker("^GSPC").history(period="2y", interval="1wk")["Close"]
        from dashboard.data import rsi as _rsi
        rsi_w = _rsi(w) if len(w) > 20 else None
        dd = (fetch_qqq_data() or {}).get("drawdown_pct")
        t = market_temperature(fear_greed=fg, rsi_w=rsi_w,
                               per_pctile_20y=v.get("per_pctile_20y"),
                               peg=v.get("peg"), drawdown_pct=dd)
        if t:
            store.append("market_temp_history",
                         {"date": today, "score": round(t["score"], 3), "sub": t["sub"]})
            print(f"온도계 스냅샷 {today}: {t['score']:+.2f}")
    except Exception as e:
        print("온도계 스냅샷 실패:", e)


def main() -> None:
    _record_market_temp()
    plans = [p for p in accumulation.load_plans() if p.get("enabled", True)]
    if not plans:
        print("자동 모으기 플랜 없음 — no-op")
        return
    from providers.market_data import fetch_exchange_rate_close
    import holding_manager

    def _record(t, qty, price, note):
        return holding_manager.buy_holding(t, qty, price, fractional=True, note=note)

    res = accumulation.run_once(get_close=_get_close,
                                get_fx=fetch_exchange_rate_close, record=_record)
    print("recorded:", res["recorded"])
    print("skipped:", res["skipped"])
    if res["errors"]:
        print("errors:", res["errors"])
    if res["recorded"] or res["errors"]:
        try:
            import notify
            lines = ["🔁 주식 모으기 자동 기록 (미 종가·기록 전용)"]
            lines += [f"  ✅ {r}" for r in res["recorded"]]
            lines += [f"  ⚠️ {e}" for e in res["errors"]]
            lines.append("※ 실계좌 주문 아님 — 포트폴리오 기록 반영")
            notify.send_telegram("\n".join(lines))
        except Exception as e:
            print("텔레그램 발송 실패:", e)


if __name__ == "__main__":
    main()
