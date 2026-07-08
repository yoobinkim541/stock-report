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
                     "ret": ret, "rt": rt_on, "cost": cost})
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


def fair_value_multiple(price, per, fper=None, eps_fwd=None) -> dict | None:
    """멀티플 유지 기준가 — 포워드 EPS × 현재 PER.

    "이익이 컨센서스대로 성장하고 시장이 현재 멀티플을 유지하면"의 가격.
    EPS(TTM) × PER 은 대체로 현재가를 재계산하는 항등식이라,
    기준가는 Forward EPS 를 우선 쓰고 없을 때만 현재가 ÷ fPER 로 역산한다.
    극단 배율(10x 초과)은 데이터 오류로 보고 제외.
    """
    p, t = _try_float(price), _try_float(per)
    if not p or not t or p <= 0 or t <= 0:
        return None
    f = _try_float(fper)
    eps = _try_float(eps_fwd)
    source = "eps_fwd"
    if not eps or eps <= 0:
        if not f or f <= 0:
            return None
        eps = p / f
        source = "implied_fper"
    fair = eps * t
    ratio = fair / p
    if ratio > 10 or ratio < 0.1:
        return None
    return {"fair": fair, "upside_pct": (ratio - 1.0) * 100.0,
            "eps_fwd": eps, "per": t, "fper": f, "source": source}


def valuation_score(price, metrics, consensus=None, intrinsic=None) -> dict | None:
    """가치평가 종합 점수 ∈[-1,1] (−1 크게 고평가 ↔ +1 크게 저평가) + 근거 라벨. 순수.

    컴포넌트(가용한 것만·가중 평균): PEG(1.0)·fwd EPS 성장률(0.5)·멀티플 기준가
    업사이드(1.0)·애널리스트 목표가 업사이드(1.0)·RIM 업사이드(0.5).
    재료 <2 개면 None(정직 생략). 표시·참고용 — 매매신호 아님.
    """
    m, c, iv = metrics or {}, consensus or {}, intrinsic or {}
    p = _try_float(price)
    if not p or p <= 0:
        return None

    def clamp(x):
        return max(-1.0, min(1.0, x))

    comps = []                                       # (weight, score, label)
    _pt = peg_textbook(m)
    peg = (_pt or {}).get("peg") or _try_float(m.get("peg"))   # 교과서식 우선·야후 폴백
    if peg and peg > 0:
        comps.append((1.0, clamp((1.75 - peg) / 1.25), f"PEG {peg:.1f}"))
    e0, e1 = _try_float(m.get("eps_ttm")), _try_float(m.get("eps_fwd"))
    if e0 and e1 and e0 > 0:
        g = (e1 / e0 - 1) * 100
        comps.append((0.5, clamp((g - 5.0) / 20.0), f"EPS성장 {g:+.0f}%"))
    fv = fair_value_multiple(p, m.get("per"), m.get("forward_pe"), m.get("eps_fwd"))
    if fv and fv.get("fair"):
        up = fv["fair"] / p - 1
        comps.append((1.0, clamp(up / 0.30), f"기준가 {up * 100:+.0f}%"))
    tgt = _try_float(c.get("target_median") or c.get("target_mean"))
    if tgt and tgt > 0:
        up = tgt / p - 1
        comps.append((1.0, clamp(up / 0.30), f"목표가 {up * 100:+.0f}%"))
    rim_up = _try_float(iv.get("upside_pct"))
    if rim_up is not None and (iv.get("rim") or {}).get("mid"):
        comps.append((0.5, clamp(rim_up / 100.0 / 0.35), f"RIM {rim_up:+.0f}%"))
    if len(comps) < 2:
        return None
    wsum = sum(w for w, _, _ in comps)
    score = sum(w * s for w, s, _ in comps) / wsum
    return {"score": clamp(score), "sub": " · ".join(lab for _, _, lab in comps[:3]),
            "n": len(comps)}


# 스크리너 판단근거 화이트리스트 — (피처, 값→한글 라벨|None). 설명 가능한 것만.
_DRIVER_RULES = [
    ("close_vs_52w_high", lambda v: "52주 고점 근접" if v >= 0.95 else
        (f"고점 -{(1 - v) * 100:.0f}%" if v <= 0.75 else None)),
    ("mom_126d", lambda v: f"6M 모멘텀 {v * 100:+.0f}%" if abs(v) >= 0.15 else None),
    ("excess_mom_60d", lambda v: f"QQQ대비 {v * 100:+.1f}%p(60d)" if abs(v) >= 0.03 else None),
    ("rsi_14", lambda v: f"RSI {v:.0f} 과매도" if v <= 35 else
        (f"RSI {v:.0f} 과열" if v >= 70 else None)),
    ("cmf_21", lambda v: "자금 유입(CMF+)" if v >= 0.08 else
        ("자금 유출(CMF−)" if v <= -0.08 else None)),
    ("vol_ratio_20", lambda v: f"거래량 급증 ×{v:.1f}" if v >= 1.5 else None),
    ("golden_cross", lambda v: "골든크로스" if v >= 1 else None),
    ("ichi_above_cloud", lambda v: "일목 구름 위" if v >= 1 else None),
    ("mom_21d", lambda v: f"1M {v * 100:+.0f}%" if abs(v) >= 0.08 else None),
    ("fund_score", lambda v: f"재무 {v:.0f}점 우수" if v >= 70 else
        (f"재무 {v:.0f}점 취약" if v <= 35 else None)),
]


def screener_drivers(feats: dict, importance: dict | None = None, top: int = 3) -> str:
    """스크리너 판단근거 — 전역 피처 중요도 순으로 개별 값 해석 → 상위 top 한글 드라이버.

    화이트리스트 규칙만 사용(설명 가능성) · SHAP 아님 — 모델 기여도가 아니라
    '이 종목의 두드러진 특징' 서술. 결측/해당 없음 → '—'. 순수.
    """
    feats = feats or {}
    imp = importance or {}
    rules = sorted(_DRIVER_RULES, key=lambda r: -float(imp.get(r[0], 0) or 0))
    out = []
    for feat, fn in rules:
        v = _try_float(feats.get(feat))
        if v is None:
            continue
        try:
            lab = fn(v)
        except Exception:
            lab = None
        if lab:
            out.append(lab)
        if len(out) >= top:
            break
    return " · ".join(out) if out else "—"


def eps_growth_fwd(metrics) -> float | None:
    """예상 EPS 증가율(%) = (Fwd EPS ÷ TTM EPS − 1)×100. 결측/비양수 TTM → None. 순수."""
    m = metrics or {}
    e0, e1 = _try_float(m.get("eps_ttm")), _try_float(m.get("eps_fwd"))
    if not e0 or e0 <= 0 or e1 is None:
        return None
    return (e1 / e0 - 1.0) * 100.0


def peg_textbook(metrics) -> dict | None:
    """교과서식 PEG = PER ÷ 예상 EPS 증가율(%) (Fwd/TTM 1년). 순수.

    yfinance trailingPegRatio(야후 자체 5년 기대성장 기반)와 다를 수 있어
    정의가 투명한 직접 계산을 표시 기본값으로 쓴다. 성장률 ≤0 → None(정직).
    반환 {"peg", "growth_pct", "per", "yahoo"} | None.
    """
    m = metrics or {}
    per = _try_float(m.get("per"))
    g = eps_growth_fwd(m)
    if not per or per <= 0 or not g or g <= 0:
        return None
    return {"peg": per / g, "growth_pct": g, "per": per,
            "yahoo": _try_float(m.get("peg"))}


# 스크리너 피처 표시 메타 — (한글 라벨, 카테고리, 포맷 kind)
# kind: price($)·pct(분수→%)·ratio(배율→%)·osc(1dp)·num·vol(축약)·flag(✓/—)·beta(2dp)
_FEAT_META = {
    "sma_5": ("5일 이평", "가격·이평", "price"), "sma_10": ("10일 이평", "가격·이평", "price"),
    "sma_20": ("20일 이평", "가격·이평", "price"), "sma_50": ("50일 이평", "가격·이평", "price"),
    "sma_200": ("200일 이평", "가격·이평", "price"),
    "ema_12": ("EMA 12", "가격·이평", "price"), "ema_26": ("EMA 26", "가격·이평", "price"),
    "ema_50": ("EMA 50", "가격·이평", "price"),
    "bb_mid_20": ("볼린저 중심", "가격·이평", "price"),
    "bb_upper_20": ("볼린저 상단", "가격·이평", "price"),
    "bb_lower_20": ("볼린저 하단", "가격·이평", "price"),
    "bb_pct_b_20": ("볼린저 %B", "가격·이평", "num"),
    "bb_bw_20": ("볼린저 밴드폭", "변동성", "num"),
    "ichi_tenkan": ("일목 전환선", "가격·이평", "price"),
    "ichi_kijun": ("일목 기준선", "가격·이평", "price"),
    "ichi_senkou_a": ("일목 선행A", "가격·이평", "price"),
    "ichi_senkou_b": ("일목 선행B", "가격·이평", "price"),
    "mom_1d": ("1일 수익률", "모멘텀", "pct"), "mom_5d": ("5일 모멘텀", "모멘텀", "pct"),
    "mom_10d": ("10일 모멘텀", "모멘텀", "pct"), "mom_21d": ("1개월 모멘텀", "모멘텀", "pct"),
    "mom_63d": ("3개월 모멘텀", "모멘텀", "pct"), "mom_126d": ("6개월 모멘텀", "모멘텀", "pct"),
    "excess_mom_20d": ("QQQ대비 20일", "모멘텀", "pct"),
    "excess_mom_60d": ("QQQ대비 60일", "모멘텀", "pct"),
    "close_vs_52w_high": ("52주 고점 대비", "모멘텀", "ratio"),
    "close_vs_52w_low": ("52주 저점 대비", "모멘텀", "ratio"),
    "close_vs_sma20": ("20일선 대비", "모멘텀", "ratio"),
    "close_vs_sma50": ("50일선 대비", "모멘텀", "ratio"),
    "disparity_20d": ("이격도 20일", "모멘텀", "num"),
    "disparity_60d": ("이격도 60일", "모멘텀", "num"),
    "disparity_120d": ("이격도 120일", "모멘텀", "num"),
    "price_accel_5d": ("가속도 5일", "모멘텀", "num"),
    "price_accel_20d": ("가속도 20일", "모멘텀", "num"),
    "rsi_14": ("RSI 14", "오실레이터", "osc"), "rsi_7": ("RSI 7", "오실레이터", "osc"),
    "macd": ("MACD", "오실레이터", "num"), "macd_signal": ("MACD 시그널", "오실레이터", "num"),
    "macd_hist": ("MACD 히스토그램", "오실레이터", "num"),
    "stoch_k": ("스토캐스틱 %K", "오실레이터", "osc"),
    "stoch_d": ("스토캐스틱 %D", "오실레이터", "osc"),
    "williams_r_14": ("윌리엄스 %R", "오실레이터", "osc"),
    "cci_20": ("CCI 20", "오실레이터", "osc"),
    "vol_10d": ("변동성 10일", "변동성", "pct"), "vol_21d": ("변동성 1개월", "변동성", "pct"),
    "vol_63d": ("변동성 3개월", "변동성", "pct"), "atr_14": ("ATR 14", "변동성", "num"),
    "vov_10_30": ("변동성의 변동성", "변동성", "num"),
    "vol_sma_20": ("거래량 20일 평균", "거래량", "vol"),
    "vol_ratio_20": ("거래량 배율(20일)", "거래량", "num"),
    "vol_zscore_20": ("거래량 z-score", "거래량", "num"),
    "obv": ("OBV 누적 흐름", "거래량", "vol"), "cmf_21": ("CMF 자금흐름", "거래량", "num"),
    "golden_cross": ("골든크로스", "신호", "flag"),
    "ema_bull_short": ("단기 EMA 정배열", "신호", "flag"),
    "ma5_above_ma20": ("5일>20일선", "신호", "flag"),
    "ichi_above_cloud": ("일목 구름 위", "신호", "flag"),
    "ichi_below_cloud": ("일목 구름 아래", "신호", "flag"),
    "ichi_cloud_bull": ("일목 구름 상승형", "신호", "flag"),
    "ichi_tk_cross_up": ("일목 전환>기준 크로스", "신호", "flag"),
    "ichi_tk_bull": ("일목 전환>기준", "신호", "flag"),
    "ichi_price_vs_kijun": ("가격/기준선 비", "신호", "num"),
    "beta_60d": ("베타 60일", "시장", "beta"), "beta_20d": ("베타 20일", "시장", "beta"),
    "beta_gamma": ("베타 감마", "시장", "beta"),
    "fg_score": ("공포·탐욕", "시장", "osc"), "vix": ("VIX", "시장", "osc"),
    "idx_rsi_d": ("지수 RSI 일봉", "시장", "osc"), "idx_rsi_w": ("지수 RSI 주봉", "시장", "osc"),
    "idx_rsi_m": ("지수 RSI 월봉", "시장", "osc"),
    "fund_score": ("재무 점수", "펀더멘털", "osc"),
    "surv_penalty": ("생존편향 감산", "기타", "num"), "sector_id": ("섹터 ID", "기타", "num"),
}


def _fmt_feat(v, kind: str) -> str:
    x = _try_float(v)
    if x is None:
        return "—"
    if kind == "flag":
        return "✓" if x >= 1 else "—"
    if kind == "pct":
        return f"{x * 100:+.1f}%"
    if kind == "ratio":
        return f"{x * 100:.0f}%"
    if kind == "price":
        return f"${x:,.2f}"
    if kind == "vol":
        a = abs(x)
        s = "-" if x < 0 else ""
        if a >= 1e9:
            return f"{s}{a / 1e9:,.1f}B"
        if a >= 1e6:
            return f"{s}{a / 1e6:,.1f}M"
        return f"{s}{a:,.0f}"
    if kind == "osc":
        return f"{x:.1f}"
    if kind == "beta":
        return f"{x:.2f}"
    return f"{x:.4g}"


def format_screener_features(feats: dict, importance: dict | None = None) -> list[dict]:
    """스크리너 전체 피처 → 표시행 [{지표, 값, 구분}] — 한글 라벨·스마트 포맷·중요도 순. 순수."""
    imp = importance or {}
    rows = []
    for k, v in (feats or {}).items():
        label, cat, kind = _FEAT_META.get(k, (k, "기타", "num"))
        rows.append({"_imp": float(imp.get(k, 0) or 0),
                     "지표": label, "값": _fmt_feat(v, kind), "구분": cat})
    rows.sort(key=lambda r: -r["_imp"])
    for r in rows:
        r.pop("_imp")
    return rows


# ── 포트폴리오 페이지 보강 (P1) — 순수 계산층 ────────────────────────────────

def growth_series(records: list) -> dict:
    """일별 히스토리 → 포트 vs QQQ 정규화(%) 시리즈 (첫 기록=0%). 순수.

    반환 {dates, port, qqq, n_days} — 레코드 <2 면 빈 dict (정직 생략).
    """
    rec = [r for r in (records or [])
           if _try_float(r.get("total_usd")) and _try_float(r.get("qqq_price"))]
    if len(rec) < 2:
        return {}
    p0 = float(rec[0]["total_usd"])
    q0 = float(rec[0]["qqq_price"])
    if p0 <= 0 or q0 <= 0:
        return {}
    return {"dates": [r.get("date") for r in rec],
            "port": [(float(r["total_usd"]) / p0 - 1) * 100 for r in rec],
            "qqq": [(float(r["qqq_price"]) / q0 - 1) * 100 for r in rec],
            "n_days": len(rec)}


def fx_attribution(records: list, days: int = 30) -> dict:
    """기간 수익 분해 — $수익률·₩수익률·환율 기여(%p). 순수.

    환율 기여 = (1+₩수익)/(1+$수익) − 1 (원화 투자자 관점의 환차 몫).
    """
    rec = [r for r in (records or [])
           if _try_float(r.get("total_usd")) and _try_float(r.get("total_krw"))]
    if len(rec) < 2:
        return {}
    win = rec[-min(len(rec), max(2, days)):]
    u0, u1 = float(win[0]["total_usd"]), float(win[-1]["total_usd"])
    k0, k1 = float(win[0]["total_krw"]), float(win[-1]["total_krw"])
    if u0 <= 0 or k0 <= 0:
        return {}
    usd_ret = u1 / u0 - 1
    krw_ret = k1 / k0 - 1
    return {"usd_ret": usd_ret * 100, "krw_ret": krw_ret * 100,
            "fx_ret": ((1 + krw_ret) / (1 + usd_ret) - 1) * 100,
            "window_days": len(win), "from": win[0].get("date"), "to": win[-1].get("date")}


def rebalance_gaps(holdings: list, targets: dict) -> dict:
    """현재 vs 목표 비중 갭 — **목표가 설정된 종목만** 비교 (순수).

    targets: {ticker: 분수} = 봇 `/holding target` 설정(성장주 슬리브). 목표 미설정
    종목(QQQI·SGOV 등 바벨 안전/인컴 축 — 별도 규칙 관리)은 갭 계산에서 제외하고
    `untargeted` 로 따로 반환 — '목표 0% = 전량 축소' 오해 방지. 표시 전용.
    반환 {"gaps": [...|갭| 내림차순], "untargeted": [ticker...], "target_sum_pct"}.
    """
    total = sum((h.get("value") or 0) for h in (holdings or []))
    if total <= 0 or not targets:
        return {}
    cur = {h["ticker"]: (h.get("value") or 0) / total * 100 for h in holdings}
    names = {h["ticker"]: h.get("name") or "" for h in holdings}
    gaps = []
    for t in sorted(targets):
        c = cur.get(t, 0.0)
        g = float(targets[t]) * 100
        gap = c - g
        gaps.append({"ticker": t, "name": names.get(t, ""), "cur": c, "tgt": g,
                     "gap_pp": gap, "usd_delta": -gap / 100 * total})
    gaps.sort(key=lambda r: -abs(r["gap_pp"]))
    return {"gaps": gaps,
            "untargeted": [t for t in cur if t not in targets],
            "target_sum_pct": sum(float(v) for v in targets.values()) * 100}


_CLASS_CASH = {"SGOV", "BIL", "SHV"}
_CLASS_LEV = {"QLD", "TQQQ", "SOXL", "UPRO", "SQQQ"}    # 레버리지 — Tier3 슬리브 (별도 분류)


def asset_class_of(ticker: str) -> str:
    """자산군 분류 (표시용) — 현금성/인컴(커버드콜)/레버리지/지수·팩터 ETF/개별주."""
    t = str(ticker).upper().split(".")[0]
    if t in _CLASS_CASH:
        return "현금성 (초단기 국채)"
    if t in _CLASS_LEV:
        return "레버리지 ETF (Tier3)"
    try:
        import etf_meta
        g = etf_meta.group_of(t)
        if g and "covered_call" in g:
            return "인컴 (커버드콜)"
        if g:
            return "지수·팩터 ETF"
    except Exception:
        pass
    try:
        from providers.etf_data import is_etf
        if is_etf(t):
            return "지수·팩터 ETF"
    except Exception:
        pass
    return "개별주"


def exposures(holdings: list) -> dict:
    """보유 → 섹터 노출(개별주)·자산군 분해 (%). 순수(정적 시드만)."""
    total = sum((h.get("value") or 0) for h in (holdings or []))
    if total <= 0:
        return {}
    try:
        from sp500_meta import SECTOR_KR
    except Exception:
        SECTOR_KR = {}
    sec: dict = {}
    cls: dict = {}
    for h in holdings:
        t = h.get("ticker", "")
        w = (h.get("value") or 0) / total * 100
        c = asset_class_of(t)
        cls[c] = cls.get(c, 0) + w
        label = (SECTOR_KR.get(t) or "기타·해외") if c == "개별주" else c
        sec[label] = sec.get(label, 0) + w
    return {"sector": dict(sorted(sec.items(), key=lambda x: -x[1])),
            "class": dict(sorted(cls.items(), key=lambda x: -x[1]))}


def market_temperature(*, fear_greed=None, rsi_w=None, per_pctile_20y=None,
                       peg=None, drawdown_pct=None) -> dict | None:
    """🌡️ 시장 온도계 ∈[-1,1] — −1 과열(신중) ↔ +1 공포·저평가(분할매수 우호). 순수.

    DCA(적립) 투자자 관점의 **역발상** 종합: 공포탐욕 낮을수록 +·주봉 RSI 과열 −·
    PER 역사 백분위 높을수록 −·PEG 낮을수록 +·QQQ 낙폭 깊을수록 +(바벨 철학).
    표시·참고용 — 매매신호 아님. 실행 규칙은 Phase(DCA 배율)가 담당. 재료 <2 → None.
    """
    def clamp(x):
        return max(-1.0, min(1.0, x))

    comps = []                                       # (weight, score, label)
    fg = _try_float(fear_greed)
    if fg is not None:
        comps.append((1.0, clamp((50.0 - fg) / 40.0), f"공포탐욕 {fg:.0f}"))
    rw = _try_float(rsi_w)
    if rw is not None:
        comps.append((1.0, clamp((55.0 - rw) / 30.0), f"주봉 RSI {rw:.0f}"))
    pct = _try_float(per_pctile_20y)
    if pct is not None:
        comps.append((1.0, clamp((60.0 - pct) / 40.0), f"PER 20y {pct:.0f}%ile"))
    pg = _try_float(peg)
    if pg is not None and pg > 0:
        comps.append((0.7, clamp((1.4 - pg) / 0.9), f"PEG {pg:.2f}"))
    dd = _try_float(drawdown_pct)
    if dd is not None:
        comps.append((0.8, clamp(-dd / 15.0), f"QQQ 낙폭 {dd:+.1f}%"))
    if len(comps) < 2:
        return None
    wsum = sum(w for w, _, _ in comps)
    score = clamp(sum(w * s for w, s, _ in comps) / wsum)
    return {"score": score,
            "sub": " · ".join(lab for _, _, lab in comps[:4]),
            "n": len(comps)}


def top_feature_bars(feats: dict, importance: dict | None = None, top: int = 8) -> dict:
    """모델 중요도 상위 피처 → 바 차트 재료 (순수).

    반환 {labels: ['6개월 모멘텀 · +11.2%', ...], values: [중요도...]} — '모델이 이
    종목에서 무엇을 보는가'를 값과 함께 시각화. 중요도 없으면 빈 dict.
    """
    imp = {k: float(v) for k, v in (importance or {}).items() if v}
    if not imp or not feats:
        return {}
    labels, values = [], []
    for k, w in sorted(imp.items(), key=lambda x: -x[1]):
        if k not in feats:
            continue
        meta = _FEAT_META.get(k, (k, "기타", "num"))
        labels.append(f"{meta[0]} · {_fmt_feat(feats[k], meta[2])}")
        values.append(w)
        if len(labels) >= top:
            break
    return {"labels": labels, "values": values} if labels else {}


def rank_badge(rank) -> str:
    """순위 배지 — 1~3위 메달·이하 숫자 (순수)."""
    r = _try_float(rank)
    if r is None:
        return "—"
    r = int(r)
    return {1: "🥇 1", 2: "🥈 2", 3: "🥉 3"}.get(r, str(r))


def rank_move(rank, prev_rank) -> str:
    """직전 실행 대비 순위 변동 — ▲n 상승·▼n 하락·〓 유지·NEW 신규 (순수)."""
    r = _try_float(rank)
    if r is None:
        return "—"
    p = _try_float(prev_rank)
    if p is None:
        return "NEW"
    d = int(p) - int(r)
    return f"▲{d}" if d > 0 else (f"▼{-d}" if d < 0 else "〓")


def entry_levels(price, supports: list, resistances: list, fairs: list) -> dict:
    """진입 레벨 가이드 조립 (순수) — 기술적 지지/저항 + 밸류 기준가 합성.

    supports/resistances/fairs: [(라벨, 가격)]. 반환:
    {entries: [(라벨, 가격, 현재가比%)] 현재가 아래 근접순 최대 3 — 분할 진입 후보 레벨,
     resists: 위쪽 최대 2, fairs: 유효 밸류 기준가+갭, fair_gap_pct: 평균 기준가 대비}
    **레벨 후보 서술이지 예측/매매신호 아님** — 캡션 정직 병기 전제.
    """
    p = _try_float(price)
    if not p or p <= 0:
        return {}

    def _pct(v):
        return (v / p - 1) * 100

    # 지지 클러스터링 — 1.5% 이내 재료를 존으로 묶음 (겹침 = 강도·신뢰)
    raw = sorted(((lab, float(v)) for lab, v in supports
                  if _try_float(v) and 0 < float(v) < p), key=lambda x: -x[1])
    zones = []
    for lab, v in raw:
        if zones and (zones[-1]["lo"] / v - 1) < 0.015:
            z = zones[-1]
            z["lo"] = min(z["lo"], v)
            z["hi"] = max(z["hi"], v)
            z["labels"].append(lab)
        else:
            zones.append({"lo": v, "hi": v, "labels": [lab]})
    ent = []
    for z in zones[:3]:
        mid = (z["lo"] + z["hi"]) / 2
        ent.append((" + ".join(z["labels"][:3]), mid))
        z["mid"] = mid
        z["n"] = len(z["labels"])
    res = sorted(((lab, float(v)) for lab, v in resistances
                  if _try_float(v) and float(v) > p),
                 key=lambda x: x[1])[:2]
    fv = [(lab, float(v)) for lab, v in fairs if _try_float(v) and float(v) > 0]
    if not ent and not fv:
        return {}
    gap = None
    if fv:
        avg = sum(v for _, v in fv) / len(fv)
        gap = _pct(avg)
    return {"price": p,
            "entries": [(lab, v, _pct(v)) for lab, v in ent],
            "zones": [{**z, "pct": _pct(z["mid"])} for z in zones[:3]],
            "resists": [(lab, v, _pct(v)) for lab, v in res],
            "fairs": [(lab, v, _pct(v)) for lab, v in fv],
            "fair_gap_pct": gap}


def twr_series(records: list, flows_by_date: dict) -> dict:
    """시간가중 수익률(TWR) — 일별 총액 + 외부 현금흐름(거래 원장) 보정 (순수).

    r_t = (V_t − F_t) / V_{t−1} − 1  (F_t = 당일 순유입 — 적립 매수 +, 매도 −).
    적립이 수익처럼 보이는 단순 총액 왜곡을 제거. 반환 {dates, twr[%], simple[%],
    flows_total} — 레코드 <2 → {}.
    """
    rec = [r for r in (records or []) if _try_float(r.get("total_usd"))]
    if len(rec) < 2:
        return {}
    base = float(rec[0]["total_usd"])
    if base <= 0:
        return {}
    dates, twr, simple = [rec[0].get("date")], [0.0], [0.0]
    idx, prev, flows_total = 1.0, base, 0.0
    for r in rec[1:]:
        v = float(r["total_usd"])
        f = float((flows_by_date or {}).get(r.get("date"), 0.0))
        flows_total += f
        if prev > 0:
            idx *= max(0.0, (v - f) / prev)
        dates.append(r.get("date"))
        twr.append((idx - 1) * 100)
        simple.append((v / base - 1) * 100)
        prev = v
    return {"dates": dates, "twr": twr, "simple": simple,
            "flows_total": flows_total, "n_days": len(rec)}


def load_kr_holdings(path: str | None = None) -> dict:
    """국내(KR)북 보유 — 키움 동기화 스냅샷 domestic 섹션 (원화). graceful {}."""
    snap = _load_snap(path)
    dom = snap.get("domestic") or {}
    rows = []
    for h in dom.get("holdings") or []:
        sh = _try_float(h.get("shares")) or 0
        cur = _try_float(h.get("current_price")) or 0
        rows.append({"name": h.get("name") or h.get("ticker") or "?",
                     "shares": sh, "avg": _try_float(h.get("avg_price")),
                     "cur": cur, "value": sh * cur,
                     "ret": _try_float(h.get("return_pct")),
                     "pnl": _try_float(h.get("pnl_krw"))})
    if not rows:
        return {}
    total = sum(r["value"] for r in rows)
    return {"rows": rows, "total": total,
            "last_sync": snap.get("last_domestic_sync")}
