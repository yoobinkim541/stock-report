"""가격만으로 재현하는 X·논문 전략. 룩어헤드 없음. 네트워크 없음.

랭킹 열은 횡단면 z(±3). 필터 열(template, stage2, …)은 원시값.
현재 믹스 가중은 호출 시 kr_policy.DEFAULT_POLICY 가격축을 읽는다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

STRATEGY_IDS = (
    "current_price_mix", "jt_mom12", "george_hi52", "blitz_lowvol", "jegadeesh_rev",
    "mom6_1", "mom3_1", "novy_marx", "clenow", "bab", "residual_mom", "gray_qm",
    "minervini", "weinstein", "antonacci_abs", "antonacci_gem", "faber_sma",
    "donchian55", "qulla_proxy", "aqr_ensemble", "ewmac", "regime_switch",
    "current_stage2", "current_abs_filter", "current_not_extended",
    "jt_rev_avoid", "dual_mom", "vaa_breadth", "sma_stack", "vol_breakout", "han_trend",
    "oneil_stop8", "barroso_vol_mom12", "invvol_current",
)
OVERLAY_IDS = ("oneil_stop8", "barroso_vol_mom12", "invvol_current")
SELECTION_IDS = tuple(s for s in STRATEGY_IDS if s not in OVERLAY_IDS)


def zscore(s: pd.Series) -> pd.Series:
    """kr_policy_backtest._z 와 동일. 표준편차 0 → 0."""
    sd = s.std()
    if not sd or np.isnan(sd):
        return s * 0.0
    return ((s - s.mean()) / sd).clip(-3.0, 3.0)


def current_weights() -> dict[str, float]:
    from ml import kr_policy
    p = kr_policy.DEFAULT_POLICY
    return {
        "hi52": float(p["w_hi52"]),
        "vol_inv": float(p["w_lowvol"]),
        "mom12": float(p["w_mom12"]),
        "rev1": float(p["w_mom"]),
    }


def clenow_scores(log_px: np.ndarray) -> np.ndarray:
    """(T, N) 로그가격 → 연율 기울기 × R². Clenow 90일 창."""
    t_len, n = log_px.shape
    x = np.arange(t_len, dtype=float)
    xc = x - x.mean()
    varx = float(np.dot(xc, xc))
    if varx <= 0 or n == 0:
        return np.full(n, np.nan)
    y = np.array(log_px, dtype=float, copy=True)
    finite = np.isfinite(y).all(axis=0)
    y[:, ~finite] = np.nan
    y_mean = np.nanmean(y, axis=0)
    y = y - y_mean
    y[:, ~finite] = 0.0
    slope = (xc @ y) / varx
    resid = y - np.outer(xc, slope)
    ss_res = np.sum(resid * resid, axis=0)
    ss_tot = np.sum(y * y, axis=0)
    r2 = np.zeros(n)
    np.divide(ss_tot - ss_res, ss_tot, out=r2, where=ss_tot > 0)
    out = (np.exp(slope * 252.0) - 1.0) * r2
    out[~finite] = np.nan
    return out


def minervini_template(px, ma50, ma150, ma200, ma200_prev, low52, high52, rs_pct) -> pd.Series:
    """8규칙 충족 개수. RS 백분위는 호출부가 횡단면으로 넣는다."""
    return (
        ((px > ma150) & (px > ma200)).astype(int)
        + (ma150 > ma200).astype(int)
        + (ma200 > ma200_prev).astype(int)
        + ((ma50 > ma150) & (ma50 > ma200)).astype(int)
        + (px > ma50).astype(int)
        + (px >= low52 * 1.30).astype(int)
        + (px >= high52 * 0.75).astype(int)
        + (rs_pct >= 0.70).astype(int)
    )


def index_state(panels: dict, t: pd.Timestamp, uni) -> dict:
    """시점 t 유니버스의 시총가중 추세. 가중은 t, 수익률은 t 이하만."""
    empty = {"above_sma200": None, "above_sma210": None, "index_mom12": None}
    mc = panels["marcap"].loc[:t].iloc[-1].reindex(uni).astype(float)
    mc = mc.where(mc > 0)
    if int(mc.notna().sum()) < 10:
        return empty
    w = mc / mc.sum()
    hist = panels["ret"].loc[:t].reindex(columns=list(uni)).iloc[-420:]
    daily = hist.mul(w, axis=1).sum(axis=1).fillna(0.0)
    px = (1.0 + daily).cumprod()
    out = dict(empty)
    if len(px) >= 200:
        out["above_sma200"] = bool(px.iloc[-1] > px.iloc[-200:].mean())
    if len(px) >= 210:
        out["above_sma210"] = bool(px.iloc[-1] > px.iloc[-210:].mean())
    if len(px) >= 252:
        out["index_mom12"] = float(px.iloc[-1] / px.iloc[-252] - 1.0)
    return out


def enrich(panels: dict, t: pd.Timestamp, base: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """features_asof z-프레임에 논문 축을 붙인다. t 이하 가격만."""
    uni = list(base.index)
    state = index_state(panels, t, uni)
    h = panels["adj"].loc[:t].reindex(columns=uni).ffill().iloc[-260:]
    if len(h) < 253:
        return base, state
    px = h.iloc[-1]
    out = base.copy()
    mom12_full = px / h.iloc[-252] - 1.0
    mom12_raw = h.iloc[-22] / h.iloc[-253] - 1.0
    mom6 = h.iloc[-22] / h.iloc[-127] - 1.0
    mom3 = h.iloc[-22] / h.iloc[-64] - 1.0
    nm = h.iloc[-127] / h.iloc[-253] - 1.0
    ma50 = h.iloc[-50:].mean()
    ma150 = h.iloc[-150:].mean()
    ma200 = h.iloc[-200:].mean()
    ma200_prev = h.iloc[-221:-21].mean()
    ma150_prev = h.iloc[-171:-21].mean()
    low52 = h.iloc[-252:].min()
    high52 = h.iloc[-252:].max()
    rs = mom12_raw.rank(pct=True)
    out["template"] = minervini_template(px, ma50, ma150, ma200, ma200_prev, low52, high52, rs)
    out["stage2"] = ((px > ma150) & (ma150 > ma150_prev)).astype(float)
    out["above_sma210"] = (px > h.iloc[-210:].mean()).astype(float)
    out["not_extended"] = (px <= ma50 * 1.25).astype(float)
    out["near_high20"] = (px / h.iloc[-20:].max() >= 0.95).astype(float)
    out["mom12_raw"] = mom12_raw
    out["mom12_full_raw"] = mom12_full
    out["mom6_raw"] = mom6
    out["mom1_raw"] = px / h.iloc[-22] - 1.0
    out["mom3_full_raw"] = px / h.iloc[-64] - 1.0
    out["mom6_full_raw"] = px / h.iloc[-127] - 1.0
    out["ma_stack"] = ((px > ma50) & (ma50 > ma200)).astype(float)
    out["vaa13612"] = (
        12.0 * out["mom1_raw"] + 4.0 * out["mom3_full_raw"]
        + 2.0 * out["mom6_full_raw"] + out["mom12_full_raw"]
    )
    donchian_raw = px / h.iloc[-55:].max()
    out["donchian55_raw"] = donchian_raw
    out["amt_spike_raw"] = np.nan
    r = panels["ret"].loc[:t].reindex(columns=uni).iloc[-252:]
    up = (r.iloc[:-21] > 0).mean()
    vol20 = r.iloc[-20:].std()
    vol60 = r.iloc[-60:].std()
    mkt = r.mean(axis=1)
    mkt_c = (mkt - mkt.mean()).to_numpy()
    var_m = float(np.dot(mkt_c, mkt_c))
    y = (r - r.mean()).fillna(0.0).to_numpy()
    beta = (mkt_c @ y) / var_m if var_m > 0 else np.zeros(len(uni))
    r_win = r.iloc[:-21]
    mkt_w = r_win.mean(axis=1)
    mkt_wc = (mkt_w - mkt_w.mean()).to_numpy()
    var_w = float(np.dot(mkt_wc, mkt_wc))
    yw = (r_win - r_win.mean()).fillna(0.0).to_numpy()
    beta_w = (mkt_wc @ yw) / var_w if var_w > 0 else np.zeros(len(uni))
    resid = r_win.fillna(0.0).to_numpy() - np.outer(mkt_w.to_numpy(), beta_w)
    logp = np.log(h.iloc[-90:].clip(lower=1e-8).to_numpy())
    amt = panels["amount"].loc[:t].reindex(columns=uni)
    spike = amt.iloc[-5:].mean() / amt.iloc[-60:].mean().replace(0, np.nan)
    out["amt_spike_raw"] = spike.reindex(out.index)
    h_long = panels["adj"].loc[:t].reindex(columns=uni).ffill().iloc[-1000:]
    for lag in (3, 5, 10, 20, 50, 100, 200, 400, 600, 800, 1000):
        if len(h_long) < lag:
            continue
        out[f"han_{lag}"] = h_long.iloc[-lag:].mean() / px
    ranked = {
        "mom12_full": mom12_full,
        "mom6_1": mom6,
        "mom3_1": mom3,
        "nm_12_7": nm,
        "clenow": pd.Series(clenow_scores(logp), index=uni),
        "bab": pd.Series(-beta, index=uni),
        "resid_mom": pd.Series(resid.sum(axis=0), index=uni),
        "up_frac": up,
        "donchian55": px / h.iloc[-55:].max(),
        "ewmac": (px / ma50 - 1.0) / vol60.replace(0, np.nan),
        "tight20": -vol20,
        "vol_spike": spike,
    }
    for name, series in ranked.items():
        out[name] = zscore(series.reindex(out.index).astype(float))
    out["qulla"] = out["mom6_1"].fillna(0.0) + out["tight20"].fillna(0.0) + out["vol_spike"].fillna(0.0)
    return out, state

def _month_forward(ret: pd.DataFrame, t, t_next, codes) -> pd.Series:
    dates = ret.index
    start = int(dates.searchsorted(t, side="right"))
    end = int(dates.searchsorted(t_next, side="right"))
    if start >= end:
        return pd.Series(dtype=float)
    seg = ret.iloc[start:end].reindex(columns=list(codes))
    good = seg.notna().mean() >= 0.8
    seg = seg.loc[:, good].fillna(0.0)
    return (1.0 + seg).prod() - 1.0


def _cs_beta(x: pd.DataFrame, y: pd.Series) -> pd.Series | None:
    common = x.dropna().index.intersection(y.dropna().index)
    if len(common) < x.shape[1] + 8:
        return None
    design = np.column_stack([np.ones(len(common)), x.loc[common].to_numpy()])
    coef, *_ = np.linalg.lstsq(design, y.loc[common].to_numpy(), rcond=None)
    return pd.Series(coef[1:], index=x.columns)


def attach_han_trend(frames: list, ret: pd.DataFrame) -> None:
    """Han, Zhou & Zhu 2016. t 시점 점수는 t 이전에 끝난 달의 계수만 평균한다."""
    hist: list[pd.Series] = []
    for i, (_, frame, _) in enumerate(frames):
        cols = [c for c in frame.columns if c.startswith("han_")]
        if i >= 1 and cols:
            prev_t, prev, _ = frames[i - 1]
            prev_cols = [c for c in cols if c in prev.columns]
            beta = _cs_beta(prev[prev_cols], _month_forward(ret, prev_t, frames[i][0], prev.index))
            if beta is not None:
                hist.append(beta)
        frame["han_trend"] = 0.0
        if len(hist) < 12 or not cols:
            continue
        common = set(cols)
        for beta in hist[-12:]:
            common &= set(beta.index)
        common = sorted(common)
        if not common:
            continue
        mean_b = sum(beta.reindex(common).fillna(0.0) for beta in hist[-12:]) / 12.0
        x = frame[common].astype(float)
        x = x.fillna(x.mean())
        frame["han_trend"] = zscore(x.dot(mean_b))


def rank_top(frame: pd.DataFrame, weights: dict, k: int, mask: pd.Series | None = None) -> list[str]:
    score = pd.Series(0.0, index=frame.index)
    for name, w in weights.items():
        if name in frame.columns:
            score = score + float(w) * frame[name].fillna(0.0)
    if mask is not None:
        score = score[mask.reindex(score.index).fillna(False).astype(bool)]
    if score.empty:
        return []
    return [str(c) for c in score.nlargest(min(k, len(score))).index]


def picks_for(name: str, frame: pd.DataFrame, state: dict, k: int,
              current_w: dict | None = None) -> list[str]:
    """월말 선택. 오버레이 전략은 베이스 선택을 반환하고 시뮬이 변형한다."""
    w = current_w or current_weights()
    if name in ("current_price_mix", "oneil_stop8", "invvol_current"):
        return rank_top(frame, w, k)
    if name == "jt_mom12" or name == "barroso_vol_mom12":
        return rank_top(frame, {"mom12": 1.0}, k)
    if name == "george_hi52":
        return rank_top(frame, {"hi52": 1.0}, k)
    if name == "blitz_lowvol":
        return rank_top(frame, {"vol_inv": 1.0}, k)
    if name == "jegadeesh_rev":
        return rank_top(frame, {"rev1": -1.0}, k)
    if name == "mom6_1":
        return rank_top(frame, {"mom6_1": 1.0}, k)
    if name == "mom3_1":
        return rank_top(frame, {"mom3_1": 1.0}, k)
    if name == "novy_marx":
        return rank_top(frame, {"nm_12_7": 1.0}, k)
    if name == "clenow":
        return rank_top(frame, {"clenow": 1.0}, k)
    if name == "bab":
        return rank_top(frame, {"bab": 1.0}, k)
    if name == "residual_mom":
        return rank_top(frame, {"resid_mom": 1.0}, k)
    if name == "gray_qm":
        return rank_top(frame, {"mom12": 0.7, "up_frac": 0.3}, k)
    if name == "minervini":
        return rank_top(frame, {"mom12": 1.0}, k, frame["template"] >= 6)
    if name == "weinstein":
        return rank_top(frame, {"mom12": 1.0}, k, frame["stage2"] > 0)
    if name == "antonacci_abs":
        return rank_top(frame, {"mom12_full": 1.0}, k, frame["mom12_full_raw"] > 0)
    if name == "antonacci_gem":
        if state.get("index_mom12") is not None and state["index_mom12"] <= 0:
            return []
        return rank_top(frame, {"mom12_full": 1.0}, k)
    if name == "faber_sma":
        if state.get("above_sma210") is False:
            return []
        return rank_top(frame, {"mom12_full": 1.0}, k, frame["above_sma210"] > 0)
    if name == "donchian55":
        return rank_top(frame, {"donchian55": 1.0}, k)
    if name == "qulla_proxy":
        mask = (frame["near_high20"] > 0) & (frame["mom6_raw"] > 0.15)
        return rank_top(frame, {"qulla": 1.0}, k, mask)
    if name == "aqr_ensemble":
        return rank_top(frame, {"mom12": 0.25, "hi52": 0.25, "vol_inv": 0.25, "bab": 0.25}, k)
    if name == "ewmac":
        return rank_top(frame, {"ewmac": 1.0}, k)
    if name == "regime_switch":
        if state.get("above_sma200") is False:
            return rank_top(frame, {"vol_inv": 1.0}, k)
        return rank_top(frame, {"hi52": 1.0}, k)
    if name == "current_stage2":
        return rank_top(frame, w, k, frame["stage2"] > 0)
    if name == "current_abs_filter":
        return rank_top(frame, w, k, frame["mom12_raw"] > 0)
    if name == "current_not_extended":
        return rank_top(frame, w, k, frame["not_extended"] > 0)
    if name == "jt_rev_avoid":
        return rank_top(frame, {"mom12": 0.7, "rev1": -0.3}, k)
    if name == "dual_mom":
        if state.get("index_mom12") is not None and state["index_mom12"] <= 0:
            return []
        return rank_top(frame, {"mom12_full": 1.0}, k, frame["mom12_full_raw"] > 0)
    if name == "vaa_breadth":
        score = frame["vaa13612"]
        bad = score.isna() | (score <= 0)
        if float(bad.mean()) >= 0.5:
            return []
        return rank_top(frame, {"vaa13612": 1.0}, k, score > 0)
    if name == "sma_stack":
        return rank_top(frame, {"mom12": 1.0}, k, frame["ma_stack"] > 0)
    if name == "vol_breakout":
        mask = (frame["donchian55_raw"] >= 0.98) & (frame["amt_spike_raw"] >= 1.5)
        return rank_top(frame, {"mom12": 1.0}, k, mask)
    if name == "han_trend":
        if "han_trend" not in frame.columns or not float(frame["han_trend"].std() or 0):
            return []
        return rank_top(frame, {"han_trend": 1.0}, k)
    raise KeyError(name)


def simulate_with_cash(ret: pd.DataFrame, picks: dict, *,
                       buy_bps: float, sell_bps: float) -> pd.Series:
    """월말 선택 → 익일 편입. 빈 선택은 현금(0). 현금 구간을 빼지 않는다."""
    dates = ret.index
    rebal = sorted(picks)
    out = pd.Series(0.0, index=dates, dtype=float)
    active = pd.Series(False, index=dates)
    prev: set[str] = set()
    for i, t in enumerate(rebal):
        start = int(dates.searchsorted(t, side="right"))
        end_t = rebal[i + 1] if i + 1 < len(rebal) else dates[-1]
        end = int(dates.searchsorted(end_t, side="right"))
        if start >= end:
            continue
        cur = [c for c in picks[t] if c in ret.columns]
        if not cur:
            if prev:
                out.iloc[start] -= sell_bps / 1e4
            active.iloc[start:end] = True
            prev = set()
            continue
        seg = ret.iloc[start:end][cur].fillna(0.0)
        vals = (1.0 + seg).cumprod()
        port = vals.mean(axis=1)
        r = port / port.shift(1).fillna(1.0) - 1.0
        n_sell = len(prev - set(cur))
        n_buy = len(set(cur) - prev)
        denom = max(len(prev), 1) if prev else len(cur)
        cost = (n_sell / denom) * sell_bps / 1e4 + (n_buy / len(cur)) * buy_bps / 1e4
        r.iloc[0] -= cost
        out.iloc[start:end] = r.to_numpy()
        active.iloc[start:end] = True
        prev = set(cur)
    if not active.any():
        return out.iloc[0:0]
    return out.loc[active.idxmax():]


def simulate_invvol(ret: pd.DataFrame, picks: dict, *,
                    buy_bps: float, sell_bps: float, lookback: int = 60) -> pd.Series:
    """선택 종목을 직전 60일 역변동 비중으로 보유(드리프트). 비중은 t까지."""
    dates = ret.index
    rebal = sorted(picks)
    out = pd.Series(0.0, index=dates, dtype=float)
    active = pd.Series(False, index=dates)
    prev: set[str] = set()
    for i, t in enumerate(rebal):
        start = int(dates.searchsorted(t, side="right"))
        end_t = rebal[i + 1] if i + 1 < len(rebal) else dates[-1]
        end = int(dates.searchsorted(end_t, side="right"))
        if start >= end:
            continue
        cur = [c for c in picks[t] if c in ret.columns]
        if not cur:
            if prev:
                out.iloc[start] -= sell_bps / 1e4
            active.iloc[start:end] = True
            prev = set()
            continue
        vol = ret.loc[:t, cur].iloc[-lookback:].std().replace(0, np.nan)
        inv = (1.0 / vol).replace([np.inf, -np.inf], np.nan).fillna(0.0)
        if float(inv.sum()) <= 0:
            w = pd.Series(1.0 / len(cur), index=cur)
        else:
            w = inv / inv.sum()
        seg = ret.iloc[start:end][cur].fillna(0.0)
        vals = (1.0 + seg).cumprod()
        port = (vals * w).sum(axis=1)
        r = port / port.shift(1).fillna(1.0) - 1.0
        n_sell = len(prev - set(cur))
        n_buy = len(set(cur) - prev)
        denom = max(len(prev), 1) if prev else len(cur)
        cost = (n_sell / denom) * sell_bps / 1e4 + (n_buy / len(cur)) * buy_bps / 1e4
        r.iloc[0] -= cost
        out.iloc[start:end] = r.to_numpy()
        active.iloc[start:end] = True
        prev = set(cur)
    if not active.any():
        return out.iloc[0:0]
    return out.loc[active.idxmax():]


def simulate_stop(ret: pd.DataFrame, adj: pd.DataFrame, picks: dict, *,
                  stop: float, buy_bps: float, sell_bps: float) -> pd.Series:
    """리밸런스 종가 대비 stop 이면 그 종목은 월말까지 현금. 매도비용 부과."""
    dates = ret.index
    rebal = sorted(picks)
    out = pd.Series(0.0, index=dates, dtype=float)
    active = pd.Series(False, index=dates)
    prev: set[str] = set()
    for i, t in enumerate(rebal):
        start = int(dates.searchsorted(t, side="right"))
        end_t = rebal[i + 1] if i + 1 < len(rebal) else dates[-1]
        end = int(dates.searchsorted(end_t, side="right"))
        if start >= end:
            continue
        cur = [c for c in picks[t] if c in ret.columns and c in adj.columns]
        if not cur:
            if prev:
                out.iloc[start] -= sell_bps / 1e4
            active.iloc[start:end] = True
            prev = set()
            continue
        entry_pos = int(adj.index.searchsorted(t, side="right")) - 1
        if entry_pos < 0:
            continue
        entry = adj.iloc[entry_pos][cur].astype(float)
        stopped: set[str] = set()
        n_sell = len(prev - set(cur))
        n_buy = len(set(cur) - prev)
        denom = max(len(prev), 1) if prev else len(cur)
        entry_cost = (n_sell / denom) * sell_bps / 1e4 + (n_buy / len(cur)) * buy_bps / 1e4
        first = True
        for pos in range(start, end):
            alive = [c for c in cur if c not in stopped]
            r = float(ret.iloc[pos][alive].fillna(0.0).mean()) if alive else 0.0
            if first:
                r -= entry_cost
                first = False
            newly = 0
            px_row = adj.iloc[pos]
            for c in alive:
                px = px_row[c] if c in px_row.index else np.nan
                ent = entry.get(c)
                if pd.notna(px) and pd.notna(ent) and float(ent) > 0 and float(px) / float(ent) - 1.0 <= -stop:
                    stopped.add(c)
                    newly += 1
            if newly:
                r -= (newly / len(cur)) * sell_bps / 1e4
            out.iloc[pos] = r
            active.iloc[pos] = True
        prev = set(cur) - stopped
    if not active.any():
        return out.iloc[0:0]
    return out.loc[active.idxmax():]


def scale_to_vol(returns: pd.Series, *, target: float = 0.12, lookback: int = 63,
                 cap: float = 1.0) -> pd.Series:
    """전일까지 실현변동으로 노출 조절. cap=1 이면 레버리지 없음(모의 주식북 정합)."""
    rv = returns.rolling(lookback).std() * np.sqrt(252.0)
    scale = (target / rv.shift(1)).clip(lower=0.0, upper=cap)
    return returns * scale.fillna(1.0)


# 2018년 이전 목적함수가 양수이고 역할이 다른 슬리브만. 2019년 이후 숫자로 이 목록을 고르지 않는다.
# aqr_ensemble 은 이미 혼합이고 초과가 0에 가깝다. current_abs_filter 는 낙폭이 지수보다 컸다.
KR_ADVANTAGE_SLEEVES = (
    "current_stage2",
    "current_not_extended",
    "barroso_vol_mom12",
    "regime_switch",
    "invvol_current",
)


def _window_objective(strat: pd.Series, bench: pd.Series) -> float | None:
    """직전 창의 목적함수. 수익은 창 안에서만 본다."""
    from ml.adaptive.reward import max_drawdown, objective_score
    r = strat.dropna()
    if len(r) < 2:
        return None
    b = bench.reindex(r.index).fillna(0.0)
    nav_s = (1.0 + r).cumprod()
    nav_b = (1.0 + b).cumprod()
    excess = float(nav_s.iloc[-1] - nav_b.iloc[-1])
    return objective_score(
        excess,
        max_drawdown(list(nav_s.to_numpy())),
        max_drawdown(list(nav_b.to_numpy())),
    )


def blend_sleeves(
    sleeves: dict[str, pd.Series],
    bench: pd.Series,
    *,
    buy_bps: float,
    sell_bps: float,
    lookback: int = 756,
    min_obs: int = 252,
    rebalance: list | None = None,
    risk_on: pd.Series | None = None,
) -> tuple[pd.Series, pd.DataFrame]:
    """월말까지의 목적함수로만 비중을 정하고 다음 거래일부터 적용한다.

    양수 목적함수만 남긴다. 없으면 현금. 슬리브 수익은 이미 종목 비용을 포함한 것으로 보고,
    슬리브 사이 비중 변화에만 buy_bps·sell_bps 를 민다. risk_on 이 False 인 월말은 현금.
    """
    names = [name for name, series in sleeves.items() if series is not None and len(series)]
    if not names:
        return pd.Series(dtype=float), pd.DataFrame()
    frame = pd.DataFrame({name: sleeves[name] for name in names}).sort_index()
    frame = frame[~frame.index.duplicated(keep="last")]
    bench = bench.sort_index()
    bench = bench[~bench.index.duplicated(keep="last")]
    dates = frame.index
    if rebalance is None:
        marks = list(dates.to_series().groupby(dates.to_period("M")).tail(1).index)
    else:
        marks = [t for t in rebalance if t in dates]
    marks = sorted(marks)
    weights = pd.DataFrame(0.0, index=marks, columns=names)
    out = pd.Series(0.0, index=dates, dtype=float)
    active = pd.Series(False, index=dates)
    prev = {name: 0.0 for name in names}
    gate = None if risk_on is None else risk_on.sort_index().dropna()
    for i, t in enumerate(marks):
        start = int(dates.searchsorted(t, side="right"))
        end_t = marks[i + 1] if i + 1 < len(marks) else dates[-1]
        end = int(dates.searchsorted(end_t, side="right"))
        if start >= end:
            continue
        hist = frame.loc[:t].iloc[-lookback:]
        raw = {}
        for name in names:
            col = hist[name].dropna()
            if len(col) < min_obs:
                raw[name] = 0.0
                continue
            obj = _window_objective(col, bench.reindex(col.index).fillna(0.0))
            raw[name] = 0.0 if obj is None or obj <= 0.0 else float(obj)
        allow = True
        if gate is not None and len(gate.loc[:t]):
            allow = bool(gate.loc[:t].iloc[-1])
        total = sum(raw.values())
        cur = {
            name: (raw[name] / total if allow and total > 0.0 else 0.0)
            for name in names
        }
        weights.loc[t] = [cur[name] for name in names]
        cost = 0.0
        for name in names:
            dw = cur[name] - prev[name]
            if dw > 0.0:
                cost += dw * buy_bps / 1e4
            elif dw < 0.0:
                cost += (-dw) * sell_bps / 1e4
        held = frame.iloc[start:end]
        r = sum(cur[name] * held[name].fillna(0.0) for name in names)
        r.iloc[0] = float(r.iloc[0]) - cost
        out.iloc[start:end] = r.to_numpy()
        active.iloc[start:end] = True
        prev = cur
    if not active.any():
        return out.iloc[0:0], weights.iloc[0:0]
    first = active.idxmax()
    return out.loc[first:], weights

