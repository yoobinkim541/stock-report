"""providers/intrinsic.py — 내재가치 모델 (DDM·RIM).

핵심 정직성:
- **DDM**(배당할인, Gordon)은 고배당·성숙주에만 유효. 저배당 성장주는 구조적
  저평가로 나옴 → payout<40% 면 ddm_reliable=False 로 표시.
- **RIM**(잔여이익)이 범용 1차 추정 — 고ROE의 보유가치를 포착(저배당주도 작동).
- 단일값 금지: r(자본비용) 밴드로 범위 제시. ROE 영속 가정은 caveat 로 명시.
입력은 기존 earnings_data.valuation_metrics(roe·pbr·payout·div_yield) + 현재가.
"""
from __future__ import annotations


def ddm_value(d0: float, g: float, r: float) -> float | None:
    """Gordon 성장 DDM: V = D0(1+g)/(r-g). r>g·D0>0 필수."""
    if d0 is None or d0 <= 0 or r <= g:
        return None
    return d0 * (1 + g) / (r - g)


def rim_value(bv0: float, roe: float, r: float, g: float) -> float | None:
    """잔여이익모델: V = BV0 + BV0(ROE-r)/(r-g). r>g·BV0>0 필수."""
    if bv0 is None or bv0 <= 0 or roe is None or r <= g:
        return None
    return bv0 + bv0 * (roe - r) / (r - g)


def _band(fn, r_band) -> dict | None:
    vals = sorted(v for v in (fn(r) for r in r_band) if v is not None)
    if not vals:
        return None
    return {"low": vals[0], "mid": vals[len(vals) // 2], "high": vals[-1]}


def _spot_price(ticker: str) -> float | None:
    try:
        import yfinance as yf
        # FastInfo 는 dict .get() 이 camelCase 키만 인식해 .get("last_price") 는 항상
        # None — 속성 접근(.last_price)만 정확 (dashboard/cached.py 와 동일 원인 버그)
        p = getattr(yf.Ticker(ticker).fast_info, "last_price", None)
        if p:
            return float(p)
        h = yf.Ticker(ticker).history(period="5d")
        if not h.empty:
            c = h["Close"].dropna()
            if len(c):
                return float(c.iloc[-1])
    except Exception:
        pass
    return None


def intrinsic(ticker: str, *, price: float | None = None,
              r_band=(0.08, 0.095, 0.11), g: float = 0.04) -> dict:
    """DDM·RIM 적정가 밴드 + 상승여력. 입력 결측 시 해당 모델은 None."""
    from providers import earnings_data
    m = earnings_data.valuation_metrics(ticker) or {}
    if price is None:
        price = _spot_price(ticker)

    out: dict = {"ticker": ticker, "price": price, "g": g, "r_band": r_band,
                 "payout": m.get("payout")}

    div_yield = m.get("div_yield")          # 이미 퍼센트 (0.98 = 0.98%)
    d0 = (div_yield / 100.0) * price if (div_yield and price) else 0.0
    out["ddm"] = _band(lambda r: ddm_value(d0, g, r), r_band) if d0 > 0 else None
    payout = m.get("payout")
    out["ddm_reliable"] = bool(payout is not None and payout >= 0.4)

    pbr, roe = m.get("pbr"), m.get("roe")
    bv0 = price / pbr if (pbr and price) else None
    out["rim"] = _band(lambda r: rim_value(bv0, roe, r, g), r_band) if (bv0 and roe is not None) else None

    fair = (out["rim"] or {}).get("mid") or (out["ddm"] or {}).get("mid")
    out["fair_mid"] = fair
    out["upside_pct"] = (fair / price - 1) * 100 if (fair and price) else None
    return out
