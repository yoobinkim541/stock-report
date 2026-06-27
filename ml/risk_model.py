#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ml/risk_model.py — 포트폴리오 리스크 계측 + 성장최적 레버리지 계기판 (Tier 1).

기관급(Aladdin식) 리스크 분석을 개인 포트폴리오에 적용 — 공분산·리스크기여·유효분산·
팩터노출 + Kelly/낙폭예산 레버리지. **분석·표시 전용 — 배분(Phase·DCA·레버리지) 불변.**
(실제 구조적 레버리지 적용은 Tier 3에서 백테스트 게이트 통과 후.)

설계(정직):
- 단순수익률(pct_change) — 리스크기여 항등식 r_p=Σwᵢrᵢ 가 정확히 성립.
- Ledoit-Wolf 수축 공분산 ×252 — 소표본 노이즈↓·PSD 보장.
- 리스크기여 PCᵢ = wᵢ(Σw)ᵢ / wᵀΣw (Euler Σ=1; 음수기여 가능 — 정보).
- 유효분산 = 상관행렬 참여비 (Σλ)²/Σλ² ∈ [1,N].
- 팩터 QQQ(시장)+TLT(금리) OLS(절편 포함) + 개별 idio share(1−R²).
- Kelly는 μ에 극도 민감 → 낙폭예산 상한(robust·μ불필요)을 주 구속, half-Kelly는 맥락.

현재 시점 스냅샷 분석(룩어헤드 무관). 결측·단종목·이력부족 graceful(None/"").
import = numpy·pandas(상단) · sklearn(지연) · providers.market_data._history_cached(지연). 상향 import 금지.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

MIN_OBS = 60                 # 최소 거래일 (이보다 적으면 위험 추정 생략)
TRADING_DAYS = 252
DEFAULT_RF = 0.04            # 무위험(단기국채 ~4%)
DEFAULT_BUDGET = 0.50        # 낙폭예산 (사용자 확정)
FACTORS = ("QQQ", "TLT")     # 시장·금리 팩터
LEV_PROXY = "QQQ"            # 레버리지/MDD 장기 프록시
PROXY_PERIOD = "15y"
# look-through 주식 익스포저 배율 (현재 유효 레버리지 계산용)
_EQ_FACTOR = {"SGOV": 0.0, "BIL": 0.0, "SHV": 0.0, "QLD": 2.0, "SSO": 2.0,
              "TQQQ": 3.0, "UPRO": 3.0, "SPXL": 3.0}


# ══════════════════════════════════════════════════════════════════════
#  순수 통계 헬퍼
# ══════════════════════════════════════════════════════════════════════

def _ann_vol(returns_series) -> float:
    """연율 실현변동성 (일수익 std × √252)."""
    sd = float(np.asarray(returns_series, dtype=float).std(ddof=1)) if len(returns_series) > 1 else 0.0
    return sd * (TRADING_DAYS ** 0.5)


def _ann_geo_return(returns_series) -> float:
    """연율 기하수익률 (∏(1+r))^(252/n) − 1."""
    r = np.asarray(returns_series, dtype=float)
    n = len(r)
    if n < 2:
        return 0.0
    growth = float(np.prod(1.0 + r))
    if growth <= 0:
        return -1.0
    return growth ** (TRADING_DAYS / n) - 1.0


def _max_drawdown_from_prices(prices) -> float:
    """가격 시계열의 최대낙폭(양수 크기)."""
    p = pd.Series(prices, dtype=float)
    peak = p.cummax()
    return float(abs((p / peak - 1.0).min()))


def _corr_from_cov(cov) -> np.ndarray:
    """공분산 → 상관행렬 (대각 0 보호)."""
    C = np.asarray(cov, dtype=float)
    d = np.sqrt(np.clip(np.diag(C), 1e-18, None))
    return C / np.outer(d, d)


# ══════════════════════════════════════════════════════════════════════
#  리스크 계측 코어
# ══════════════════════════════════════════════════════════════════════

def fetch_returns(tickers, period: str = "1y") -> pd.DataFrame:
    """종목별 일간 단순수익률 → 교집합 정렬 DataFrame. 실패 종목은 제외(attrs['dropped'])."""
    from providers.market_data import _history_cached
    cols: dict[str, pd.Series] = {}
    dropped: list[str] = []
    for t in tickers:
        try:
            h = _history_cached(t, period)
            if h is None or getattr(h, "empty", True) or "Close" not in getattr(h, "columns", []):
                dropped.append(t)
                continue
            s = h["Close"].dropna()
            if getattr(s.index, "tz", None) is not None:
                s.index = s.index.tz_localize(None)
            r = s.pct_change().dropna()
            if len(r) < 2:
                dropped.append(t)
                continue
            cols[t] = r
        except Exception:
            dropped.append(t)
    df = pd.DataFrame(cols).dropna() if cols else pd.DataFrame()
    df.attrs["dropped"] = dropped
    return df


def shrunk_cov(returns, shrink: bool = True) -> np.ndarray:
    """연율 공분산. shrink=True → Ledoit-Wolf(노이즈↓·PSD), False → 표본cov(테스트용)."""
    R = returns.values if hasattr(returns, "values") else np.asarray(returns, dtype=float)
    R = np.atleast_2d(R)
    if shrink:
        from sklearn.covariance import LedoitWolf
        cov = LedoitWolf().fit(R).covariance_
    else:
        cov = np.cov(R, rowvar=False)
    return np.atleast_2d(np.asarray(cov, dtype=float)) * TRADING_DAYS


def risk_contributions(weights, cov):
    """종목별 위험기여 PCᵢ = wᵢ(Σw)ᵢ / wᵀΣw (Euler Σ=1). wᵀΣw≤0 → None."""
    w = np.asarray(weights, dtype=float)
    C = np.atleast_2d(np.asarray(cov, dtype=float))
    m = C @ w
    denom = float(w @ C @ w)
    if denom <= 0:
        return None
    port_vol = denom ** 0.5
    return {"pc": w * m / denom, "marginal": m / port_vol, "port_vol": port_vol}


def effective_bets(corr) -> float:
    """유효 독립 베팅 수 = 상관행렬 고유값 참여비 (Σλ)²/Σλ² ∈ [1,N]."""
    C = np.atleast_2d(np.asarray(corr, dtype=float))
    if C.shape[0] < 1:
        return float("nan")
    lam = np.clip(np.linalg.eigvalsh(C), 0.0, None)
    s2 = float((lam ** 2).sum())
    return float((lam.sum() ** 2) / s2) if s2 > 0 else float("nan")


def factor_betas(returns, factor_returns):
    """홀딩 수익률을 팩터(QQQ·TLT)에 OLS 회귀(절편 포함). 종목별 β·idio + 공선성 폴백.

    returns: DF(holdings), factor_returns: DF(factors). 동일 일자 교집합으로 정렬.
    """
    if returns is None or factor_returns is None or returns.empty or factor_returns.empty:
        return {}
    joined = pd.concat([returns, factor_returns], axis=1).dropna()
    if len(joined) < 2:
        return {}
    hold = list(returns.columns)
    facs = [c for c in factor_returns.columns if c in joined.columns]
    caveat = None
    F = joined[facs].values
    if len(facs) >= 2:
        cc = np.corrcoef(F, rowvar=False)
        if abs(float(cc[0, 1])) > 0.95:
            facs = facs[:1]
            F = joined[facs].values
            caveat = "팩터 공선성>0.95 — 단일팩터 폴백"
    X = np.column_stack([np.ones(len(joined)), F])
    beta: dict[str, dict] = {}
    for h in hold:
        y = joined[h].values
        coef, *_ = np.linalg.lstsq(X, y, rcond=None)
        resid = y - X @ coef
        ss_res = float((resid ** 2).sum())
        ss_tot = float(((y - y.mean()) ** 2).sum())
        r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
        beta[h] = {
            "alpha": float(coef[0]),
            "mkt": float(coef[1]) if len(coef) > 1 else 0.0,
            "rate": float(coef[2]) if len(coef) > 2 else 0.0,
            "r2": r2,
            "idio": max(0.0, 1.0 - r2),
        }
    return {"beta": beta, "factors": facs, "caveat": caveat}


# ══════════════════════════════════════════════════════════════════════
#  성장최적 레버리지 계기판 (분석·표시 — 라이브 레버리지 변경 아님)
# ══════════════════════════════════════════════════════════════════════

def growth_optimal_leverage(port_returns, rf: float = DEFAULT_RF):
    """Kelly 레버리지 밴드 L*=(μ−rf)/σ². μ에 민감하므로 보수/중도/추세 3가정 밴드로 반환."""
    sigma = _ann_vol(port_returns)
    if sigma <= 0:
        return None
    mu_tr = _ann_geo_return(port_returns)

    def kelly(mu):
        return (mu - rf) / (sigma ** 2)

    bands = {"conservative": kelly(0.06), "moderate": kelly(0.10), "trailing": kelly(mu_tr)}
    return {"sigma": sigma, "mu_trailing": mu_tr,
            "kelly": bands, "half": {k: v / 2.0 for k, v in bands.items()}}


def drawdown_budget_leverage(mdd_est, budget: float = DEFAULT_BUDGET):
    """낙폭예산 상한 = budget / 추정MDD (μ 불필요·robust). 순수함수."""
    if mdd_est is None or mdd_est <= 0:
        return None
    return {"mdd_est": float(mdd_est), "cap": budget / float(mdd_est), "budget": budget}


def ruin_metrics(leverage, mdd_est, port_sigma=None, budget: float = DEFAULT_BUDGET):
    """레버리지 L 하 기대 MDD ≈ L×MDD, 예산초과 여부, LETF 변동성감쇠 근사."""
    if leverage is None or mdd_est is None:
        return None
    implied = float(leverage) * float(mdd_est)
    out = {"implied_mdd": implied, "breach": implied > budget, "budget": budget}
    if port_sigma:
        out["letf_drag"] = 0.5 * (leverage - 1.0) * leverage * float(port_sigma) ** 2
    return out


def leverage_recommendation(growth, dd_cap_info, current: float = 1.0):
    """권고 = 낙폭예산 상한(생존 구속). Kelly half 밴드는 맥락으로 노출."""
    if not dd_cap_info:
        return None
    cap = dd_cap_info["cap"]
    return {
        "dd_cap": cap,
        "kelly_half": (growth or {}).get("half", {}),
        "recommend": cap,
        "current": current,
        "budget": dd_cap_info.get("budget", DEFAULT_BUDGET),
        "note": "μ가정에 따라 Kelly 가변 — 낙폭예산 상한(dd_cap)이 robust 구속",
    }


def _estimate_portfolio_mdd(port_sigma, proxy: str = LEV_PROXY):
    """장기 프록시(QQQ 15y) MDD를 포트 변동성 비율로 스케일 → 추정 MDD. (네트워크)"""
    from providers.market_data import _history_cached
    try:
        h = _history_cached(proxy, PROXY_PERIOD)
        if h is None or getattr(h, "empty", True) or "Close" not in getattr(h, "columns", []):
            return None
        px = h["Close"].dropna()
        proxy_mdd = _max_drawdown_from_prices(px)
        proxy_sigma = _ann_vol(px.pct_change().dropna())
        if proxy_sigma <= 0:
            return proxy_mdd
        return proxy_mdd * (port_sigma / proxy_sigma)
    except Exception as e:
        logger.debug("프록시 MDD 추정 실패: %s", e)
        return None


def _current_leverage(weights: dict) -> float:
    """현재 유효 주식 익스포저 (look-through; 현금 0·레버리지ETF 배율)."""
    return float(sum(w * _EQ_FACTOR.get(t, 1.0) for t, w in weights.items()))


# ══════════════════════════════════════════════════════════════════════
#  요약 오케스트레이터
# ══════════════════════════════════════════════════════════════════════

def portfolio_risk_summary(weights: dict, period: str = "1y",
                           budget: float = DEFAULT_BUDGET, rf: float = DEFAULT_RF):
    """전체 리스크 요약. T<60 or N<2 → None(graceful). 배분 변경 side-effect 없음."""
    if not weights:
        return None
    nz = {t: float(w) for t, w in weights.items() if w}
    if len(nz) < 2:
        return None
    norm0 = sum(nz.values()) or 1.0
    cur_lev = _current_leverage({t: w / norm0 for t, w in nz.items()})

    R = fetch_returns(list(nz), period)
    if R is None or R.empty or len(R) < MIN_OBS or R.shape[1] < 2:
        return None
    cols = list(R.columns)
    w = np.array([nz[t] for t in cols], dtype=float)
    w = w / w.sum()                                  # 분석집합 정규화 (pc는 스케일 불변)

    cov = shrunk_cov(R)
    rc = risk_contributions(w, cov)
    if rc is None:
        return None
    n_eff = effective_bets(_corr_from_cov(cov))

    port_ret = (R[cols] * w).sum(axis=1)
    growth = growth_optimal_leverage(port_ret, rf)
    mdd_est = _estimate_portfolio_mdd(rc["port_vol"])
    ddc = drawdown_budget_leverage(mdd_est, budget)
    rec = leverage_recommendation(growth, ddc, current=cur_lev)

    fac = fetch_returns(list(FACTORS), period)
    fb = factor_betas(R, fac) if not fac.empty else {}
    net = {}
    if fb.get("beta"):
        for key in ("mkt", "rate"):
            net[key] = float(sum(w[i] * fb["beta"].get(cols[i], {}).get(key, 0.0)
                                 for i in range(len(cols))))

    contribs = sorted(
        [(cols[i], float(w[i]), float(rc["pc"][i])) for i in range(len(cols))],
        key=lambda x: -x[2],
    )
    return {
        "port_vol": rc["port_vol"],
        "contributions": contribs,
        "n_eff": n_eff,
        "risk_hhi": float((rc["pc"] ** 2).sum()),
        "top_risk": contribs[0][0],
        "factor_net": net,
        "factor_caveat": fb.get("caveat"),
        "growth": growth,
        "mdd_est": mdd_est,
        "leverage": rec,
        "n_assets": len(cols),
        "dropped": R.attrs.get("dropped", []),
        "caveat": "과거 1년 실현 기반·미래 보장 아님·배분 변경 아님(참고용·국내 제외)",
    }


# ══════════════════════════════════════════════════════════════════════
#  표시 헬퍼 (None/실패 → "" graceful)
# ══════════════════════════════════════════════════════════════════════

def _bar(ratio: float, width: int = 10, fill: str = "▓", empty: str = "░") -> str:
    n = max(0, min(width, round(ratio * width)))
    return fill * n + empty * (width - n)


def risk_oneliner(weights: dict) -> str:
    """/portfolio 1줄 요약. 실패·이력부족 → ""."""
    try:
        s = portfolio_risk_summary(weights)
        if not s or not s.get("contributions"):
            return ""
        top = s["contributions"][0]
        lev = s.get("leverage") or {}
        rec = lev.get("recommend")
        rectxt = f" · 권고레버 {rec:.1f}x" if rec else ""
        return (f"📊 위험: 변동성 {s['port_vol']*100:.0f}% · 유효분산 {s['n_eff']:.1f}종목"
                f" · 최대기여 {top[0]} {top[2]*100:.0f}%{rectxt}")
    except Exception:
        return ""


def dollar_vs_risk_table(weights: dict) -> str:
    """/rebalance 첨부: 달러비중 vs 위험기여 표. 실패 → ""."""
    try:
        s = portfolio_risk_summary(weights)
        if not s or not s.get("contributions"):
            return ""
        L = ["━━━ ⚖ 달러 vs 리스크 비중 ━━━"]
        for tk, dollar, pc in s["contributions"]:
            flag = "  ⚠집중" if pc - dollar > 0.10 else ""
            L.append(f"  {tk:5s}  달러 {dollar*100:4.0f}%  →  위험 {pc*100:4.0f}%{flag}")
        lev = s.get("leverage") or {}
        if lev.get("recommend"):
            L.append(f"  · 권고 레버리지 상한 {lev['recommend']:.1f}x "
                     f"(낙폭예산 {lev['budget']*100:.0f}% · 현재 {lev.get('current',1.0):.2f}x)")
        return "\n".join(L)
    except Exception:
        return ""


def format_risk_report(summary, now: str | None = None) -> str:
    """/risk 전체 리포트. summary None → 안내문."""
    if not summary:
        return "🛡 리스크 분석 — 데이터 부족(이력 <60거래일 또는 보유 1종목 이하)"
    L = ["🛡 리스크 분석" + (f"  ({now})" if now else ""), "━" * 23,
         f"연변동성  {summary['port_vol']*100:.1f}%        "
         f"유효분산  {summary['n_eff']:.1f} / {summary['n_assets']}종목"]
    for tk, dollar, pc in summary["contributions"][:6]:
        L.append(f"  {tk:5s} {pc*100:4.0f}%  {_bar(max(0.0, pc))}  (달러 {dollar*100:.0f}%)")
    net = summary.get("factor_net") or {}
    if net:
        idio = ""
        L.append(f"팩터노출  QQQ β {net.get('mkt',0):.2f} · 금리 β {net.get('rate',0):+.2f}{idio}")
    lev = summary.get("leverage") or {}
    if lev.get("dd_cap"):
        half = (summary.get("growth") or {}).get("half") or {}
        vals = [v for v in half.values()]
        lo, hi = (min(vals), max(vals)) if vals else (0.0, 0.0)
        L += ["",
              f"⚙ 성장최적 레버리지 (낙폭예산 {lev['budget']*100:.0f}%)",
              f"  낙폭예산 상한  {lev['dd_cap']:.1f}x   (robust·μ불필요)",
              f"  half-Kelly     {lo:.1f}~{hi:.1f}x   (μ가정에 가변)",
              f"  현재 / 권고    {lev.get('current',1.0):.2f}x → {lev['recommend']:.1f}x"]
        ru = ruin_metrics(1.5, summary.get("mdd_est"), budget=lev["budget"])
        if ru:
            L.append(f"  ※ 1.5x → 기대 MDD {ru['implied_mdd']*100:.0f}% "
                     f"({'예산 초과' if ru['breach'] else '예산 이내'})")
    if summary.get("factor_caveat"):
        L.append(f"  ({summary['factor_caveat']})")
    L += ["", f"※ {summary.get('caveat','')}"]
    return "\n".join(L)
