"""dashboard/trendlines.py — 추세선·채널 자동 감지 (순수: numpy/pandas/scipy만).

알고리즘 (LLM 아님 — 재현성·비용·좌표 정밀도. docs 참조):
  피벗   : scipy.signal.find_peaks + ATR(Wilder) prominence — 평탄 고점 처리·봉 단위 불변
  추세선 : 최근 피벗 쌍 후보 → ≥2터치(0.25×ATR 허용오차) + 종가 이탈(1×ATR) 기각 +
           최신성 exp 가중 스코어 → 중복 억제 → 지지 2·저항 2 상한
  채널   : ln(Close) 회귀 ±2σ — 단기 60봉·장기 250봉, 라벨 up/down/flat(R²+ATR 상당 이동)

전 기하는 정수 봉 인덱스(0..n−1) 기준 — 주말 갭·봉 단위가 무력화되고 타임스탬프는
출력 시에만 매핑. 표시·참고용(매매 신호 아님).
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

# 기본 파라미터 (Plan 스펙)
MAX_PIVOTS = 12          # 유형별 피벗 상한 (조합 폭발 방지 — C(12,2)=66)
MIN_TOUCHES = 2
MIN_SPAN = 10            # 앵커 간 최소 봉 수
MAX_LINES_PER_SIDE = 2
PROJ_BARS = 5            # 우측 연장 봉 수
TOL_ATR = 0.25           # 터치 허용오차 (×ATR_med)
BREACH_ATR = 1.0         # 이탈 판정 (×ATR_med, 종가 기준)
DUP_ATR = 0.5            # 중복 선 판정 (양끝 값 차 ×ATR_med)
CH_SHORT, CH_LONG = 60, 250
CH_MIN_SHORT, CH_MIN_LONG = 48, 120
BAND_MULT = 2.0
R2_MIN = 0.30


def _atr14(df: pd.DataFrame) -> pd.Series:
    """Wilder ATR(14) — ml/features.atr 참조 구현(대문자 컬럼·dashboard 무결합)."""
    h, l, c = df["High"], df["Low"], df["Close"]
    prev = c.shift(1)
    tr = pd.concat([h - l, (h - prev).abs(), (l - prev).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / 14, adjust=False).mean()


def _pivots(df: pd.DataFrame, atr_med: float) -> tuple[np.ndarray, np.ndarray]:
    """(피벗 고점 idx, 피벗 저점 idx) — find_peaks(distance·prominence)."""
    from scipy.signal import find_peaks
    n = len(df)
    k = 3 if n <= 120 else 5
    prom = max(0.5 * atr_med, float(df["Close"].median()) * 0.0005)
    hi, _ = find_peaks(df["High"].values, distance=k, prominence=prom)
    lo, _ = find_peaks(-df["Low"].values, distance=k, prominence=prom)
    return hi, lo


def _fit_lines(df: pd.DataFrame, piv: np.ndarray, atr_med: float, *,
               kind: str, window_start: int) -> list[dict]:
    """피벗 쌍 → 유효 추세선 (지지 kind='support' 저점 기준 / 저항 'resistance' 고점)."""
    n = len(df)
    ys_all = df["Low"].values if kind == "support" else df["High"].values
    closes = df["Close"].values
    piv = piv[piv >= window_start][-MAX_PIVOTS:]
    if len(piv) < 2:
        return []
    tol = TOL_ATR * atr_med
    breach = BREACH_ATR * atr_med
    tau = max((n - window_start) / 4.0, 1.0)
    last_close = closes[-1]
    cands = []
    for a in range(len(piv)):
        for b in range(a + 1, len(piv)):
            i, j = int(piv[a]), int(piv[b])
            if j - i < MIN_SPAN:
                continue
            slope = (ys_all[j] - ys_all[i]) / (j - i)
            y_at = ys_all[i] + slope * (np.arange(i, n) - i)      # i..n-1 선 값
            y_last = y_at[-1]
            if abs(y_last - last_close) > 10 * atr_med:            # 기울기 새니티
                continue
            seg_close = closes[i:]
            if kind == "support":
                if np.any(seg_close < y_at - breach):              # 구간 내 이탈 기각
                    continue
                if last_close < y_last - tol:                       # 이미 깨진 선 기각
                    continue
                touch_pool = piv[(piv >= i)]
                touches = int(np.sum(np.abs(ys_all[touch_pool]
                                            - (ys_all[i] + slope * (touch_pool - i))) <= tol))
            else:
                if np.any(seg_close > y_at + breach):
                    continue
                if last_close > y_last + tol:
                    continue
                touch_pool = piv[(piv >= i)]
                touches = int(np.sum(np.abs(ys_all[touch_pool]
                                            - (ys_all[i] + slope * (touch_pool - i))) <= tol))
            if touches < MIN_TOUCHES:
                continue
            score = (touches - 2) + 2.0 * math.exp(-(n - 1 - j) / tau) \
                + (j - i) / max(n - window_start, 1)
            cands.append({"i": i, "j": j, "slope": slope, "touches": touches,
                          "score": score, "y0": float(ys_all[i]), "y_last": float(y_last)})
    # 결정적 정렬: 점수 → 터치 → 스팬 → 앞선 앵커
    cands.sort(key=lambda c: (-c["score"], -c["touches"], -(c["j"] - c["i"]), c["i"]))
    picked: list[dict] = []
    for c in cands:
        dup = False
        for p in picked:
            # 양끝(윈도 시작·마지막 봉)에서의 선 값 근접 → 동일 취급
            y_ws_c = c["y0"] + c["slope"] * (window_start - c["i"])
            y_ws_p = p["y0"] + p["slope"] * (window_start - p["i"])
            if (abs(y_ws_c - y_ws_p) < DUP_ATR * atr_med
                    and abs(c["y_last"] - p["y_last"]) < DUP_ATR * atr_med):
                dup = True
                break
        if not dup:
            picked.append(c)
        if len(picked) >= MAX_LINES_PER_SIDE:
            break
    return picked


def _proj_ts(index: pd.Index, bars: int) -> pd.Timestamp:
    """마지막 봉 + bars×중앙값(Δindex) — projection 타임스탬프."""
    if len(index) < 2:
        return index[-1]
    step = pd.Series(index).diff().median()
    return index[-1] + step * bars


def _channel(df: pd.DataFrame, length: int, name: str, atr_med: float) -> dict | None:
    closes = df["Close"].values[-length:]
    idx = df.index[-length:]
    if len(closes) < length or np.any(closes <= 0):
        return None
    x = np.arange(len(closes), dtype=float)
    logc = np.log(closes)
    slope, intercept = np.polyfit(x, logc, 1)
    fit = intercept + slope * x
    resid = logc - fit
    sigma = float(resid.std(ddof=1)) if len(resid) > 2 else 0.0
    ss_res = float((resid ** 2).sum())
    ss_tot = float(((logc - logc.mean()) ** 2).sum()) or 1e-12
    r2 = 1.0 - ss_res / ss_tot
    move = abs(slope) * (len(closes) - 1)                       # 로그 단위 총 이동
    thresh = 2.0 * atr_med / float(closes[-1])                  # 2×ATR 상당
    trend = "flat"
    if r2 >= R2_MIN and move >= thresh:
        trend = "up" if slope > 0 else "down"
    proj = PROJ_BARS
    xs = [0, len(closes) - 1 + proj]
    def _y(xv, off=0.0):
        return float(np.exp(intercept + slope * xv + off))
    out = {
        "kind": "channel",
        "label": {"up": f"{name} 상승채널", "down": f"{name} 하락채널",
                  "flat": f"{name} 횡보채널"}[trend] + f"({length})",
        "x0": idx[0], "x1": _proj_ts(df.index, proj),
        "y0": _y(xs[0]), "y1": _y(xs[1]),
        "upper": (_y(xs[0], BAND_MULT * sigma), _y(xs[1], BAND_MULT * sigma)),
        "lower": (_y(xs[0], -BAND_MULT * sigma), _y(xs[1], -BAND_MULT * sigma)),
        "path": None, "touches": 0,
        "meta": {"slope_per_bar": float(slope), "r2": round(r2, 3), "trend": trend,
                 "window": length, "score": r2, "tol": None, "projected_bars": proj},
    }
    if abs(slope) * (length - 1) > 0.25:                        # 로그 곡률 큼 → 폴리라인 폴백
        step = max(length // 24, 1)
        pxs = list(range(0, length, step)) + [length - 1]
        base = len(df) - length
        out["path"] = {
            "x": [df.index[base + p] for p in pxs],
            "upper": [_y(p, BAND_MULT * sigma) for p in pxs],
            "lower": [_y(p, -BAND_MULT * sigma) for p in pxs],
        }
    return out


def detect_trendlines(hist: pd.DataFrame, *, channels: tuple[str, ...] = ("short", "long"),
                      lines: bool = True) -> list[dict]:
    """자동 감지 — [{kind, label, x0/x1, y0/y1, upper/lower, path, touches, meta}]. 표시·참고용.

    hist: OHLCV DataFrame (대문자 컬럼·DatetimeIndex). n<30 → []. 결정적(동일 입력=동일 출력).
    """
    if hist is None or getattr(hist, "empty", True):
        return []
    cols = set(hist.columns)
    if not {"High", "Low", "Close"} <= cols:
        return []
    df = hist.dropna(subset=["Close"]).copy()
    if not df.index.is_monotonic_increasing:
        df = df.sort_index()
    n = len(df)
    if n < 30:
        return []
    atr = _atr14(df)
    window_start = max(0, n - 250)
    atr_med = float(atr.iloc[window_start:].median())
    if not atr_med or math.isnan(atr_med):
        atr_med = float(df["Close"].median()) * 0.005 or 1.0

    out: list[dict] = []
    if lines:
        hi, lo = _pivots(df, atr_med)
        proj_x = _proj_ts(df.index, PROJ_BARS)
        for kind, piv in (("support", lo), ("resistance", hi)):
            for c in _fit_lines(df, piv, atr_med, kind=kind, window_start=window_start):
                x_end = n - 1 + PROJ_BARS
                out.append({
                    "kind": kind,
                    "label": ("지지선" if kind == "support" else "저항선") + f" ({c['touches']}터치)",
                    "x0": df.index[c["i"]], "x1": proj_x,
                    "y0": c["y0"], "y1": float(c["y0"] + c["slope"] * (x_end - c["i"])),
                    "upper": None, "lower": None, "path": None,
                    "touches": c["touches"],
                    "meta": {"slope_per_bar": float(c["slope"]), "r2": None, "trend": None,
                             "window": n - window_start, "score": round(c["score"], 3),
                             "tol": round(TOL_ATR * atr_med, 6), "projected_bars": PROJ_BARS},
                })
    if "long" in channels and n >= CH_MIN_LONG:
        ch = _channel(df, min(CH_LONG, n), "장기", atr_med)
        if ch:
            out.append(ch)
        if "short" in channels and n >= CH_MIN_SHORT and min(CH_LONG, n) != CH_SHORT:
            ch2 = _channel(df, CH_SHORT, "단기", atr_med)
            if ch2:
                out.append(ch2)
    elif "short" in channels and n >= CH_MIN_SHORT:
        ch = _channel(df, min(CH_SHORT, n), "단기", atr_med)
        if ch:
            out.append(ch)
    return out[:6]
