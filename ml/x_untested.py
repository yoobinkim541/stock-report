"""일별·오버레이로만 검증할 수 있는 규칙. 룩어헤드 없음. 네트워크 없음.

Connors RSI(2): Short Term Trading Strategies That Work (2008).
  진입은 RSI(2)<5 이고 종가>200일선. 청산은 종가>5일선. 보유는 최대 k종목.
Gatev pairs: Pairs Trading (2006). 12개월 거리로 짝을 고르고, 형성기 표준편차 2배에서
  진입, 스프레드 0 회귀에서 청산. 형성기 가격만으로 짝을 고른다.
PAA: Keller & Keuning (2016). 위험자산 12개월 수익이 양수인 비율만큼만 주식을 보유한다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def wilder_rsi(px: pd.DataFrame, n: int = 2) -> pd.DataFrame:
    """Wilder RSI. 손실이 0이면 100, 이익이 0이면 0, 둘 다 0이면 50."""
    delta = px.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()
    avg_loss = loss.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()
    rsi = pd.DataFrame(np.nan, index=px.index, columns=px.columns)
    both = (avg_gain == 0) & (avg_loss == 0)
    up = (avg_loss == 0) & (avg_gain > 0)
    down = (avg_gain == 0) & (avg_loss > 0)
    normal = avg_loss > 0
    rs = avg_gain.where(normal) / avg_loss.where(normal)
    rsi = rsi.mask(normal, 100.0 - 100.0 / (1.0 + rs))
    return rsi.mask(up, 100.0).mask(down, 0.0).mask(both, 50.0)


def connors_weights(px: pd.DataFrame, eligible: pd.DataFrame, *,
                    k: int = 5, entry: float = 5.0,
                    trend: int = 200, exit_n: int = 5) -> pd.DataFrame:
    """종가 기준 목표 비중. 다음날 수익률에 적용한다.

    보유 중이면 5일선을 넘기 전까지 유지한다. 신규 진입은 RSI 조건과 200일선
    위, 그리고 eligible 이 모두 참일 때만. 같은 날 청산 조건이 참이면 진입하지 않는다.
    """
    rsi = wilder_rsi(px, 2)
    sma_trend = px.rolling(trend, min_periods=trend).mean()
    sma_exit = px.rolling(exit_n, min_periods=exit_n).mean()
    elig = eligible.reindex(index=px.index, columns=px.columns).fillna(False)
    enter = ((rsi < entry) & (px > sma_trend) & elig).fillna(False).to_numpy()
    leave = (px > sma_exit).fillna(False).to_numpy()
    valid = px.notna().to_numpy()
    state = np.zeros(px.shape[1], dtype=bool)
    held = np.zeros_like(enter)
    for i in range(px.shape[0]):
        state = np.where(leave[i], False, state | enter[i]) & valid[i]
        held[i] = state
    mask = pd.DataFrame(held, index=px.index, columns=px.columns)
    order = rsi.where(mask).rank(axis=1, method="first", ascending=True)
    keep = mask & (order <= k)
    weights = keep.astype(float)
    denom = weights.sum(axis=1).replace(0.0, np.nan)
    return weights.div(denom, axis=0).fillna(0.0)


def closest_pairs(prices: pd.DataFrame, n_pairs: int) -> list[tuple[str, str, float]]:
    """형성기 정규화 가격의 거리. 같은 종목을 두 짝에 쓰지 않는다."""
    if prices.shape[1] < 2 or len(prices) < 2:
        return []
    norm = prices / prices.iloc[0]
    arr = norm.to_numpy(dtype=float)
    diff = arr[:, :, None] - arr[:, None, :]
    dist = np.nansum(diff * diff, axis=0)
    names = list(prices.columns)
    iu = np.triu_indices(len(names), 1)
    order = np.argsort(dist[iu])
    used: set[int] = set()
    pairs = []
    for idx in order:
        i, j = int(iu[0][idx]), int(iu[1][idx])
        if i in used or j in used:
            continue
        used.add(i)
        used.add(j)
        pairs.append((names[i], names[j], float(dist[i, j])))
        if len(pairs) >= n_pairs:
            break
    return pairs


def _pair_state(spread: pd.Series, sd: float, entry_z: float) -> pd.Series:
    """+1 은 첫 종목 매수·둘째 매도. 형성기 표준편차만 쓴다."""
    if not np.isfinite(sd) or sd <= 0:
        return pd.Series(0, index=spread.index, dtype=int)
    z = entry_z * sd
    state = 0
    out = []
    for value in spread:
        if not np.isfinite(value):
            state = 0
        elif state == 0:
            if value > z:
                state = -1
            elif value < -z:
                state = 1
        elif state == 1 and value >= 0:
            state = 0
        elif state == -1 and value <= 0:
            state = 0
        out.append(state)
    return pd.Series(out, index=spread.index, dtype=int)


def pairs_weights(px: pd.DataFrame, marcap: pd.DataFrame, rebal: list,
                  *, n_names: int = 40, n_pairs: int = 5, form: int = 252,
                  trade_months: int = 6, entry_z: float = 2.0,
                  long_only: bool = False) -> pd.DataFrame:
    """비중복 형성·매매. 짝 선정은 형성기 종가까지만 본다."""
    weights = pd.DataFrame(0.0, index=px.index, columns=px.columns)
    dates = px.index
    for i, t in enumerate(rebal):
        if i + trade_months >= len(rebal):
            break
        if i % trade_months != 0:
            continue
        end = int(dates.searchsorted(t, side="right"))
        start = end - form
        if start < 0:
            continue
        trade_end_t = rebal[i + trade_months]
        trade_end = int(dates.searchsorted(trade_end_t, side="right"))
        if trade_end <= end:
            continue
        window = px.iloc[start:end]
        row = marcap.reindex(columns=px.columns).loc[t].dropna()
        complete = window.columns[window.notna().all()]
        row = row.reindex(complete).dropna()
        if len(row) < 4:
            continue
        names = list(row.nlargest(min(n_names, len(row))).index)
        chosen = closest_pairs(window[names], n_pairs)
        if not chosen:
            continue
        leg = 1.0 / len(chosen) if long_only else 0.5 / len(chosen)
        base = window.iloc[0]
        for a, b, _dist in chosen:
            formed = window[[a, b]]
            spread_f = formed[a] / base[a] - formed[b] / base[b]
            sd = float(spread_f.std(ddof=1))
            trade_px = px.iloc[end:trade_end][[a, b]]
            norm_a = trade_px[a] / base[a]
            norm_b = trade_px[b] / base[b]
            state = _pair_state(norm_a - norm_b, sd, entry_z)
            missing = trade_px.isna().any(axis=1)
            state = state.mask(missing, 0)
            sign = state.to_numpy()
            if long_only:
                add_a = np.where(sign > 0, leg, 0.0)
                add_b = np.where(sign < 0, leg, 0.0)
            else:
                add_a = sign * leg
                add_b = -sign * leg
            weights.loc[state.index, a] = weights.loc[state.index, a].to_numpy() + add_a
            weights.loc[state.index, b] = weights.loc[state.index, b].to_numpy() + add_b
    return weights


def paa_weights(mom: pd.Series, risky: list[str], protective: list[str]) -> dict[str, float]:
    """양수 모멘텀 위험자산 비율만큼 주식. 나머지는 모멘텀 1위 보호자산. 둘 다 음수면 현금."""
    risky_m = mom.reindex(risky).dropna()
    prot_m = mom.reindex(protective).dropna()
    if risky_m.empty:
        return {}
    good = risky_m[risky_m > 0]
    breadth = float(len(good) / len(risky_m))
    out: dict[str, float] = {}
    if len(good):
        for name in good.index:
            out[name] = breadth / len(good)
    room = 1.0 - breadth
    if room > 0 and len(prot_m):
        best = prot_m.idxmax()
        if float(prot_m.loc[best]) > 0:
            out[str(best)] = out.get(str(best), 0.0) + room
    return out


def meta_decisions(features: np.ndarray, labels: np.ndarray, *,
                   min_train: int = 36, threshold: float = 0.5) -> np.ndarray:
    """labels[i] 는 i 시점 결정의 사후 결과. 학습은 i 이전만 본다."""
    from sklearn.linear_model import LogisticRegression

    take = np.ones(len(labels), dtype=bool)
    for i in range(len(labels)):
        if i < min_train or not np.isfinite(features[i]).all():
            continue
        y = labels[:i]
        x = features[:i]
        ok = np.isfinite(y) & np.isfinite(x).all(axis=1)
        y, x = y[ok], x[ok]
        if len(y) < min_train or len(np.unique(y)) < 2:
            take[i] = bool(len(y) and y.mean() >= 0.5)
            continue
        model = LogisticRegression(max_iter=400)
        model.fit(x, y.astype(int))
        take[i] = bool(model.predict_proba(features[i:i + 1])[0, 1] >= threshold)
    return take


def simulate_signed(ret: pd.DataFrame, weights: pd.DataFrame, *,
                    buy_bps: float, sell_bps: float) -> pd.Series:
    """비중은 종가에 확정되고 다음날 수익률에 곱한다. 공매도 비중은 음수."""
    w = weights.reindex(index=ret.index, columns=ret.columns).fillna(0.0)
    held = w.shift(1).fillna(0.0)
    prev = w.shift(2).fillna(0.0)
    price = ret.reindex(columns=w.columns).fillna(0.0)
    opened_long = (held.clip(lower=0) - prev.clip(lower=0)).clip(lower=0)
    closed_long = (prev.clip(lower=0) - held.clip(lower=0)).clip(lower=0)
    opened_short = (prev.clip(upper=0) - held.clip(upper=0)).clip(lower=0)
    closed_short = (held.clip(upper=0) - prev.clip(upper=0)).clip(lower=0)
    buy = (opened_long + closed_short).sum(axis=1) * buy_bps / 1e4
    sell = (closed_long + opened_short).sum(axis=1) * sell_bps / 1e4
    net = (held * price).sum(axis=1) - buy - sell
    active = held.abs().sum(axis=1)
    if not bool(active.gt(0).any()):
        return net.iloc[0:0]
    return net.loc[active.gt(0).idxmax():]
