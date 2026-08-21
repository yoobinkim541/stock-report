"""tests/test_us_policy_quality_unit.py — quality 축 ROE 단위 정합 (무네트워크·순수).

감사 후속(2026-08-21): us_policy.extract_features 의 quality 축이

    quality = _clamp01(roe / 30.0)      # 주석: "ROE 0~30% → 0~1"

로 **퍼센트**(30 = 30%)를 기대하는데, 공급원 providers/earnings_data.valuation_metrics
는 yfinance `returnOnEquity` 를 **분수 그대로**(0.34 = 34%) 넘긴다. 100배 축소되어
모든 실제 종목의 quality 가 0.01~0.05 로 눌렸다(가중치 0.20 이 사실상 무력화).

실측 대조(라이브):
    MSFT roe=0.3404 → quality 0.0113 (실제 34.0%)
    NVDA roe=1.1429 → quality 0.0381 (실제 114.3%)
    AAPL roe=1.4875 → quality 0.0496 (실제 148.8%)
섀도 원장에 적재된 값과 정확히 일치 → 라이브 선택에 그대로 반영되고 있었음.

부작용: 레버리지 ETF 는 earnings={} 라 quality 가 기본값 0.5 를 받는데, 실제 종목은
버그로 ~0.01 을 받아 **데이터가 없는 쪽이 이기는** 역전이 발생했다(섀도 첫날 상위 10위
전부 레버리지 ETF).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ml import us_policy  # noqa: E402


def _q(roe):
    return us_policy.extract_features({}, {"roe": roe}, {})["quality"]


def test_roe_fraction_is_interpreted_as_percent():
    """yfinance 분수 표기(0.30 = 30%) 를 올바로 해석해야 한다."""
    assert _q(0.30) == 0.5           # ROE 30% → 중간값 0.5 (0~60% 스케일)
    assert _q(0.60) == 1.0           # ROE 60%+ → 상한
    assert _q(0.15) == 0.25          # ROE 15% → 0.25


def test_high_roe_names_score_near_top():
    """NVDA(114%)·AAPL(149%) 같은 초고 ROE 는 상한에 붙어야 한다(0.03 이 아니라)."""
    assert _q(1.14288) == 1.0
    assert _q(1.4875101) == 1.0
    assert _q(0.34039) > 0.5         # MSFT 34% — 평균 이상


def test_low_roe_penalized():
    assert _q(0.03) < 0.1            # ROE 3% → 낮은 점수
    assert _q(0.0) == 0.5            # 데이터 없음(0) → 중립 유지(기존 동작)


def test_negative_roe_clamped_not_crash():
    assert _q(-0.25) == 0.0          # 적자 기업 → 0 (clamp)


def test_quality_discriminates_across_universe():
    """회귀 방어 — 실제 ROE 분포에서 quality 가 서로 다른 값을 내야 한다.

    버그 시절엔 전 종목이 0.01~0.05 로 뭉쳐 변별력이 사실상 0 이었다.
    """
    roes = [0.34039, 1.14288, 1.4875, 0.0521, 0.2013, 0.4402]
    qs = [_q(r) for r in roes]
    assert max(qs) - min(qs) > 0.5, f"변별력 부족: {qs}"


def test_missing_roe_still_neutral():
    """ROE 키 자체가 없으면 기존대로 0.5(중립) — 레버리지 ETF 경로 불변."""
    assert us_policy.extract_features({}, {}, {})["quality"] == 0.5
