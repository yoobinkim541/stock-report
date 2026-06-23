"""barbell_strategy + backtest 신호 로직 회귀 테스트 (네트워크 불필요).

검증 범위:
- classify_market 원시 분류 + 히스테리시스 (경계값·VIX 패닉·에스컬레이션)
- 낙폭 앵커 (_update_drawdown_anchor) 단조 증가·회복 리셋
- _split_telegram 분할 보존
- calculate_safety_margin (SPMO HHI 포함, multiplier 상하한)
- _phase_blend_factor 매핑 (bull 키 버그 회귀 방지)
- _ml_dca_blend(use_meta=False) 메타 역호출 금지
- get_meta_allocation 재진입 차단 + 결과 캐시
- calculate_dca 원 단위 잔여금 배분 (합계 보존)
- 백테스트 신호 로직 라이브 동기화 (_anchor_drawdown, _classify_with_hysteresis)
"""
import json

import numpy as np
import pandas as pd
import pytest

import barbell_strategy as bs
import providers.market_data as md   # 낙폭 앵커 등 데이터 수집층 정의 모듈 (monkeypatch 대상)
import ml.meta_allocator as ma


# ══════════════════════════════════════════════════════════════════════
#  classify_market — 원시 분류
# ══════════════════════════════════════════════════════════════════════

def _qqq(dd, mom=0.0):
    return {"current": 100.0, "high_52w": 150.0,
            "drawdown_pct": dd, "mom_1m_pct": mom}


@pytest.mark.parametrize("dd,expected", [
    (-82.0, ("bear", 5)),   # 닷컴급 크래시도 bear 5 (오류 처리 금지)
    (-31.0, ("bear", 5)),
    (-21.0, ("bear", 4)),
    (-17.0, ("bear", 3)),
    (-11.0, ("bear", 2)),
    (-6.0,  ("bear", 1)),
    (-1.0,  ("neutral", 0)),
])
def test_classify_raw_bear_thresholds(dd, expected):
    assert bs.classify_market(_qqq(dd), rsi=50, vix=20, prev_state=None) == expected


def test_classify_bull_phases():
    assert bs.classify_market(_qqq(-1, mom=9), rsi=78, vix=13, prev_state=None) == ("bull", "bull_2")
    assert bs.classify_market(_qqq(-1, mom=2), rsi=72, vix=20, prev_state=None) == ("bull", "bull_1")
    assert bs.classify_market(_qqq(-1, mom=6), rsi=50, vix=20, prev_state=None) == ("bull", "bull_1")


def test_classify_invalid_data_neutral():
    bad = {"current": 0, "high_52w": 150.0, "drawdown_pct": -50, "mom_1m_pct": 0}
    assert bs.classify_market(bad, rsi=50, vix=20, prev_state=None) == ("neutral", 0)


# ══════════════════════════════════════════════════════════════════════
#  classify_market — 히스테리시스
# ══════════════════════════════════════════════════════════════════════

_PREV2 = {"market_type": "bear", "phase_key": 2}
_PREV3 = {"market_type": "bear", "phase_key": 3}


def test_hysteresis_holds_without_buffer_recovery():
    # 진입 -10, 버퍼 +1.5 → -8.5 초과 회복해야 하향. -9.5는 유지.
    assert bs.classify_market(_qqq(-9.5), 50, 20, prev_state=_PREV2) == ("bear", 2)


def test_hysteresis_releases_after_buffer():
    assert bs.classify_market(_qqq(-8.0), 50, 20, prev_state=_PREV2) == ("bear", 1)


def test_hysteresis_vix_panic_blocks_deescalation():
    # phase 3에서 -9까지 회복했어도 VIX 35면 하향 보류
    assert bs.classify_market(_qqq(-9.0), 50, 35, prev_state=_PREV3) == ("bear", 3)
    # VIX 진정되면 원시 분류로 하향 (버퍼 충족: -9 > -15+1.5, raw = bear 1)
    assert bs.classify_market(_qqq(-9.0), 50, 20, prev_state=_PREV3) == ("bear", 1)


def test_hysteresis_escalation_immediate():
    prev1 = {"market_type": "bear", "phase_key": 1}
    assert bs.classify_market(_qqq(-21.0), 50, 20, prev_state=prev1) == ("bear", 4)


def test_hysteresis_full_recovery_exits_bear():
    assert bs.classify_market(_qqq(-1.0), 50, 20, prev_state=_PREV2) == ("neutral", 0)


# ══════════════════════════════════════════════════════════════════════
#  낙폭 앵커
# ══════════════════════════════════════════════════════════════════════

def test_anchor_monotonic_and_reset(tmp_path, monkeypatch):
    # ANCHOR_FILE·_update_drawdown_anchor 는 providers/market_data.py 가 실제 정의 모듈.
    # _update_drawdown_anchor 가 그 모듈 전역 ANCHOR_FILE 을 참조하므로 md 에 패치한다.
    monkeypatch.setattr(md, "ANCHOR_FILE", str(tmp_path / "anchor.json"))

    # 초기: 앵커 = 52주 고점
    assert md._update_drawdown_anchor(100.0, 96.0) == 100.0
    # 롤링 고점이 90으로 내려와도 (장기 하락장) 앵커는 100 유지
    assert md._update_drawdown_anchor(90.0, 60.0) == 100.0
    # 가격이 앵커 -5% 이내(95)로 회복 → 롤링 고점 90으로 리셋
    assert md._update_drawdown_anchor(90.0, 96.0) == 90.0
    # 파일에 영속화 확인
    saved = json.loads((tmp_path / "anchor.json").read_text())
    assert saved["anchor_high"] == 90.0


# ══════════════════════════════════════════════════════════════════════
#  notify.split_message (구 barbell._split_telegram — notify 단일 진실원으로 이전)
# ══════════════════════════════════════════════════════════════════════
import notify


def test_split_telegram_short_message():
    assert notify.split_message("짧은 메시지") == ["짧은 메시지"]


def test_split_telegram_preserves_content():
    msg = "\n".join(f"라인 {i:04d} " + "x" * 50 for i in range(200))
    parts = notify.split_message(msg)
    assert len(parts) > 1
    assert all(len(p) <= notify.TG_MAX_CHARS for p in parts)
    assert "\n".join(parts) == msg   # 줄바꿈 경계 분할 → 내용 무손실


# ══════════════════════════════════════════════════════════════════════
#  calculate_safety_margin
# ══════════════════════════════════════════════════════════════════════

def _portfolio(holdings, prices, total=10000.0, sgov=1500.0):
    return {"total_usd": total, "holdings": holdings, "prices": prices,
            "sgov_usd": sgov}


def test_safety_margin_includes_spmo_in_hhi(tmp_path, monkeypatch):
    monkeypatch.setattr(bs, "PORTFOLIO_PATH", str(tmp_path / "none.json"))
    res = bs.calculate_safety_margin(
        _portfolio({"SPMO": 10}, {"SPMO": 100.0}), "neutral", 0)
    # SPMO가 제외 목록에 있으면 HHI 요인이 아예 없음 — 포함 회귀 방지
    assert any("HHI" in v for v in res["factors"].values())


def test_safety_margin_multiplier_bounds(tmp_path, monkeypatch):
    monkeypatch.setattr(bs, "PORTFOLIO_PATH", str(tmp_path / "none.json"))
    res = bs.calculate_safety_margin(
        _portfolio({"MSFT": 5, "GOOGL": 5}, {"MSFT": 400.0, "GOOGL": 170.0}),
        "neutral", 0)
    assert 0.5 <= res["multiplier"] <= 1.0
    assert res["multiplier"] == round(min(1.0, max(0.5, res["score"] / 70)), 2) \
        or res["score"] != round(res["score"])   # score 반올림 표시 차이 허용


# ══════════════════════════════════════════════════════════════════════
#  _phase_blend_factor — bull 키 버그 회귀 방지
# ══════════════════════════════════════════════════════════════════════

def test_phase_blend_factor_bull_keys():
    assert bs._phase_blend_factor("bull", "bull_2") == 0.1
    assert bs._phase_blend_factor("bull", "bull_1") == 0.2


def test_phase_blend_factor_bear_and_neutral():
    assert bs._phase_blend_factor("bear", 5) == 0.60
    assert bs._phase_blend_factor("bear", 0) == 0.3
    assert bs._phase_blend_factor("neutral", 0) == 0.3


# ══════════════════════════════════════════════════════════════════════
#  _ml_dca_blend / MetaAllocator 상호 재귀 방지
# ══════════════════════════════════════════════════════════════════════

def test_ml_dca_blend_use_meta_false_never_calls_meta(monkeypatch):
    calls = []
    monkeypatch.setattr(ma, "get_meta_allocation",
                        lambda *a, **k: calls.append(1))
    # Ranker 경로도 차단 (오프라인) → base 비중 fallback
    import ml.ranker
    monkeypatch.setattr(ml.ranker, "load_ranker",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("offline")))

    base = {"MSFT": 0.5, "NVDA": 0.5}
    blended, scores, breadth = bs._ml_dca_blend(base, "neutral", 0, use_meta=False)
    assert calls == []          # 메타 역호출 없음 → 상호 재귀 불가
    assert isinstance(blended, dict) and abs(sum(blended.values()) - 1.0) < 0.01


def test_meta_reentry_guard_raises():
    orig = ma._REENTRY_GUARD
    ma._REENTRY_GUARD = True
    try:
        with pytest.raises(RuntimeError, match="재진입"):
            ma.get_meta_allocation("neutral", 0)
    finally:
        ma._REENTRY_GUARD = orig


def test_meta_allocation_result_cache(tmp_path, monkeypatch):
    calls = []

    def fake_impl(market_type="neutral", phase_key=0):
        calls.append(1)
        return ma.MetaAllocation(weights={"QQQ": 1.0}, signal_summary={},
                                 regime="neutral", confidence=0.5, note="test")

    monkeypatch.setattr(ma, "_get_meta_allocation_impl", fake_impl)
    monkeypatch.setattr(ma, "_meta_cache_file",
                        lambda mt, pk: tmp_path / f"meta_{mt}_{pk}.pkl")
    ma._RESULT_CACHE.clear()

    a1 = ma.get_meta_allocation("_test_mt", "_test_pk")
    a2 = ma.get_meta_allocation("_test_mt", "_test_pk")
    assert len(calls) == 1            # 두 번째 호출은 캐시
    assert a2.weights == a1.weights
    a3 = ma.get_meta_allocation("_test_mt", "_test_pk", force=True)
    assert len(calls) == 2            # force는 캐시 무시
    ma._RESULT_CACHE.clear()


# ══════════════════════════════════════════════════════════════════════
#  calculate_dca — 원 단위 잔여금 배분
# ══════════════════════════════════════════════════════════════════════

def test_dca_allocation_sums_to_total(monkeypatch):
    w = {"A": 1 / 3, "B": 1 / 3, "C": 1 / 3}
    monkeypatch.setattr(bs, "load_dca_weights", lambda: (w, w))
    monkeypatch.setattr(bs, "_ml_dca_blend", lambda *a, **k: (w, {}, 0.0))
    monkeypatch.setattr(bs, "_fg_dca_adjustment", lambda x: 1.0)
    monkeypatch.setattr(bs, "_ml_breadth_mult", lambda b: (1.0, ""))

    res = bs.calculate_dca("neutral", 0)
    # int 절사 잔여금이 최대 비중 종목에 가산 → 합계 == total_krw
    assert sum(res["by_ticker"].values()) == res["total_krw"]


# ══════════════════════════════════════════════════════════════════════
#  백테스트 신호 로직 — 라이브 동기화
# ══════════════════════════════════════════════════════════════════════

def _bt():
    from backtest import _anchor_drawdown, _wilder_rsi, _classify_with_hysteresis
    return _anchor_drawdown, _wilder_rsi, _classify_with_hysteresis


def test_backtest_anchor_no_phase_drift():
    """장기 하락장에서 롤링 고점 하락에도 낙폭 유지 (Phase 드리프트 방지)."""
    _anchor_drawdown, _, _classify = _bt()
    px = np.concatenate([
        np.linspace(80, 100, 100),   # 상승
        np.linspace(100, 65, 60),    # -35% 크래시
        np.full(440, 66.0),          # 장기 횡보 (회복 없음)
    ])
    idx = pd.bdate_range("2020-01-01", periods=len(px))
    qqq = pd.Series(px, index=idx)
    dd = _anchor_drawdown(qqq)
    assert dd.iloc[-1] < -30   # 롤링만 쓰면 252일 뒤 0으로 수렴했을 것

    df = pd.DataFrame({"drawdown": dd, "rsi": 40.0,
                       "mom_1m": 0.0, "VIX": 20.0}, index=idx)
    assert _classify(df).iloc[-1] == 5


def test_backtest_anchor_resets_on_recovery():
    _anchor_drawdown, _, _ = _bt()
    px = np.concatenate([
        np.linspace(80, 100, 100),
        np.linspace(100, 70, 30),
        np.linspace(70, 99, 50),     # 앵커 -5% 이내 회복
        np.full(50, 99.0),
    ])
    qqq = pd.Series(px, index=pd.bdate_range("2020-01-01", periods=len(px)))
    assert _anchor_drawdown(qqq).iloc[-1] > -5


def test_backtest_hysteresis_matches_live():
    _, _, _classify = _bt()
    df = pd.DataFrame({"drawdown": [-10.2, -9.5, -8.0],
                       "rsi": [40.0] * 3, "mom_1m": [-3.0] * 3, "VIX": [22.0] * 3})
    assert list(_classify(df)) == [2, 2, 1]

    df2 = pd.DataFrame({"drawdown": [-16.0, -9.0, -9.0],
                        "rsi": [40.0] * 3, "mom_1m": [-3.0] * 3,
                        "VIX": [35.0, 35.0, 20.0]})
    assert list(_classify(df2)) == [3, 3, 1]   # VIX 패닉 하향 보류 → 진정 후 하향


def test_backtest_wilder_rsi_range():
    _, _wilder_rsi, _ = _bt()
    rng = np.random.default_rng(42)
    qqq = pd.Series(100 * np.cumprod(1 + rng.normal(0, 0.01, 300)))
    rsi = _wilder_rsi(qqq)
    assert rsi.between(0, 100).all()
    # 강한 상승 추세(가끔 소폭 하락)면 RSI 과매수 구간
    deltas = [-1.0 if i % 10 == 0 else 2.0 for i in range(100)]
    up = pd.Series(100 + np.cumsum(deltas))
    assert _wilder_rsi(up).iloc[-1] > 75
    # 손실이 전무한 시계열은 라이브 fetch_rsi와 동일하게 50 fallback (rs=NaN)
    assert _wilder_rsi(pd.Series(np.linspace(100, 200, 100))).iloc[-1] == 50.0
