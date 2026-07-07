"""lib/intraday_status.py — 단기(1분봉) 모의 슬리브 현황 요약 (read-only·리포트 공용).

kiwoom_mock_report·us_mock_report·/paper 가 공용하는 "🕐 단기 슬리브" 섹션 빌더.
state(~/.cache/intraday_mock_state.json) + 원장(Ledger {mk}_intraday) 만 읽음 — 쓰기 0.
단기 서브시스템 미사용(데이터 전무) 시 빈 리스트 → 섹션 자체가 숨음(기존 리포트 불변).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

_STATE_PATH = os.path.expanduser("~/.cache/intraday_mock_state.json")
_TZ = {"KR": ZoneInfo("Asia/Seoul"), "US": ZoneInfo("America/New_York")}


def _state() -> dict:
    try:
        with open(_STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def intraday_summary(market: str) -> dict | None:
    """단기 슬리브 요약 dict — 데이터 전무 시 None. {today_n, wins, net_today, cum,
    halt, open_positions:[{ticker,qty,shadow,unreal_r}], shadow, currency}"""
    mk = market.upper()
    st = _state()
    counters = (st.get("counters") or {}).get(mk) or {}
    positions = [p for k, p in (st.get("positions") or {}).items()
                 if k.startswith(f"{mk}:")]
    today = datetime.now(_TZ.get(mk, timezone.utc)).strftime("%Y-%m-%d")
    outs = []
    try:
        from ml.adaptive import Ledger
        outs = [o for o in Ledger(f"{mk.lower()}_intraday").read_outcomes()
                if o.get("date") == today]
    except Exception:
        pass
    if not counters and not positions and not outs:
        return None
    net_today = sum(float(o.get("net_pnl") or 0) for o in outs)
    return {
        "today_n": len(outs),
        "wins": sum(1 for o in outs if o.get("success")),
        "net_today": net_today,
        "cum": float(counters.get("sleeve_pnl_cum") or 0.0),
        "halt": bool((st.get("halt") or {}).get(mk)),
        "open_positions": [{"ticker": p.get("ticker"), "qty": p.get("qty"),
                            "shadow": p.get("shadow", True)} for p in positions],
        "shadow": os.getenv("INTRADAY_SHADOW_ONLY", "true").lower() == "true",
        "currency": "₩" if mk == "KR" else "$",
    }


def intraday_section(market: str, html: bool = False) -> list[str]:
    """리포트용 섹션 라인들 — 데이터 없으면 [] (섹션 숨김)."""
    s = intraday_summary(market)
    if s is None:
        return []
    import fmt
    _B = fmt.b if html else (lambda x: x)
    cur = s["currency"]

    def _sm(v: float) -> str:
        return ("+" if v >= 0 else "-") + fmt.money(abs(v), cur, abbrev=True)

    mode = "shadow(가상체결)" if s["shadow"] else "모의 집행"
    lines = [fmt.sep(f"🕐 단기 슬리브 ({mode})")]
    if s["today_n"]:
        lines.append(f"오늘 {s['today_n']}트레이드 · 승 {s['wins']} · 실현 {_B(_sm(s['net_today']))}")
    else:
        lines.append("오늘 트레이드 없음 (신호 없는 날 = 정상)")
    lines.append(f"누적 {_sm(s['cum'])}" + ("  🛑 일손실 정지" if s["halt"] else ""))
    if s["open_positions"]:
        ops = " · ".join(f"{p['ticker']} {p['qty']}주" for p in s["open_positions"][:4])
        lines.append(f"오픈 {len(s['open_positions'])}건: {ops}")
    return lines
