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


def _merged_usd(snap: dict) -> list[dict]:
    """USD 해외북 두 섹션을 **티커별 합산** — general(holdings_usd) + fractional(holdings).

    같은 티커의 별도 lot(예: NVDA general 2.79주 + fractional 0.76주)을 하나로 합쳐
    중복 행/과소계상을 막는다. fetch_portfolio_value(providers.market_data)의 집계 방식과 동일.
    ⚠️ fractional 섹션의 실제 키는 'holdings'(general 은 'holdings_usd') — 혼동 주의.
    """
    agg: dict[str, dict] = {}
    for sec, key in (("overseas_general", "holdings_usd"), ("overseas_fractional", "holdings")):
        for h in snap.get(sec, {}).get(key, []) or []:
            t = (h.get("ticker") or "").upper()
            if not t:
                continue
            a = agg.setdefault(t, {"ticker": t, "name": "", "shares": 0.0,
                                   "value_usd": 0.0, "cost_usd": 0.0})
            a["shares"] += float(h.get("shares", 0) or 0)
            a["value_usd"] += float(h.get("value_usd", 0) or 0)
            a["cost_usd"] += float(h.get("cost_usd", 0) or 0)
            if not a["name"]:
                a["name"] = h.get("name", "") or ""
    for a in agg.values():
        c = a["cost_usd"]
        a["avg_price_usd"] = (c / a["shares"]) if a["shares"] else None
        a["return_pct"] = ((a["value_usd"] / c - 1) * 100) if c > 0 else 0.0
    return list(agg.values())


def portfolio_summary(path: str | None = None) -> dict:
    """USD 해외북 총액·수익률·종목수 (헤더용) — general+fractional 티커별 합산."""
    snap = _load_snap(path)
    usd = _merged_usd(snap)
    total = sum(h.get("value_usd", 0) or 0 for h in usd)
    cost = sum(h.get("cost_usd", 0) or 0 for h in usd)
    ret = (total / cost - 1) * 100 if cost else 0.0
    return {"total_usd": total, "return_pct": ret, "n_holdings": len(usd),
            "cost_usd": cost, "pnl_usd": total - cost}


def load_holdings(path: str | None = None) -> list[dict]:
    """USD 해외북 보유 정규화 (비중 % 포함) — 표·리스크 가중치용. general+fractional 티커별 합산."""
    snap = _load_snap(path)
    usd = _merged_usd(snap)
    try:
        import ticker_names
    except Exception:
        ticker_names = None
    try:
        from providers import market_data as _md    # 실시간 가격 seam (off/mi스 시 None → 스냅샷)
    except Exception:
        _md = None
    rows = []
    for h in usd:
        v = h.get("value_usd", 0) or 0
        tk = h.get("ticker", "")
        nm = h.get("name", "") or ""
        sh = h.get("shares", 0) or 0
        cost = h.get("cost_usd", 0) or 0
        ret = h.get("return_pct", 0) or 0
        # 스냅샷 이름이 없거나 티커와 같으면 resolver 로 회사명 보강(무네트워크)
        if (not nm or nm == tk) and ticker_names:
            nm = ticker_names.display_name(tk, allow_net=False) or nm
        # 실시간 가격 오버레이 (보유는 스트림 워치리스트에 포함 → 캐시 즉시). value·ret 재계산.
        rt_on = False
        rt = _md._realtime_current(tk) if (_md and tk) else None
        if rt and rt > 0 and sh:
            v = sh * rt
            ret = (v - cost) / cost * 100 if cost > 0 else ret
            rt_on = True
        rows.append({"ticker": tk, "name": nm, "shares": sh, "value": v,
                     "ret": ret, "rt": rt_on})
    tot = sum(r["value"] for r in rows) or 1    # 오버레이 후 총액으로 비중 재계산
    for r in rows:
        r["weight"] = r["value"] / tot * 100
    return rows


def holding_position(ticker: str, path: str | None = None) -> dict | None:
    """현재 보유 포지션(해외 general — avg_price_usd 보유): {shares,avg_price_usd,value,ret,cost} or None."""
    snap = _load_snap(path)
    tu = (ticker or "").upper()
    for h in snap.get("overseas_general", {}).get("holdings_usd", []) or []:
        if (h.get("ticker") or "").upper() == tu and (h.get("shares", 0) or 0) > 0:
            return {"shares": h.get("shares", 0) or 0, "avg_price_usd": h.get("avg_price_usd"),
                    "value": h.get("value_usd", 0) or 0, "ret": h.get("return_pct", 0) or 0,
                    "cost": h.get("cost_usd", 0) or 0}
    return None


def trade_events(ticker: str, *, include_mock: bool = True) -> list[dict]:
    """Chart overlay trade markers for a ticker."""
    try:
        from lib import trade_events as _te
        return _te.trades_for_ticker(ticker, include_mock=include_mock)
    except Exception:
        return []


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


# ── 기술 신호 (게이지용·순수) ────────────────────────────────────────────────
def rsi(close, period: int = 14):
    """RSI(14). close = pandas Series. 데이터 부족 시 None."""
    try:
        c = close.dropna()
        if len(c) < period + 1:
            return None
        d = c.diff()
        up = d.clip(lower=0).rolling(period).mean()
        dn = (-d.clip(upper=0)).rolling(period).mean()
        rs = up / dn.replace(0, 1e-9)
        return float((100 - 100 / (1 + rs)).iloc[-1])
    except Exception:
        return None


def technical_score(close) -> dict | None:
    """가격·MA20·MA60·RSI 종합 기술 점수 ∈[-1,1] (강력매도↔강력매수) + 보조 라벨. 순수."""
    try:
        c = close.dropna()
        if len(c) < 25:
            return None
        price = float(c.iloc[-1])
        ma20 = float(c.rolling(20).mean().iloc[-1])
        ma60 = float(c.rolling(60).mean().iloc[-1]) if len(c) >= 60 else ma20
        r = rsi(c) or 50.0
        s = (0.30 if price > ma20 else -0.30) + (0.25 if price > ma60 else -0.25)
        s += (0.20 if ma20 > ma60 else -0.20)
        s += max(-0.25, min(0.25, (r - 50) / 50 * 0.25))
        return {"score": max(-1.0, min(1.0, s)), "rsi": r,
                "sub": f"RSI {r:.0f} · MA20 {'↑' if price > ma20 else '↓'} · MA60 {'↑' if price > ma60 else '↓'}"}
    except Exception:
        return None


def company_analysis_summary(metrics: dict | None, trends: dict | None = None,
                             intrinsic: dict | None = None) -> dict:
    """기업 분석 첫 화면용 요약. 입력 dict만 쓰는 순수 판단 레이어."""
    m, tr, iv = metrics or {}, trends or {}, intrinsic or {}
    positives, risks, checks = [], [], []

    roe = _try_float(m.get("roe"))
    per = _try_float(m.get("per"))
    pbr = _try_float(m.get("pbr"))
    eps = _try_float(m.get("eps_ttm"))
    rev_yoy = _try_float(tr.get("rev_yoy"))
    margin = _try_float(tr.get("net_margin"))
    margin_chg = _try_float(tr.get("net_margin_chg"))
    debt = _try_float(tr.get("debt_to_assets"))
    upside = _try_float(iv.get("upside_pct"))

    if roe is not None and roe >= 0.15:
        positives.append(f"ROE {roe * 100:.1f}%")
    if rev_yoy is not None and rev_yoy > 0:
        positives.append(f"매출 성장 {rev_yoy * 100:+.1f}%")
    if margin is not None and margin > 0.15:
        positives.append(f"순마진 {margin * 100:.1f}%")
    if eps is not None and eps > 0:
        positives.append("EPS 흑자")
    if upside is not None and upside >= 10:
        positives.append(f"RIM 상승여력 {upside:+.0f}%")

    if m.get("per_status") == "loss" or (eps is not None and eps <= 0):
        risks.append("적자 또는 EPS 비양수")
    if per is not None and per >= 40:
        risks.append(f"PER {per:.1f}x 부담")
    if rev_yoy is not None and rev_yoy < 0:
        risks.append(f"매출 역성장 {rev_yoy * 100:+.1f}%")
    if debt is not None and debt >= 0.75:
        risks.append(f"부채/자산 {debt * 100:.0f}%")
    if pbr is not None and pbr >= 5 and (roe is None or roe < 0.15):
        risks.append(f"PBR {pbr:.1f}x 대비 ROE 낮음")
    if margin_chg is not None and margin_chg < -0.03:
        risks.append(f"순마진 악화 {margin_chg * 100:+.1f}%p")
    if upside is not None and upside <= -10:
        risks.append(f"RIM 하방 {upside:+.0f}%")

    if m.get("market_type") == "kr":
        checks.append("DART 기준연도·마캡 기준일 확인")
    if not m:
        checks.append("밸류에이션 데이터 소스 확인")
    if not tr:
        checks.append("재무 추세 데이터 확인")
    checks.append("다음 실적·가이던스 확인")
    checks.append("최근 공시·뉴스 확인")

    if risks and len(risks) >= 2:
        verdict = "주의 우선"
    elif positives and risks:
        verdict = "선별 관찰"
    elif positives:
        verdict = "양호"
    else:
        verdict = "데이터 확인 필요"

    return {
        "verdict": verdict,
        "positives": positives[:3] or ["뚜렷한 강점 데이터 부족"],
        "risks": risks[:4] or ["특이 위험 제한적"],
        "checks": list(dict.fromkeys(checks))[:3],
    }


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
