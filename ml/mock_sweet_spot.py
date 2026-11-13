"""모의투자 스위트 스팟. 연구용 ml/sweet_spot.py 와 분리.

한국: mom12+lowvol 북 50% + lowvol+hi52 북 50%. 각 북은 상위 5종목 균등.
미국: 앙상블 추세 + 금 방어 슬리브(N). QLD/QQQ/SGOV/GLD 비중.
둘 다 2026-10-27 제약(MDD≤지수) 안 최선점이며, 연 초과 10%p 통과는 아니다.
실계좌 주문 경로는 없다.
"""
from __future__ import annotations

OFFENSE = {"mom12": 0.6, "lowvol": 0.4}
DEFENSE = {"lowvol": 0.6, "hi52": 0.4}
SLEEVE_K = 5
SLEEVE_BUDGET = 0.5


def _feat(signal: dict, key: str) -> float | None:
    feats = signal.get("features") or {}
    raw = feats.get(key)
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if value != value:
        return None
    return value


def _axis_score(signal: dict, weights: dict[str, float]) -> float | None:
    num = den = 0.0
    for key, weight in weights.items():
        value = _feat(signal, key)
        if value is None:
            continue
        num += weight * value
        den += weight
    if den <= 0:
        return None
    return num / den


def kr_sleeve_weights(signals: list[dict], *, k: int = SLEEVE_K) -> dict[str, float]:
    """코드 → 투자예산 대비 목표 비중. 축 점수가 없으면 빈 dict."""
    offense = []
    defense = []
    for signal in signals:
        if float(signal.get("price") or 0) <= 0:
            continue
        code = str(signal.get("code") or "").strip()
        if not code:
            continue
        off = _axis_score(signal, OFFENSE)
        deff = _axis_score(signal, DEFENSE)
        if off is not None:
            offense.append((off, code))
        if deff is not None:
            defense.append((deff, code))
    if not offense or not defense:
        return {}
    off_codes = [code for _, code in sorted(offense, reverse=True)[:k]]
    def_codes = [code for _, code in sorted(defense, reverse=True)[:k]]
    weights: dict[str, float] = {}
    for codes in (off_codes, def_codes):
        share = SLEEVE_BUDGET / len(codes)
        for code in codes:
            weights[code] = weights.get(code, 0.0) + share
    return weights


def apply_kr_signals(signals: list[dict]) -> tuple[list[dict], int]:
    """슬리브 종목만 매수 대상으로 표시하고 (signals, 목표 종목 수)를 반환."""
    weights = kr_sleeve_weights(signals)
    if not weights:
        return signals, 0
    n = len(weights)
    for signal in signals:
        code = str(signal.get("code") or "")
        weight = weights.get(code, 0.0)
        if weight > 0:
            signal["is_buy"] = True
            signal["selection_score"] = weight
            signal["momentum_multiplier"] = n * weight
            signal["sweet_spot_weight"] = weight
        else:
            signal["is_buy"] = False
            signal["selection_score"] = -1.0
    return signals, n


def us_n_weights(closes: dict, *, target_vol: float = 0.25, vol_win: int = 20,
                 min_vix_term: float = 0.95) -> dict[str, float]:
    """N 슬리브의 마지막 시그널 비중. 이력 부족 시 빈 dict."""
    import pandas as pd

    def series(name: str):
        raw = closes.get(name)
        if raw is None:
            return None
        s = pd.Series(raw).dropna()
        return s if len(s) else None

    qqq, qld, sgov = series("QQQ"), series("QLD"), series("SGOV")
    if qqq is None or qld is None or sgov is None or len(qqq) < 252 or len(qld) < vol_win + 2:
        return {}
    idx = qqq.index.intersection(qld.index).intersection(sgov.index)
    if len(idx) < 252:
        return {}
    qqq, qld = qqq.reindex(idx), qld.reindex(idx)
    votes = sum((qqq > qqq.rolling(n, min_periods=n).mean()).astype(float)
                for n in (50, 100, 150, 200, 250)) / 5.0
    vol = qld.pct_change().rolling(vol_win).std() * (252 ** 0.5)
    lev_frac = (target_vol / vol).clip(upper=1.0)
    vix, vix3m = series("^VIX"), series("^VIX3M")
    if vix is not None and vix3m is not None and len(vix) and len(vix3m):
        term = vix3m.reindex(idx).ffill() / vix.reindex(idx).ffill()
        term_ok = term.isna() | (term >= min_vix_term)
    else:
        term_ok = pd.Series(True, index=idx)
    risk = votes.where(term_ok, 0.0)
    w_l = float((risk * lev_frac).iloc[-1])
    w_q = float(risk.iloc[-1]) - w_l
    w_c = 1.0 - w_l - w_q
    if w_l != w_l or w_q != w_q or w_c != w_c:
        return {}
    w_l, w_q, w_c = max(0.0, w_l), max(0.0, w_q), max(0.0, w_c)
    total = w_l + w_q + w_c
    if total <= 0:
        return {}
    w_l, w_q, w_c = w_l / total, w_q / total, w_c / total
    gld = series("GLD")
    if gld is None or len(gld) < 20:
        return {"QLD": w_l, "QQQ": w_q, "SGOV": w_c}
    return {"QLD": w_l, "QQQ": w_q, "SGOV": w_c / 2.0, "GLD": w_c / 2.0}


def etf_orders(weights: dict[str, float], positions: dict, nav: float, prices: dict,
               *, band: float = 0.25) -> list[dict]:
    """목표 ETF 비중 → 모의 주문. 바구니 밖 보유는 판다."""
    orders = []
    if nav <= 0 or not weights:
        return orders
    keep = {sym for sym, weight in weights.items() if weight > 0}
    for sym, pos in positions.items():
        shares = int((pos or {}).get("shares", 0) or 0)
        if shares > 0 and sym not in keep:
            orders.append({"symbol": sym, "side": "sell", "qty": shares, "reason": "스위트스팟이탈"})
    for sym, weight in weights.items():
        price = float(prices.get(sym) or 0)
        if weight <= 0 or price <= 0:
            continue
        held = int((positions.get(sym) or {}).get("shares", 0) or 0)
        target = int((nav * weight) / price)
        delta = target - held
        if target > 0 and abs(delta) * price / (nav * weight) <= band:
            continue
        if delta > 0:
            orders.append({"symbol": sym, "side": "buy", "qty": delta, "reason": "스위트스팟"})
        elif delta < 0 and held > 0:
            orders.append({"symbol": sym, "side": "sell", "qty": -delta, "reason": "스위트스팟축소"})
    return [o for o in orders if o["side"] == "sell"] + [o for o in orders if o["side"] == "buy"]
