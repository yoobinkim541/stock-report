"""barbell_strategy.calculate_dca — 배율 합성 회귀 테스트 (네트워크 불필요).

머니 계산(DCA 권고 금액)에 단위 테스트 공백이 있어 신규 추가.
각 Phase(neutral·bear 0~5·bull_1·bull_2)에서 다음을 명시적으로 검증:
  - 0 < multiplier <= MAX_DCA_MULTIPLIER   (절대 상한 가드)
  - total_krw == DCA_DAILY_BASE_KRW * multiplier
  - sum(by_ticker) == total_krw            (int 절사 잔여금 가산 보존)
  - multiplier == round(base_mult * fg_adj * ml_mult, 2)  (안전 가드 무개입 시)

네트워크/ML 회피: 모든 외부 의존을 monkeypatch 로 고정값 주입.
  - bs.load_dca_weights   : 합 1.0 비중 (normal, bear 동일)
  - bs._ml_dca_blend      : 입력 비중 그대로 반환, breadth 0 (ML 중립)
  - bs._realized_vol_annual: 0.0 → leverage_dca_guard 변동성 캡 미적용
  - ml.data_pipeline.get_fg_proxy_score : 50 (중립) → F&G 보정 1.0
    (calculate_dca 가 함수 내부에서 import 하므로 원본 모듈을 패치)
"""
import pytest

import barbell_strategy as bs
import ml.data_pipeline as dp


# 합 1.0 (정규화된) 기본 비중 — 9 종목 균등 분배로 int 절사 잔여금 발생 유도
_WEIGHTS = {
    "MSFT": 0.12, "QQQI": 0.12, "ORCL": 0.11, "SAP": 0.11, "UNH": 0.11,
    "NVDA": 0.11, "GOOGL": 0.11, "SPMO": 0.11, "SGOV": 0.10,
}
assert abs(sum(_WEIGHTS.values()) - 1.0) < 1e-9


@pytest.fixture
def _isolate_dca(monkeypatch):
    """calculate_dca 외부 의존을 전부 결정론적 고정값으로 치환."""
    monkeypatch.setattr(bs, "load_dca_weights", lambda: (_WEIGHTS, _WEIGHTS))
    # ML 블렌딩: 입력 비중 그대로, breadth 0 (강도 보정 1.0)
    monkeypatch.setattr(bs, "_ml_dca_blend",
                        lambda weights, *a, **k: (dict(weights), {}, 0.0))
    # 변동성 캡 미적용 (QQQ 히스토리 fetch 회피)
    monkeypatch.setattr(bs, "_realized_vol_annual", lambda *a, **k: 0.0)
    # F&G proxy: 중립(50) → 보정 1.0. 함수 내부 import 대상이라 원본 모듈 패치.
    monkeypatch.setattr(dp, "get_fg_proxy_score", lambda *a, **k: 50.0)


# (market_type, phase_key, 기대 base_mult)
_CASES = [
    ("neutral", 0,        1.0),
    ("bear",    0,        1.0),
    ("bear",    1,        1.5),
    ("bear",    2,        2.0),
    ("bear",    3,        2.5),
    ("bear",    4,        3.0),
    ("bear",    5,        5.0),
    ("bull",    "bull_1", 0.8),
    ("bull",    "bull_2", 0.5),
]


@pytest.mark.parametrize("market_type,phase_key,base_mult", _CASES)
def test_dca_multiplier_within_bounds(_isolate_dca, market_type, phase_key, base_mult):
    """모든 Phase에서 배율이 (0, MAX_DCA_MULTIPLIER] 범위."""
    res = bs.calculate_dca(market_type, phase_key)
    mult = res["multiplier"]
    assert 0 < mult <= bs.MAX_DCA_MULTIPLIER, f"{market_type}/{phase_key}: {mult}"
    assert res["base_mult"] == base_mult


@pytest.mark.parametrize("market_type,phase_key,base_mult", _CASES)
def test_dca_total_matches_base_times_multiplier(_isolate_dca, market_type, phase_key, base_mult):
    """total_krw == int(DCA_DAILY_BASE_KRW * multiplier)."""
    res = bs.calculate_dca(market_type, phase_key)
    assert res["total_krw"] == int(bs.DCA_DAILY_BASE_KRW * res["multiplier"])


@pytest.mark.parametrize("market_type,phase_key,base_mult", _CASES)
def test_dca_by_ticker_sums_to_total(_isolate_dca, market_type, phase_key, base_mult):
    """종목별 배분 합 == total_krw (int 절사 잔여금 최대비중 가산 보존)."""
    res = bs.calculate_dca(market_type, phase_key)
    assert sum(res["by_ticker"].values()) == res["total_krw"]


@pytest.mark.parametrize("market_type,phase_key,base_mult", _CASES)
def test_dca_multiplier_is_synthesis_no_guard(_isolate_dca, market_type, phase_key, base_mult):
    """안전 가드 무개입(변동성 0·낙폭 없음) 시 배율 = base × fg × ml.

    F&G 중립(50)→1.0, ML breadth 0→1.0 이므로 결과 = base_mult.
    단 절대 상한(MAX_DCA_MULTIPLIER) 이내여야 함.
    """
    res = bs.calculate_dca(market_type, phase_key)
    expected = round(base_mult * res["fg_adj"] * res["ml_mult"], 2)
    expected = min(expected, bs.MAX_DCA_MULTIPLIER)
    assert res["multiplier"] == expected
    # 고정 모킹값 확인
    assert res["fg_adj"] == 1.0
    assert res["ml_mult"] == 1.0
    # 변동성 캡 미적용 → vol_scale 1.0, halt 없음
    assert res["vol_scale"] == 1.0
    assert res["dca_halt"] is False


# ── F&G 보정이 실제로 곱해지는지 (극단 공포 → 증액) ──────────────────────
def test_dca_fear_greed_amplifies_neutral_phase(monkeypatch):
    """극도공포(F&G≤20)면 neutral(base 1.0)에서 배율 1.2배."""
    monkeypatch.setattr(bs, "load_dca_weights", lambda: (_WEIGHTS, _WEIGHTS))
    monkeypatch.setattr(bs, "_ml_dca_blend",
                        lambda weights, *a, **k: (dict(weights), {}, 0.0))
    monkeypatch.setattr(bs, "_realized_vol_annual", lambda *a, **k: 0.0)
    monkeypatch.setattr(dp, "get_fg_proxy_score", lambda *a, **k: 10.0)  # 극도공포

    res = bs.calculate_dca("neutral", 0)
    assert res["fg_adj"] == 1.2
    assert res["multiplier"] == round(1.0 * 1.2 * 1.0, 2)   # 1.2
    assert res["total_krw"] == int(bs.DCA_DAILY_BASE_KRW * 1.2)


def test_dca_extreme_phase_skips_fg_amplification(monkeypatch):
    """bear 2+ 극단 Phase 는 F&G 보정 생략 (상관 신호 과잉증폭 차단)."""
    monkeypatch.setattr(bs, "load_dca_weights", lambda: (_WEIGHTS, _WEIGHTS))
    monkeypatch.setattr(bs, "_ml_dca_blend",
                        lambda weights, *a, **k: (dict(weights), {}, 0.0))
    monkeypatch.setattr(bs, "_realized_vol_annual", lambda *a, **k: 0.0)
    monkeypatch.setattr(dp, "get_fg_proxy_score", lambda *a, **k: 10.0)  # 극도공포

    res = bs.calculate_dca("bear", 2)
    assert res["fg_adj"] == 1.0          # 극단 Phase → 보정 생략
    assert res["multiplier"] == 2.0      # base 2.0 그대로


# ── 절대 상한 가드: 합성 배율이 MAX 초과해도 캡 ──────────────────────────
def test_dca_absolute_cap_enforced(monkeypatch):
    """ML 강세(×1.1)가 bear 5(base 5.0)에 곱해져도 MAX_DCA_MULTIPLIER 로 캡."""
    monkeypatch.setattr(bs, "load_dca_weights", lambda: (_WEIGHTS, _WEIGHTS))
    # breadth 0.01 → _ml_breadth_mult 1.1 (강세) → 5.0*1.1=5.5 > 5.0 캡
    monkeypatch.setattr(bs, "_ml_dca_blend",
                        lambda weights, *a, **k: (dict(weights), {}, 0.01))
    monkeypatch.setattr(bs, "_realized_vol_annual", lambda *a, **k: 0.0)
    monkeypatch.setattr(dp, "get_fg_proxy_score", lambda *a, **k: 50.0)

    res = bs.calculate_dca("bear", 5)
    assert res["ml_mult"] == 1.1
    assert res["multiplier"] == bs.MAX_DCA_MULTIPLIER   # 5.5 → 5.0 캡
    assert res["multiplier"] <= bs.MAX_DCA_MULTIPLIER


# ── 낙폭 정지: drawdown ≤ HALT 이면 배율 1.0 정지 ───────────────────────
def test_dca_drawdown_halt_caps_to_one(monkeypatch):
    """낙폭이 LEVERAGE_HALT_DRAWDOWN 이하면 배율 1.0 정지(전소 방어)."""
    monkeypatch.setattr(bs, "load_dca_weights", lambda: (_WEIGHTS, _WEIGHTS))
    monkeypatch.setattr(bs, "_ml_dca_blend",
                        lambda weights, *a, **k: (dict(weights), {}, 0.0))
    monkeypatch.setattr(bs, "_realized_vol_annual", lambda *a, **k: 0.0)
    monkeypatch.setattr(dp, "get_fg_proxy_score", lambda *a, **k: 50.0)

    # bear 5 (base 5.0)인데 낙폭 -60% ≤ -55% → 1.0 정지
    res = bs.calculate_dca("bear", 5, drawdown_pct=bs.LEVERAGE_HALT_DRAWDOWN - 5.0)
    assert res["dca_halt"] is True
    assert res["multiplier"] == 1.0
    assert res["total_krw"] == int(bs.DCA_DAILY_BASE_KRW * 1.0)
    assert sum(res["by_ticker"].values()) == res["total_krw"]
