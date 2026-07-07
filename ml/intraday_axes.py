#!/usr/bin/env python3
"""intraday_axes.py — 단기 트레이딩 판단 축·가드·청산·사이징·가상체결 (전부 순수 함수).

네트워크·파일 IO·env 접근 0 — 엔진(crons/intraday_mock_track)이 데이터를 조립해 주입하고,
여기서는 확정 분봉·호가 스냅샷만으로 판단한다(합성 데이터 단위 테스트 대상).

방향: **롱 전용 v1** — 모의 어댑터(kiwoom_mock·kis_mock)가 공매도 미지원.
룩어헤드 0: 모든 축은 확정 봉(직전 분까지)만 사용. 근거는 docs/intraday-mock-trading-design.md §1.
"""
from __future__ import annotations

import math

# ── 호가단위 (KRX 2023 개편·미국) ─────────────────────────────────────────────

_KR_TICKS = ((2_000, 1), (5_000, 5), (20_000, 10), (50_000, 50),
             (200_000, 100), (500_000, 500))


def kr_tick(price: float) -> float:
    for limit, tick in _KR_TICKS:
        if price < limit:
            return float(tick)
    return 1000.0


def us_tick(price: float) -> float:
    return 0.01


def tick_size(price: float, market: str) -> float:
    return kr_tick(price) if market.upper() == "KR" else us_tick(price)


# ── 축 (각 [0,1] — None 은 결측·재정규화 대상) ────────────────────────────────

def opening_range(df, minutes: int = 15):
    """세션 첫 minutes 개 확정 1m 봉의 (고가, 저가). 미확정(봉 부족)이면 None.

    df: 당일 세션 OHLCV DataFrame (확정 봉만). 장중 신규 편입 심볼(개장 미관측)은
    첫 봉 시각이 개장 후라 이 함수를 쓰지 말고 axis_orb 에 None 전달(축 결측).
    """
    if df is None or len(df) < minutes:
        return None
    head = df.iloc[:minutes]
    return float(head["High"].max()), float(head["Low"].min())


def axis_orb(close: float, or_range, vol_z_tod: float | None) -> float | None:
    """시가범위 상단 돌파 + 거래량 확인. or_range 미확정 → None(결측)."""
    if or_range is None or not close:
        return None
    or_hi, or_lo = or_range
    if or_hi <= or_lo or close <= or_hi:
        return 0.0
    base = 0.6                                   # 종가 기준 상단 돌파
    if vol_z_tod is not None and vol_z_tod >= 1.5:
        base += min(0.4, 0.2 + 0.1 * (vol_z_tod - 1.5))   # 거래량 확인 가점
    # 과확장 페널티 — 범위의 1.5배 이상 이탈은 늦은 추격
    ext = (close - or_hi) / max(or_hi - or_lo, 1e-9)
    if ext > 1.5:
        base *= 0.5
    return min(1.0, base)


def axis_vwap(vwap_dev_series, closes, opens) -> float | None:
    """VWAP 과매도 반전 / 리클레임. 시계열(확정 봉) 최소 10봉 필요."""
    try:
        n = len(vwap_dev_series)
    except TypeError:
        return None
    if n < 10:
        return None
    dev = [float(x) for x in vwap_dev_series[-n:]]
    cur, prev = dev[-1], dev[-2]
    finite = [d for d in dev if not math.isnan(d)]
    if len(finite) < 10 or math.isnan(cur) or math.isnan(prev):
        return None
    mean = sum(finite) / len(finite)
    std = math.sqrt(sum((d - mean) ** 2 for d in finite) / len(finite)) or 1e-9
    z = (cur - mean) / std
    last_up = float(closes[-1]) > float(opens[-1])           # 직전 봉 반전(양봉) 확인
    if z <= -2.0 and last_up:
        return min(1.0, 0.6 + 0.2 * (-z - 2.0))              # 과매도 반전
    if prev < 0 <= cur and last_up:
        return 0.6                                           # VWAP 리클레임
    return 0.0


def tod_vol_z(volume: float, hhmm: str, profile: dict) -> float | None:
    """시간대 정규화 거래량 z — 같은 분대(HH:MM)의 최근 세션 평균 대비. 표본 부족 None."""
    ent = (profile or {}).get(hhmm)
    if not ent or ent.get("n", 0) < 5 or not ent.get("std"):
        return None
    return (volume - ent["mean"]) / ent["std"]


def vol_z_fallback(volumes) -> float | None:
    """프로파일 부재 시 당일 자체 20봉 z (신뢰 강등 — 개장 U자형 미보정)."""
    vs = [float(v) for v in volumes]
    if len(vs) < 21:
        return None
    window, cur = vs[-21:-1], vs[-1]
    mean = sum(window) / len(window)
    std = math.sqrt(sum((v - mean) ** 2 for v in window) / len(window))
    if std <= 0:
        return None
    return (cur - mean) / std


def axis_volspike(vol_z: float | None, impulse_pct: float | None) -> float | None:
    """거래량 스파이크 모멘텀 — z≥3 + 임펄스 ≥ +0.5% 에서 만점 접근. 롱 전용(음의 임펄스 0)."""
    if vol_z is None or impulse_pct is None:
        return None
    if impulse_pct < 0.5 or vol_z < 2.0:
        return 0.0
    z_part = min(0.6, 0.3 + 0.15 * (vol_z - 2.0))
    imp_part = min(0.4, 0.2 + 0.2 * (impulse_pct - 0.5))
    return min(1.0, z_part + imp_part)


def obi(orderbook: dict | None) -> float | None:
    """호가 잔량 불균형 (Σbid−Σask)/(Σbid+Σask) ∈ [−1,1]. 호가 없으면 None."""
    if not orderbook:
        return None
    bid_q = sum(q for _, q in (orderbook.get("bids") or []))
    ask_q = sum(q for _, q in (orderbook.get("asks") or []))
    tot = bid_q + ask_q
    if tot <= 0:
        return None
    return (bid_q - ask_q) / tot


def axis_ofi(obi_samples) -> float | None:
    """최근(≤60초) OBI 표본 평균 → 롱 가점. 매수 우세 +0.3 부터 점수, 매도 우세 0.

    단독 진입 금지 — 확인용 축이라 최대 1 이지만 가중이 낮다(정책 기본 KR .15/US .05).
    """
    xs = [float(x) for x in (obi_samples or []) if x is not None]
    if not xs:
        return None
    m = sum(xs) / len(xs)
    if m <= 0.3:
        return 0.0
    return min(1.0, (m - 0.3) / 0.5)


def axis_news(events, symbol: str, now_epoch: float, *, window_min: int = 60) -> float | None:
    """뉴스 이벤트 창 내 방향×강도 → [0,1]. 창 내 이벤트 없으면 None(결측).

    events: [{symbols:[base…], epoch, direction(-1|0|1)|None, strength(1~5)|None}] —
    엔진이 news_spike 이벤트·LLM 라벨을 이 형태로 정규화해 전달. 롱 전용: 악재는 0(진입 억제).
    """
    if not events:
        return None
    best = None
    for e in events:
        if symbol not in (e.get("symbols") or []):
            continue
        age_min = (now_epoch - float(e.get("epoch") or 0)) / 60.0
        if age_min < 0 or age_min > window_min:
            continue
        d = e.get("direction")
        s = float(e.get("strength") or 3)
        if d is None or d == 0:
            val = 0.5                                        # 방향 미상 — 중립
        elif d > 0:
            val = min(1.0, 0.5 + 0.1 * s) * (1.0 - 0.5 * age_min / window_min)
        else:
            val = 0.0                                        # 악재 — 롱 억제
        best = val if best is None else max(best, val)
    return best


def axis_legacy(feat_row: dict) -> dict:
    """기존 intraday_signal 지표 → 저가중 축 {ema, rsi, bb}. NaN 은 None."""
    def _get(k):
        v = feat_row.get(k)
        try:
            f = float(v)
            return None if math.isnan(f) else f
        except (TypeError, ValueError):
            return None
    ema = 1.0 if (_get("ema_cross_up") or 0) >= 1 else (0.5 if (_get("ema_cross") or 0) >= 1 else 0.0)
    rsi = _get("rsi")
    rsi_ax = None if rsi is None else (0.8 if 30 <= rsi <= 45 else (0.0 if rsi >= 70 else 0.3))
    bb = _get("bb_pct_b")
    width = _get("bb_width")
    bb_ax = None
    if bb is not None and width is not None:
        bb_ax = 0.7 if (width < 0.01 and bb > 0.8) else (0.3 if bb > 0.5 else 0.0)   # 스퀴즈 후 상단
    return {"ema": ema, "rsi": rsi_ax, "bb": bb_ax}


def regime_er(closes, window: int = 30) -> float | None:
    """Kaufman Efficiency Ratio — |순변화| / Σ|봉간 변화| ∈ [0,1] (확정 봉만·룩어헤드 0)."""
    cs = [float(c) for c in closes]
    if len(cs) < window + 1:
        return None
    seg = cs[-(window + 1):]
    net = abs(seg[-1] - seg[0])
    path = sum(abs(seg[i + 1] - seg[i]) for i in range(window))
    if path <= 0:
        return 0.0
    return net / path


def regime_multipliers(er: float | None) -> dict:
    """추세일 → 돌파축 증폭·회귀축 감쇠, 횡보일 → 반대. 미상 → 1.0."""
    if er is None:
        return {"orb": 1.0, "volspike": 1.0, "vwap": 1.0}
    if er >= 0.35:
        return {"orb": 1.2, "volspike": 1.2, "vwap": 0.8}
    if er <= 0.20:
        return {"orb": 0.8, "volspike": 0.8, "vwap": 1.2}
    return {"orb": 1.0, "volspike": 1.0, "vwap": 1.0}


def apply_regime(axes: dict, mult: dict) -> dict:
    """축 dict 에 레짐 승수 적용 (None 은 유지, [0,1] 클램프)."""
    out = dict(axes)
    for k, m in (mult or {}).items():
        v = out.get(k)
        if v is not None:
            out[k] = min(1.0, max(0.0, v * m))
    return out


# ── 진입 가드 (순서 고정 — 첫 실패 사유 반환) ────────────────────────────────

def spread_bps(best_bid, best_ask) -> float | None:
    if not best_bid or not best_ask or best_ask <= 0 or best_ask < best_bid:
        return None
    mid = (best_bid + best_ask) / 2.0
    return (best_ask - best_bid) / mid * 10_000


def spread_cap_bps(price: float, market: str, cap_bps: float) -> float:
    """유효 스프레드 상한 — KR 은 호가단위상 1틱이 이미 수십 bps 일 수 있어 max(2틱, cap)."""
    if market.upper() == "KR" and price > 0:
        two_ticks = 2.0 * kr_tick(price) / price * 10_000
        return max(cap_bps, two_ticks)
    return cap_bps


def entry_guards(ctx: dict) -> tuple[bool, str]:
    """진입 하드가드. ctx 는 엔진이 조립한 순수 dict — 순서 고정:

    halt → EOD 창 → 일왕복 상한 → 쿨다운 → 기보유 → 신선도 → 스프레드 → qty.
    """
    if ctx.get("halt"):
        return False, "halt"
    if ctx["now_min"] >= ctx["close_min"] - ctx.get("flat_buffer_min", 15) - ctx.get("entry_cutoff_min", 30):
        return False, "eod_window"
    if ctx.get("trades_today", 0) >= ctx.get("max_trades", 6):
        return False, "max_trades"
    if not ctx.get("cooldown_ok", True):
        return False, "cooldown"
    if ctx.get("held"):
        return False, "held"
    if not ctx.get("fresh", False):
        return False, "stale_data"
    sp, cap = ctx.get("spread"), ctx.get("spread_cap")
    if sp is None or (cap is not None and sp > cap):
        return False, "spread"
    if ctx.get("qty", 0) < 1:
        return False, "qty"
    return True, "ok"


# ── 청산 판정 (우선순위 고정·봉 확정 기준) ────────────────────────────────────

def check_exit(pos: dict, bar: dict | None, score: float | None,
               now_min: int, close_min: int, cfg: dict) -> tuple[str, float] | None:
    """(exit_reason, 보수적 체결 기준가) | None.

    pos: {entry_price, stop, target, entry_min, risk_per_share}
    bar: 최신 확정 봉 {h, l, c} (없으면 EOD 만 판정 — 엔진이 REST 가로 처리)
    우선순위: stop → target → timestop → signal_collapse → eod_flat.
    """
    flat_at = close_min - int(cfg.get("flat_buffer_min", 15))
    if bar:
        c = float(bar["c"])
        if float(bar["l"]) <= pos["stop"]:
            return "stop", min(float(pos["stop"]), c)        # 갭 하향 시 종가가 더 불리 — 보수
        if float(bar["h"]) >= pos["target"]:
            return "target", float(pos["target"])
        held_min = now_min - int(pos["entry_min"])
        rps = max(float(pos.get("risk_per_share") or 0), 1e-9)
        progress_r = abs(c - float(pos["entry_price"])) / rps
        if held_min >= int(cfg.get("timestop_min", 90)) and progress_r < 0.3:
            return "timestop", c
        if score is not None and score < float(cfg.get("theta_exit", 0.25)):
            return "signal_collapse", c
        if now_min >= flat_at:
            return "eod_flat", c
        return None
    if now_min >= flat_at:
        return "eod_flat", float(pos["entry_price"])          # bar 부재 — 엔진이 REST 가로 대체
    return None


# ── 사이징·가상체결 ───────────────────────────────────────────────────────────

def position_size(sleeve_nav: float, risk_frac: float, price: float, stop: float,
                  max_pos_frac: float = 1.0 / 3.0) -> int:
    """리스크 기반 주수 — 손절 도달 시 슬리브 손실 = risk_frac. per-position 가치 캡."""
    stop_dist = price - stop
    if sleeve_nav <= 0 or price <= 0 or stop_dist <= 0:
        return 0
    qty = int(sleeve_nav * risk_frac / stop_dist)
    cap = int(sleeve_nav * max_pos_frac / price)
    return max(0, min(qty, cap))


def virtual_fill(side: str, best_bid, best_ask, last_price, market: str) -> tuple[float, float] | None:
    """shadow 가상체결 — best 호가 기준(중간가 금지) + 보수 페널티(스프레드/2 + 1틱)/주.

    반환 (체결가, 페널티/주) | None(가격 원천 전무). 호가 없으면 last + 2틱 페널티.
    """
    buy = side == "buy"
    px = (best_ask if buy else best_bid) or last_price
    if not px or px <= 0:
        return None
    tick = tick_size(px, market)
    if best_bid and best_ask and best_ask >= best_bid:
        penalty = (best_ask - best_bid) / 2.0 + tick
    else:
        penalty = 2.0 * tick                                  # 호가 미상 — 보수 가산
    return float(px), float(penalty)
