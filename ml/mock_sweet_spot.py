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
_SLEEVE_LABEL = {"offense": "공격(mom12·lowvol)", "defense": "방어(lowvol·hi52)"}


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


def kr_sleeve_rankings(signals: list[dict]) -> dict[str, list[tuple[str, float]]]:
    """슬리브별 [(code, 축 점수)] 내림차순. 가격·코드·축 점수가 없는 신호는 빠진다."""
    rows: dict[str, list[tuple[float, str]]] = {"offense": [], "defense": []}
    for signal in signals:
        if float(signal.get("price") or 0) <= 0:
            continue
        code = str(signal.get("code") or "").strip()
        if not code:
            continue
        for name, axes in (("offense", OFFENSE), ("defense", DEFENSE)):
            score = _axis_score(signal, axes)
            if score is not None:
                rows[name].append((score, code))
    return {name: [(code, score) for score, code in sorted(items, reverse=True)]
            for name, items in rows.items()}


def _weights_from(rankings: dict[str, list[tuple[str, float]]], k: int) -> dict[str, float]:
    if not rankings.get("offense") or not rankings.get("defense"):
        return {}
    weights: dict[str, float] = {}
    for name in ("offense", "defense"):
        codes = [code for code, _ in rankings[name][:k]]
        share = SLEEVE_BUDGET / len(codes)
        for code in codes:
            weights[code] = weights.get(code, 0.0) + share
    return weights


def kr_sleeve_weights(signals: list[dict], *, k: int = SLEEVE_K) -> dict[str, float]:
    """코드 → 투자예산 대비 목표 비중. 축 점수가 없으면 빈 dict."""
    return _weights_from(kr_sleeve_rankings(signals), k)


def _kr_basis(ranks: dict[str, tuple[int, float] | None], weight: float, buffered: bool,
              k: int) -> str:
    parts = []
    for name in ("offense", "defense"):
        r = ranks.get(name)
        parts.append(f"{_SLEEVE_LABEL[name]} {r[0]}위({r[1]:.2f})" if r else f"{_SLEEVE_LABEL[name]} —")
    if weight > 0:
        return f"스위트스팟 선정 · {' · '.join(parts)} · 목표 {weight:.0%}"
    if buffered:
        return f"스위트스팟 유예 · {' · '.join(parts)} · 상위 {k}위 밖이지만 보유 시 유지"
    return f"스위트스팟 제외 · {' · '.join(parts)} · 상위 {k}위 밖"


def apply_kr_signals(signals: list[dict], *, k: int = SLEEVE_K,
                     exit_buffer: int = 0) -> tuple[list[dict], int]:
    """슬리브 종목만 매수 대상으로 표시하고 (signals, 목표 종목 수)를 반환.

    모든 신호에 실제 선택 근거(`selection_basis`, `sweet_spot_ranks`)를 남긴다 — 알림·원장은
    이 근거를 쓰고, 선택에 쓰이지 않는 _decision_v2 판단은 참고로만 보인다.
    exit_buffer>0: 어느 슬리브든 상위 k+exit_buffer 안인 비목표 종목은 is_buy 로 남기되
    목표 비중 0(신규·증액 없음)·목표보다 낮은 점수로 둔다. plan_rebalance 의 keep(top-N+buffer)에
    들어가 보유분은 팔지 않는다(경계 flip-flop 방지).
    """
    rankings = kr_sleeve_rankings(signals)
    weights = _weights_from(rankings, k)
    if not weights:
        return signals, 0
    n = len(weights)
    pos = {name: {code: (i + 1, score) for i, (code, score) in enumerate(rows)}
           for name, rows in rankings.items()}
    for signal in signals:
        code = str(signal.get("code") or "")
        ranks = {name: pos[name].get(code) for name in pos}
        weight = weights.get(code, 0.0)
        best = min((r[0] for r in ranks.values() if r), default=None)
        buffered = weight <= 0 and exit_buffer > 0 and best is not None and best <= k + exit_buffer
        signal["sweet_spot_ranks"] = {name: {"rank": r[0], "score": round(r[1], 4)}
                                      for name, r in ranks.items() if r}
        signal["selection_basis"] = _kr_basis(ranks, weight, buffered, k)
        if weight > 0:
            signal["is_buy"] = True
            signal["selection_score"] = weight
            signal["momentum_multiplier"] = n * weight
            signal["sweet_spot_weight"] = weight
        elif buffered:
            signal["is_buy"] = True
            signal["selection_score"] = -best / 1000.0   # 목표(>0) 아래 — 순위 좋은 순
            signal["momentum_multiplier"] = 0.0          # 목표금액 0 → 신규·증액 없음
            signal["sweet_spot_weight"] = 0.0
        else:
            signal["is_buy"] = False
            signal["selection_score"] = -1.0
    return signals, n


def us_n_state(closes: dict, *, target_vol: float = 0.25, vol_win: int = 20,
               min_vix_term: float = 0.95) -> dict:
    """N 슬리브의 마지막 시그널 — 비중과 그 근거. 이력 부족 시 빈 dict.

    반환 {weights, votes(0~1), term(VIX3M/VIX 또는 None), term_ok, qld_vol, lev_frac}
    """
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
    term = None
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
        weights = {"QLD": w_l, "QQQ": w_q, "SGOV": w_c}
    else:
        weights = {"QLD": w_l, "QQQ": w_q, "SGOV": w_c / 2.0, "GLD": w_c / 2.0}
    last_term = float(term.iloc[-1]) if term is not None else None
    return {
        "weights": weights,
        "votes": float(votes.iloc[-1]),
        "term": last_term if last_term == last_term else None,
        "term_ok": bool(term_ok.iloc[-1]),
        "qld_vol": float(vol.iloc[-1]),
        "lev_frac": float(lev_frac.iloc[-1]),
    }


def us_n_weights(closes: dict, **kwargs) -> dict[str, float]:
    """N 슬리브의 마지막 시그널 비중. 이력 부족 시 빈 dict."""
    return us_n_state(closes, **kwargs).get("weights", {})


def us_n_basis(state: dict) -> str:
    """us_n_state → 한 줄 근거(알림·원장용). 입력에 없는 값은 쓰지 않는다."""
    if not state:
        return ""
    parts = [f"QQQ 추세 {round(state['votes'] * 5)}/5 (50~250일 이평 위)"]
    if state.get("term") is not None:
        parts.append(f"VIX3M/VIX {state['term']:.2f}" + ("" if state.get("term_ok") else " 역전→위험 0"))
    vol = state.get("qld_vol")
    if vol is not None and vol == vol:
        parts.append(f"QLD 20일 변동 {vol:.0%}")
    weights = " ".join(f"{k} {v:.0%}" for k, v in state.get("weights", {}).items() if v > 0)
    return "스위트스팟 N · " + " · ".join(parts) + (f" → {weights}" if weights else "")


def etf_orders(weights: dict[str, float], positions: dict, nav: float, prices: dict,
               *, band: float = 0.25, cash: float | None = None,
               min_cash: float = 0.0) -> list[dict]:
    """목표 ETF 비중 → 모의 주문. 바구니 밖 보유는 판다.

    cash: 매수에 쓸 수 있는 현금(버퍼 적용 후). None 이면 상한 없음. 매수 총액이 이를 넘으면
    모든 매수를 같은 비율로 줄인다(매도 대금은 세지 않는다 — 주식 경로 plan_rebalance 와 동일).
    min_cash 미만이면 매수를 만들지 않는다(어차피 거부될 소액 주문 방지).
    """
    orders = []
    if nav <= 0 or not weights:
        return orders
    keep = {sym for sym, weight in weights.items() if weight > 0}
    for sym, pos in positions.items():
        shares = int((pos or {}).get("shares", 0) or 0)
        if shares > 0 and sym not in keep:
            orders.append({"symbol": sym, "side": "sell", "qty": shares, "reason": "스위트스팟이탈"})
    buys: list[tuple[str, int, float]] = []
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
            buys.append((sym, delta, price))
        elif delta < 0 and held > 0:
            orders.append({"symbol": sym, "side": "sell", "qty": -delta, "reason": "스위트스팟축소"})
    if cash is not None:
        avail = cash if cash >= min_cash else 0.0
        need = sum(qty * price for _, qty, price in buys)
        if need > avail:
            scale = max(0.0, avail) / need if need > 0 else 0.0
            buys = [(sym, int(qty * scale), price) for sym, qty, price in buys]
    for sym, qty, _ in buys:
        if qty > 0:
            orders.append({"symbol": sym, "side": "buy", "qty": qty, "reason": "스위트스팟"})
    return [o for o in orders if o["side"] == "sell"] + [o for o in orders if o["side"] == "buy"]
