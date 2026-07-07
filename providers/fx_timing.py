#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""USD/KRW 환전 타이밍 지표 (원화→달러).

포트폴리오가 전부 달러 자산(미국주식)이라 원화로 달러를 사는 구조 → **환전 레이트**가
실현 수익률에 직접 영향을 준다. 이 모듈은 주식 DCA와 **동일한 '분할 규율' 철학**을 환전에
적용한다: USD/KRW 가 역사적으로 낮을수록(=원화 강세, 달러가 싸다) 더 많이 환전, 높을수록
(원화 약세) 축소/대기.

**정직 원칙**: 이것은 환율 방향 *예측*이 아니다. 환율은 주식만큼 평균회귀 엣지가 뚜렷하지
않으므로, 백테스트 게이트를 통과하기 전까지 **정보·표시용 분할 환전 가이드**로만 쓴다
(레포의 다른 티어들과 동일 — 무엣지면 정직 라벨). 실제 환전 집행은 항상 사람 수동.

순수 코어(`compute_fx_timing`)는 네트워크 무관·리스트 입력 → 단위 테스트 가능.
`fetch_fx_timing`만 yfinance 를 건드리고 graceful 폴백한다.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))

# 분할 배율 절대 상한 (환전 폭주 차단 — 주식 BARBELL_MAX_DCA_MULT 와 같은 취지)
MAX_FX_MULT = float(os.getenv("FX_TIMING_MAX_MULT", "2.0"))

# 관측 창(거래일). 3년 ≈ 756 거래일. 원화/달러 사이클을 담기에 충분하고 과도하게 길지 않음.
DEFAULT_WINDOW = int(os.getenv("FX_TIMING_WINDOW_DAYS", "756"))

HONEST_LABEL = "무엣지·분할 규율(환율 예측 아님·집행은 수동)"

# percentile(현재 환율의 창 내 위치, 낮을수록 원화 강세=환전 유리) → 배율·판정.
# 오름차순 임계값; 첫 매칭 존 적용. 주식 Phase 표와 같은 이산 존 미학.
FX_ZONES = [
    # (percentile 상한, 배율, 이모지, 짧은 판정, 행동 설명)
    (0.10, 2.0, "🟢", "환전 적극", "원화 매우 강세 — 달러 적극 분할 환전"),
    (0.30, 1.5, "🟢", "환전 유리", "원화 강세 — 평소보다 더 환전"),
    (0.60, 1.0, "🟡", "중립·분할", "역사적 중간 — 정액 분할 유지"),
    (0.85, 0.6, "🟠", "환전 축소", "원화 약세 — 환전 비중 축소"),
    (1.01, 0.3, "🔴", "대기", "원화 약세 — 급하지 않으면 대기·최소 환전"),
]

_MIN_POINTS = 30  # 이보다 적으면 판정 보류


def _clean(closes) -> list[float]:
    out = []
    for v in closes or []:
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        # NaN 및 비현실 환율 제거 (yfinance 결측/오류 방어)
        if f != f or not (900.0 < f < 2500.0):
            continue
        out.append(f)
    return out


def _percentile_rank(value: float, series: list[float]) -> float:
    """value 가 series 에서 차지하는 위치 [0,1]. 0=최저(원화 최강)·1=최고(원화 최약)."""
    if not series:
        return 0.5
    below = sum(1 for x in series if x < value)
    equal = sum(1 for x in series if x == value)
    # midrank (동점 절반 반영) — 경계 안정성
    return (below + equal / 2.0) / len(series)


def _rsi(closes: list[float], period: int = 14):
    """Wilder RSI. 순수 파이썬. 데이터 부족 시 None."""
    if len(closes) < period + 1:
        return None
    gains = losses = 0.0
    for i in range(1, period + 1):
        ch = closes[i] - closes[i - 1]
        if ch >= 0:
            gains += ch
        else:
            losses -= ch
    avg_gain = gains / period
    avg_loss = losses / period
    for i in range(period + 1, len(closes)):
        ch = closes[i] - closes[i - 1]
        gain = ch if ch > 0 else 0.0
        loss = -ch if ch < 0 else 0.0
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100.0 - 100.0 / (1.0 + rs), 1)


def _ma_gap_pct(closes: list[float], window: int = 200):
    """현재 환율 vs 최근 window 평균 갭%. 음수=평균 하회(달러 상대적 저렴)."""
    if len(closes) < 2:
        return None
    w = min(window, len(closes))
    ma = sum(closes[-w:]) / w
    if ma <= 0:
        return None
    return round((closes[-1] / ma - 1.0) * 100.0, 2)


def _zone_for(percentile: float):
    for thr, mult, emoji, verdict, action in FX_ZONES:
        if percentile <= thr:
            return mult, emoji, verdict, action
    # 이론상 도달 불가(마지막 임계 1.01) — 방어
    return FX_ZONES[-1][1:]


def compute_fx_timing(
    closes,
    *,
    window: int = DEFAULT_WINDOW,
    rsi_period: int = 14,
    ma_window: int = 200,
    as_of: str | None = None,
) -> dict:
    """USD/KRW 종가 시퀀스(오래된→최신) → 환전 타이밍 판정. 네트워크 무관·순수.

    반환 dict: rate·percentile·pct_display·ma200_gap_pct·rsi·multiplier·emoji·
    verdict·action·window_days·low·high·honest_label·as_of·ok
    """
    ts = as_of or datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    series = _clean(closes)
    if len(series) < _MIN_POINTS:
        return {
            "ok": False,
            "rate": series[-1] if series else None,
            "percentile": None,
            "pct_display": None,
            "ma200_gap_pct": None,
            "rsi": None,
            "multiplier": 1.0,
            "emoji": "⚪",
            "verdict": "데이터 부족",
            "action": "환율 이력 부족 — 정액 분할 유지",
            "window_days": len(series),
            "low": min(series) if series else None,
            "high": max(series) if series else None,
            "honest_label": HONEST_LABEL,
            "as_of": ts,
        }

    win = series[-window:] if window and len(series) > window else series
    rate = series[-1]
    pct = _percentile_rank(rate, win)
    mult, emoji, verdict, action = _zone_for(pct)
    mult = round(min(mult, MAX_FX_MULT), 2)

    return {
        "ok": True,
        "rate": round(rate, 1),
        "percentile": round(pct, 4),
        "pct_display": int(round(pct * 100)),
        "ma200_gap_pct": _ma_gap_pct(series, ma_window),
        "rsi": _rsi(series, rsi_period),
        "multiplier": mult,
        "emoji": emoji,
        "verdict": verdict,
        "action": action,
        "window_days": len(win),
        "low": round(min(win), 1),
        "high": round(max(win), 1),
        "honest_label": HONEST_LABEL,
        "as_of": ts,
    }


def fetch_fx_timing(window: int = DEFAULT_WINDOW) -> dict:
    """USDKRW=X 3년 종가 조회 → compute_fx_timing. graceful(조회 실패 시 ok=False)."""
    try:
        import yfinance as yf

        hist = yf.Ticker("USDKRW=X").history(period="3y")
        if hist is not None and not hist.empty:
            closes = [float(x) for x in hist["Close"].tolist()]
            return compute_fx_timing(closes, window=window)
    except Exception:
        pass
    return compute_fx_timing([], window=window)


def _b(text: str, html: bool) -> str:
    return f"<b>{text}</b>" if html else text


def render_fx_timing(timing: dict, *, html: bool = False) -> str:
    """리포트/텔레그램 공용 렌더러. html=True 면 텔레그램 리치텍스트(굵게), False 면 평문.

    (크론·리포트·봇 단일 진실원 — CLAUDE.md '공유 빌더 html= 파라미터' 규약).
    """
    if not timing:
        return ""
    title = _b("💱 환전 타이밍 (원화→달러)", html)
    if not timing.get("ok"):
        return f"{title}\n- {timing.get('action', '데이터 부족')}"

    rate = timing["rate"]
    pct = timing["pct_display"]
    mult = timing["multiplier"]
    gap = timing.get("ma200_gap_pct")
    rsi = timing.get("rsi")
    lo, hi = timing.get("low"), timing.get("high")
    yrs = round(timing.get("window_days", 0) / 252.0, 1)

    verdict_line = f"{timing['emoji']} {_b(timing['verdict'], html)} — {timing['action']}"
    lines = [
        title,
        f"- 현재 {_b(f'{rate:,.1f}원', html)}  ·  {yrs}년 위치 {pct}%ile (낮을수록 원화 강세)",
        f"- 분할 환전 배율 {_b(f'{mult:g}×', html)} (평소 정액 대비)",
        f"- {verdict_line}",
    ]
    ctx = []
    if gap is not None:
        ctx.append(f"MA200 갭 {gap:+.1f}%")
    if rsi is not None:
        ctx.append(f"RSI {rsi:g}")
    if lo is not None and hi is not None:
        ctx.append(f"{yrs}년 밴드 {lo:,.0f}~{hi:,.0f}")
    if ctx:
        lines.append("- " + " · ".join(ctx))
    lines.append(f"- ℹ️ {timing['honest_label']}")
    return "\n".join(lines)


if __name__ == "__main__":  # 수동 점검
    import json

    t = fetch_fx_timing()
    print(json.dumps(t, ensure_ascii=False, indent=2))
    print()
    print(render_fx_timing(t))
