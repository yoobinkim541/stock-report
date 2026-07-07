#!/usr/bin/env python3
"""test_fx_timing.py — USD/KRW 환전 타이밍 지표 (무네트워크·순수 코어).

검증: percentile 존 매핑(원화 강세→고배율·약세→저배율·중립 1.0×) · MAX 클램프 ·
데이터 부족 graceful · Wilder RSI · midrank percentile · NaN/비현실 환율 필터 ·
공용 렌더러(html/plain).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from providers import fx_timing
from providers.fx_timing import (
    compute_fx_timing,
    render_fx_timing,
    _rsi,
    _percentile_rank,
    _ma_gap_pct,
)


# ── percentile 존 → 배율·판정 ───────────────────────────────────────────────

def test_won_strong_high_multiplier():
    # 창의 대부분이 고환율(원화 약세)인데 현재가 최저 → 원화 강세 → 적극 환전
    closes = [1450.0] * 100 + [1300.0]
    t = compute_fx_timing(closes)
    assert t["ok"] is True
    assert t["pct_display"] <= 10
    assert t["multiplier"] == 2.0
    assert t["verdict"] == "환전 적극"


def test_won_weak_low_multiplier():
    # 현재가 창 최고(원화 약세) → 대기·최소 환전
    closes = [1300.0] * 100 + [1450.0]
    t = compute_fx_timing(closes)
    assert t["ok"] is True
    assert t["pct_display"] >= 85
    assert t["multiplier"] == 0.3
    assert t["verdict"] == "대기"


def test_neutral_middle_is_flat_multiplier():
    closes = [1300.0 + i for i in range(101)] + [1350.0]  # 현재 1350 = 중앙값
    t = compute_fx_timing(closes)
    assert 40 <= t["pct_display"] <= 60
    assert t["multiplier"] == 1.0
    assert t["verdict"] == "중립·분할"


def test_multiplier_never_exceeds_cap(monkeypatch):
    monkeypatch.setattr(fx_timing, "MAX_FX_MULT", 1.2)
    closes = [1450.0] * 100 + [1300.0]  # 원래 2.0× 존
    t = compute_fx_timing(closes)
    assert t["multiplier"] == 1.2  # 상한으로 클램프


# ── graceful / 입력 위생 ────────────────────────────────────────────────────

def test_insufficient_data_is_graceful():
    t = compute_fx_timing([1350.0] * 5)
    assert t["ok"] is False
    assert t["multiplier"] == 1.0
    assert t["verdict"] == "데이터 부족"


def test_filters_nan_and_unrealistic_rates():
    good = [1350.0 + (i % 5) for i in range(40)]
    dirty = good + [None, "x", float("nan"), 50.0, 3000.0]  # 전부 제거되어야
    t = compute_fx_timing(dirty)
    assert t["ok"] is True
    assert t["window_days"] == 40  # 40개만 살아남음


def test_empty_input_graceful():
    t = compute_fx_timing([])
    assert t["ok"] is False
    assert t["rate"] is None


# ── 지표 헬퍼 ────────────────────────────────────────────────────────────────

def test_rsi_all_gains_is_100():
    assert _rsi([1000.0 + i for i in range(30)]) == 100.0


def test_rsi_all_losses_is_zero():
    assert _rsi([1000.0 - i for i in range(30)]) == 0.0


def test_rsi_insufficient_returns_none():
    assert _rsi([1350.0, 1351.0], period=14) is None


def test_percentile_midrank():
    # value 5 in 1..10 → below=4, equal=1 → 4.5/10
    assert abs(_percentile_rank(5.0, [float(i) for i in range(1, 11)]) - 0.45) < 1e-9


def test_percentile_empty_is_half():
    assert _percentile_rank(1350.0, []) == 0.5


def test_ma_gap_below_average_is_negative():
    closes = [1400.0] * 200 + [1350.0]
    gap = _ma_gap_pct(closes, 200)
    assert gap is not None and gap < 0


# ── 공용 렌더러 ──────────────────────────────────────────────────────────────

def test_render_plain_has_no_html_tags():
    t = compute_fx_timing([1450.0] * 100 + [1300.0])
    out = render_fx_timing(t, html=False)
    assert "<b>" not in out
    assert "환전 적극" in out
    assert "배율" in out
    assert t["honest_label"] in out


def test_render_html_bolds():
    t = compute_fx_timing([1450.0] * 100 + [1300.0])
    out = render_fx_timing(t, html=True)
    assert "<b>" in out


def test_render_graceful_when_not_ok():
    t = compute_fx_timing([])
    out = render_fx_timing(t, html=False)
    assert out  # 빈 문자열 아님
    assert "환전" in out
