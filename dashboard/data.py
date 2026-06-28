"""dashboard/data.py — 표시용 데이터 준비 (순수 함수·무 streamlit, 테스트 가능).

streamlit 을 import 하지 않는다 → 단위 테스트에서 그대로 호출 가능.
무거운 provider 호출은 app.py 에서 st.cache_data 로 래핑한다.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

_REPO = os.getenv("STOCK_REPORT_PROJECT_DIR") or str(Path(__file__).resolve().parent.parent)

# (market_type, phase_key) → (이모지, 라벨, DCA배율)
_PHASE = {
    ("bull", "bull2"): ("🫧", "Bull-2 버블", 0.5),
    ("bull", "bull1"): ("🐂", "Bull-1 강세", 0.8),
    ("bear", "0"): ("🟢", "0 정상", 1.0),
    ("bear", "1"): ("🟡", "1 조정", 1.5),
    ("bear", "2"): ("🟠", "2 중조정", 2.0),
    ("bear", "3"): ("🔴", "3 심조정", 2.5),
    ("bear", "4"): ("🚨", "4 급락", 3.0),
    ("bear", "5"): ("💥", "5 폭락", 5.0),
}


def _snapshot_path() -> str:
    return os.path.join(_REPO, "portfolio_snapshot.json")


def _load_snap(path: str | None = None) -> dict:
    try:
        with open(path or _snapshot_path(), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def portfolio_summary(path: str | None = None) -> dict:
    """USD 해외북 총액·수익률·종목수 (헤더용)."""
    snap = _load_snap(path)
    usd = []
    for sec in ("overseas_general", "overseas_fractional"):
        usd += snap.get(sec, {}).get("holdings_usd", []) or []
    total = sum(h.get("value_usd", 0) or 0 for h in usd)
    cost = sum(h.get("cost_usd", 0) or 0 for h in usd)
    ret = (total / cost - 1) * 100 if cost else 0.0
    return {"total_usd": total, "return_pct": ret, "n_holdings": len(usd)}


def load_holdings(path: str | None = None) -> list[dict]:
    """USD 해외북 보유 정규화 (비중 % 포함) — 표·리스크 가중치용."""
    snap = _load_snap(path)
    usd = []
    for sec in ("overseas_general", "overseas_fractional"):
        usd += snap.get(sec, {}).get("holdings_usd", []) or []
    tot = sum(h.get("value_usd", 0) or 0 for h in usd) or 1
    rows = []
    for h in usd:
        v = h.get("value_usd", 0) or 0
        rows.append({
            "ticker": h.get("ticker", ""), "name": h.get("name", ""),
            "shares": h.get("shares", 0) or 0, "value": v,
            "ret": h.get("return_pct", 0) or 0, "weight": v / tot * 100,
        })
    return rows


def portfolio_weights(path: str | None = None) -> dict:
    """{ticker: weight(0~1)} — risk_model.portfolio_risk_summary 입력용."""
    rows = load_holdings(path)
    return {r["ticker"]: r["weight"] / 100 for r in rows if r["ticker"]}


def phase_badge(state_path: str | None = None) -> dict:
    """~/.cache/barbell_state.json → Phase 배지 (이모지·라벨·DCA·낙폭)."""
    p = state_path or os.path.expanduser("~/.cache/barbell_state.json")
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return {"emoji": "⚪", "label": "—", "dca": 1.0, "drawdown": 0.0}
    mt, pk = d.get("market_type", "bear"), str(d.get("phase_key", "0"))
    emoji, label, dca = _PHASE.get((mt, pk), ("⚪", f"{mt}-{pk}", 1.0))
    return {"emoji": emoji, "label": label, "dca": dca,
            "drawdown": d.get("drawdown_pct", 0) or 0.0}


# ── 표시 포맷터 (None 안전·스케일 명시) ─────────────────────────────────────────
# 제공 데이터 스케일이 필드마다 다름: roe·마진·성장률=분수(×100), div_yield·
# target_upside_pct=이미 퍼센트. 필드별로 올바른 포맷터를 골라 써야 함.
def _try_float(x):
    try:
        f = float(x)
        return f if f == f else None   # NaN 거름
    except (TypeError, ValueError):
        return None


def f_ratio(x, dec: int = 1) -> str:
    f = _try_float(x)
    return "—" if f is None else f"{f:.{dec}f}"


def f_frac_pct(x, dec: int = 1) -> str:
    """분수 → 퍼센트 (0.34 → '34.0%')."""
    f = _try_float(x)
    return "—" if f is None else f"{f * 100:.{dec}f}%"


def f_frac_pct_s(x, dec: int = 1) -> str:
    """분수 → 부호 퍼센트 (0.10 → '+10.0%')."""
    f = _try_float(x)
    return "—" if f is None else f"{f * 100:+.{dec}f}%"


def f_pct(x, dec: int = 1) -> str:
    """이미 퍼센트 (0.98 → '0.98%')."""
    f = _try_float(x)
    return "—" if f is None else f"{f:.{dec}f}%"


def f_pct_s(x, dec: int = 1) -> str:
    """이미 퍼센트 → 부호 (50.4 → '+50.4%')."""
    f = _try_float(x)
    return "—" if f is None else f"{f:+.{dec}f}%"


def f_usd(x, dec: int = 2) -> str:
    f = _try_float(x)
    return "—" if f is None else f"${f:,.{dec}f}"
